"""Agregación de opinión pública sobre dirección de precio.

Fuentes:
  - StockTwits: mensajes de traders con etiqueta explícita Bullish/Bearish.
  - Reddit: búsqueda pública RSS en subreddits de trading + VADER sobre los textos.
  - Fear & Greed Index (alternative.me) para cripto.
  - TradingView: Ideas (dirección Long/Short explícita) y Minds (VADER).

Cada fuente produce un agregado (bullish en [0,1], tamaño de muestra) y la
lista de publicaciones individuales (`items`) para el visor de opiniones.
La media ponderada por muestra da la "probabilidad" agregada de subida.
"""

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

from curl_cffi.requests import AsyncSession

from . import history
from .news import official_news
from .config import SENTIMENT_TTL, Symbol
from .market_data import client
from .tradingview import classify_trader_text, ideas_source, minds_source

_cache: dict[str, tuple[float, dict]] = {}

# StockTwits está detrás de Cloudflare y rechaza la huella TLS de httpx;
# curl_cffi imita la de Chrome y pasa sin problema.
_curl: AsyncSession | None = None


def _curl_session() -> AsyncSession:
    global _curl
    if _curl is None:
        _curl = AsyncSession(impersonate="chrome", timeout=15)
    return _curl


async def close_session() -> None:
    global _curl
    if _curl is not None:
        await _curl.close()
        _curl = None


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


async def _stocktwits_stream(stream: str) -> list[dict]:
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{stream}.json"
    r = await _curl_session().get(url)
    r.raise_for_status()
    return r.json().get("messages", [])


async def _stocktwits(sym: Symbol) -> dict | None:
    """Agrega uno o varios streams (un mismo mensaje puede aparecer en varios)."""
    if not sym.stocktwits:
        return None
    streams = (
        sym.stocktwits if isinstance(sym.stocktwits, tuple) else (sym.stocktwits,)
    )
    results = await asyncio.gather(
        *(_stocktwits_stream(s) for s in streams), return_exceptions=True
    )
    seen: set[int] = set()
    bull = bear = total_msgs = 0
    items: list[dict] = []
    for msgs in results:
        if isinstance(msgs, Exception):
            continue
        for m in msgs:
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            total_msgs += 1
            basic = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            if basic == "Bullish":
                bull += 1
            elif basic == "Bearish":
                bear += 1
            user = (m.get("user") or {})
            items.append({
                "user": user.get("username"),
                "avatar": user.get("avatar_url"),
                "text": (m.get("body") or "")[:600],
                "badge": basic.lower() if basic else "neutral",
                "likes": (m.get("likes") or {}).get("total") or 0,
                "time": _epoch(m.get("created_at")),
                "url": f"https://stocktwits.com/{user.get('username')}/message/{m.get('id')}",
                "image": None,
            })
    total = bull + bear
    if total == 0:
        return None
    items.sort(key=lambda i: i["time"] or 0, reverse=True)
    label = f" ({', '.join(streams)})" if len(streams) > 1 else ""
    return {
        "source": f"StockTwits{label}",
        "detail": f"{bull} alcistas / {bear} bajistas de {total_msgs} mensajes",
        "bullish": bull / total,
        "sample": total,
        "items": items,
    }


_TAG_RE = re.compile(r"<[^>]+>")
_ATOM = "{http://www.w3.org/2005/Atom}"

# Reddit limita agresivamente por IP: serializar y espaciar sus peticiones.
_reddit_lock = asyncio.Lock()
_REDDIT_SPACING = 4.0
_last_reddit = 0.0


async def _reddit(sym: Symbol) -> dict | None:
    # El endpoint JSON de Reddit devuelve 403 sin OAuth; el feed RSS sigue abierto.
    global _last_reddit
    if not sym.reddit_subs:
        return None
    subs = "+".join(sym.reddit_subs)
    url = f"https://www.reddit.com/r/{subs}/search.rss"
    params = {
        "q": sym.reddit_query,
        "restrict_sr": 1,
        "sort": "new",
        "t": "week",
        "limit": 50,
    }
    async with _reddit_lock:
        wait = _REDDIT_SPACING - (time.time() - _last_reddit)
        if wait > 0:
            await asyncio.sleep(wait)
        r = await client().get(url, params=params)
        if r.status_code == 429:
            await asyncio.sleep(_REDDIT_SPACING)
            r = await client().get(url, params=params)
        _last_reddit = time.time()
    r.raise_for_status()
    root = ET.fromstring(r.text)
    n_posts = 0
    pos = neg = 0
    items: list[dict] = []
    for entry in root.iter(f"{_ATOM}entry"):
        n_posts += 1
        title = entry.findtext(f"{_ATOM}title") or ""
        content = entry.findtext(f"{_ATOM}content") or ""
        content = html.unescape(_TAG_RE.sub(" ", content))
        author = (entry.findtext(f"{_ATOM}author/{_ATOM}name") or "").lstrip("/u/")
        link_el = entry.find(f"{_ATOM}link")
        link = link_el.get("href") if link_el is not None else None
        badge = classify_trader_text(f"{title}. {content[:500]}")
        if badge == "positive":
            pos += 1
        elif badge == "negative":
            neg += 1
        items.append({
            "user": author,
            "avatar": None,
            "text": f"{title} — {content[:400]}".strip(" —"),
            "badge": badge,
            "likes": None,  # el RSS de Reddit no incluye los votos
            "time": _epoch(entry.findtext(f"{_ATOM}updated")),
            "url": link,
            "image": None,
        })
    scored = pos + neg
    if scored == 0:
        return None
    return {
        "source": f"Reddit (r/{', r/'.join(sym.reddit_subs)})",
        "detail": f"{pos} positivos / {neg} negativos de {n_posts} posts",
        "bullish": pos / scored,
        "sample": scored,
        "items": items,
    }


async def _fear_greed(sym: Symbol) -> dict | None:
    if not sym.is_crypto:
        return None
    r = await client().get("https://api.alternative.me/fng/?limit=1")
    r.raise_for_status()
    data = r.json()["data"][0]
    value = int(data["value"])  # 0 = miedo extremo, 100 = codicia extrema
    return {
        "source": "Fear & Greed Index",
        "detail": f"{value}/100 ({data['value_classification']})",
        "bullish": value / 100,
        "sample": 10,  # peso fijo moderado: es un índice, no una muestra
        "items": [],
    }


async def _compute_uncached(sym: Symbol) -> dict:
    """Resultado completo (agregados + items), cacheado SENTIMENT_TTL segundos."""
    now = time.time()
    cached = _cache.get(sym.id)
    if cached and now - cached[0] < SENTIMENT_TTL:
        return cached[1]

    calls = (_stocktwits(sym), _reddit(sym), _fear_greed(sym), ideas_source(sym), minds_source(sym), official_news())
    results = await asyncio.gather(*(asyncio.wait_for(c, timeout=25) for c in calls), return_exceptions=True)
    sources = [r for r in results if isinstance(r, dict)]
    errors = [type(r).__name__ for r in results if isinstance(r, Exception)]

    total = sum(s["sample"] for s in sources)
    bullish = (
        sum(s["bullish"] * s["sample"] for s in sources) / total if total else 0.5
    )
    label = (
        "muy alcista" if bullish >= 0.7 else
        "alcista" if bullish >= 0.55 else
        "muy bajista" if bullish <= 0.3 else
        "bajista" if bullish <= 0.45 else
        "neutral"
    )
    result = {
        "symbol": sym.id,
        "bullishPct": round(bullish * 100, 1),
        "bearishPct": round((1 - bullish) * 100, 1),
        "label": label,
        "sampleSize": total,
        "sources": sources,
        "errors": errors,
        "updatedAt": int(now),
    }
    # no cachear fallos totales: así el siguiente intento reintenta enseguida
    if sources or not errors:
        _cache[sym.id] = (now, result)
        if total > 0:
            history.record(sym.id, result["bullishPct"], total)
    return result


_compute_locks: dict[str, asyncio.Lock] = {}


async def _compute(sym: Symbol) -> dict:
    async with _compute_locks.setdefault(sym.id, asyncio.Lock()):
        return await _compute_uncached(sym)


async def candidate_sources(sym: Symbol, timeout: float = 4) -> dict:
    """Entrega las fuentes que llegaron a tiempo, aunque otra esté caída.

    El candidato ya fue verificado por el motor. Las publicaciones conservan
    sus fechas; sin evidencia no se inventa cobertura ni un veto.
    """
    from dataclasses import replace
    from .config import SYMBOLS_BY_ID
    cached=peek_cached(sym.id)
    if cached: return cached
    known=SYMBOLS_BY_ID.get(sym.id)
    if known:
        sym=known
    elif sym.source=='binance' and sym.category=='cripto' and sym.id.isalnum() and len(sym.id)<=24:
        sym=replace(sym,stocktwits=sym.id+'.X',reddit_query='"'+sym.id+'" crypto',
                    reddit_subs=('CryptoCurrency','CryptoMarkets'),tradingview=sym.source_symbol)
    tasks=[asyncio.create_task(c) for c in (_stocktwits(sym),_reddit(sym),ideas_source(sym),minds_source(sym),official_news())]
    try:
        done,pending=await asyncio.wait(tasks,timeout=timeout)
        sources=[]
        for task in done:
            try:
                result=task.result()
                if isinstance(result,dict): sources.append(result)
            except Exception:
                pass
        return dict(symbol=sym.id,sources=sources,partial=bool(pending))
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


def peek_cached(symbol_id: str) -> dict | None:
    """Sentimiento cacheado aún vigente, sin disparar ningún cálculo.

    Lo usan los bots: nunca bloquean esperando a las fuentes; si no hay
    caché lanzan un refresco en segundo plano y operan solo con técnico.
    """
    cached = _cache.get(symbol_id)
    if cached and time.time() - cached[0] < SENTIMENT_TTL:
        return cached[1]
    return None


async def get_sentiment(sym: Symbol) -> dict:
    """Agregados sin las publicaciones individuales (respuesta ligera)."""
    full = await _compute(sym)
    return {
        **full,
        "sources": [
            {
                **{k: v for k, v in s.items() if k != "items"},
                "hasItems": bool(s.get("items")),
            }
            for s in full["sources"]
        ],
    }


async def get_opinions(sym: Symbol) -> dict:
    """Publicaciones individuales por fuente, para el visor de opiniones."""
    full = await _compute(sym)
    return {
        "symbol": full["symbol"],
        "updatedAt": full["updatedAt"],
        "sources": [
            {"source": s["source"], "detail": s["detail"], "items": s["items"]}
            for s in full["sources"]
            if s.get("items")
        ],
    }
