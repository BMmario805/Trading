"""Perfiles especializados de la Mesa Siete.

Reutilizan el radar (`grok-x-pulse`) y el commander (`grok-desk-commander`).
No abren una búsqueda nueva si el lote ya está en caché, no llaman a una API
de pago y no arrancan subagentes en el puente de ejecución. El veredicto no
autoriza órdenes ni modifica stops, tamaño ni el modo demo.
"""

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JOINT_DAILY_CAP = 4
CACHE_TTL = 2700.0
RECYCLE_AFTER = 36 * 3600.0
FRESH_CATALYSTS = ('listing', 'institutional', 'macro', 'protocol')
CAUSAL_FEATURES = (
    'atr_pct', 'extension_atr', 'spread_to_atr', 'rs_score',
    'funding_rate', 'oi_change_pct', 'hour_utc',
)
PROTECTED_PATHS = (
    'backend/app/subscription_bridge.py',
    'backend/app/portfolio_risk.py',
    'backend/app/runtime_guard.py',
)

# Una frase por perfil. Los agentes de `.grok/agents/` deben repetirla tal cual.
SUCCESS = {
    'news-verifier': (
        'El veredicto es usable solo si las fechas cuadran, hay al menos dos dominios '
        'independientes, no hay contradicción y la noticia no está reciclada.'
    ),
    'quant-researcher': (
        'La propuesta es válida solo si la hipótesis es falsable, los costes son netos, '
        'las variables son causales y el holdout no se usa para elegir.'
    ),
    'technical-reviewer': (
        'El cambio se acepta solo si el fallo es demostrable y queda cubierto por una '
        'prueba de regresión que no toca el puente ni los controles de riesgo.'
    ),
}

PROFILES = {
    'news-verifier': dict(
        reuses=('grok-x-pulse', 'grok-desk-commander'),
        activates='Incertidumbre del radar: riesgo de titular, fakeout, trampa bilateral, catalizador fresco sin dos fuentes o convicción alta sin dominios independientes.',
        inputs=('BatchPulseReport', 'pulse_cache', 'commander.disagreements'),
        tools=('read_file', 'grep', 'web_search', 'web_fetch', 'x_keyword_search'),
        marketTools=(),
        output='NewsVerdict',
        success=SUCCESS['news-verifier'],
        marketBridgeSubagents=False,
    ),
    'quant-researcher': dict(
        reuses=('grok-desk-commander', 'chatgpt-quant-risk'),
        activates='Solo con una hipótesis concreta: frase, métrica, universo, horizonte, dirección y muestra mínima.',
        inputs=('Hypothesis', 'entryContext', 'net-costs-v1'),
        tools=('read_file', 'grep', 'list_dir'),
        marketTools=(),
        output='ExperimentProposal',
        success=SUCCESS['quant-researcher'],
        marketBridgeSubagents=False,
    ),
    'technical-reviewer': dict(
        reuses=('grok-desk-commander',),
        activates='Solo ante un fallo demostrable, con prueba que falla y mensaje observado.',
        inputs=('failingTest', 'observed', 'files'),
        tools=('read_file', 'grep', 'search_replace'),
        marketTools=(),
        output='ReviewGate',
        success=SUCCESS['technical-reviewer'],
        marketBridgeSubagents=False,
    ),
}


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    statement: str = Field(min_length=24, max_length=240)
    metric: str
    universe: str
    horizon: str
    direction: str
    minSample: int = Field(ge=30, le=500)


def _db_path():
    from .ai_swarm import DB_PATH
    return DB_PATH


@contextmanager
def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute('PRAGMA busy_timeout=5000')
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
            CREATE TABLE IF NOT EXISTS specialist_cache (
                digest TEXT PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT NOT NULL,
                ts REAL NOT NULL, searches INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS specialist_budget (
                day TEXT PRIMARY KEY, used INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS citation_seen (
                url TEXT PRIMARY KEY, first_seen REAL NOT NULL,
                last_seen REAL NOT NULL, hits INTEGER NOT NULL);
        ''')
        yield con
        con.commit()
    finally:
        con.close()


def _day(now):
    return datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')


def _domain(url):
    try:
        host = urlsplit(str(url)).hostname or ''
    except ValueError:
        return ''
    host = host.lower().removeprefix('www.')
    if not host or '.' not in host:
        return ''
    return host


def _digest(kind, payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f'{kind}:{raw}'.encode('utf-8')).hexdigest()


def uncertain(report, commander=None):
    """El verificador solo entra si el radar o el commander dejan la noticia en duda."""
    if not isinstance(report, dict):
        return False
    risk = report.get('headlineRisk')
    if risk in ('elevated', 'event') or report.get('fakeout') is True or report.get('trapDirection') == 'both':
        return True
    citations = report.get('citations') if isinstance(report.get('citations'), list) else []
    domains = {_domain(url) for url in citations}
    domains.discard('')
    fresh = report.get('catalystType') in FRESH_CATALYSTS and len(domains) < 2
    confident = _num(report.get('convictionMultiplier'))
    overconfident = confident is not None and confident > 1 and len(domains) < 2
    text = ' '.join(str(item) for item in (commander or {}).get('disagreements') or [])
    disputed = any(word in text.lower() for word in ('titular', 'cataliz', 'fakeout', 'noticia'))
    return bool(fresh or overconfident or disputed)


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _stamp(value, now):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _cached(con, digest, now):
    row = con.execute('SELECT ts, payload FROM specialist_cache WHERE digest=?', (digest,)).fetchone()
    if not row:
        return None
    stamp, payload = row
    if not isinstance(stamp, (int, float)) or not 0 <= now - stamp < CACHE_TTL:
        return None
    try:
        found = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if isinstance(found, dict):
        found['cacheHit'] = True
        found['searches'] = 0
    return found


def _consume(con, now):
    day = _day(now)
    row = con.execute('SELECT used FROM specialist_budget WHERE day=?', (day,)).fetchone()
    used = int(row[0]) if row else 0
    if used >= JOINT_DAILY_CAP:
        return False
    con.execute('''INSERT INTO specialist_budget(day, used) VALUES (?, ?)
                   ON CONFLICT(day) DO UPDATE SET used=excluded.used''', (day, used + 1))
    return True


def _remember_citations(con, urls, now):
    recycled = []
    for url in urls:
        if not _domain(url):
            continue
        row = con.execute('SELECT first_seen, hits FROM citation_seen WHERE url=?', (url,)).fetchone()
        if row and now - float(row[0]) > RECYCLE_AFTER:
            recycled.append(url)
        if row:
            con.execute('UPDATE citation_seen SET last_seen=?, hits=? WHERE url=?', (now, int(row[1]) + 1, url))
        else:
            con.execute('INSERT INTO citation_seen(url, first_seen, last_seen, hits) VALUES (?,?,?,1)', (url, now, now))
    return recycled


def verify_news(report, now, sources=None, commander=None):
    """Comprueba el radar ya guardado. searches queda en 0: no repite XSearch."""
    from . import public_sources
    citations = [url for url in (report.get('citations') or []) if isinstance(url, str)]
    domains = sorted({_domain(url) for url in citations if _domain(url)})
    by_url = {}
    retrieved = set()
    captures=[]
    for supplied in sources or []:
        genuine=public_sources.valid_capture(supplied,now)
        source=supplied.receipt if isinstance(supplied,public_sources.RetrievedSource) else supplied
        if isinstance(source, dict) and isinstance(source.get('url'), str):
            by_url[source['url']] = source.get('publishedAt')
            # A date asserted by a model is not a retrieved publication. The
            # caller must supply a receipt from the retrieval layer.
            stamp = _stamp(source.get('retrievedAt'), now)
            digest = source.get('contentSha256')
            if (genuine and source.get('provenance') == 'retrieved' and stamp is not None
                    and 0 <= now - stamp <= RECYCLE_AFTER
                    and isinstance(digest, str) and len(digest) == 64
                    and all(ch in '0123456789abcdef' for ch in digest.lower())):
                retrieved.add(source['url'])
                captures.append(supplied)
    published = []
    for url in citations:
        if url in by_url:
            stamp = _stamp(by_url[url], now)
            if stamp is not None:
                published.append(stamp)
    dates_ok = True
    if published:
        dates_ok = all(-2 * 3600 <= now - stamp <= RECYCLE_AFTER for stamp in published)
        if max(published) - min(published) > 7 * 86400:
            dates_ok = False
    elif report.get('headlineRisk') == 'event' or report.get('catalystType') in FRESH_CATALYSTS:
        dates_ok = False
    independent = len(domains) >= 2
    contradictions = []
    kind = report.get('catalystType')
    bias = _num(report.get('bias')) or 0.0
    if report.get('headlineRisk') == 'event' and kind == 'retail_noise':
        contradictions.append('event-ruido')
    if kind in ('listing', 'institutional', 'protocol') and bias < -0.3:
        contradictions.append('catalizador-alcista-con-sesgo-bajista')
    if kind == 'unlock_or_hack' and bias > 0.3 and report.get('fakeout') is not True:
        contradictions.append('evento-bajista-con-sesgo-alcista')
    if commander and uncertain({'headlineRisk': 'none'}, commander) and report.get('headlineRisk') == 'none':
        contradictions.append('commander-en-desacuerdo')
    with _connect() as con:
        recycled = _remember_citations(con, citations, now)
    fresh_claim = kind in FRESH_CATALYSTS or report.get('headlineRisk') == 'event'
    recycled_flag = bool(recycled) and fresh_claim
    usable = bool(dates_ok and independent and not contradictions and not recycled_flag)
    def normalized_url(url):
        try: return public_sources.canonical_url(url)
        except ValueError: return url
    claimed={normalized_url(url) for url in citations}
    relevant=[source for source in captures if source.receipt['url'] in claimed]
    relevant_urls={source.receipt['url'] for source in relevant}
    retrieved_domains={_domain(url) for url in relevant_urls}
    retrieval_verified=bool(claimed) and claimed<=retrieved
    captured_dates=bool(relevant) and retrieval_verified and all(
        type(source.receipt.get('publishedAt')) in (int,float)
        and 0<=now-source.receipt['publishedAt']<=RECYCLE_AFTER for source in relevant)
    blockers=[]
    if not claimed: blockers.append('citation_urls_missing')
    if not retrieval_verified: blockers.append('source_content_not_retrieved')
    if not captured_dates: blockers.append('publication_date_missing_or_stale')
    if contradictions: blockers.append('contradictory_radar_claim')
    if recycled_flag: blockers.append('recycled_catalyst')
    claims=[]
    for value in [*(report.get('catalysts') or []),report.get('reason')]:
        if not isinstance(value,str) or not value.strip(): continue
        normalized=public_sources.normalized_text(value)
        matches=[source.receipt['textSha256'] for source in relevant
                 if len(normalized)>=8 and normalized in public_sources.normalized_text(source.text)]
        claims.append(dict(claim=value[:240],matchedCapturedContent=bool(matches),
                           contentHashes=sorted(set(matches)),factVerified=False))
    unique_content={source.receipt['textSha256'] for source in relevant}
    return dict(
        profile='news-verifier', symbol=report.get('symbol'), usable=usable, searches=0,
        cacheHit=False, activated=True, success=usable, criterion=SUCCESS['news-verifier'],
        checks=dict(dates=dates_ok, independent=independent, domains=domains,
                    contradictions=contradictions, recycled=recycled_flag),
        contractPassed=usable, evidenceVerified=False,
        retrievalVerified=retrieval_verified,factVerified=False,editorialIndependenceVerified=False,
        researchOnlyEligible=bool(retrieval_verified and captured_dates and not contradictions and not recycled_flag),
        captureBlockers=blockers,claimsPending=claims,
        verificationBlockers=[*blockers,'claim_not_corroborated','editorial_independence_unverified'],
        verificationScope='captured-public-content' if relevant else 'supplied-metadata', profitabilityProven=False,
        evidence=dict(claimedUrls=len(citations), retrievedUrls=len(relevant_urls),
                      retrievedIndependentDomains=len(retrieved_domains),uniqueCapturedContents=len(unique_content),
                      contentIndependentlyChecked=False,publicationDatesObserved=captured_dates,
                      publisherGroups=sorted({source.receipt['publisherGroup'] for source in relevant})),
        reuses=('grok-x-pulse', 'grok-desk-commander'),
    )


def observe_radar(reports, now=None, commander=None, sources=None, force_citations=False):
    """Verifica solo los símbolos inciertos. Un acierto de caché no gasta cupo."""
    now = time_now() if now is None else now
    results = []
    for report in reports or []:
        if not uncertain(report, commander) and not (force_citations and (report.get('citations') or report.get('catalysts')
                or report.get('catalystType') not in (None,'none') or report.get('headlineRisk') not in (None,'none'))):
            continue
        digest = _digest('news', dict(report={key: report.get(key) for key in (
            'symbol', 'bias', 'fakeout', 'trapDirection', 'headlineRisk', 'catalystType', 'citations', 'reason')},
            sources=[source.receipt if hasattr(source,'receipt') else source for source in sources or []]))
        with _connect() as con:
            con.execute('BEGIN IMMEDIATE')
            cached = _cached(con, digest, now)
            if cached is not None:
                results.append(cached)
                continue
            if not _consume(con, now):
                results.append(dict(profile='news-verifier', symbol=report.get('symbol'), usable=False,
                                    searches=0, cacheHit=False, activated=True, success=False, status='budget',
                                    criterion=SUCCESS['news-verifier']))
                continue
        verdict = verify_news(report, now, sources=sources, commander=commander)
        with _connect() as con:
            con.execute('''INSERT INTO specialist_cache(digest, kind, symbol, ts, searches, payload)
                           VALUES (?,?,?,?,?,?)
                           ON CONFLICT(digest) DO UPDATE SET ts=excluded.ts, payload=excluded.payload,
                           searches=0''',
                        (digest, 'news', str(report.get('symbol') or ''), now, 0,
                         json.dumps(verdict, ensure_ascii=False)))
        results.append(verdict)
    return results


def time_now():
    import time
    return time.time()


def concrete_hypothesis(raw):
    if not isinstance(raw, dict):
        return None
    body = {key: value for key, value in raw.items() if key != 'applyToLive'}
    try:
        hypothesis = Hypothesis.model_validate(body)
    except ValidationError:
        return None
    allowed_metrics = ('expectancyNet', 'profitFactor', 'netRewardRisk')
    allowed_universe = ('BTC', 'LINK', 'SPY', 'trend', 'squeeze', 'swing', 'meanrev', 'orb', 'desk-maker')
    if (hypothesis.metric not in allowed_metrics or hypothesis.universe not in allowed_universe
            or hypothesis.horizon not in ('1h', '4h', '1d') or hypothesis.direction not in ('long', 'short')):
        return None
    if hypothesis.metric not in hypothesis.statement:
        return None
    return hypothesis


def propose_experiment(raw, now=None, features=None):
    """Propone un experimento. No cambia pesos, tamaño ni el modo demo."""
    now = time_now() if now is None else now
    hypothesis = concrete_hypothesis(raw)
    if hypothesis is None:
        return dict(profile='quant-researcher', activated=False, success=False, searches=0,
                    applyToLive=False, criterion=SUCCESS['quant-researcher'])
    if isinstance(raw, dict) and raw.get('applyToLive') is True:
        return dict(profile='quant-researcher', activated=True, success=False, searches=0,
                    applyToLive=False, status='rejected', reason='Una hipótesis no se aplica en vivo',
                    criterion=SUCCESS['quant-researcher'])
    chosen = list(features or CAUSAL_FEATURES[:4])
    if any(feature not in CAUSAL_FEATURES for feature in chosen):
        return dict(profile='quant-researcher', activated=True, success=False, searches=0,
                    applyToLive=False, status='rejected', reason='Hay una variable que no es causal',
                    criterion=SUCCESS['quant-researcher'])
    digest = _digest('hypothesis', hypothesis.model_dump())
    with _connect() as con:
        con.execute('BEGIN IMMEDIATE')
        cached = _cached(con, digest, now)
        if cached is not None:
            return cached
        if not _consume(con, now):
            return dict(profile='quant-researcher', activated=True, success=False, searches=0,
                        applyToLive=False, status='budget', criterion=SUCCESS['quant-researcher'])
        con.commit()
    threshold = '<= 0' if hypothesis.metric != 'profitFactor' else '< 1.15'
    proposal = dict(
        profile='quant-researcher', activated=True, success=True, searches=0, cacheHit=False,
        contractPassed=True, experimentExecuted=False, profitabilityProven=False,
        applyToLive=False, costs='net-costs-v1', hypothesis=hypothesis.model_dump(),
        falsifier=f'{hypothesis.metric} {threshold} en el holdout, tras comisión, spread y slippage',
        causalFeatures=chosen,
        oos=dict(train=0.6, validate=0.2, holdout=0.2, holdoutUsedForSelection=False,
                 status='proposal-only', appliedTrainingProtocol=False),
        criterion=SUCCESS['quant-researcher'],
        reuses=('grok-desk-commander', 'chatgpt-quant-risk'),
    )
    with _connect() as con:
        con.execute('''INSERT INTO specialist_cache(digest, kind, symbol, ts, searches, payload)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(digest) DO UPDATE SET ts=excluded.ts, payload=excluded.payload''',
                    (digest, 'hypothesis', hypothesis.universe, now, 0, json.dumps(proposal, ensure_ascii=False)))
    return proposal


def accept_review(finding):
    """Exige un fallo ya observado y una regresión. No habilita el puente."""
    if not isinstance(finding, dict):
        finding = {}
    failing = str(finding.get('failingTest') or '').strip()
    observed = str(finding.get('observed') or '').strip()
    regression = str(finding.get('regressionTest') or '').strip()
    files = finding.get('files') if isinstance(finding.get('files'), list) else []
    from pathlib import PurePosixPath
    def canonical(path):
        value = str(path).replace('\\', '/')
        if ':' in value or value.startswith('/') or '..' in PurePosixPath(value).parts:
            return None
        return str(PurePosixPath(value))
    normalized = [canonical(path) for path in files]
    protected = [path for path, clean in zip(files, normalized)
                 if clean is None or clean in PROTECTED_PATHS]
    demonstrable = bool(failing and observed)
    regression_path = canonical(regression)
    covered = bool(regression_path and regression_path.startswith('backend/tests/test_') and regression_path.endswith('.py'))
    success = bool(demonstrable and covered and not protected)
    return dict(
        profile='technical-reviewer', activated=demonstrable, success=success, searches=0,
        demonstrable=demonstrable, failingTest=failing or None, regressionTest=regression or None,
        protected=protected, marketBridgeSubagents=False, applyToLive=False,
        contractPassed=success, regressionExecuted=False, profitabilityProven=False,
        criterion=SUCCESS['technical-reviewer'],
    )
