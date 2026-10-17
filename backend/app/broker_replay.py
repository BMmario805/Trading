"""Conservative, deterministic MT5 contract replay. No terminal or order I/O.

Bars are MT5 bid bars. Buys pay ask and sells close at ask. Money is in the
account currency, lots obey the broker grid, and exits mirror DemoEngine:
broker SL/TP, breakeven/trailing after 1R and time exit, with 300 s cooldown.
An OHLC replay cannot reconstruct intrabar ticks or historical FX conversion;
such snapshots are informative and cannot promote a strategy to validated.
"""

import math
from dataclasses import asdict, dataclass, replace
from itertools import product

from .execution import fresh_signal
from .mt5_gateway import lot_floor
from .strategy import extension_series

VERSION = 1


@dataclass(frozen=True)
class BrokerContract:
    symbol: str
    currency: str
    tick_size: float
    point: float
    digits: int
    stops_level: int
    freeze_level: int
    volume_min: float
    volume_step: float
    volume_max: float
    money_per_price_lot: float
    margin_per_lot: float
    spread: float
    commission_roundtrip_per_lot: float | None = None
    carry_per_lot_day: float | None = None
    slippage: float = 0.0
    historical_verified: bool = False
    clock_offset: int = 0

    @property
    def promotable(self):
        return (self.historical_verified and self.commission_roundtrip_per_lot is not None
                and self.carry_per_lot_day is not None)

    def validate(self):
        positives = (self.tick_size, self.point, self.volume_min, self.volume_step,
                     self.volume_max, self.money_per_price_lot)
        nonnegative = (self.margin_per_lot, self.spread, self.slippage, self.stops_level, self.freeze_level)
        if not all(math.isfinite(x) and x > 0 for x in positives):
            raise ValueError('Especificación monetaria o de lotes del bróker inválida')
        if not all(math.isfinite(x) and x >= 0 for x in nonnegative):
            raise ValueError('Margen o costes del bróker inválidos')
        for cost in (self.commission_roundtrip_per_lot, self.carry_per_lot_day):
            if cost is not None and (not math.isfinite(cost) or cost < 0):
                raise ValueError('Costes monetarios inválidos')


def capture_contract(api, symbol, account, config, tick, clock_offset=0):
    """Read-only terminal adapter. Capture before dispatching a pure worker.

    Profit/margin calculators return account currency. The current conversion
    is not treated as a historical conversion series, nor are missing fees zero.
    """
    info = api.symbol_info(symbol)
    if info is None:
        raise ValueError('Contrato MT5 no disponible')
    step = info.trade_tick_size or info.point
    probe = max(step*100, tick.ask*.001)
    probes = [api.order_calc_profit(kind, symbol, 1., tick.ask, tick.ask+move)
              for kind, move in ((api.ORDER_TYPE_BUY, probe), (api.ORDER_TYPE_BUY, -probe),
                                 (api.ORDER_TYPE_SELL, probe), (api.ORDER_TYPE_SELL, -probe))]
    if any(v is None or not math.isfinite(v) or v == 0 for v in probes):
        raise ValueError('No se puede reproducir PnL monetario del contrato')
    coefficients = [abs(v/probe) for v in probes]
    # Capture is conservative for losses, but remains indicative if conversion
    # is nonlinear or historical conversion/costs have not been supplied.
    costs = config.get('replay_costs', {}).get(symbol, {})
    margin = api.order_calc_margin(api.ORDER_TYPE_BUY, symbol, 1., tick.ask)
    sell_margin = api.order_calc_margin(api.ORDER_TYPE_SELL, symbol, 1., tick.bid)
    if margin is None or sell_margin is None:
        raise ValueError('No se puede reproducir el margen del contrato')
    currency = getattr(account, 'currency', '')
    same_currency = getattr(info, 'currency_profit', None) == currency
    contract = BrokerContract(
        symbol=symbol, currency=currency, tick_size=step, point=info.point,
        digits=info.digits, stops_level=info.trade_stops_level,
        freeze_level=getattr(info, 'trade_freeze_level', 0),
        volume_min=info.volume_min, volume_step=info.volume_step, volume_max=info.volume_max,
        money_per_price_lot=max(coefficients), margin_per_lot=max(margin, sell_margin),
        spread=tick.ask-tick.bid, commission_roundtrip_per_lot=costs.get('roundTripCommissionPerLot'),
        carry_per_lot_day=costs.get('carryPerLotDay'),
        slippage=max(0, costs.get('slippagePoints', 2))*info.point,
        historical_verified=bool(costs.get('historicalVerified') is True and same_currency
                                 and max(coefficients)/min(coefficients) <= 1.00001),
        clock_offset=int(clock_offset))
    contract.validate()
    return contract


def entry_plan(contract, direction, price, atr, sl_atr, tp_atr, equity, risk_pct, max_open_risk_pct):
    """Same lot floor, stop grid, risk reserve and 25% margin cap as gateway."""
    c = contract
    c.validate()
    distance = max(atr*sl_atr, c.stops_level*c.point+c.spread+2*c.point)
    stop = round((math.floor((price-distance)/c.tick_size) if direction > 0
                  else math.ceil((price+distance)/c.tick_size))*c.tick_size, c.digits)
    target_distance = max(atr*tp_atr, distance)
    target = round((math.ceil((price+target_distance)/c.tick_size) if direction > 0
                    else math.floor((price-target_distance)/c.tick_size))*c.tick_size, c.digits)
    if min(stop, target) <= 0 or equity <= 0:
        return dict(executable=False, reason='capital-or-price')
    loss = abs(price-stop)*c.money_per_price_lot
    per_lot_risk = loss+max(loss*.1,c.commission_roundtrip_per_lot or 0)
    budget = equity*min(risk_pct, max_open_risk_pct)
    lots = lot_floor(min(c.volume_max, budget/per_lot_risk), c.volume_step)
    minimum_risk = per_lot_risk*c.volume_min
    required = max(minimum_risk/min(risk_pct, max_open_risk_pct), 4*c.volume_min*c.margin_per_lot)
    result = dict(executable=False, lots=lots, stop=stop, target=target, riskBudget=budget,
                  minimumLotRisk=minimum_risk, requiredCapital=required, currency=c.currency)
    if lots < c.volume_min:
        return dict(result, reason='minimum-lot')
    if lots*c.margin_per_lot > equity*.25:
        return dict(result, reason='margin')
    costs = (c.commission_roundtrip_per_lot or 0)*lots + 2*c.slippage*c.money_per_price_lot*lots
    if (target-price)*direction*c.money_per_price_lot*lots-costs <= 0:
        return dict(result, reason='net-target')
    return dict(result, executable=True, reason=None)


def simulate(candles, scores, atrs, contract, params, *, initial, risk_pct,
             max_open_risk_pct, max_hold_seconds, start=0, end=None,
             extensions=None, overext_atr=float('inf'), max_daily_loss_pct=.02):
    """OHLC approximation; no contrary-signal exit absent from MT5 management."""
    c = contract
    c.validate()
    end = min(end if end is not None else len(candles), len(candles))
    equity = peak = initial
    max_dd = 0.
    trades, blocked = [], {}
    pos, cooldown_until = None, 0
    current_day, day_initial, day_paused = None, initial, False
    threshold = params['entryThreshold']

    def close(price, stamp, reason):
        nonlocal pos, equity, peak, max_dd, cooldown_until
        price -= pos['direction']*c.slippage
        pnl = (price-pos['entry'])*pos['direction']*pos['lots']*c.money_per_price_lot
        pnl -= (c.commission_roundtrip_per_lot or 0)*pos['lots']
        pnl -= (c.carry_per_lot_day or 0)*pos['lots']*max(0,stamp-pos['opened'])/86400
        equity += pnl
        peak = max(peak,equity)
        max_dd = max(max_dd,peak-equity)
        trades.append(dict(pnl=pnl, reason=reason, openedAt=pos['opened'], closedAt=stamp,
                           lots=pos['lots'], entry=pos['entry'], exit=price))
        cooldown_until = stamp+300
        pos = None

    for i in range(max(start, 1), end):
        bar = candles[i]
        stamp = bar['time']
        day = int((stamp-c.clock_offset)//86400)
        if day != current_day:
            current_day, day_initial, day_paused = day, equity, False
        if equity <= day_initial*(1-max_daily_loss_pct):
            day_paused = True
        if pos:
            direction = pos['direction']
            ask_shift = c.spread if direction < 0 else 0
            low, high, opening = bar['low']+ask_shift, bar['high']+ask_shift, bar['open']+ask_shift
            if stamp-pos['opened'] >= max_hold_seconds:
                # Time exits occur at the opening of the expired bar, before
                # seeing its later high/low. A gap through a barrier is still
                # filled conservatively at the available opening quote.
                close(opening,stamp,'time')
                continue
            if (low <= pos['stop'] if direction > 0 else high >= pos['stop']):
                fill = min(pos['stop'], opening) if direction > 0 else max(pos['stop'], opening)
                close(fill,stamp,'stop')
                continue
            if (high >= pos['target'] if direction > 0 else low <= pos['target']):
                close(pos['target'],stamp,'target')
                continue
            # Observe only close before arming next bar's trailing stop. Intrabar
            # high/low is never reused to improve an exit in the same OHLC bar.
            price = bar['close']+ask_shift
            pos['best'] = max(pos['best'],price) if direction > 0 else min(pos['best'],price)
            gain = (pos['best']-pos['entry'])*direction
            if gain >= pos['risk']:
                trail = pos['atr']*params['trailAtr']*(.75 if gain >= 2*pos['risk'] else 1)
                stop = max(pos['entry'],pos['best']-trail,pos['stop']) if direction > 0 else min(pos['entry'],pos['best']+trail,pos['stop'])
                stop = round((math.floor(stop/c.tick_size) if direction > 0 else math.ceil(stop/c.tick_size))*c.tick_size,c.digits)
                minimum = max(c.stops_level,c.freeze_level)*c.point+2*c.point
                if (price-stop)*direction >= minimum and abs(stop-pos['stop']) >= c.tick_size:
                    pos['stop'] = stop
            continue
        if stamp < cooldown_until or day_paused or equity <= 0:
            continue
        a, score = atrs[i], scores[i]
        if a is None or not math.isfinite(a) or a <= 0 or not fresh_signal(score,scores[max(0,i-3):i],threshold):
            continue
        direction = 1 if score > 0 else -1
        if a < 3*c.spread:
            blocked['spread'] = blocked.get('spread',0)+1
            continue
        ext = extensions[i] if extensions is not None else None
        if ext is not None and ext*direction > overext_atr:
            continue
        if i+1 >= end:
            continue
        next_bar = candles[i+1]
        if abs(next_bar['open']-bar['close']) > a:
            continue
        price = next_bar['open']+(c.spread if direction > 0 else 0)+direction*c.slippage
        plan = entry_plan(c,direction,price,a,params['slAtr'],params['tpAtr'],equity,risk_pct,max_open_risk_pct)
        if not plan['executable']:
            blocked[plan['reason']] = blocked.get(plan['reason'],0)+1
            continue
        pos = dict(entry=price,stop=plan['stop'],target=plan['target'],lots=plan['lots'],
                   direction=direction,atr=a,risk=abs(price-plan['stop']),best=price,opened=next_bar['time'])
    if pos:
        close(candles[end-1]['close']+(c.spread if pos['direction'] < 0 else 0),candles[end-1]['time'],'boundary')
    pnls = [t['pnl'] for t in trades]
    loss = -sum(min(0,p) for p in pnls)
    gain = sum(max(0,p) for p in pnls)
    return dict(trades=len(trades),pnl=round(sum(pnls),6),expectancy=sum(pnls)/len(pnls) if pnls else None,
                wins=sum(p > 0 for p in pnls),profitFactor=gain/loss if loss else None,
                maxDrawdown=round(max_dd,6),blocked=blocked,executions=trades,
                costsKnown=c.promotable)


def optimize(cfg, candles, scores, atrs, contract, *, initial, risk_pct, max_open_risk_pct,
             max_daily_loss_pct, engine_version):
    """Small preregistered grid, chronological validation and untouched holdout."""
    base = dict(entryThreshold=cfg.entry_threshold,slAtr=cfg.sl_atr,tpAtr=cfg.tp_atr,trailAtr=cfg.trail_atr)
    extensions = extension_series(candles)
    embargo = max(3, math.ceil(cfg.max_hold_seconds/max(1,candles[1]['time']-candles[0]['time'])))
    train_end, val_end = int(len(candles)*.6), int(len(candles)*.8)
    common = dict(initial=initial,risk_pct=risk_pct,max_open_risk_pct=max_open_risk_pct,
                  max_hold_seconds=cfg.max_hold_seconds,extensions=extensions,overext_atr=cfg.overext_atr,
                  max_daily_loss_pct=max_daily_loss_pct)
    best, best_train = base, None
    for threshold,stop,target in product((cfg.entry_threshold*.8,cfg.entry_threshold,cfg.entry_threshold*1.2),
                                         (cfg.sl_atr,cfg.sl_atr*1.25),(cfg.tp_atr,cfg.tp_atr*1.25)):
        params = dict(base,entryThreshold=threshold,slAtr=stop,tpAtr=target)
        result = simulate(candles,scores,atrs,contract,params,end=train_end,**common)
        if best_train is None or (result['trades'] >= 8 and result['pnl'] > best_train['pnl']):
            best,best_train = params,result
    val = simulate(candles,scores,atrs,contract,best,start=min(train_end+embargo,val_end),end=val_end,**common)
    holdout = simulate(candles,scores,atrs,contract,best,start=min(val_end+embargo,len(candles)),**common)
    stress_contract = replace(contract,spread=contract.spread*2,slippage=contract.slippage*2,
                              commission_roundtrip_per_lot=None if contract.commission_roundtrip_per_lot is None else contract.commission_roundtrip_per_lot*2)
    stress = simulate(candles,scores,atrs,stress_contract,best,start=min(val_end+embargo,len(candles)),**common)
    accepted = (contract.promotable and best_train['trades'] >= 8 and best_train['pnl'] > 0
                and all(m['trades'] >= 5 and m['pnl'] > 0 for m in (val,holdout,stress)))
    reason = ('Replay contractual y tramos temporales superados' if accepted else
              'Replay informativo: faltan costes históricos o conversión monetaria verificados' if not contract.promotable else
              'Ejecuciones, rentabilidad neta o estabilidad fuera de muestra insuficientes')
    if not best_train['trades'] and best_train['blocked'].get('minimum-lot'):
        reason = 'El lote mínimo no cabe en el presupuesto: ninguna ejecución contractual viable'
    # Detailed execution ledgers belong in offline diagnostics, not per-symbol UI.
    for result in (best_train,val,holdout,stress):
        result.pop('executions',None)
    return dict(ok=True,applied=accepted,params=best,train=best_train,validation=val,holdout=holdout,
                stress=stress,reason=reason,engineVersion=engine_version,brokerReplayVersion=VERSION,
                bars=len(candles),contract=asdict(contract),embargoBars=embargo,
                executionModel='mt5-bid-ohlc-conservative',costsVerified=contract.promotable)
