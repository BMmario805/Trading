"""Lectura conjunta de posiciones: solo instantáneas ya obtenidas por los motores.

No consulta fuentes externas, no cambia el estado de riesgo y no envía órdenes.
Los importes conservan su divisa; los lotes MT5 nunca se tratan como unidades.
"""

import math
import time

from . import bots, desk, mt5_demo, opportunities
from .config import SYMBOLS_BY_ID
from .hub import hub


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def _fresh(stamp, now):
    value = _number(stamp, positive=True)
    return value is not None and -5 <= now - value <= 120


def _book(data, symbol, side, now):
    """Precio de salida observable, nunca una petición para obtener otro precio."""
    if not isinstance(data, dict):
        return None, None
    stamp = _number(data.get('at'), positive=True)
    book = data.get('books', {}).get(symbol, {})
    if 'referenceAt' in book:
        stamp=_number(book['referenceAt'],positive=True)
    bid = _number(book.get('bidPrice'), positive=True)
    ask = _number(book.get('askPrice'), positive=True)
    if not _fresh(stamp, now) or bid is None or ask is None or ask < bid:
        return None, None
    return (bid if side == 'long' else ask), stamp


def _lab_currency(symbol, data):
    sym = SYMBOLS_BY_ID.get(symbol)
    if sym is None:
        return None
    if sym.source == 'binance':
        meta = (data or {}).get('metadata', {}).get(sym.source_symbol, {})
        if meta.get('quoteAsset'):
            return meta['quoteAsset']
        return next((quote for quote in ('USDT', 'USDC', 'FDUSD', 'BTC', 'ETH')
                     if sym.source_symbol.endswith(quote)), None)
    if sym.category == 'forex' and len(sym.id) == 6:
        return sym.id[3:]
    if sym.source == 'swissquote':
        return sym.source_symbol.rsplit('/', 1)[-1]
    # El catálogo Yahoo actual usa acciones/ETF/futuros de EE. UU. Un sufijo
    # internacional requiere metadatos de divisa, no una conversión supuesta.
    return 'USD' if sym.source == 'yahoo' and '.' not in sym.source_symbol else None


def _source(source, label, mode, available, updated_at=None, stale=False, message=None):
    return dict(source=source, label=label, accountMode=mode, available=available,
                updatedAt=updated_at, stale=stale, message=message)


def _desk_positions(now, engine=None):
    engine = engine or desk.DESK
    if engine is None:
        return [], _source('desk', 'Mesa Siete', 'paper', False, message='Mesa no iniciada')
    state = engine.state
    desk_id=getattr(engine,'desk_id','crypto')
    label='Mesa Siete' if desk_id=='crypto' else f'Mesa Siete · {"Forex" if desk_id=="forex" else "Acciones"}'
    data = engine.last_data
    risk_data=getattr(engine,'last_risk_data',None)
    stamp = max(_number((data or {}).get('at'), positive=True) or 0,
                _number((risk_data or {}).get('at'), positive=True) or 0) or None
    # El libro actual tiene una posición; admite además una lista explícita.
    raw = state.get('positions')
    raw = raw if isinstance(raw, list) else ([state['position']] if state.get('position') else [])
    positions = []
    for p in raw:
        if not isinstance(p, dict) or p.get('status', 'open') != 'open':
            continue
        observed=max((d for d in (data,risk_data) if isinstance(d,dict) and p['symbol'] in d.get('books',{})),
                     key=lambda d:d.get('at',0),default=None)
        price, price_at = _book(observed, p['symbol'], 'long', now)
        fresh = price is not None
        price = price if fresh else _number(p.get('lastBid'), positive=True)
        qty = _number(p.get('qty'), positive=True)
        notional = _number(p.get('notional'))
        fee = _number(p.get('fee'))
        cost=p.get('costPolicy') or {}
        pnl = (_number(qty * price * (1 - cost.get('slippageRate',desk.SLIPPAGE)) * (1 - cost.get('feeRate',desk.FEE)) - notional - fee)
               if None not in (qty, price, notional, fee) else None)
        positions.append(dict(
            id=f"desk:{'' if desk_id=='crypto' else desk_id+':'}{p.get('entryKey', p['symbol'])}", source='desk', sourceLabel=label,
            botId='desk' if desk_id=='crypto' else desk_id, botName=label, symbol=p['symbol'], side='long',
            quantity=qty, quantityUnit='units', entryPrice=_number(p.get('entry'), positive=True),
            currentPrice=price, notional=notional, unrealizedPnl=pnl, currency=getattr(engine,'currency','USDT'),
            openedAt=_number(p.get('entryTime'), positive=True),
            stopLoss=_number(p.get('sl'), positive=True),
            takeProfit=None if p.get('exitPolicy') and not p.get('partialSkippedByFilters') else _number(p.get('tp'), positive=True),
            priceFresh=fresh, priceUpdatedAt=price_at, accountMode='paper', pnlBasis='estimated-net',
        ))
    stale = not _fresh(stamp, now) or bool(engine.last_error)
    return positions, _source('desk', 'Mesa Siete', 'paper', True, stamp, stale,
                              'Última instantánea; datos pendientes de actualizar' if stale else None)


def _lab_positions(now, data):
    positions = []
    stamps = []
    for bot_id, bot in list(bots.BOTS.items()):
        for p in list(bot.positions.values()):
            tick = hub._last.get(p.symbol, {})
            tick_price = _number(tick.get('price'), positive=True)
            stamp = _number(tick.get('time'), positive=True)
            fresh = tick_price is not None and _fresh(stamp, now)
            price = tick_price if fresh else None
            sym = SYMBOLS_BY_ID.get(p.symbol)
            if price is None and sym and sym.source == 'binance':
                price, stamp = _book(data, sym.source_symbol, p.direction, now)
                fresh = price is not None
            if price is None:
                price = _number(p.last_price, positive=True)
                stamp = None  # El motor antiguo no guarda la fecha de last_price.
            if stamp is not None:
                stamps.append(stamp)
            qty = _number(p.qty, positive=True)
            conversion=getattr(p,'quote_to_account',1.0)
            entry = _number(p.entry_price, positive=True)
            pnl = _number(p.unrealized(price)) if None not in (price, entry, qty) else None
            costs=getattr(p,'execution_costs',None)
            positions.append(dict(
                id=f'lab:{bot_id}:{p.id}', source='lab', sourceLabel='Laboratorio',
                botId=bot_id, botName=bot.cfg.name, symbol=p.symbol, side=p.direction,
                quantity=qty, quantityUnit='units', entryPrice=entry, currentPrice=price,
                notional=_number(qty * entry * conversion) if None not in (qty, entry) else None,
                unrealizedPnl=pnl, currency=(getattr(bot,'session',None) or {}).get('currency') or _lab_currency(p.symbol, data),
                openedAt=_number(p.entry_time, positive=True),
                stopLoss=_number(p.sl, positive=True), takeProfit=_number(p.tp, positive=True),
                priceFresh=fresh, priceUpdatedAt=stamp, accountMode='paper',
                pnlBasis='estimated-net' if costs else 'gross',
                executionMode=costs.get('mode') if costs else 'legacy-simulation',costAssumptions=costs,
            ))
    available = bool(bots.BOTS)
    stale = any(not p['priceFresh'] for p in positions)
    message = ('Laboratorio no iniciado' if not available else
               'Algunos precios son la última estimación del bot, sin fecha verificable' if stale else None)
    return positions, _source('lab', 'Laboratorio', 'paper', available,
                              max(stamps) if stamps else None, stale, message)


def _mt5_positions(now):
    # STATE se sustituye tras DemoGateway.account(): ninguna llamada al terminal
    # en esta ruta. Una desconexión conserva una instantánea DEMO marcada antigua.
    state = dict(mt5_demo.STATE)
    verified = state.get('configured') is True and state.get('demo') is True
    if not verified:
        return [], _source('mt5', 'MetaTrader 5', 'demo', False,
                            message='Sin instantánea de cuenta DEMO verificada')
    stamp = _number(state.get('updatedAt'), positive=True)
    fresh = state.get('connected') is True and _fresh(stamp, now)
    names = {cfg.id: cfg.name for cfg in bots.ALL_BOTS}
    positions = []
    for raw in list(state.get('positions') or []):
        p = dict(raw)
        price = _number(p.get('currentPrice'), positive=True)
        price_at = _number(p.get('priceAt'), positive=True)
        bot_id = p.get('bot') or 'manual/otro'
        positions.append(dict(
            id=f"mt5:{p['ticket']}", source='mt5', sourceLabel='MetaTrader 5',
            botId=bot_id, botName=names.get(bot_id, 'Manual / otros robots'),
            symbol=p['symbol'], side=p['direction'], quantity=_number(p.get('volume'), positive=True),
            quantityUnit='lots', entryPrice=_number(p.get('entry'), positive=True), currentPrice=price,
            # El nominal requiere tamaño del contrato y conversión del bróker.
            notional=_number(p.get('notional')), unrealizedPnl=_number(p.get('pnl')),
            currency=state.get('currency') or None,
            openedAt=_number(p.get('openedAt'), positive=True),
            stopLoss=_number(p.get('sl'), positive=True), takeProfit=_number(p.get('tp'), positive=True),
            priceFresh=fresh and price is not None and _fresh(price_at, now),
            priceUpdatedAt=price_at if price is not None else None,
            accountMode='demo', pnlBasis='broker-floating',
        ))
    return positions, _source('mt5', 'MetaTrader 5', 'demo', True, stamp, not fresh,
                              'Última instantánea DEMO; conexión o sincronización pendiente' if not fresh else None)


def snapshot():
    """Contrato de GET /api/positions/open; importes por posición, sin total mixto."""
    now = time.time()
    cached = opportunities._cache.get('quotes')
    data = cached[1] if cached else (desk.DESK.last_data if desk.DESK else None)
    desk_rows, desk_source = _desk_positions(now)
    for key,engine in desk.DESKS.items():
        if key=='crypto': continue
        rows,_=_desk_positions(now,engine)
        desk_rows.extend(rows)
    lab_rows, lab_source = _lab_positions(now, data)
    mt5_rows, mt5_source = _mt5_positions(now)
    positions = desk_rows + lab_rows + mt5_rows
    positions.sort(key=lambda p: (-(p['openedAt'] or 0), p['id']))
    return dict(updatedAt=int(now), positions=positions,
                counts=dict(desk=len(desk_rows), lab=len(lab_rows), mt5=len(mt5_rows), total=len(positions)),
                sources=[desk_source, lab_source, mt5_source])
