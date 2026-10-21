"""Acceso a MT5 ligado a una única cuenta DEMO; no almacena contraseñas."""

import math
from decimal import Decimal, ROUND_DOWN


class DemoOnlyError(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details


def lot_floor(volume, step):
    return float((Decimal(str(volume)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
                 * Decimal(str(step)))


class DemoGateway:
    def __init__(self, api, config):
        self.api, self.config = api, config
        self.clock_skew = 0.0
        self.last_entry_diagnostics = None

    def account(self, trading=False):
        a, t = self.api.account_info(), self.api.terminal_info()
        if a is None or t is None or not t.connected:
            raise DemoOnlyError('MetaTrader desconectado')
        if a.trade_mode != self.api.ACCOUNT_TRADE_MODE_DEMO:
            raise DemoOnlyError('BLOQUEADO: la cuenta conectada no es demo')
        if a.login != self.config['login'] or a.server != self.config['server']:
            raise DemoOnlyError('BLOQUEADO: cambió la cuenta o el servidor autorizado')
        if a.margin_mode != self.api.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
            raise DemoOnlyError('Esta integración requiere cuenta demo hedging')
        if trading and (not a.trade_allowed or not a.trade_expert or not t.trade_allowed or t.tradeapi_disabled):
            raise DemoOnlyError('Activa Algo Trading y permite la API de Python en MT5')
        return a, t

    def positions(self):
        self.account()
        result = self.api.positions_get()
        if result is None:
            raise DemoOnlyError('No se pueden sincronizar las posiciones de MT5')
        return list(result)

    def tick(self, symbol, now, clock_skew=None):
        tick = self.api.symbol_info_tick(symbol)
        # MT5 puede devolver la hora del servidor (distinta de la del equipo).
        # El motor estima ese desfase una vez por ciclo y lo pasa aquí.
        effective_skew = self.clock_skew if clock_skew is None else clock_skew
        age = now + effective_skew - tick.time if tick is not None else float('inf')
        if (tick is None or not -5 <= age <= 120
                or not all(math.isfinite(v) and v > 0 for v in (tick.bid, tick.ask)) or tick.ask < tick.bid):
            raise DemoOnlyError('Sin cotización reciente del bróker')
        return tick

    def prepare_entry(self, symbol, direction, atr, sl_multiple, tp_multiple, magic, comment, now,
                      risk_mult=1.0, virtual_equity=None, min_atr_spread=3):
        self.last_entry_diagnostics = None
        if not math.isfinite(risk_mult) or not 0 < risk_mult <= 1:
            raise DemoOnlyError('Multiplicador de riesgo inválido')
        api = self.api
        account, _ = self.account(trading=True)
        positions = self.positions()
        equity=account.equity
        margin_free=account.margin_free
        margins=[]
        if virtual_equity is not None:
            if not math.isfinite(virtual_equity) or virtual_equity<=0:
                raise DemoOnlyError('Presupuesto virtual agotado o inválido')
            equity=min(equity,virtual_equity)
            margins=[api.order_calc_margin(p.type,p.symbol,p.volume,p.price_open) for p in positions]
            if any(m is None or not math.isfinite(m) or m<0 for m in margins):
                raise DemoOnlyError('Margen de cartera virtual desconocido')
            margin_free=min(margin_free,max(0,equity-sum(margins)))
        if len(positions) >= self.config['max_positions'] or any(p.symbol == symbol for p in positions):
            raise DemoOnlyError('Límite compartido de posiciones o símbolo ya abierto')
        if not api.symbol_select(symbol, True):
            raise DemoOnlyError('Instrumento no disponible')
        info = api.symbol_info(symbol)
        if info is None or info.trade_mode != api.SYMBOL_TRADE_MODE_FULL:
            raise DemoOnlyError('Instrumento cerrado para nuevas operaciones')
        tick = self.tick(symbol, now)
        spread = tick.ask - tick.bid
        if not math.isfinite(atr) or atr <= 0 or atr < min_atr_spread * spread:
            raise DemoOnlyError('ATR insuficiente para el spread del bróker')
        buy = direction > 0
        kind = api.ORDER_TYPE_BUY if buy else api.ORDER_TYPE_SELL
        price = tick.ask if buy else tick.bid
        stop_dist = max(atr * sl_multiple, info.trade_stops_level * info.point + spread + 2 * info.point)
        step = info.trade_tick_size or info.point
        sl = round((math.floor((price-stop_dist)/step) if buy else math.ceil((price+stop_dist)/step))*step, info.digits)
        tp_dist = max(atr * tp_multiple, stop_dist)
        tp = round((math.ceil((price+tp_dist)/step) if buy else math.floor((price-tp_dist)/step))*step, info.digits)
        if sl <= 0 or tp <= 0:
            raise DemoOnlyError('Stop u objetivo inválido')
        unit_loss = api.order_calc_profit(kind, symbol, 1.0, price, sl)
        if unit_loss is None or not math.isfinite(unit_loss) or unit_loss >= 0:
            raise DemoOnlyError('No se puede calcular el riesgo en la divisa de la cuenta')
        open_risk = 0.0
        for pos in positions:
            if pos.sl <= 0:
                raise DemoOnlyError('Hay una posición sin stop; se bloquean nuevas entradas')
            loss = api.order_calc_profit(pos.type, pos.symbol, pos.volume, pos.price_open, pos.sl)
            if loss is None or not math.isfinite(loss):
                raise DemoOnlyError('Riesgo de cartera desconocido')
            open_risk += max(0, -loss)
        budget = min(equity * self.config['risk_pct'] * risk_mult,
                     equity * self.config['max_open_risk_pct'] - open_risk)
        costs = self.config.get('replay_costs', {}).get(symbol, {})
        commission = costs.get('roundTripCommissionPerLot')
        if commission is not None and (not math.isfinite(commission) or commission < 0):
            raise DemoOnlyError('Comisión del contrato inválida')
        risk_per_lot = -unit_loss + max(-unit_loss*.1, commission or 0)
        minimum_risk = risk_per_lot * info.volume_min
        minimum_margin = api.order_calc_margin(kind, symbol, info.volume_min, price)
        if minimum_margin is None or not math.isfinite(minimum_margin) or minimum_margin < 0:
            raise DemoOnlyError('No se puede calcular el margen del lote mínimo')
        required_capital = max(minimum_risk / (self.config['risk_pct'] * risk_mult),
                               (open_risk + minimum_risk) / self.config['max_open_risk_pct'],
                               minimum_margin * 4 + sum(margins))
        details = dict(symbol=symbol, currency=getattr(account, 'currency', None),
                       equity=equity, riskBudget=max(0, budget), minimumLot=info.volume_min,
                       volumeStep=info.volume_step, minimumLotRisk=minimum_risk,
                       minimumLotMargin=minimum_margin, requiredCapital=required_capital,
                       riskMultiplier=risk_mult,commissionKnown=commission is not None)
        self.last_entry_diagnostics = details
        # Reserva del 10% para fricción; los gaps todavía pueden exceder el stop.
        volume = lot_floor(min(info.volume_max, max(0, budget) / risk_per_lot), info.volume_step)
        if volume < info.volume_min:
            currency = details['currency'] or 'divisa de cuenta'
            raise DemoOnlyError(
                f'El lote mínimo ({info.volume_min:g}) arriesga {minimum_risk:.4f} {currency}; '
                f'presupuesto {max(0, budget):.4f}. Capital requerido: {required_capital:.2f} {currency}', details)
        margin = api.order_calc_margin(kind, symbol, volume, price)
        if margin is None or not math.isfinite(margin) or margin > margin_free * .25:
            raise DemoOnlyError('Margen libre insuficiente para esta entrada', details)
        target_profit = api.order_calc_profit(kind,symbol,volume,price,tp)
        if target_profit is None or not math.isfinite(target_profit) or target_profit <= 0:
            raise DemoOnlyError('Objetivo monetario del contrato inválido', details)
        if commission is not None:
            # Prices already include the quoted side. This reserve covers
            # configured broker commission, rather than importing Binance fees.
            target_net = target_profit-commission*volume
            details.update(targetAfterCommission=target_net,targetNetKnown=False,
                           costWarning='Objetivo tras comisión; swap y deslizamiento realizado pendientes de reconciliar')
            if target_net <= 0:
                raise DemoOnlyError('El objetivo neto no cubre la comisión del bróker',details)
        else:
            details.update(targetNet=None,targetNetKnown=False,
                           costWarning='Comisión del bróker sin verificar; presupuesto a prueba con reserva de fricción')
        request = dict(action=api.TRADE_ACTION_DEAL, symbol=symbol, volume=volume, type=kind,
                       price=price, sl=sl, tp=tp, deviation=20, magic=magic, comment=comment,
                       type_time=api.ORDER_TIME_GTC)
        return self.check_deal(request, info), abs(price-sl)

    def check_deal(self, request, info):
        api = self.api
        modes = []
        if info.filling_mode & 2:
            modes.append(api.ORDER_FILLING_IOC)
        if info.filling_mode & 1:
            modes.append(api.ORDER_FILLING_FOK)
        if info.trade_exemode != api.SYMBOL_TRADE_EXECUTION_MARKET:
            modes.append(api.ORDER_FILLING_RETURN)
        for mode in modes:
            self.account(trading=True)
            candidate = dict(request, type_filling=mode)
            check = api.order_check(candidate)
            if check is not None and check.retcode == 0:
                return candidate
        raise DemoOnlyError('MT5 no acepta la orden en la comprobación previa')

    def send(self, request):
        # Único punto del proyecto que envía órdenes MT5; siempre revalida DEMO.
        self.account(trading=True)
        return self.api.order_send(request)

    def close_request(self, pos, now):
        self.account(trading=True)
        tick = self.tick(pos.symbol, now)
        buy = pos.type == self.api.POSITION_TYPE_BUY
        return self.check_deal(dict(
            action=self.api.TRADE_ACTION_DEAL, position=pos.ticket, symbol=pos.symbol,
            volume=pos.volume, type=self.api.ORDER_TYPE_SELL if buy else self.api.ORDER_TYPE_BUY,
            price=tick.bid if buy else tick.ask, deviation=20, magic=pos.magic,
            comment='cx-time-exit', type_time=self.api.ORDER_TIME_GTC,
        ), self.api.symbol_info(pos.symbol))
