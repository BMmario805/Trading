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
from copy import deepcopy

from .config import SYMBOLS

POLICY=dict(version='portfolio-v2',maxRiskPct=3.0,maxFactorRiskPct=1.5,
            maxGrossMultiple=2.0,maxFactorGrossMultiple=.75,maxSameSymbol=2)
_lock=threading.RLock()
_reservations={}
_catalog={s.id: {'cripto':'crypto','forex':'fx','acciones':'us-equities',
                  'indices':'us-equities'}.get(s.category,'unknown') for s in SYMBOLS}
_catalog.update({s:'us-equities' for s in ('AMD','AVGO','JPM','V','MA','COST','WMT','XOM','UNH','NFLX','PLTR','ES','NQ')})
_catalog.update({s:'fx' for s in ('EURUSD','GBPUSD','USDJPY','AUDUSD','NZDUSD','USDCHF','USDCAD')})
_catalog.update(GOLD='gold',CL='energy')
_aliases={'XAUUSD':'GOLD','PAXG':'GOLD','GC':'GOLD','GOLD':'GOLD'}
_aliases.update({s.source_symbol:s.id for s in SYMBOLS if s.source_symbol != s.id})


def register_spot_symbols(metadata):
    """Only corroborated exchange metadata may expand the crypto catalogue."""
    with _lock:
        for symbol, row in (metadata or {}).items():
            base=row.get('baseAsset')
            if (isinstance(base,str) and base.isalnum() and symbol==base+'USDT'
                    and row.get('quoteAsset')=='USDT' and row.get('status')=='TRADING'
                    and row.get('isSpotTradingAllowed') is True):
                _catalog.setdefault(base,'crypto')
                _aliases[symbol]=base


def instrument(symbol, canonical_symbol=None):
    symbol=str(canonical_symbol or symbol).upper().replace('/','')
    symbol=_aliases.get(symbol,symbol)
    if symbol.endswith('USDT') and symbol[:-4] in _catalog: symbol=symbol[:-4]
    return _aliases.get(symbol,symbol)


def factor(symbol):
    key=instrument(symbol)
    return _catalog.get(key,'unknown')


def _finite(value, default=0.0):
    try: result=float(value)
    except (TypeError,ValueError): return default
    return result if math.isfinite(result) else default


def _fresh_at(stamp, maximum=120):
    stamp=_finite(stamp,-1)
    return stamp>0 and -5<=time.time()-stamp<=maximum


def _collect():
    from . import bots,desk,mt5_demo
    accounts=[]
    rows=[]
    for bot in list(bots.BOTS.values()):
        # Las cuentas nuevas tienen EUR explícito; no se mezclan unidades legacy.
        if (getattr(bot,'session',None) or {}).get('currency')!='EUR': continue
        positions=list(bot.positions.values())
        equity=max(0,bot.balance+sum(p.unrealized() for p in positions))
        accounts.append(dict(source='lab',bot=bot.cfg.id,equityEUR=equity,initialEUR=bot.cfg.initial_balance,
                             equityBasis='ledger-conversion'))
        for p in positions:
            mark_at=getattr(p,'last_quote_at',None)
            mark_fresh=_fresh_at(mark_at)
            conversion_at=getattr(p,'risk_conversion_at',None)
            current_rate=_finite(getattr(p,'risk_quote_to_account',None))
            conversion_source=getattr(p,'risk_conversion_source',None)
            conversion_known=current_rate>0 and _fresh_at(conversion_at) and bool(conversion_source)
            reference_risk=max(0,-p.unrealized(p.sl))
            old_rate=_finite(p.quote_to_account)
            risk_known=conversion_known and old_rate>0
            rows.append(dict(source='lab',bot=bot.cfg.id,symbol=instrument(p.symbol),factor=factor(p.symbol),
                direction=p.direction,riskEUR=reference_risk/old_rate*current_rate if risk_known else 0,
                referenceRiskEUR=reference_risk,riskKnown=risk_known,
                notionalEUR=p.qty*max(0,_finite(p.last_price))*current_rate if conversion_known else 0,
                notionalKnown=_finite(p.last_price)>0 and mark_fresh and conversion_known,markAt=mark_at,markFresh=mark_fresh,
                riskConversionAt=conversion_at,riskQuoteToAccount=current_rate if conversion_known else None,
                riskConversionSource=conversion_source,conversionKnown=conversion_known,exposureBasis='mark'))
    engines=dict(desk.DESKS)
    if desk.DESK: engines['crypto']=desk.DESK
    for key,engine in engines.items():
        if engine: register_spot_symbols((getattr(engine,'last_data',None) or {}).get('metadata'))
        if not engine: continue
        funding=engine.state.get('funding') or {}
        if not funding and not engine.state.get('positions') and not engine.state.get('makerOrders'): continue
        bot_id='desk' if key=='crypto' else key
        reader=getattr(engine,'risk_conversion',None)
        rate=reader() if callable(reader) else funding.get('netQuotePerEuro')
        conversion_known=_finite(rate)>0
        divisor=rate if conversion_known else 1.0
        accounts.append(dict(source='desk',bot=bot_id,equityEUR=max(0,engine.equity()/divisor) if conversion_known else 0,
                             initialEUR=funding.get('amount',0),conversionKnown=conversion_known))
        for p in list(engine.state['positions']):
            fee=(p.get('costPolicy') or {}).get('feeRate',desk.FEE)
            slip=(p.get('costPolicy') or {}).get('slippageRate',desk.SLIPPAGE)
            risk=max(0,p['notional']+p['fee']-p['qty']*p['sl']*(1-slip)*(1-fee))
            mark_fresh=_fresh_at(p.get('quoteAt'))
            rows.append(dict(source='desk',bot=bot_id,symbol=instrument(p['symbol']),factor=factor(p['symbol']),
                             direction='long',riskEUR=risk/divisor if conversion_known else 0,
                             notionalEUR=p['qty']*max(0,_finite(p.get('lastBid')))/divisor if conversion_known else 0,
                             notionalKnown=conversion_known and _finite(p.get('lastBid'))>0 and mark_fresh,
                             markAt=p.get('quoteAt'),markFresh=mark_fresh,exposureBasis='mark'))
        for pending in engine.state.get('makerOrders',[]):
            if pending['side']!='buy' or pending['reservedRisk']<=0: continue
            symbol=pending['symbol']
            rows.append(dict(source='desk',bot=bot_id,symbol=instrument(symbol),factor=factor(symbol),
                             direction='long',riskEUR=pending['reservedRisk']/divisor if conversion_known else 0,
                             notionalEUR=pending['reservedCash']/divisor if conversion_known else 0,
                             notionalKnown=conversion_known,pending=True))
    state=dict(mt5_demo.STATE)
    session=state.get('session') or {}
    if state.get('demo') is True and state.get('currency')=='EUR' and session.get('initial') is not None:
        account_fresh=state.get('connected') is True and _fresh_at(state.get('updatedAt'),30)
        accounts.append(dict(source='mt5',bot='shared',equityEUR=max(0,_finite(state.get('equity'))),
                             initialEUR=session.get('allocatedCapital',session['initial']),snapshotFresh=account_fresh))
        for p in list(state.get('positions',[])):
            # Riesgo desconocido consume el máximo de cartera de esa cuenta.
            risk=p.get('riskEUR')
            risk_known=_finite(risk,-1)>=0
            risk=_finite(risk) if risk_known else max(0,_finite(state.get('equity')))*.06
            canonical=instrument(p['symbol'],p.get('canonicalSymbol'))
            rows.append(dict(source='mt5',bot=p.get('bot','manual/otro'),symbol=canonical,factor=factor(canonical),
                direction=p.get('direction','long'),riskEUR=max(0,risk),notionalEUR=max(0,_finite(p.get('notionalEUR'))),
                riskKnown=risk_known,notionalKnown=_finite(p.get('notionalEUR'),-1)>=0 and _fresh_at(p.get('priceAt')),
                markAt=p.get('priceAt'),markFresh=_fresh_at(p.get('priceAt')),exposureBasis='mark'))
    return accounts,rows


def _snapshot():
    accounts,rows=_collect()
    keys={(p['source'],p['bot'],p['symbol']) for p in rows}
    now=time.time()
    for token,row in list(_reservations.items()):
        key=(row['source'],row['bot'],row['symbol'])
        if key in keys and row['source']!='mt5':
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
    unknown=sum(p.get('notionalKnown') is False or p.get('riskKnown') is False or p['factor']=='unknown' for p in rows)
    return dict(policy=dict(POLICY),capitalEUR=capital,initialEUR=sum(a['initialEUR'] for a in accounts),
                accounts=accounts,positions=rows,factors=dict(factors),
                openRiskEUR=sum(p['riskEUR'] for p in rows),grossExposureEUR=sum(p['notionalEUR'] for p in rows),
                pendingReservations=len(_reservations),updatedAt=now,
                exposureComplete=unknown==0 and all(a.get('conversionKnown',True) for a in accounts)
                    and all(a.get('snapshotFresh',True) for a in accounts) and _external_recovery_complete(),
                conversionComplete=all(a.get('conversionKnown',True) for a in accounts)
                    and all(p.get('conversionKnown',True) for p in rows),
                accountSnapshotsFresh=all(a.get('snapshotFresh',True) for a in accounts),
                staleExposurePositions=sum(p.get('markFresh') is False for p in rows),
                unknownExposurePositions=unknown,exposureBasis='mark',
                scope='Suma de cuentas virtuales independientes; no es un único depósito de 100 EUR')


def _external_recovery_complete():
    from . import mt5_demo
    return mt5_demo.STATE.get('portfolioRecoveryReady',True) is True


def public_snapshot():
    with _lock: return _snapshot()


def assess(symbol,direction,proposedRiskEUR,proposedNotionalEUR,source,bot_id,reserve=False,*,canonical_symbol=None,isolated=False):
    with _lock:
        snapshot=_snapshot()
        risk=_finite(proposedRiskEUR,-1)
        nominal=_finite(proposedNotionalEUR,-1)
        if risk<0 or nominal<0:
            return dict(allowed=False,reason='Riesgo o exposición global sin cuantificar')
        capital=snapshot['capitalEUR']
        if not snapshot['accounts']:
            return dict(allowed=isolated is True,reason='Evaluación aislada explícita' if isolated is True else
                        'Cartera no registrada; entradas bloqueadas',token=None)
        key=instrument(symbol,canonical_symbol)
        group=factor(key)
        bucket=snapshot['factors'].get(group,dict(riskEUR=0,notionalEUR=0))
        reason=None
        if group=='unknown': reason='Instrumento sin factor canónico verificado'
        elif not snapshot['exposureComplete']: reason='Exposición existente sin cuantificar o antigua; pendiente de reconciliación'
        elif capital<=0: reason='Capital global agotado'
        elif snapshot['openRiskEUR']+risk>capital*POLICY['maxRiskPct']/100: reason='Límite global de riesgo agregado'
        elif bucket['riskEUR']+risk>capital*POLICY['maxFactorRiskPct']/100: reason=f'Límite de riesgo del factor {group}'
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


def reservation(token):
    """Serializable payload persisted with the external order intent, before send."""
    with _lock:
        return deepcopy(_reservations.get(token))


def mark_durable(token):
    """The intent transaction committed; even an offline retry cannot free it."""
    with _lock:
        if token in _reservations: _reservations[token]['durable']=True


def restore(token, payload):
    """Rehydrate an unresolved external intent; elapsed time never releases risk."""
    if not token or not isinstance(payload,dict): return False
    required=('source','bot','symbol','factor','direction','riskEUR','notionalEUR','at')
    if any(key not in payload for key in required): return False
    if any(not isinstance(payload[k],str) or not payload[k] for k in ('source','bot','symbol')): return False
    if payload['direction'] not in ('long','short'): return False
    if any(_finite(payload[k],-1)<0 for k in ('riskEUR','notionalEUR','at')): return False
    row={key:payload[key] for key in required}
    for key in ('riskEUR','notionalEUR','at'): row[key]=_finite(row[key])
    row['symbol']=instrument(row['symbol'])
    row['factor']=factor(row['symbol'])
    row['durable']=True
    with _lock: _reservations[token]=row
    return True
