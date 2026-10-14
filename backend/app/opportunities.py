"""Calidad de mercado desde datos públicos de Binance; nunca envía órdenes."""

import asyncio
import math
import time

from . import market_data
from .config import Symbol

_cache: dict[str, tuple[float, object]] = {}
_lock = asyncio.Lock()
MIN_QUOTE_VOLUME = 10_000_000  # USDT negociados en 24 h, no profundidad ejecutable
MAX_SPREAD = .001


async def snapshot() -> dict:
    async with _lock:
        now = time.time()
        if 'quotes' in _cache and now - _cache['quotes'][0] < 30:
            return _cache['quotes'][1]
        metadata = _cache.get('metadata')
        if not metadata or now - metadata[0] >= 3600:
            exchange = await market_data._binance_get('/api/v3/exchangeInfo', {})
            metadata = (time.time(), {s['symbol']: s for s in exchange['symbols']})
            _cache['metadata'] = metadata
        tickers, books = await asyncio.gather(
            market_data._binance_get('/api/v3/ticker/24hr', {}),
            market_data._binance_get('/api/v3/ticker/bookTicker', {}),
        )
        result = dict(metadata=metadata[1], tickers={t['symbol']: t for t in tickers},
                      books={b['symbol']: b for b in books}, at=int(time.time()))
        _cache['quotes'] = (time.time(), result)
        return result


def assess(sym: Symbol, data: dict) -> dict:
    key = sym.source_symbol
    meta, ticker, book = (data[k].get(key, {}) for k in ('metadata', 'tickers', 'books'))
    out = dict(symbol=sym.id, source='Binance Spot', at=data['at'], eligible=False)
    if meta.get('status') != 'TRADING' or not meta.get('isSpotTradingAllowed'):
        return dict(out, reason='Mercado no disponible para spot')
    try:
        bid, ask, volume = float(book['bidPrice']), float(book['askPrice']), float(ticker['quoteVolume'])
        if not all(math.isfinite(x) for x in (bid, ask, volume)) or bid <= 0 or ask < bid or volume < 0:
            raise ValueError('Datos inválidos')
        spread = (ask - bid) / ((ask + bid) / 2)
    except (ValueError, KeyError, TypeError):
        return dict(out, reason='Sin datos válidos de spread o volumen')
    out.update(spreadPct=spread * 100, quoteVolume24h=volume, bid=bid, ask=ask)
    if spread > MAX_SPREAD:
        return dict(out, reason='Spread observado demasiado amplio')
    if volume < MIN_QUOTE_VOLUME:
        return dict(out, reason='Volumen negociado insuficiente')
    return dict(out, eligible=True, reason='Spread y volumen aceptables')


async def market_quality(sym: Symbol) -> dict:
    if sym.source != 'binance':
        return dict(symbol=sym.id, eligible=True, reason='Sin filtro de libro para esta fuente')
    return assess(sym, await snapshot())
