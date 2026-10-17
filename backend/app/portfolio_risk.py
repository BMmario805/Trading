"""Asignación agregada de riesgo para las cuentas virtuales independientes.

No mueve saldos: coordina entradas nuevas y conserva las posiciones existentes.
La suma de capitales es explícita; no presenta nueve cuentas como una de 100 EUR.
Las reservas cubren la espera entre admitir una orden y publicar su posición.
"""

import math
import threading
import time
import uuid
from collections import defaultdict

POLICY=dict(version='portfolio-v1',maxRiskPct=3.0,maxFactorRiskPct=1.5,
            maxGrossMultiple=2.0,maxFactorGrossMultiple=.75,maxSameSymbol=2)
_lock=threading.RLock()
_reservations={}


def instrument(symbol):
    symbol=symbol.upper().replace('/','')
    if symbol.endswith('USDT'): symbol=symbol[:-4]
    if symbol in ('XAUUSD','PAXG','GC','GOLD'): return 'GOLD'
    return symbol


def factor(symbol):
    key=instrument(symbol)
    if key=='GOLD': return 'gold'
    if key in ('AAPL','MSFT','NVDA','TSLA','AMZN','META','GOOGL','SPY','QQQ','ES','NQ'): return 'us-equities'
    if key in ('EURUSD','GBPUSD','USDJPY'): return 'fx'
    if key=='CL': return 'energy'
    return 'crypto'


def _finite(value, default=0.0):
    try: result=float(value)
    except (TypeError,ValueError): return default
    return result if math.isfinite(result) else default


def _collect():
    from . import bots,desk,mt5_demo
    accounts=[]
    rows=[]
    for bot in list(bots.BOTS.values()):
        # Las cuentas nuevas tienen EUR explícito; no se mezclan unidades legacy.
        if (getattr(bot,'session',None) or {}).get('currency')!='EUR': continue
        positions=list(bot.positions.values())
        equity=max(0,bot.balance+sum(p.unrealized() for p in positions))
        accounts.append(dict(source='lab',bot=bot.cfg.id,equityEUR=equity,initialEUR=bot.cfg.initial_balance))
        for p in positions:
            rows.append(dict(source='lab',bot=bot.cfg.id,symbol=instrument(p.symbol),factor=factor(p.symbol),
                direction=p.direction,riskEUR=max(0,-p.unrealized(p.sl)),
                notionalEUR=p.qty*p.entry_price*p.quote_to_account))
    engine=desk.DESK
    if engine and (engine.state.get('funding') or {}).get('netQuotePerEuro',0)>0:
        rate=engine.state['funding']['netQuotePerEuro']
        accounts.append(dict(source='desk',bot='desk',equityEUR=max(0,engine.equity()/rate),initialEUR=engine.state['funding']['amount']))
        for p in list(engine.state['positions']):
            fee=(p.get('costPolicy') or {}).get('feeRate',desk.FEE)
            slip=(p.get('costPolicy') or {}).get('slippageRate',desk.SLIPPAGE)
            risk=max(0,p['notional']+p['fee']-p['qty']*p['sl']*(1-slip)*(1-fee))
            rows.append(dict(source='desk',bot='desk',symbol=instrument(p['symbol']),factor=factor(p['symbol']),
                             direction='long',riskEUR=risk/rate,notionalEUR=p['notional']/rate))
    state=dict(mt5_demo.STATE)
    session=state.get('session') or {}
    if state.get('demo') is True and state.get('currency')=='EUR' and session.get('initial') is not None:
        accounts.append(dict(source='mt5',bot='shared',equityEUR=max(0,_finite(state.get('equity'))),initialEUR=session['initial']))
        for p in list(state.get('positions',[])):
            # Riesgo desconocido consume el máximo de cartera de esa cuenta.
            risk=p.get('riskEUR')
            risk=_finite(risk) if risk is not None else max(0,_finite(state.get('equity')))*.06
            rows.append(dict(source='mt5',bot=p.get('bot','manual/otro'),symbol=instrument(p['symbol']),factor=factor(p['symbol']),
                direction=p.get('direction','long'),riskEUR=max(0,risk),notionalEUR=max(0,_finite(p.get('notionalEUR'))),
                notionalKnown=p.get('notionalEUR') is not None))
    return accounts,rows


def _snapshot():
    accounts,rows=_collect()
    keys={(p['source'],p['bot'],p['symbol']) for p in rows}
    now=time.time()
    for token,row in list(_reservations.items()):
        key=(row['source'],row['bot'],row['symbol'])
        if key in keys:
            _reservations.pop(token,None)
        else:
            # Nunca liberar un envío incierto solo porque pasó tiempo.
            rows.append(dict(row,pending=True,ageSeconds=now-row['at']))
    capital=sum(a['equityEUR'] for a in accounts)
    factors=defaultdict(lambda:dict(riskEUR=0.0,notionalEUR=0.0,positions=0))
    for row in rows:
        bucket=factors[row['factor']]
        bucket['riskEUR']+=row['riskEUR']
        bucket['notionalEUR']+=row['notionalEUR']
        bucket['positions']+=1
    unknown=sum(p.get('notionalKnown') is False for p in rows)
    return dict(policy=dict(POLICY),capitalEUR=capital,initialEUR=sum(a['initialEUR'] for a in accounts),
                accounts=accounts,positions=rows,factors=dict(factors),
                openRiskEUR=sum(p['riskEUR'] for p in rows),grossExposureEUR=sum(p['notionalEUR'] for p in rows),
                pendingReservations=len(_reservations),updatedAt=now,
                exposureComplete=unknown==0,unknownExposurePositions=unknown,
                scope='Suma de cuentas virtuales independientes; no es un único depósito de 100 EUR')


def public_snapshot():
    with _lock: return _snapshot()


def assess(symbol,direction,proposedRiskEUR,proposedNotionalEUR,source,bot_id,reserve=False):
    with _lock:
        snapshot=_snapshot()
        risk=_finite(proposedRiskEUR,-1)
        nominal=_finite(proposedNotionalEUR,-1)
        if risk<0 or nominal<0:
            return dict(allowed=False,reason='Riesgo o exposición global sin cuantificar')
        capital=snapshot['capitalEUR']
        if not snapshot['accounts']:
            # Evaluadores aislados sin motores registrados no representan cartera viva.
            return dict(allowed=True,reason='Evaluación aislada sin cartera registrada',token=None)
        key=instrument(symbol)
        group=factor(symbol)
        bucket=snapshot['factors'].get(group,dict(riskEUR=0,notionalEUR=0))
        reason=None
        if capital<=0: reason='Capital global agotado'
        elif snapshot['openRiskEUR']+risk>capital*POLICY['maxRiskPct']/100: reason='Límite global de riesgo agregado'
        elif bucket['riskEUR']+risk>capital*POLICY['maxFactorRiskPct']/100: reason=f'Límite de riesgo del factor {group}'
        elif not snapshot['exposureComplete']: reason='Exposición existente sin cuantificar; pendiente de reconciliación'
        elif snapshot['grossExposureEUR']+nominal>capital*POLICY['maxGrossMultiple']: reason='Límite de exposición bruta global'
        elif bucket['notionalEUR']+nominal>capital*POLICY['maxFactorGrossMultiple']: reason=f'Concentración de exposición en {group}'
        elif sum(p['symbol']==key for p in snapshot['positions'])>=POLICY['maxSameSymbol']: reason=f'Exposición repetida en {key}'
        result=dict(allowed=reason is None,reason=reason or 'Presupuesto global disponible',
                    capitalEUR=capital,factor=group,policyVersion=POLICY['version'],token=None)
        if result['allowed'] and reserve:
            token=uuid.uuid4().hex
            _reservations[token]=dict(source=source,bot=bot_id,symbol=key,factor=group,direction=direction,
                riskEUR=risk,notionalEUR=nominal,at=time.time())
            result['token']=token
        return result


def release(token):
    with _lock: _reservations.pop(token,None)
