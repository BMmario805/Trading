"""Bounded research coordination. Local profiles are not Grok desktop bots.

No stage submits orders, edits code, runs tests, or promotes a strategy. A
completed workflow produces a proposal requiring independent validation.
GET snapshots never create a database, start clients, or inspect auth files.
"""
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import inspect
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = None  # Default follows ai_swarm.DB_PATH, including test isolation.
HANDOFFS = ROOT / '.grok' / 'handoffs'
PROTOCOL = 'mesa-siete-workflow-v1'
HANDOFF_PROTOCOL = 'mesa-siete-handoff-v1'
MAX_TASKS_PER_DAY = 4
STAGES = ('encargo', 'radar', 'verificador', 'investigador', 'revisor', 'coordinador', 'propuesta')
AGENTS = {'market-radar', 'news-verifier', 'quant-researcher', 'technical-reviewer', 'grok-desk-commander'}
IDENTITY = dict(encargo='local-intake', radar='grok-x-pulse', verificador='news-verifier',
                investigador='quant-researcher', revisor='technical-reviewer',
                coordinador='local-coordinator', propuesta='local-proposal-gate')


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    brief: str = Field(min_length=8, max_length=1000)
    symbols: list[str] = Field(min_length=1, max_length=5)
    hypothesis: dict | None = None
    finding: dict | None = None
    radarMode: str = 'cached'
    requireDesktopReceipt: bool = False
    taskId: str | None = None
    correlationId: str | None = None
    idempotencyKey: str | None = Field(default=None, min_length=8, max_length=96)

    @field_validator('symbols')
    @classmethod
    def symbols_valid(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(r'[A-Z0-9]{2,12}', v) for v in values):
            raise ValueError('Símbolos duplicados o inválidos')
        return values

    @model_validator(mode='after')
    def bounded(self):
        if self.radarMode not in ('cached', 'subscription'):
            raise ValueError('Radar: cached o subscription')
        for value in (self.taskId, self.correlationId):
            if value is not None and not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', value):
                raise ValueError('Identificador inválido')
        if bool(self.taskId) != bool(self.correlationId):
            raise ValueError('taskId y correlationId se proporcionan juntos')
        if len(json.dumps(self.model_dump(), ensure_ascii=False)) > 16_000:
            raise ValueError('Encargo demasiado grande')
        return self


class WorkflowConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _path():
    if DB_PATH is not None:
        return Path(DB_PATH)
    from .ai_swarm import DB_PATH as swarm_path
    return Path(swarm_path).with_name('team-workflow.sqlite')


@contextmanager
def _connect(write=False):
    path = _path()
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=5)
        con.executescript('''
            CREATE TABLE IF NOT EXISTS workflow_tasks (
                task_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL,
                request_digest TEXT NOT NULL, idempotency_key TEXT UNIQUE,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                status TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_receipts (
                task_id TEXT NOT NULL, stage TEXT NOT NULL, attempt INTEGER NOT NULL,
                ts REAL NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL,
                duration REAL NOT NULL, input_digest TEXT NOT NULL,
                output_digest TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(task_id,stage,attempt));
        ''')
    else:
        con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        con.execute('PRAGMA query_only=ON')
    try:
        yield con
        if write:
            con.commit()
    finally:
        con.close()


def _safe_artifact(relative):
    """Only bounded text artifacts inside this repository; never config/auth/db."""
    if (not isinstance(relative, str) or '\\' in relative or ':' in relative
            or Path(relative).is_absolute()):
        raise ValueError('artifact_path')
    parts = Path(relative).parts
    if '..' in parts or not parts or parts[0] in ('.git', '.codex'):
        raise ValueError('artifact_path')
    path = (ROOT / relative).resolve(strict=True)
    path.relative_to(ROOT.resolve())
    if (not path.is_file() or path.suffix.lower() not in ('.txt', '.md')
            or path.stat().st_size > 1_000_000
            or any(word in path.name.lower() for word in ('auth', 'credential', 'password', 'secret'))):
        raise ValueError('artifact_type')
    return path


def desktop_receipt_snapshot(*, task_id=None, correlation_id=None, now=None):
    """Import JSON receipts as data, with a local nonce challenge, never execute."""
    now = time.time() if now is None else now
    base = dict(desktopBotsConnected=False, desktopAgentsSeen=[], expectedDesktopAgents=[],
                allExpectedDesktopAgentsConnected=False, pendingAgents=[], challenges=[],
                state='pending', verifiedReceipts=0, invalidReceipts=0,
                receiptMeaning='Local nonce and artifact match; not a signed provider identity',
                errors=[])
    try:
        folder = HANDOFFS.resolve(strict=True)
        folder.relative_to(ROOT.resolve())
        required = ('correlationId', 'taskId', 'nonce', 'agent', 'artifact', 'sha256')
        files = sorted(folder.glob('receipt-*.json'))[:100]
        receipts = []
        for path in files:
            try:
                if path.resolve(strict=True).parent != folder or path.stat().st_size > 32_000:
                    raise ValueError('receipt_path')
                receipt = json.loads(path.read_text(encoding='utf-8-sig'))
                if not isinstance(receipt, dict):raise ValueError('receipt_contract')
                receipts.append((path.name,receipt))
            except (OSError, ValueError, TypeError):
                base['invalidReceipts'] += 1
        challenges = []
        legacy = folder / 'challenge.json'
        if legacy.exists():challenges.append(legacy)
        many = folder / 'challenges'
        if many.exists():
            if many.resolve(strict=True).parent != folder:raise ValueError('challenge_directory')
            challenges.extend(sorted(many.glob('*.json'))[:50])
        seen, expected, accepted_receipts = set(), set(), set()
        for path in challenges:
            public = dict(state='blocked')
            try:
                resolved=path.resolve(strict=True)
                if resolved.parent not in (folder,many.resolve()) or path.stat().st_size>16_000:
                    raise ValueError('challenge_path')
                challenge=json.loads(path.read_text(encoding='utf-8-sig'))
                if (not isinstance(challenge,dict) or challenge.get('protocol')!=HANDOFF_PROTOCOL
                        or any(not isinstance(challenge.get(k),str) for k in required)
                        or challenge['agent'] not in AGENTS or not 8<=len(challenge['nonce'])<=128
                        or not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}',challenge['taskId'])
                        or not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}',challenge['correlationId'])):
                    raise ValueError('challenge_contract')
                if ((task_id and challenge['taskId']!=task_id)
                        or (correlation_id and challenge['correlationId']!=correlation_id)):
                    continue
                public.update(taskId=challenge['taskId'],correlationId=challenge['correlationId'],
                              agent=challenge['agent'])
                expected.add(challenge['agent'])
                created=challenge.get('createdAt')
                if type(created) not in (int,float) or not math.isfinite(created) or not -120<=now-created<=3600:
                    raise ValueError('challenge_expired')
                public['createdAt']=created
                artifact=_safe_artifact(challenge['artifact'])
                digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
                if not hmac.compare_digest(digest,challenge['sha256'].lower()):
                    raise ValueError('challenge_artifact_hash')
                public['state']='pending'
                for name, receipt in receipts:
                    if receipt.get('taskId')!=challenge['taskId'] or receipt.get('correlationId')!=challenge['correlationId']:
                        continue
                    observed=receipt.get('observedAt')
                    valid=(receipt.get('protocol')==HANDOFF_PROTOCOL
                           and all(receipt.get(k)==challenge[k] for k in required)
                           and receipt.get('status')=='completed' and receipt.get('errors')==[]
                           and type(observed) in (int,float) and math.isfinite(observed)
                           and observed>=created-5 and -120<=now-observed<=3600)
                    if not valid:
                        base['invalidReceipts']+=1
                        continue
                    public.update(state='receipt_verified',observedAt=observed)
                    seen.add(challenge['agent']);accepted_receipts.add(name)
                    break
            except (OSError,ValueError,TypeError):
                public['error']='Invalid, expired, or unconfined desktop challenge'
            base['challenges'].append(public)
        base.update(desktopBotsConnected=bool(seen),desktopAgentsSeen=sorted(seen),
                    expectedDesktopAgents=sorted(expected),pendingAgents=sorted(expected-seen),
                    allExpectedDesktopAgentsConnected=bool(expected) and expected<=seen,
                    verifiedReceipts=len(accepted_receipts))
        if seen:base['state']='receipt_verified' if expected<=seen else 'partial'
        elif any(r['state']=='blocked' for r in base['challenges']) or base['invalidReceipts']:
            base.update(state='blocked',errors=['Desktop evidence has not passed the current challenge'])
        if task_id:base['taskId']=task_id
        if correlation_id:base['correlationId']=correlation_id
        if seen:base['observedAt']=max(r['observedAt'] for r in base['challenges'] if r['state']=='receipt_verified')
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        base.update(state='blocked', errors=['Desktop challenge is invalid, expired, or outside this repository'])
    return base


def task_snapshot(task_id):
    try:
        with _connect() as con:
            row = con.execute('SELECT payload FROM workflow_tasks WHERE task_id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else None
    except (OSError, sqlite3.Error, ValueError):
        return None


def _save_task(task):
    task['updatedAt'] = time.time()
    with _connect(write=True) as con:
        con.execute('UPDATE workflow_tasks SET updated_at=?,status=?,payload=? WHERE task_id=?',
                    (task['updatedAt'], task['status'], _json(task), task['taskId']))


def _receipt(task, stage, attempt, status, started, result):
    receipt = dict(taskId=task['taskId'], correlationId=task['correlationId'], stage=stage,
                   actor=dict(id=IDENTITY[stage], kind='local-validator' if stage != 'radar' else 'radar-adapter',
                              desktopBot=False), attempt=attempt, status=status,
                   receivedAt=started, observedAt=time.time(),
                   durationSeconds=max(0.0, time.time()-started), result=result,
                   profitabilityProven=False, executionAuthorized=False)
    with _connect(write=True) as con:
        con.execute('INSERT OR REPLACE INTO workflow_receipts VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (task['taskId'], stage, attempt, receipt['observedAt'], IDENTITY[stage], status,
                     receipt['durationSeconds'], task['requestDigest'], _digest(result), _json(receipt)))
    task['stages'].append(receipt)
    _save_task(task)
    return receipt


async def _stage(task, name, function, *, timeout=5.0, attempts=1):
    for attempt in range(1, attempts+1):
        started = time.time()
        _receipt(task, name, attempt, 'started', started, dict(acknowledged=True))
        try:
            async def invoke():
                result = function()
                return await result if inspect.isawaitable(result) else result
            result = await asyncio.wait_for(invoke(), timeout)
            status = result.get('status') or ('skipped' if result.get('activated') is False else
                                             'blocked' if result.get('success') is False else 'completed')
            _receipt(task, name, attempt, status, started, result)
            return result
        except asyncio.TimeoutError:
            result = dict(status='timeout', error='stage_timeout', retryable=attempt < attempts)
            _receipt(task, name, attempt, 'timeout', started, result)
        except asyncio.CancelledError:
            _receipt(task, name, attempt, 'cancelled', started, dict(error='task_cancelled'))
            task['status'] = 'cancelled'
            _save_task(task)
            raise
        except Exception as exc:
            result = dict(status='error', error='stage_failed', errorType=type(exc).__name__, retryable=False)
            _receipt(task, name, attempt, 'error', started, result)
            return result
    return result


async def load_radar(request):
    """Existing subscription transport only when explicitly requested, once."""
    from . import ai_swarm
    refreshed = False
    if request.radarMode == 'subscription':
        swarm = ai_swarm.SWARM
        if swarm is None or swarm.settings.transport != 'subscription':
            return dict(status='blocked', error='subscription_runtime_not_available', reports=[])
        refreshed = bool(await swarm.refresh_pulse_batch())
    reports, missing, ages = [], [], {}
    now = time.time()
    try:
        with closing(sqlite3.connect(Path(ai_swarm.DB_PATH).resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as con:
            con.execute('PRAGMA query_only=ON')
            for symbol in request.symbols:
                row = con.execute('SELECT ts,payload FROM pulse_cache WHERE symbol=?', (symbol,)).fetchone()
                if not row or type(row[0]) not in (int, float) or not 0 <= now-row[0] < ai_swarm.PULSE_CACHE_TTL:
                    missing.append(symbol)
                    continue
                report = ai_swarm._cached_pulse_report(row[1], symbol)
                if not report:
                    missing.append(symbol)
                    continue
                reports.append(report)
                ages[symbol] = now-row[0]
    except (OSError, sqlite3.Error):
        missing = list(request.symbols)
    return dict(status='blocked' if missing else 'completed', reports=reports,
                missingSymbols=missing, ageSeconds=ages, refreshed=refreshed,
                transport=request.radarMode, providerCallsMaximum=1 if request.radarMode=='subscription' else 0,
                evidenceScope='model-claimed citations; no independent URL retrieval')


async def _verify(radar):
    from . import public_sources, specialist_team
    reports = radar.get('reports') or []
    urls=[url for report in reports for url in report.get('citations') or []]
    retrieval=await public_sources.retrieve_urls(urls,root=_path().parent/'public-sources')
    verdicts = specialist_team.observe_radar(reports,sources=retrieval['captures'],force_citations=True)
    blocked = {v.get('symbol') for v in verdicts if v.get('evidenceVerified') is not True}
    eligible=bool(verdicts) and all(v.get('researchOnlyEligible') is True for v in verdicts)
    status=('blocked' if radar.get('status')!='completed' or retrieval['failures'] or (blocked and not eligible)
            else 'research_only' if blocked else 'completed')
    return dict(status=status,
                verdicts=verdicts, blockedSymbols=sorted(v for v in blocked if v),
                claimedUrls=sum(len(r.get('citations') or []) for r in reports),
                retrievedUrls=retrieval['retrievedUrls'],uniqueContents=retrieval['uniqueContents'],
                retrievalReceipts=[source.receipt for source in retrieval['captures']],
                retrievalFailures=retrieval['failures'],urlLimit=public_sources.MAX_URLS,
                retrievalVerified=bool(verdicts) and all(v.get('retrievalVerified') is True for v in verdicts),
                factVerified=False,editorialIndependenceVerified=False,independentlyVerifiedUrls=0,
                verificationBlockers=sorted({b for v in verdicts for b in v.get('verificationBlockers') or []}),
                claimsPending=[dict(symbol=v.get('symbol'),**claim) for v in verdicts for claim in v.get('claimsPending') or []],
                meaning='Captured content can support research proposals only; factual claims and editorial independence remain pending')


async def submit_task(raw, *, radar_reader=None, stage_timeout=None):
    """One finite DAG; no recursion, remote agent spawning, editing or execution."""
    from . import specialist_team
    request = TaskRequest.model_validate(raw)
    body = request.model_dump()
    digest = _digest(body)
    now = time.time()
    key = request.idempotencyKey
    task_id = request.taskId or 'task-' + uuid.uuid4().hex
    correlation_id = request.correlationId or uuid.uuid4().hex
    task = dict(protocol=PROTOCOL, taskId=task_id, correlationId=correlation_id,
                requestDigest=digest, status='received', createdAt=now, updatedAt=now,
                symbols=request.symbols, brief=request.brief, stages=[], proposal=None,
                profitabilityProven=False, executionAuthorized=False, applyToLive=False,
                providerE2EVerified=False)
    with _connect(write=True) as con:
        con.execute('BEGIN IMMEDIATE')
        old = con.execute('SELECT request_digest,payload FROM workflow_tasks WHERE task_id=? OR idempotency_key=?',
                          (task_id, key)).fetchone()
        if old:
            if old[0] != digest:
                raise WorkflowConflict('Identificador reutilizado con otro encargo')
            return json.loads(old[1])
        day = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if con.execute('SELECT COUNT(*) FROM workflow_tasks WHERE created_at>=?', (day,)).fetchone()[0] >= MAX_TASKS_PER_DAY:
            return dict(protocol=PROTOCOL, status='blocked', error='daily_task_budget',
                        executionAuthorized=False, applyToLive=False, profitabilityProven=False)
        con.execute('INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,?)',
                    (task_id, correlation_id, digest, key, now, now, 'received', _json(task)))
    _receipt(task, 'encargo', 1, 'completed', now,
             dict(accepted=True, dag=list(STAGES), newProviderCallsMaximum=1 if request.radarMode=='subscription' else 0))
    task['status'] = 'running'
    _save_task(task)
    reader = radar_reader or load_radar
    radar = await _stage(task, 'radar', lambda: reader(request),
                         timeout=stage_timeout or (120 if request.radarMode=='subscription' else 5),
                         attempts=1 if request.radarMode=='subscription' else 2)
    verified = await _stage(task, 'verificador', lambda: _verify(radar),timeout=6.0)
    research = await _stage(task, 'investigador', lambda: specialist_team.propose_experiment(request.hypothesis or {}))
    review = await _stage(task, 'revisor', lambda: specialist_team.accept_review(request.finding or {}))
    desktop = desktop_receipt_snapshot(task_id=task_id, correlation_id=correlation_id) if request.requireDesktopReceipt else None
    blockers = []
    if radar.get('status') != 'completed': blockers.append('radar_missing_or_failed')
    if verified.get('status') not in ('completed','research_only'): blockers.append('news_evidence_not_verified')
    if research.get('contractPassed') is not True: blockers.append('concrete_research_proposal_missing')
    if request.finding and review.get('contractPassed') is not True: blockers.append('review_contract_failed')
    if request.requireDesktopReceipt and not desktop.get('allExpectedDesktopAgentsConnected'): blockers.append('desktop_receipt_pending')
    coordination = await _stage(task, 'coordinador', lambda: dict(status='blocked' if blockers else 'completed',
        blockers=blockers, desktop=desktop, profileContractOnly=True,
        regressionExecuted=False, experimentExecuted=False, executionAuthorized=False))
    proposal = await _stage(task, 'propuesta', lambda: dict(status='blocked' if blockers else 'completed',
        kind='research-proposal', hypothesis=research.get('hypothesis'), blockers=blockers,
        scope='research_only',newsEvidenceState='captured_claims_pending' if verified.get('status')=='research_only' else verified.get('status'),
        retrievalVerified=verified.get('retrievalVerified') is True,factVerified=False,
        verificationBlockers=verified.get('verificationBlockers') or [],claimsPending=verified.get('claimsPending') or [],
        validationState='not_executed', reviewState='contract_only' if request.finding else 'not_requested',
        requiredGates=['frozen_experiment', 'net_costs', 'independent_out_of_sample',
                       'future_observation', 'regression_evidence', 'local_risk_approval'],
        applyToLive=False, executionAuthorized=False, profitabilityProven=False))
    task.update(status='blocked' if blockers else 'proposal_ready', proposal=proposal,
                workflowCompleted=True, providerE2EVerified=False)
    _save_task(task)
    return task


def health_snapshot():
    from . import subscription_bridge
    desktop = desktop_receipt_snapshot()
    result = dict(protocol=PROTOCOL, readOnly=True, observedAt=time.time(),
                  integration=dict(desktop, localProfiles=['news-verifier','quant-researcher','technical-reviewer'],
                    profileMeaning='Local validation functions; .grok/agents files are instructions, not desktop bots',
                    liveExecution=False, automaticPromotion=False),
                  provider=subscription_bridge.health_snapshot(), tasksByStatus={},
                  recentTasks=[], receipts=0, failedReceipts=0, storage='not_created',
                  limits=dict(tasksPerDay=MAX_TASKS_PER_DAY, localStageAttempts=2, subscriptionStageAttempts=1),
                  profitabilityProven=False)
    try:
        with _connect() as con:
            result['tasksByStatus'] = dict(con.execute('SELECT status,COUNT(*) FROM workflow_tasks GROUP BY status'))
            result['receipts'] = con.execute('SELECT COUNT(*) FROM workflow_receipts').fetchone()[0]
            result['failedReceipts'] = con.execute("SELECT COUNT(*) FROM workflow_receipts WHERE status IN ('error','timeout','cancelled')").fetchone()[0]
            result['recentTasks'] = [dict(taskId=r[0],correlationId=r[1],updatedAt=r[2],status=r[3])
                                    for r in con.execute('SELECT task_id,correlation_id,updated_at,status FROM workflow_tasks ORDER BY updated_at DESC LIMIT 10')]
        result['storage'] = 'readable'
    except (OSError, sqlite3.Error):
        if _path().exists(): result['storage'] = 'unavailable'
    try:
        from . import decision_audit
        result['attribution'] = decision_audit.health_snapshot()
    except (ImportError, OSError, sqlite3.Error):
        result['attribution'] = dict(available=False, incrementalAdvantageMeasured=False)
    return result
