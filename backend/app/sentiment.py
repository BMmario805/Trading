"""Agregación de opinión pública sobre dirección de precio.

Fuentes:
  - StockTwits: mensajes de traders con etiqueta explícita Bullish/Bearish.
  - Reddit: búsqueda pública JSON en subreddits de trading + VADER sobre los textos.
  - Fear & Greed Index (alternative.me) para cripto.

Cada fuente produce (bullish_ratio en [0,1], tamaño de muestra). La media
ponderada por muestra da la "probabilidad" agregada de subida percibida.
"""

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET

from curl_cffi.requests import AsyncSession
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from . import history
from .config import SENTIMENT_TTL, Symbol
from .market_data import client

_vader = SentimentIntensityAnalyzer()
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
    total = bull + bear
    if total == 0:
        return None
    label = f" ({', '.join(streams)})" if len(streams) > 1 else ""
    return {
        "source": f"StockTwits{label}",
        "detail": f"{bull} alcistas / {bear} bajistas de {total_msgs} mensajes",
        "bullish": bull / total,
        "sample": total,
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
    posts = []
    for entry in root.iter(f"{_ATOM}entry"):
        title = entry.findtext(f"{_ATOM}title") or ""
        content = entry.findtext(f"{_ATOM}content") or ""
        content = html.unescape(_TAG_RE.sub(" ", content))
        posts.append(f"{title}. {content[:500]}")
    scores = []
    for text in posts:
        compound = _vader.polarity_scores(text)["compound"]
        if abs(compound) >= 0.15:  # descartar posts neutrales
            scores.append(compound)
    if not scores:
        return None
    pos = sum(1 for s in scores if s > 0)
    return {
        "source": f"Reddit (r/{', r/'.join(sym.reddit_subs)})",
        "detail": f"{pos} positivos / {len(scores) - pos} negativos de {len(posts)} posts",
        "bullish": pos / len(scores),
        "sample": len(scores),
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
    }


async def get_sentiment(sym: Symbol) -> dict:
    now = time.time()
    cached = _cache.get(sym.id)
    if cached and now - cached[0] < SENTIMENT_TTL:
        return cached[1]

    results = await asyncio.gather(
        _stocktwits(sym), _reddit(sym), _fear_greed(sym), return_exceptions=True
    )
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
