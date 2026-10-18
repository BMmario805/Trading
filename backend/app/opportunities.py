"""Calidad de mercado desde datos públicos de Binance; nunca envía órdenes."""

import asyncio
import math
import json
import time

from . import market_data
from .config import Symbol

_cache: dict[str, tuple[float, object]] = {}
_lock = asyncio.Lock()
_book_lock = asyncio.Lock()
_book_cache: dict[tuple[str, ...], tuple[float, dict]] = {}
MIN_QUOTE_VOLUME = 10_000_000  # USDT negociados en 24 h, no profundidad ejecutable
MAX_SPREAD = .001


async def execution_snapshot(symbols, reference):
    """Libro breve e independiente: la vigilancia no espera el escaneo global."""
    keys=tuple(sorted(set(symbols)))
    if not keys or len(keys)>20:
        raise ValueError('Lote de cotizaciones inválido')
    async with _book_lock:
        cached=_book_cache.get(keys)
        if cached and time.time()-cached[0]<2:
            return cached[1]
        raw=await asyncio.wait_for(market_data._binance_get('/api/v3/ticker/bookTicker',
                                  {'symbols':json.dumps(keys,separators=(',',':'),ensure_ascii=False)}),timeout=4)
        books={row['symbol']:row for row in raw}
        if any(key not in books for key in keys):
            raise ValueError('Libro incompleto')
        result=dict(metadata=reference.get('metadata',{}),tickers=reference.get('tickers',{}),
                    books=books,at=time.time(),scope='execution-books')
        _book_cache[keys]=(time.time(),result)
        if len(_book_cache)>32:
            oldest=min(_book_cache,key=lambda key:_book_cache[key][0])
            _book_cache.pop(oldest,None)
        return result


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


def assess(sym: Symbol, data: dict, *, min_quote_volume=MIN_QUOTE_VOLUME) -> dict:
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
    if volume < min_quote_volume:
        return dict(out, reason='Volumen negociado insuficiente')
    return dict(out, eligible=True, reason='Spread y volumen aceptables')


async def market_quality(sym: Symbol) -> dict:
    if sym.source != 'binance':
        return dict(symbol=sym.id, eligible=True, reason='Sin filtro de libro para esta fuente')
    return assess(sym, await snapshot())
