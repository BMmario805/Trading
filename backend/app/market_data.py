"""Obtención de precios y velas desde Binance (cripto) y Yahoo Finance (resto)."""

import asyncio
import time

import httpx

from .config import Symbol

# data-api.binance.vision es el espejo público de solo-datos de Binance,
# accesible también desde regiones donde api.binance.com está restringido.
BINANCE_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
]

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Feed público de cotizaciones spot de Swissquote (sin API key)
SWISSQUOTE = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{symbol}"

# Futuro de Yahoo usado como base de velas para cada spot de Swissquote
SWISSQUOTE_CANDLE_PROXY = {"XAU/USD": "GC=F"}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TradingDashboard/1.0"}

# intervalo interno -> (intervalo binance, intervalo yahoo, rango yahoo)
INTERVALS = {
    "1m":  ("1m",  "1m",  "1d"),
    "5m":  ("5m",  "5m",  "5d"),
    "15m": ("15m", "15m", "5d"),
    "1h":  ("1h",  "60m", "1mo"),
    "4h":  ("4h",  "60m", "3mo"),   # yahoo no tiene 4h: se sirve 1h con más rango
    "1d":  ("1d",  "1d",  "1y"),
}

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15, headers=UA)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _binance_get(path: str, params: dict):
    last_exc: Exception | None = None
    for host in BINANCE_HOSTS:
        try:
            r = await client().get(host + path, params=params)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # probar el siguiente host
            last_exc = exc
    raise last_exc  # type: ignore[misc]


async def _swissquote_mid(source_symbol: str) -> float:
    """Precio medio (bid+ask)/2 del feed spot público de Swissquote."""
    r = await client().get(SWISSQUOTE.format(symbol=source_symbol))
    r.raise_for_status()
    profiles = r.json()[0]["spreadProfilePrices"]
    best = min(profiles, key=lambda p: p["askSpread"])
    return (best["bid"] + best["ask"]) / 2


async def _yahoo_candles(source_symbol: str, y_int: str, y_range: str, limit: int) -> list[dict]:
    r = await client().get(
        YAHOO_CHART.format(symbol=source_symbol),
        params={"interval": y_int, "range": y_range, "includePrePost": "false"},
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    ts = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    candles = []
    for i, t in enumerate(ts):
        o, h, l, c = (quote[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue
        v = quote["volume"][i] or 0
        candles.append(
            {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}
        )
    return candles[-limit:]


async def get_candles(sym: Symbol, interval: str, limit: int = 400) -> list[dict]:
    """Devuelve velas normalizadas: time (epoch s, UTC), open, high, low, close, volume."""
    if interval not in INTERVALS:
        raise ValueError(f"Intervalo no soportado: {interval}")
    b_int, y_int, y_range = INTERVALS[interval]

    if sym.source == "swissquote":
        # Sin histórico spot gratuito: velas del futuro proxy reescaladas a la
        # base spot actual, de forma que la última vela enlaza con el tick vivo.
        proxy = SWISSQUOTE_CANDLE_PROXY[sym.source_symbol]
        candles, mid = await asyncio.gather(
            _yahoo_candles(proxy, y_int, y_range, limit),
            _swissquote_mid(sym.source_symbol),
        )
        if not candles:
            return candles
        factor = mid / candles[-1]["close"]
        return [
            {
                "time": c["time"],
                "open": c["open"] * factor,
                "high": c["high"] * factor,
                "low": c["low"] * factor,
                "close": c["close"] * factor,
                "volume": c["volume"],
            }
            for c in candles
        ]

    if sym.source == "binance":
        raw = await _binance_get(
            "/api/v3/klines",
            {"symbol": sym.source_symbol, "interval": b_int, "limit": limit},
        )
        return [
            {
                "time": k[0] // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in raw
        ]

    return await _yahoo_candles(sym.source_symbol, y_int, y_range, limit)


# cache de variación diaria del proxy para no llamar a Yahoo cada 2 s
_proxy_change: dict[str, tuple[float, float]] = {}  # proxy -> (ts, changePct)


async def get_price(sym: Symbol) -> dict:
    """Último precio + variación diaria (%)."""
    if sym.source == "swissquote":
        mid = await _swissquote_mid(sym.source_symbol)
        # la variación % diaria del futuro proxy sirve como aproximación del spot
        proxy = SWISSQUOTE_CANDLE_PROXY[sym.source_symbol]
        cached = _proxy_change.get(proxy)
        if not cached or time.time() - cached[0] > 60:
            r = await client().get(
                YAHOO_CHART.format(symbol=proxy),
                params={"interval": "1m", "range": "1d"},
            )
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            pct = ((meta["regularMarketPrice"] - prev) / prev * 100) if prev else 0.0
            cached = (time.time(), round(pct, 2))
            _proxy_change[proxy] = cached
        return {
            "symbol": sym.id,
            "price": mid,
            "changePct": cached[1],
            "time": int(time.time()),
        }

    if sym.source == "binance":
        data = await _binance_get(
            "/api/v3/ticker/24hr", {"symbol": sym.source_symbol}
        )
        return {
            "symbol": sym.id,
            "price": float(data["lastPrice"]),
            "changePct": float(data["priceChangePercent"]),
            "time": int(time.time()),
        }

    r = await client().get(
        YAHOO_CHART.format(symbol=sym.source_symbol),
        params={"interval": "1m", "range": "1d"},
    )
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    change = ((price - prev) / prev * 100) if prev else 0.0
    return {
        "symbol": sym.id,
        "price": price,
        "changePct": round(change, 2),
        "time": int(time.time()),
    }
