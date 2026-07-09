"""Ideas y Minds (pensamientos) de TradingView como fuentes de opinión.

- Ideas: cada publicación lleva dirección explícita del trader
  (campo "direction" embebido en el HTML: 1 = Long, 2 = Short, 0 = neutral,
  verificado abriendo ideas individuales y comprobando su insignia),
  además de la imagen del gráfico dibujado, el autor y los votos (boosts).
- Minds: la API pública `api/v1/minds/?symbol=` devuelve los pensamientos
  de la comunidad; se puntúan con VADER.

Ambas requieren la huella TLS de Chrome (curl_cffi), como StockTwits.

Los objetos idea no se pueden decodificar como JSON directamente (están
entrelazados con el resto del estado de la página), así que se parsea por
ventanas: el texto entre dos apariciones de "direction" contiene los campos
de una idea (autor, imagen, likes, descripción, timestamp...).
"""

import json
import re
import time
from datetime import datetime

from curl_cffi.requests import AsyncSession
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .config import Symbol

_vader = SentimentIntensityAnalyzer()
_session: AsyncSession | None = None

IDEAS_URL = "https://www.tradingview.com/symbols/{symbol}/ideas/"
MINDS_URL = "https://www.tradingview.com/api/v1/minds/"

_DIR_RE = re.compile(r'"direction":(\d+)')
_TS_RE = re.compile(r'"date_timestamp":(\d+)')
_LIKES_RE = re.compile(r'"likes_count":(\d+)')
_USER_RE = re.compile(r'"username":"((?:[^"\\]|\\.)*)"')
_AVATAR_RE = re.compile(r'"picture_url":"((?:[^"\\]|\\.)*)"')
_IMAGE_RE = re.compile(r'"image":\{"big":"((?:[^"\\]|\\.)*)"')
_DESC_RE = re.compile(r'"description":"((?:[^"\\]|\\.)*)"')
_CHART_RE = re.compile(r'"chart_url":"((?:[^"\\]|\\.)*)"')

MAX_IDEA_AGE_S = 14 * 86400  # ignorar ideas de más de dos semanas

_BADGE = {"1": "long", "2": "short", "0": "neutral"}

# VADER no entiende la jerga de traders ("SELL suggestion" puntúa positivo):
# el léxico explícito de dirección tiene prioridad y VADER queda de respaldo.
_BULL_RE = re.compile(r"\b(buy|long|bullish|buying|calls?|breakout up|to the moon)\b", re.I)
_BEAR_RE = re.compile(r"\b(sell|short|bearish|selling|puts?|breakdown|dump|crash)\b", re.I)


def classify_trader_text(text: str) -> str:
    """'positive' | 'negative' | 'neutral' para un texto de trading."""
    bull = len(_BULL_RE.findall(text))
    bear = len(_BEAR_RE.findall(text))
    if bull > bear:
        return "positive"
    if bear > bull:
        return "negative"
    compound = _vader.polarity_scores(text[:800])["compound"]
    if compound >= 0.15:
        return "positive"
    if compound <= -0.15:
        return "negative"
    return "neutral"


def _get_session() -> AsyncSession:
    global _session
    if _session is None:
        _session = AsyncSession(impersonate="chrome", timeout=20)
    return _session


async def close_session() -> None:
    global _session
    if _session is not None:
        await _session.close()
        _session = None


def _unescape(raw: str | None) -> str | None:
    """Decodifica los escapes JSON (\\/, \\uXXXX, \\n...) de un valor regex."""
    if raw is None:
        return None
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw


def _first(pattern: re.Pattern, window: str) -> str | None:
    m = pattern.search(window)
    return m.group(1) if m else None


def _to_epoch(v) -> int | None:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


async def ideas_source(sym: Symbol) -> dict | None:
    """Ideas recientes con dirección, autor, gráfico dibujado y votos."""
    if not sym.tradingview:
        return None
    r = await _get_session().get(IDEAS_URL.format(symbol=sym.tradingview))
    r.raise_for_status()
    text = r.text
    marks = list(_DIR_RE.finditer(text))
    now = time.time()
    longs = shorts = total = 0
    items: list[dict] = []
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        win = text[m.end() : end]
        ts = _first(_TS_RE, win)
        if ts is None:
            continue  # bloque sin timestamp: no es una tarjeta de idea completa
        if now - int(ts) > MAX_IDEA_AGE_S:
            continue
        total += 1
        badge = _BADGE.get(m.group(1), "neutral")
        if badge == "long":
            longs += 1
        elif badge == "short":
            shorts += 1
        likes = _first(_LIKES_RE, win)
        chart_code = _unescape(_first(_CHART_RE, win))
        items.append({
            "user": _unescape(_first(_USER_RE, win)),
            "avatar": _unescape(_first(_AVATAR_RE, win)),
            "text": (_unescape(_first(_DESC_RE, win)) or "")[:600],
            "badge": badge,
            "likes": int(likes) if likes else 0,
            "time": int(ts),
            "url": (
                chart_code if chart_code and chart_code.startswith("http")
                else f"https://www.tradingview.com/chart/{sym.tradingview}/{chart_code}/"
                if chart_code else None
            ),
            "image": _unescape(_first(_IMAGE_RE, win)),
        })
    directional = longs + shorts
    if directional == 0:
        return None
    items.sort(key=lambda i: i["time"], reverse=True)
    return {
        "source": "TradingView Ideas",
        "detail": f"{longs} largos / {shorts} cortos de {total} ideas recientes",
        "bullish": longs / directional,
        "sample": directional,
        "items": items,
    }


async def minds_source(sym: Symbol) -> dict | None:
    """Sentimiento VADER sobre los pensamientos (Minds) de la comunidad."""
    if not sym.tradingview:
        return None
    r = await _get_session().get(MINDS_URL, params={"symbol": sym.tradingview})
    r.raise_for_status()
    results = r.json().get("results", [])
    pos = neg = 0
    items: list[dict] = []
    for mind in results:
        text = (mind.get("text") or "").strip()
        if not text:
            continue
        badge = classify_trader_text(text)
        if badge == "positive":
            pos += 1
        elif badge == "negative":
            neg += 1
        author = mind.get("author")
        username = (
            author.get("username") if isinstance(author, dict) else str(author or "")
        )
        avatar = author.get("picture_url") if isinstance(author, dict) else None
        items.append({
            "user": username,
            "avatar": avatar,
            "text": text[:600],
            "badge": badge,
            "likes": mind.get("total_likes") or 0,
            "time": _to_epoch(mind.get("created")),
            "url": mind.get("url"),
            "image": None,
        })
    scored = pos + neg
    if scored == 0:
        return None
    return {
        "source": "TradingView Minds",
        "detail": f"{pos} positivos / {neg} negativos de {len(results)} pensamientos",
        "bullish": pos / scored,
        "sample": scored,
        "items": items,
    }
