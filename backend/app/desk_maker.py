"""Cola maker paper persistente. Los stops conservan prioridad y coste taker.

La reserva vive en el estado y en el riesgo agregado; un reinicio o una laguna
del stream cancela el remanente. Nunca rellena por tocar una cotización.
"""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import json
import math
import time

from .book_stream import BOOKS
from .maker_simulation import PostOnlyOrder
from .economics import planned_trade
from .hub import hub
from . import telemetry
from .spot_orders import valid_quantity

TTL = 60
HOURLY_TTL = 180


def valid_lot(quantity,price,filters):
    return valid_quantity(quantity,price,filters,order_type='LIMIT')


class DeskMaker:
    def __init__(self,desk):
        self.desk=desk
        self.last_reject=None

    @property
    def orders(self):
        return self.desk.state.setdefault('makerOrders',[])

    def public(self):
        books=BOOKS.fresh([o['symbol'] for o in self.orders])
        pending=[]
        for row in self.orders:
            order=row['order']
            book=books.get(row['symbol'])
            bid=float(book['bidQty']) if book else None
            ask=float(book['askQty']) if book else None
            tape=BOOKS.taker_supports_bid(row['symbol'])
            pending.append(dict(symbol=row['symbol'],entryKey=row['entryKey'],side=row['side'],
                quantity=order['quantity'],filled=order['filled'],expiresAt=order['expires_at'],
                placedAt=order['placed_at'],remainingSeconds=max(0,order['expires_at']-time.time()),
                pressure=(bid-ask)/(bid+ask) if book and bid+ask>0 else None,
                bookFresh=bool(book),pressureBasis='best-bid-ask',status=order['status'],
                buyVolume=tape['buyVolume'],sellVolume=tape['sellVolume'],cvdDelta=tape['cvdDelta'],
                tradeVelocity=tape['tradeVelocity'],takerSupportsBid=tape['allowed']))
        return dict(mode='paper-observed-queue',ttlSeconds=TTL,hourlyPullbackRecoveryTtlSeconds=HOURLY_TTL,pending=pending,
            reservedCash=sum(o['reservedCash'] for o in self.orders),
            history=self.desk.state.get('makerHistory',[]),executionVerified=False,feeDiscountAssumed=False)

    def publish(self):
        hub.broadcast_event(dict(type='orderbook_pulse',desk=self.desk.desk_id,maker=self.public()))

    def commit(self,mutate):
        engine=self.desk
        previous=deepcopy(engine.state)
        try:
            mutate()
            engine.focus_position()
            with engine.connect() as con:
                for p in engine.state['positions']:
                    con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?) ON CONFLICT(entry_key) DO UPDATE SET payload=excluded.payload',
                                (p['entryKey'],json.dumps(p)))
                con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(engine.state),))
        except BaseException:
            engine.state=previous
            engine.focus_position()
            raise
        self.publish()
        telemetry.changed(engine.desk_id)

    def post(self,position,side,quantity):
        engine=self.desk
        self.last_reject=None
        symbol=position['symbol']
        if any(o['symbol']==symbol for o in self.orders): return False
        if side=='buy':
            tape=BOOKS.taker_supports_bid(symbol)
            # Sin 10 trades no hay evidencia; con CVD < -0.15 el agresor vende contra el bid.
            if tape['cvdDelta'] is not None and not tape['allowed']:
                self.last_reject='cvd'
                return False
        books=BOOKS.fresh([symbol])
        book=books.get(symbol)
        if not book: return False
        price=float(book['bidPrice' if side=='buy' else 'askPrice'])
        if not valid_lot(quantity,price,position.get('marketFilters',[])): return False
        BOOKS.watch_trades([symbol])
        strategy = position.get('strategy') or position.get('evidence',{}).get('tape',{}).get('strategy')
        ttl = HOURLY_TTL if (side=='buy' and position.get('candleInterval')=='1h'
                            and strategy in ('trend-pullback','recovery')) else TTL
        order=PostOnlyOrder.place(symbol,side,quantity,dict(book,at=book['receivedAt']),
                                  now=time.time(),ttl=ttl,fee_rate=engine.costs(position)[0])
        lot=next(f for f in position['marketFilters'] if f['filterType']=='LOT_SIZE')
        order.quantity_step=float(lot['stepSize'])
        row=dict(symbol=symbol,entryKey=position['entryKey'],side=side,order=asdict(order),
                 generation=BOOKS.generation_for(symbol),accountedQty=0.0,template=deepcopy(position),
                 reservedRisk=position['risk'] if side=='buy' else 0,
                 reservedCash=position['notional']+position['fee'] if side=='buy' else 0)
        self.commit(lambda:self.orders.append(row))
        engine.role('Exec','pending',f'Orden limitada SIMULADA: cola observada, hasta {ttl} s; sin fill supuesto')
        return True

    def cancel(self,symbol):
        # El llamador confirma la cancelación junto con su cambio de posición.
        self.desk.state['makerOrders']=[o for o in self.orders if o['symbol']!=symbol]

    def cancel_entries(self,reason='cancelled-control'):
        """Persist cancellation of buy remainders, preserving recorded fills/exits.

        A control first reconciles observations through its timestamp, then calls
        this method while holding the desk lock. Later events cannot fill a buy
        that has already been removed from the pending book.
        """
        entries=[row for row in self.orders if row['side']=='buy']
        if not entries: return 0
        def update():
            for row in entries:
                order=PostOnlyOrder(**row['order'])
                order.status=reason
                row['order']=asdict(order)
                self._finish(row,order)
        self.commit(update)
        return len(entries)

    def pending_entry(self):
        return any(o['side']=='buy' for o in self.orders)

    def _finish(self,row,order):
        engine=self.desk
        self.orders.remove(row)
        history=engine.state.setdefault('makerHistory',[])
        history.append(dict(symbol=row['symbol'],side=row['side'],at=time.time(),
                            status=order.status,filled=order.filled,quantity=order.quantity,fee=order.fees,
                            price=order.price,fills=order.fills,executionVerified=False,feeDiscountAssumed=False))
        del history[:-25]
        if row['side']=='buy' and order.filled==0:
            engine.state.setdefault('makerFallback',{})[row['entryKey']]=order.expires_at
            engine.state['makerFallback']=dict(list(engine.state['makerFallback'].items())[-100:])
        elif row['side']=='sell':
            p=next((p for p in engine.state['positions'] if p['entryKey']==row['entryKey']),None)
            if p:
                remaining=order.quantity-row['accountedQty']
                p['makerPartialFallbackQty']=max(0,remaining)
                p['makerPartialAttempted']=True

    def reconcile(self,data,cutoff=None):
        if cutoff is not None and (type(cutoff) not in (int,float) or not math.isfinite(cutoff)):
            raise ValueError('A finite maker reconciliation cutoff is required')
        engine=self.desk
        if not self.orders: return
        BOOKS.watch_trades([o['symbol'] for o in self.orders])
        for row in list(self.orders):
            order=PostOnlyOrder(**row['order'])
            events=BOOKS.observed_trades(row['symbol'],order.placed_at,order.last_id,row['generation'])
            if events is None:
                order.disconnect()
                events=[]
            if cutoff is not None:
                events=[event for event in events if event['at']<=cutoff]
            def update():
                previous_fills=len(order.fills)
                for event in events:
                    order.trade(event)
                # Flujos individuales observados, mismo precio límite y comisión
                # explícita; nunca se descuenta una supuesta tarifa maker menor.
                delta=float(Decimal(str(order.filled))-Decimal(str(row['accountedQty'])))
                observed=order.fills[previous_fills:]
                if delta>0:
                    if row['side']=='buy': self._buy_fill(row,order,delta,data,observed)
                    else:
                        p=next((p for p in engine.state['positions'] if p['entryKey']==row['entryKey']),None)
                        if p:
                            engine.take_partial(p,order.price,data,qty_override=delta,
                                                price_override=order.price,persist=False)
                            p['partialExits'][-1]['observedFills']=observed
                    row['accountedQty']=order.filled
                order.advance(time.time() if cutoff is None else cutoff)
                if row['side']=='buy' and (not engine.state['enabled'] or engine.state.get('flatten') or engine.state['dailyStopped']):
                    if order.status=='pending': order.status='cancelled-risk'
                row['order']=asdict(order)
                if order.status!='pending': self._finish(row,order)
            self.commit(update)

    def _buy_fill(self,row,order,quantity,data,observed):
        engine=self.desk
        template=row['template']
        cost=quantity*order.price
        fee=cost*order.fee_rate
        p=next((p for p in engine.state['positions'] if p['entryKey']==row['entryKey']),None)
        if p is None:
            p=deepcopy(template)
            p.update(qty=0.0,notional=0.0,fee=0.0,initialRisk=0.0,entryTime=order.fills[0]['at'],
                     entryExecution='paper-maker-observed',executionVerified=False)
            engine.state['positions'].append(p)
        p['qty']=float(Decimal(str(p['qty']))+Decimal(str(quantity)))
        p['notional']+=cost
        p['fee']+=fee
        p['entry']=p['notional']/p['qty']
        p['initialDist']=p['entry']-p['sl']
        rate,slip=engine.costs(p)
        p['initialRisk']=p.get('initialRisk',0)+max(0,cost+fee-quantity*template['sl']*(1-slip)*(1-rate))
        p['risk']=p['notional']+p['fee']-p['qty']*p['sl']*(1-slip)*(1-rate)
        p['economics']=planned_trade(p['entry'],p['sl'],p['tp'],p['qty'],fee_rate=rate,exit_slippage=slip)
        engine.state['cash']-=cost+fee
        left=max(0,order.quantity-order.filled)/order.quantity
        row['reservedCash']=(template['notional']+template['fee'])*left
        row['reservedRisk']=template['risk']*left
        p['entrySlippage']=0.0
        p.setdefault('entryMakerFills',[]).extend(observed)
        if engine.state.get('selectorDecision'):
            engine.state['selectorDecision']['executed']=True
