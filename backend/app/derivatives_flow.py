"""Lecturas públicas de Binance USDT-M Futures. Sin clave y sin API de pago.

Un fallo o un timeout de fapi.binance.com devuelve un régimen neutro. La
vigilancia de stops no espera esta lectura: el escaneo solo consulta la caché
o un refresco que ya está en marcha, con un tope de 3 s dentro de la tarea.
"""

import asyncio
import math
import time

import httpx

FAPI = 'https://fapi.binance.com'
TTL = 300.0
TIMEOUT = 3.0
_CACHE = {}
_INFLIGHT = {}


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def futures_symbol(symbol):
    """Símbolo USDT-M. SPY y el resto de nombres no alfanuméricos no salen a la red."""
    raw = ''.join(ch for ch in str(symbol or '').upper() if ch.isalnum())
    if not raw or raw in ('SPY', 'USDT'):
        return None
    pair = raw if raw.endswith('USDT') else raw + 'USDT'
    if not pair.isalnum() or not 6 <= len(pair) <= 20 or pair == 'USDT':
        return None
    return pair


def derivatives_regime(funding_rate, oi_change_1h_pct, price_change_1h_pct, direction):
    """Régimen de posicionamiento. Puro: no lee red ni reloj.

    crowdedLong: funding > 0,05 % por 8 h y el interés abierto sube más de un
    3 % en 1 h mientras el precio no avanza (variación 1 h <= 0). Riesgo de
    long squeeze.
    cleanBreakout: el precio sube, el interés abierto acompaña (> 1,5 %) y el
    funding no está caro (<= 0,02 % por 8 h).
    flowAdjustment está en [-0,25, +0,25] y ya mira a `direction`: positivo
    apoya esa dirección. Los porcentajes de precio e interés abierto van en
    puntos (3.0 == 3 %).
    """
    funding = _num(funding_rate)
    oi_change = _num(oi_change_1h_pct)
    price_change = _num(price_change_1h_pct)
    if direction in ('long', 'buy', 1, 1.0):
        side = 1.0
    elif direction in ('short', 'sell', -1, -1.0):
        side = -1.0
    else:
        side = 0.0
    crowded = bool(funding is not None and funding > 0.0005 and oi_change is not None
                   and oi_change > 3.0 and price_change is not None and price_change <= 0.0)
    clean = bool(price_change is not None and price_change > 0.0 and oi_change is not None
                 and oi_change > 1.5 and funding is not None and funding <= 0.0002)
    raw = -0.25 if crowded else 0.25 if clean else 0.0
    return dict(crowdedLong=crowded, cleanBreakout=clean,
                flowAdjustment=max(-0.25, min(0.25, raw * side)))


def _empty(symbol, reason, direction='long'):
    return dict(available=False, symbol=symbol, fundingRate=None, markPrice=None, indexPrice=None,
                basis=None, oiChange1hPct=None, oiChange4hPct=None, longShortRatio=None,
                priceChange1hPct=None, reason=reason, source=FAPI,
                regime=derivatives_regime(None, None, None, direction))


def oi_changes(rows):
    """Variación porcentual del interés abierto a 1 h y 4 h sobre velas de 15 m."""
    points = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stamp = _num(row.get('timestamp'))
        value = _num(row.get('sumOpenInterest'))
        if stamp is None or value is None or value < 0:
            continue
        points.append((stamp, value))
    points.sort()
    if len(points) < 2:
        return None, None
    return _change_over(points, 3_600_000), _change_over(points, 14_400_000)


def _change_over(points, horizon_ms):
    latest_stamp, latest = points[-1]
    target = latest_stamp - horizon_ms
    prior = min(points[:-1], key=lambda item: abs(item[0] - target))
    # Una vela de 15 m de tolerancia: limit=16 cubre justo el tramo de 4 h.
    if abs(prior[0] - target) > 20 * 60 * 1000 or prior[1] == 0:
        return None
    return (latest - prior[1]) / abs(prior[1]) * 100.0


def _price_change(rows):
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    previous, latest = rows[-2], rows[-1]
    try:
        old, new = float(previous[4]), float(latest[4])
    except (TypeError, ValueError, IndexError):
        return None
    if not math.isfinite(old) or not math.isfinite(new) or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def _assemble(pair, premium, oi_rows, ratio_rows, klines):
    funding = mark = index = basis = None
    if isinstance(premium, dict):
        funding = _num(premium.get('lastFundingRate'))
        mark = _num(premium.get('markPrice'))
        index = _num(premium.get('indexPrice'))
        if mark is not None and index not in (None, 0.0):
            basis = (mark - index) / index
    oi_1h, oi_4h = oi_changes(oi_rows) if isinstance(oi_rows, list) else (None, None)
    ratio = None
    if isinstance(ratio_rows, list) and ratio_rows and isinstance(ratio_rows[-1], dict):
        ratio = _num(ratio_rows[-1].get('longShortRatio'))
    price_change = _price_change(klines)
    available = any(value is not None for value in (funding, oi_1h, oi_4h, ratio))
    return dict(available=available, symbol=pair, fundingRate=funding, markPrice=mark, indexPrice=index,
                basis=basis, oiChange1hPct=oi_1h, oiChange4hPct=oi_4h, longShortRatio=ratio,
                priceChange1hPct=price_change, reason=None if available else 'Lectura pública incompleta',
                source=FAPI)


def _with_regime(snap, direction, price_change_1h_pct):
    price = _num(price_change_1h_pct)
    if price is None:
        price = snap.get('priceChange1hPct')
    out = dict(snap)
    out['regime'] = derivatives_regime(snap.get('fundingRate'), snap.get('oiChange1hPct'), price, direction)
    return out


async def _get(client, path, params):
    try:
        response = await client.get(FAPI + path, params=params)
        response.raise_for_status()
        body = response.json()
    except Exception:
        return None
    return body


async def _fetch(pair):
    try:
        timeout = httpx.Timeout(TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout, headers={'User-Agent': 'TradingDashboard/1.0'}) as client:
            premium, oi_rows, ratio_rows, klines = await asyncio.gather(
                _get(client, '/fapi/v1/premiumIndex', {'symbol': pair}),
                _get(client, '/futures/data/openInterestHist',
                     {'symbol': pair, 'period': '15m', 'limit': 16}),
                _get(client, '/futures/data/topLongShortPositionRatio',
                     {'symbol': pair, 'period': '15m', 'limit': 4}),
                _get(client, '/fapi/v1/klines', {'symbol': pair, 'interval': '1h', 'limit': 2}),
            )
        snap = _assemble(pair, premium, oi_rows, ratio_rows, klines)
    except asyncio.CancelledError:
        raise
    except Exception:
        snap = _empty(pair, 'fapi.binance.com no respondió')
    if snap.get('available'):
        _CACHE[pair] = (time.monotonic(), {key: value for key, value in snap.items() if key != 'regime'})
        if len(_CACHE) > 256:
            oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
            _CACHE.pop(oldest, None)
    return snap


def schedule(symbol):
    """Refresco en segundo plano. No espera y no propaga errores de red."""
    pair = futures_symbol(symbol)
    if not pair:
        return None
    cached = _CACHE.get(pair)
    if cached and time.monotonic() - cached[0] < TTL:
        return None
    current = _INFLIGHT.get(pair)
    if current is not None and not current.done():
        return current
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_refresh(pair), name=f'fapi-{pair}')
    _INFLIGHT[pair] = task
    return task


async def _refresh(pair):
    try:
        await asyncio.wait_for(_fetch(pair), timeout=TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    finally:
        if _INFLIGHT.get(pair) is asyncio.current_task():
            _INFLIGHT.pop(pair, None)


def peek(symbol, direction='long', price_change_1h_pct=None):
    """Solo caché. No abre red y no lanza."""
    pair = futures_symbol(symbol)
    if not pair:
        return _empty(str(symbol or ''), 'Sin contrato USDT-M público', direction)
    cached = _CACHE.get(pair)
    if not cached or time.monotonic() - cached[0] >= TTL:
        return _empty(pair, 'Sin lectura vigente', direction)
    return _with_regime(cached[1], direction, price_change_1h_pct)


async def peek_or_wait(symbol, direction='long', price_change_1h_pct=None, wait=1.0):
    """Usa la caché. Si hay un refresco en curso, lo espera como máximo `wait` segundos."""
    snap = peek(symbol, direction, price_change_1h_pct)
    if snap.get('available'):
        return snap
    task = _INFLIGHT.get(futures_symbol(symbol) or '')
    if task is None or task.done():
        return snap
    try:
        await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.CancelledError:
        raise
    except Exception:
        return snap
    return peek(symbol, direction, price_change_1h_pct)


async def read_symbol(symbol, direction='long', price_change_1h_pct=None):
    """Lectura con caché de 300 s y timeout de 3 s. Nunca lanza por un fallo de red."""
    pair = futures_symbol(symbol)
    if not pair:
        return _empty(str(symbol or ''), 'Sin contrato USDT-M público', direction)
    cached = _CACHE.get(pair)
    if cached and time.monotonic() - cached[0] < TTL:
        return _with_regime(cached[1], direction, price_change_1h_pct)
    task = _INFLIGHT.get(pair)
    if task is None or task.done():
        task = asyncio.create_task(_fetch(pair), name=f'fapi-{pair}')
        _INFLIGHT[pair] = task
    try:
        snap = await asyncio.wait_for(asyncio.shield(task), timeout=TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception:
        snap = _empty(pair, 'fapi.binance.com no respondió')
    finally:
        if task.done() and _INFLIGHT.get(pair) is task:
            _INFLIGHT.pop(pair, None)
    if not isinstance(snap, dict) or not snap.get('available'):
        # _fetch ya degradó. Un timeout deja la tarea viva para la próxima lectura.
        if task.done() and not task.cancelled():
            try:
                snap = task.result()
            except Exception:
                snap = _empty(pair, 'fapi.binance.com no respondió')
        elif not isinstance(snap, dict):
            snap = _empty(pair, 'fapi.binance.com no respondió')
    if not snap.get('available'):
        return _empty(pair, snap.get('reason') or 'fapi.binance.com no respondió', direction)
    return _with_regime(snap, direction, price_change_1h_pct)
