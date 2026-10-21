"""Motor DEMO independiente del paper trading, con cuenta y cotizaciones del bróker."""

import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .bots import ALL_BOTS, INTERVAL_SECONDS
from .database import Connection
from .execution import closed_candles, fresh_signal
from .indicators import atr_series
from .mt5_gateway import DemoGateway, DemoOnlyError
from .strategy import score_series, resample, TREND_STEP, extension_atr
from . import training
from . import mt5_performance
from . import mt5_market
from . import learning
from . import broker_replay
from . import portfolio_risk

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / '.runtime' / 'mt5-demo.json'
DB_PATH = ROOT / '.runtime' / 'mt5-demo.sqlite'
MAGICS = {cfg.id: 9260900 + i for i, cfg in enumerate(ALL_BOTS)}
log = logging.getLogger('mt5-demo')
STATE = {'configured': False, 'connected': False, 'message': 'MT5 demo no configurado', 'positions': []}
_task = None


def connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, factory=Connection)
    con.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS intents (key TEXT PRIMARY KEY, symbol TEXT, status TEXT, detail TEXT)')
    return con


def read(key, default=None):
    with connection() as con:
        row = con.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def save(key, value):
    with connection() as con:
        con.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value)))


class DemoEngine:
    def __init__(self, config, api):
        self.config, self.api = config, api
        self.gateway = DemoGateway(api, config)
        self.owner = f"{config['server']}:{config['login']}:"
        self.last_scan = self.last_train = 0
        self.last_performance = 0
        self.clock_skew = 0.0
        self._clock_ticks = {}
        self._clock_confirmed = False
        self.cursor = 0
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mt5-validation')
        self.training_job = None
        self.stopping = False
        self.diagnostics = {}
        self.risk_diagnostics = {}
        self.timeframes = {k: getattr(api, 'TIMEFRAME_' + v) for k, v in
                           [('1m', 'M1'), ('5m', 'M5'), ('15m', 'M15'), ('1h', 'H1'), ('4h', 'H4'), ('1d', 'D1')]}

    def bars(self, symbol, interval, count=500):
        if not self.api.symbol_select(symbol, True):
            raise DemoOnlyError('Símbolo no disponible en MT5')
        rows = self.api.copy_rates_from_pos(symbol, self.timeframes[interval], 0, count)
        if rows is None:
            raise DemoOnlyError('No se han recibido velas de MT5')
        return closed_candles([dict(time=int(r['time']), open=float(r['open']), high=float(r['high']),
                                    low=float(r['low']), close=float(r['close']),
                                    volume=float(r['real_volume'] or r['tick_volume'])) for r in rows],
                              INTERVAL_SECONDS[interval], time.time() + self.clock_skew)

    def market_read(self, kind, interval, count):
        self.gateway.account()
        symbol = self.config.get('symbols', {}).get('XAUUSD')
        if not symbol:
            return None
        provenance = dict(provider='mt5', instrument=symbol, account=self.owner)
        if kind == 'candles':
            rows = self.bars(symbol, interval, count)
            # Mismo eje UTC que el resto del panel.
            offset = round(self.clock_skew / 3600) * 3600
            return [dict(c, time=int(c['time']-offset), dataSource=provenance) for c in rows]
        tick = self.gateway.tick(symbol, time.time())
        mid = (tick.bid+tick.ask)/2
        daily = self.bars(symbol, '1d', 2)
        change = (mid/daily[-1]['close']-1)*100 if daily else 0.0
        return dict(symbol='XAUUSD', price=mid, changePct=round(change, 2),
                    time=int(tick.time-self.clock_skew), dataSource=provenance)

    def update_clock_skew(self):
        """Only an advancing tick can calibrate a whole-hour server offset.

        The age residual is never folded into the offset. Frozen/closed markets
        therefore become stale rather than resetting their apparent age to zero.
        UTC is the documented default; an observed broker offset needs two reads.
        """
        local = time.time()
        skews = []
        for symbol in self.config.get('symbols', {}).values():
            if not self.api.symbol_select(symbol, True):
                continue
            tick = self.api.symbol_info_tick(symbol)
            stamp = getattr(tick, 'time', 0) if tick is not None else 0
            if isinstance(stamp, (int, float)) and math.isfinite(stamp) and stamp > 0:
                previous = self._clock_ticks.get(symbol)
                self._clock_ticks[symbol] = stamp
                skew = round((stamp - local) / 3600) * 3600
                age = local + skew - stamp
                if (previous is not None and stamp > previous and 0 <= skew <= 12*3600
                        and -5 <= age <= 120):
                    skews.append(skew)
        if skews:
            # Retain a confirmed offset until a new, advancing tick provides
            # evidence of a server clock change (e.g. DST).
            self.clock_skew = max(set(skews), key=lambda x: (skews.count(x), -abs(x-self.clock_skew)))
            self.gateway.clock_skew = self.clock_skew
            self._clock_confirmed = True

    def submit(self, key, symbol, request, portfolio_token=None):
        if self.stopping:
            portfolio_risk.release(portfolio_token)
            raise DemoOnlyError('Motor detenido')
        # Se registra ANTES de enviar. Ante una respuesta ambigua jamás se reenvía a ciegas.
        with connection() as con:
            if con.execute('SELECT 1 FROM intents WHERE key=?', (self.owner+key,)).fetchone():
                portfolio_risk.release(portfolio_token)
                return None
            detail = dict(request=request, submittedAt=time.time(), clockOffset=self.clock_skew,
                          portfolioToken=portfolio_token)
            con.execute('INSERT INTO intents VALUES (?,?,?,?)', (self.owner+key, symbol, 'pending', json.dumps(detail)))
        try:
            result = self.gateway.send(request)
        except DemoOnlyError:
            portfolio_risk.release(portfolio_token)
            with connection() as con:
                con.execute('UPDATE intents SET status=? WHERE key=?', ('rejected', self.owner+key))
            raise
        status = 'uncertain'
        if result is not None:
            detail.update(retcode=result.retcode, order=result.order, deal=result.deal, comment=result.comment)
            if result.retcode in (self.api.TRADE_RETCODE_DONE, self.api.TRADE_RETCODE_DONE_PARTIAL):
                status = 'accepted'
            elif result.retcode not in (self.api.TRADE_RETCODE_TIMEOUT, self.api.TRADE_RETCODE_CONNECTION,
                                       self.api.TRADE_RETCODE_PLACED):
                status = 'rejected'
        with connection() as con:
            con.execute('UPDATE intents SET status=?, detail=? WHERE key=?', (status, json.dumps(detail), self.owner+key))
        if status == 'rejected':
            portfolio_risk.release(portfolio_token)
        log.info('MT5 DEMO %s %s %s', symbol, status, detail)
        return result

    def reconcile_intents(self, positions, now):
        """Resolve ambiguity with positive broker evidence; never resend an intent.

        Absence of a position or an empty history response is not proof that an
        order failed. Legacy intents without a request identity remain blocked.
        """
        with connection() as con:
            rows = con.execute("SELECT key,symbol,detail FROM intents WHERE key LIKE ? "
                               "AND status IN ('pending','uncertain')", (self.owner+'%',)).fetchall()
        for key, symbol, raw in rows:
            detail = json.loads(raw)
            request = detail.get('request') or {}
            comment, magic = request.get('comment'), request.get('magic')
            if not request:
                continue
            evidence, status = None, None
            if not request.get('position') and comment and comment.startswith(('cx-', 'cxp-')):
                found = next((p for p in positions if p.symbol == symbol and p.magic == magic
                              and getattr(p, 'comment', None) == comment), None)
                if found:
                    evidence, status = dict(position=found.ticket), 'accepted'
            submitted = detail.get('submittedAt')
            if not evidence and isinstance(submitted, (int, float)):
                start = max(0, int(submitted + detail.get('clockOffset', 0)) - 120)
                try:
                    deals = self.api.history_deals_get(start, int(now+self.clock_skew)+86400)
                    order_id = detail.get('order')
                    for deal in deals or ():
                        exact_order = bool(order_id and getattr(deal, 'order', None) == order_id)
                        exact_identity = bool(comment and getattr(deal, 'comment', None) == comment
                                              and getattr(deal, 'magic', None) == magic
                                              and getattr(deal, 'symbol', None) == symbol
                                              and getattr(deal, 'time', 0) >= start)
                        if request.get('position'):
                            exact_identity = exact_identity and getattr(deal, 'position_id', None) == request['position']
                        if exact_order or exact_identity:
                            evidence, status = dict(deal=deal.ticket), 'accepted'
                            break
                    if not evidence and order_id:
                        orders = self.api.history_orders_get(ticket=order_id)
                        definitive = {getattr(self.api, 'ORDER_STATE_'+v, None) for v in ('CANCELED','REJECTED','EXPIRED')}
                        definitive = {v for v in definitive if isinstance(v, int)}
                        found = next((o for o in orders or () if o.ticket == order_id and o.state in definitive), None)
                        if found:
                            evidence, status = dict(order=found.ticket, state=found.state), 'rejected'
                except Exception as exc:
                    log.warning('MT5 intent reconciliation unavailable: %s', exc)
            if evidence:
                detail.update(reconciledAt=int(now), evidence=evidence)
                if status == 'rejected':
                    portfolio_risk.release(detail.get('portfolioToken'))
                with connection() as con:
                    con.execute('UPDATE intents SET status=?,detail=? WHERE key=?', (status,json.dumps(detail),key))

    def uncertain(self, symbol):
        with connection() as con:
            return con.execute("SELECT 1 FROM intents WHERE key LIKE ? AND symbol=? AND status IN ('pending','uncertain')",
                               (self.owner+'%', symbol)).fetchone() is not None

    def reconcile_portfolio_reservations(self, positions, now):
        """Release a filled order only when its risk is published or proven closed.

        A fast round trip can open and close between two UI snapshots. Its
        reservation is released from a complete, reconciled execution ledger,
        never just from the position disappearing or the passage of time.
        """
        with connection() as con:
            rows = con.execute("SELECT key,symbol,detail FROM intents WHERE key LIKE ? AND status='accepted'",
                               (self.owner+'%',)).fetchall()
        fresh_history = mt5_performance.entry_readiness(self.owner,now)
        live_ids = {getattr(p,'identifier',p.ticket) for p in positions}
        for key,symbol,raw in rows:
            detail = json.loads(raw)
            token = detail.get('portfolioToken')
            if not token or detail.get('portfolioReleasedAt'):
                continue
            request = detail.get('request') or {}
            published = any(p.symbol == symbol and p.magic == request.get('magic')
                            and getattr(p,'comment',None) == request.get('comment') for p in positions)
            closed = False
            if not published and fresh_history['entryReady']:
                deal_id = detail.get('deal') or (detail.get('evidence') or {}).get('deal')
                if deal_id:
                    with mt5_performance.connection() as con:
                        row = con.execute('SELECT position_id FROM mt5_deals WHERE owner=? AND ticket=?',
                                          (self.owner,deal_id)).fetchone()
                        if row and row[0] and row[0] not in live_ids:
                            volumes = con.execute('SELECT entry,SUM(volume) FROM mt5_deals WHERE owner=? '
                                'AND position_id=? AND kind IN (0,1) GROUP BY entry',(self.owner,row[0])).fetchall()
                            volumes = dict(volumes)
                            entered = volumes.get(0,0)
                            closed = entered > 0 and volumes.get(1,0)+volumes.get(3,0) >= entered-1e-8
            if published or closed:
                portfolio_risk.release(token)
                detail.update(portfolioReleasedAt=int(now),portfolioReleaseEvidence='published' if published else 'closed-ledger')
                with connection() as con:
                    con.execute('UPDATE intents SET detail=? WHERE key=?',(json.dumps(detail),key))

    def manage(self, positions, now):
        configs = {MAGICS[c.id]: c for c in ALL_BOTS}
        for pos in positions:
            if self.stopping:
                return
            cfg = configs.get(pos.magic)
            if not cfg:
                continue  # nunca modifica posiciones manuales u otros robots
            key = self.owner + f'position:{pos.ticket}'
            data = read(key)
            if data is None:
                # SL inicial enviado con la orden, válido incluso tras reiniciar antes de registrar el ticket.
                if pos.sl <= 0:
                    continue
                data = dict(risk=abs(pos.price_open-pos.sl), best=pos.price_open,
                            atr=abs(pos.price_open-pos.sl)/cfg.sl_atr, trail=cfg.trail_atr)
                save(key, data)
            try:
                tick = self.gateway.tick(pos.symbol, now, self.clock_skew)
                if now + self.clock_skew-pos.time >= cfg.max_hold_seconds:
                    if not self.uncertain(pos.symbol):
                        request = self.gateway.close_request(pos, now)
                        result = self.submit(f'close:{pos.ticket}:{pos.volume}', pos.symbol, request)
                        if result is not None and result.retcode in (self.api.TRADE_RETCODE_DONE, self.api.TRADE_RETCODE_DONE_PARTIAL):
                            save(self.owner+'cooldown:'+pos.symbol, now+300)
                    continue
                buy = pos.type == self.api.POSITION_TYPE_BUY
                price = tick.bid if buy else tick.ask
                data['best'] = max(data['best'], price) if buy else min(data['best'], price)
                save(key, data)
                gain = (data['best']-pos.price_open)*(1 if buy else -1)
                if data['risk'] <= 0 or gain < data['risk']:
                    continue
                trail = data['atr'] * data['trail'] * (.75 if gain >= 2*data['risk'] else 1)
                stop = max(pos.price_open, data['best']-trail, pos.sl) if buy else min(pos.price_open, data['best']+trail, pos.sl)
                info = self.api.symbol_info(pos.symbol)
                step = info.trade_tick_size or info.point
                import math
                stop = round((math.floor(stop/step) if buy else math.ceil(stop/step))*step, info.digits)
                distance = (price-stop)*(1 if buy else -1)
                minimum = max(info.trade_stops_level, info.trade_freeze_level)*info.point + 2*info.point
                if distance >= minimum and abs(stop-pos.sl) >= step:
                    self.gateway.send(dict(action=self.api.TRADE_ACTION_SLTP, position=pos.ticket,
                                           symbol=pos.symbol, sl=stop, tp=pos.tp, magic=pos.magic))
            except DemoOnlyError as exc:
                self.diagnostics[cfg.id+'/'+pos.symbol] = str(exc)

    def training(self, candidates, now):
        if self.training_job:
            key, future = self.training_job
            if not future.done():
                return
            try:
                result = future.result()
                result['at'] = int(now)
                save(self.owner+'params:'+key, result)
            except Exception as exc:
                save(self.owner+'params:'+key, dict(at=int(now), engineVersion=training.ENGINE_VERSION, applied=False, reason=str(exc)))
            self.training_job = None
        if now-self.last_train < 300:
            return
        for cfg, symbol in candidates:
            key = cfg.id+'/'+symbol
            previous = read(self.owner+'params:'+key, {})
            if (previous.get('engineVersion') == training.ENGINE_VERSION
                    and previous.get('brokerReplayVersion') == broker_replay.VERSION
                    and now-previous.get('at', 0) < 6*3600):
                continue
            self.last_train = now
            try:
                data = self.bars(symbol, cfg.work_interval, 5000)
                if len(data) < 500:
                    raise DemoOnlyError('Histórico del bróker insuficiente para entrenar')
                tick = self.gateway.tick(symbol, now)
                account, _ = self.gateway.account()
                virtual=mt5_performance.capital(self.owner,account,self.gateway.positions())
                equity=virtual['equity'] if virtual else account.equity
                contract = broker_replay.capture_contract(self.api,symbol,account,self.config,tick,self.clock_skew)
                def optimize():
                    scores = score_series(cfg.id, data, resample(data, TREND_STEP[cfg.id]))
                    result = broker_replay.optimize(cfg,data,scores,atr_series(data,cfg.atr_period),contract,
                        initial=equity,risk_pct=self.config['risk_pct'],
                        max_open_risk_pct=self.config['max_open_risk_pct'],
                        max_daily_loss_pct=self.config['max_daily_loss_pct'],engine_version=training.ENGINE_VERSION)
                    result['source'] = dict(provider='mt5',symbol=symbol,account=self.owner)
                    return result
                self.training_job = (key, self.worker.submit(optimize))
            except Exception as exc:
                save(self.owner+'params:'+key, dict(at=int(now), engineVersion=training.ENGINE_VERSION, applied=False, reason=str(exc)))
            break

    def sync_performance(self, now):
        if now-self.last_performance < 10:
            return
        self.last_performance = now
        try:
            info = mt5_performance.sync_info(self.owner)
            full = now-info['lastFull'] >= 86400 or not mt5_performance.entry_readiness(self.owner, now)['reconciled']
            start = 0 if full else max(0, info['updatedAt']-86400)
            # Algunos servidores fechan ejecuciones con su reloj local adelantado.
            # El margen incluye esas ejecuciones ya existentes, nunca órdenes futuras.
            deals = self.api.history_deals_get(start, int(now)+86400)
            if deals is None:
                raise DemoOnlyError('No se ha podido sincronizar el historial de MT5')
            positions = self.gateway.positions()
            account, _ = self.gateway.account()
            mt5_performance.ingest(self.owner, account, positions, deals, now, full,
                                   clock_offset=(getattr(self, 'clock_skew', 0)
                                       if getattr(self,'_clock_confirmed',True) else None))
        except Exception as exc:
            mt5_performance.sync_error(self.owner, str(exc))

    def cycle(self):
        api, cfg = self.api, self.config
        if not api.initialize(cfg['terminal'], timeout=10000):
            raise DemoOnlyError('No se puede conectar con el terminal MT5')
        account, terminal = self.gateway.account()
        self.update_clock_skew()
        now = time.time()
        positions = self.gateway.positions()
        self.reconcile_intents(positions, now)
        old_positions = read(self.owner+'last_positions', {})
        tickets = {str(p.ticket): p.symbol for p in positions if p.magic in MAGICS.values()}
        for ticket, symbol in old_positions.items():
            if ticket not in tickets:
                save(self.owner+'cooldown:'+symbol, now+300)
        save(self.owner+'last_positions', tickets)
        self.manage(positions, now)
        self.sync_performance(now)
        # Management may have closed a position. Publish and size from the
        # current broker read, never the pre-management position list.
        positions = self.gateway.positions()
        account, terminal = self.gateway.account()
        virtual=mt5_performance.capital(self.owner,account,positions,now=now)
        equity=virtual['equity'] if virtual else account.equity
        day_key = self.owner+f'day:{int(now//86400)}'
        daily = read(day_key, dict(equity=equity, paused=False))
        if equity <= daily['equity']*(1-cfg['max_daily_loss_pct']):
            daily['paused'] = True
        save(day_key, daily)
        enabled = read(self.owner+'enabled', cfg['enabled'])
        allowed = enabled and terminal.trade_allowed and not terminal.tradeapi_disabled and account.trade_allowed and account.trade_expert
        if virtual and (not virtual['entryReady'] or not self._clock_confirmed):
            allowed = False
        message = ('Cuenta DEMO activa: buscando señales' if allowed else
                   'Entradas pausadas' if not enabled else 'Activa Algo Trading en MetaTrader 5')
        if daily['paused']:
            message = 'Límite diario alcanzado: nuevas entradas pausadas hasta mañana UTC'
        if virtual and not virtual['entryReady']:
            message = virtual['syncError']
        elif virtual and not self._clock_confirmed:
            message = 'Verificando avance de ticks y reloj del bróker antes de nuevas entradas'
        position_rows = []
        offset = round(self.clock_skew / 3600) * 3600
        for p in positions:
            tick = api.symbol_info_tick(p.symbol)
            tick_time = getattr(tick, 'time', None)
            opened_time = getattr(p, 'time', None)
            loss = api.order_calc_profit(p.type,p.symbol,p.volume,p.price_open,p.sl) if p.sl > 0 else None
            risk_eur = max(0,-loss)*1.1 if (account.currency == 'EUR' and isinstance(loss,(int,float)) and math.isfinite(loss)) else None
            info = api.symbol_info(p.symbol)
            nominal_eur = None
            contract_size = getattr(info,'trade_contract_size',None)
            if account.currency == 'EUR' and isinstance(contract_size,(int,float)) and math.isfinite(contract_size):
                if getattr(info,'currency_base',None) == 'EUR':
                    nominal_eur = p.volume*contract_size
                elif getattr(info,'currency_profit',None) == 'EUR':
                    nominal_eur = p.volume*contract_size*p.price_open
            position_rows.append(dict(
                ticket=p.ticket, symbol=p.symbol,
                bot=next((b for b,m in MAGICS.items() if m==p.magic), 'manual/otro'),
                direction='long' if p.type==api.POSITION_TYPE_BUY else 'short', volume=p.volume,
                entry=p.price_open, currentPrice=getattr(p, 'price_current', None),
                openedAt=int(opened_time-offset) if isinstance(opened_time, (int, float)) else None,
                priceAt=int(tick_time-offset) if isinstance(tick_time, (int, float)) else None,
                quantityUnit='lots', sl=p.sl, tp=p.tp, pnl=p.profit,
                riskEUR=risk_eur,notionalEUR=nominal_eur,
                exposureKnown=nominal_eur is not None))
        STATE.update(configured=True, connected=True, demo=True, server=account.server,
                     currency=account.currency, balance=virtual['balance'] if virtual else account.balance, equity=equity,
                     session=virtual,brokerBalance=account.balance,brokerEquity=account.equity,
                     enabled=enabled, tradingAllowed=bool(allowed and not daily['paused']), message=message,
                     riskPct=cfg['risk_pct']*100,
                     probationRiskPct=round(cfg['risk_pct']*learning.PROBATION_SIZE*100, 6),
                     maxPositions=cfg['max_positions'], updatedAt=int(now),
                     positions=position_rows,
                     diagnostics=dict(self.diagnostics), riskDiagnostics=dict(self.risk_diagnostics),
                     clockOffsetSeconds=self.clock_skew, clockConfirmed=self._clock_confirmed,
                     training=self.training_job[0] if self.training_job else None)
        self.reconcile_portfolio_reservations(positions,now)
        candidates = [(bot, cfg['symbols'][symbol]) for bot in ALL_BOTS for symbol in bot.universe if symbol in cfg['symbols']]
        self.training(candidates, now)
        if not allowed or daily['paused'] or now-self.last_scan < 60:
            return
        self.last_scan = now
        offset = self.cursor % len(candidates)
        self.cursor += 1
        for bot, symbol in candidates[offset:] + candidates[:offset]:
            if self.stopping:
                return
            key = bot.id+'/'+symbol
            try:
                if now < read(self.owner+'cooldown:'+symbol, 0):
                    raise DemoOnlyError('Cooldown de cinco minutos tras un cierre')
                if self.uncertain(symbol):
                    raise DemoOnlyError('Respuesta de orden incierta: revisar MT5 antes de continuar en este símbolo')
                params = read(self.owner+'params:'+key, {})
                p = params.get('params', {}) if (params.get('applied') and params.get('engineVersion') == training.ENGINE_VERSION
                    and params.get('brokerReplayVersion') == broker_replay.VERSION
                    and params.get('source', {}).get('account') == self.owner
                    and params.get('source', {}).get('symbol') == symbol) else {}
                if bot.require_validation and not p:
                    raise DemoOnlyError('Esperando validación sobre histórico MT5')
                probation = not bool(p)
                current_positions = self.gateway.positions()
                if len(current_positions) >= cfg['max_positions']:
                    self.diagnostics[key] = 'Límite compartido de posiciones alcanzado'
                    continue
                if probation:
                    probation_count = sum(
                        1 for pos in current_positions if pos.magic == MAGICS[bot.id]
                        and ((read(self.owner+f'position:{pos.ticket}', {}) or {}).get('probation')
                             or str(getattr(pos, 'comment', '')).startswith('cxp-'))
                    )
                    if probation_count >= learning.MAX_PROBATION_POSITIONS:
                        raise DemoOnlyError('Límite de posiciones a prueba alcanzado')
                work = self.bars(symbol, bot.work_interval)
                higher = self.bars(symbol, bot.trend_interval)
                if len(work) < 60 or now+self.clock_skew-work[-1]['time'] > 3*INTERVAL_SECONDS[bot.work_interval]+60:
                    raise DemoOnlyError('Velas insuficientes o antiguas')
                scores = score_series(bot.id, work, higher)
                threshold = p.get('entryThreshold', bot.entry_threshold)
                if probation:
                    threshold *= learning.PROBATION_THRESHOLD_MULT
                if not fresh_signal(scores[-1], scores[-4:-1], threshold):
                    raise DemoOnlyError('Sin cruce reciente de señal')
                direction = 1 if scores[-1] > 0 else -1
                extension = extension_atr(work)
                if extension is not None and extension*direction > bot.overext_atr:
                    raise DemoOnlyError('Precio demasiado extendido')
                atr = atr_series(work, bot.atr_period)[-1]
                if not atr or atr <= 0:
                    continue
                tick = self.gateway.tick(symbol, now, self.clock_skew)
                if abs((tick.ask+tick.bid)/2-work[-1]['close']) > atr:
                    raise DemoOnlyError('Precio alejado de la señal')
                bar = work[-1]['time']
                comment = f"{'cxp' if probation else 'cx'}-{bot.id}-{bar}"
                current_virtual=mt5_performance.capital(self.owner,account,current_positions,now=time.time())
                if current_virtual and not current_virtual['entryReady']:
                    raise DemoOnlyError(current_virtual['syncError'])
                request, risk = self.gateway.prepare_entry(symbol, direction, atr,
                    p.get('slAtr', bot.sl_atr), p.get('tpAtr', bot.tp_atr), MAGICS[bot.id], comment, time.time(),
                    risk_mult=learning.PROBATION_SIZE if probation else 1.0,
                    virtual_equity=(current_virtual or {}).get('equity'))
                if self.gateway.last_entry_diagnostics:
                    self.risk_diagnostics[key] = dict(self.gateway.last_entry_diagnostics)
                portfolio_token = None
                if request:
                    if account.currency != 'EUR':
                        raise DemoOnlyError('Sin conversión monetaria verificada a EUR para el riesgo global')
                    loss = api.order_calc_profit(request['type'],symbol,request['volume'],request['price'],request['sl'])
                    if loss is None or not math.isfinite(loss):
                        raise DemoOnlyError('Riesgo monetario global desconocido')
                    info = api.symbol_info(symbol)
                    contract_size = getattr(info,'trade_contract_size',None)
                    nominal_eur = None
                    if isinstance(contract_size,(int,float)) and math.isfinite(contract_size) and contract_size > 0:
                        if getattr(info,'currency_base',None) == 'EUR':
                            nominal_eur = request['volume']*contract_size
                        elif getattr(info,'currency_profit',None) == 'EUR':
                            nominal_eur = request['volume']*contract_size*request['price']
                    proposal = portfolio_risk.assess(symbol,'long' if direction > 0 else 'short',
                        max(0,-loss)*1.1,nominal_eur,source='mt5',bot_id=bot.id,reserve=True)
                    if not proposal['allowed']:
                        raise DemoOnlyError(proposal['reason'])
                    portfolio_token = proposal['token']
                    self.risk_diagnostics.setdefault(key,{}).update(
                        portfolioPolicy=proposal.get('policyVersion'),notionalEUR=nominal_eur,
                        exposureWarning=None if nominal_eur is not None else 'Nocional EUR sin conversión verificada')
                result = self.submit(f'entry:{bot.id}:{symbol}:{bar}', symbol, request, portfolio_token=portfolio_token)
                if result is not None and result.retcode in (api.TRADE_RETCODE_DONE, api.TRADE_RETCODE_DONE_PARTIAL):
                    for pos in self.gateway.positions():
                        if pos.symbol == symbol and pos.magic == MAGICS[bot.id]:
                            save(self.owner+f'position:{pos.ticket}', dict(risk=risk, atr=atr, best=pos.price_open,
                                                                      trail=p.get('trailAtr', bot.trail_atr),
                                                                      probation=probation))
                    self.diagnostics[key] = (f'Orden DEMO a prueba ejecutada ({learning.PROBATION_SIZE:.0%} del riesgo habitual)' if probation
                                             else 'Orden DEMO ejecutada en MT5')
                else:
                    self.diagnostics[key] = 'Orden ya procesada, rechazada o pendiente de reconciliación'
            except DemoOnlyError as exc:
                self.diagnostics[key] = str(exc)
                if exc.details:
                    self.risk_diagnostics[key] = exc.details
            except Exception as exc:
                self.diagnostics[key] = 'Error de evaluación: '+str(exc)


async def _run(config):
    import MetaTrader5 as api
    engine = DemoEngine(config, api)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mt5-terminal')
    async def market_read(kind, interval, count):
        return await asyncio.get_running_loop().run_in_executor(executor, engine.market_read, kind, interval, count)
    mt5_market.set_reader(market_read)
    try:
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(executor, engine.cycle)
            except Exception as exc:
                STATE.update(configured=True, connected=False, tradingAllowed=False, message=str(exc))
                log.warning('MT5 demo: %s', exc)
            await asyncio.sleep(5)
    finally:
        mt5_market.set_reader(None)
        engine.stopping = True
        engine.worker.shutdown(wait=False, cancel_futures=True)
        executor.shutdown(wait=False, cancel_futures=True)


def start():
    global _task
    if os.getenv('TRADING_MT5_ENABLED', '1') == '0' or sys.platform != 'win32':
        STATE.update(configured=False, connected=False, tradingAllowed=False,
                     message='MT5 desactivado en este servidor; requiere el terminal de Windows')
        return
    if CONFIG_PATH.exists() and (_task is None or _task.done()):
        config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        _task = asyncio.create_task(_run(config), name='mt5-demo')


async def stop():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None


def set_enabled(enabled):
    if not CONFIG_PATH.exists():
        raise DemoOnlyError('MT5 demo no configurado')
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    save(f"{cfg['server']}:{cfg['login']}:enabled", bool(enabled))
