"""Comparación exploratoria de horizontes y salidas, sin modificar libros ni motores.

Las propuestas se fijan antes de ejecutar esta comparación. Sus datos solapan
estudios ya observados: no son una validación independiente ni autorizan promoción.
"""

import argparse
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import time

from . import desk, market_data
from .research import _bounded_history
from .training import expectancy_uncertainty
from .spot_orders import partial_quantity
from .execution import closed_candles
from .indicators import atr, adx_series
from .strategy import resample


@dataclass(frozen=True)
class ExitPolicy:
    id: str
    max_hold_seconds: int | None
    partial: bool = False
    trail_atr: float = 1.5
    trail_start_r: float = 1.0


POLICIES = (ExitPolicy('fixed-6h',6*3600),ExitPolicy('fixed-48h',48*3600),
            ExitPolicy('partial-trail-48h',48*3600,True,2.5),
            ExitPolicy('partial-trail-unlimited',None,True,2.5))
INTERVALS = {'15m':900,'1h':3600,'4h':14400}


def replay(rows,info,seconds,policy,*,start_time=0,cost_multiple=1,analyses=None,
           order_options=None,partial_fraction=.5,trail_after_r=0,residual_stop_r=None,
           fee_rate=None,slippage_rate=None,spread_pct=.0002,desk_policy_version=None,
           flow_observations=None):
    versioned=desk.exit_policy(desk_policy_version) if desk_policy_version else None
    if versioned:
        partial_fraction=versioned['partialFraction']
        trail_after_r=versioned['trailAfterR']
        residual_stop_r=versioned['residualStopR']
    cash=peak=100.0
    max_dd=0.0
    position=None
    trades=[]
    partials=skipped=0
    cooldown=0
    day=None
    stopped=False
    day_equity=100.0
    fee,slip,spread=(desk.FEE if fee_rate is None else fee_rate)*cost_multiple,(desk.SLIPPAGE if slippage_rate is None else slippage_rate)*cost_multiple,spread_pct*cost_multiple
    exit_factor=(1-slip)*(1-fee)

    def realize(bid,qty):
        nonlocal cash
        fill=bid*(1-slip)
        proceeds=fill*qty*(1-fee)
        cash+=proceeds
        position['proceeds']+=proceeds
        position['exitFees']+=fill*qty*fee
        position['qty']-=qty

    def close(bid,reason,stamp):
        nonlocal position,cooldown
        realize(bid,position['qty'])
        pnl=position['proceeds']-position['notional']-position['fee']
        trades.append(dict(pnl=pnl,reason=reason,at=stamp,fees=position['fee']+position['exitFees'],
                           initialRisk=position['risk'],partial=position['partialDone']))
        cooldown=stamp+seconds*(4 if pnl<=0 else 1)
        position=None

    for i in range(1,len(rows)):
        bar=rows[i]
        now=bar['time']
        if now<start_time: continue
        bid=bar['open']*(1-spread/2)
        equity=cash+(position['qty']*bid*exit_factor if position else 0)
        if now//86400!=day:
            day=now//86400;day_equity=equity;stopped=False
        if equity<day_equity*.95: stopped=True
        had_position=position is not None
        a=analyses.get(i) if analyses is not None else desk.tape(rows[max(0,i-250):i],now,seconds) if i>=250 else None
        if position:
            if bid<=position['sl']:
                close(bid,'gap-stop',now)
            elif stopped:
                close(bid,'daily-stop',now)
            elif policy.max_hold_seconds is not None and now-position['entryTime']>=policy.max_hold_seconds:
                close(bid,'time',now)
            elif a and not a['trend'] and a['close']<a['ema50']-2*a['atr']:
                close(bid,'structure',now)
            elif position['partialPending']:
                # Decisión al cierre anterior; ejecución al precio de apertura real,
                # con sus gaps y costes. No se supone un fill intravela perfecto.
                qty=partial_quantity(position['qty'],bid,info,fraction=partial_fraction)
                position['partialPending']=False
                position['partialTried']=True
                if qty:
                    realize(bid,qty)
                    position['partialDone']=True
                    partials+=1
                    equilibrium=(position['entry']+residual_stop_r*position['risk']/(position['notional']/position['entry'])
                                 if residual_stop_r is not None else position['entry']*(1+fee)/exit_factor)
                    if versioned and versioned.get('netResidualStop'):
                        equilibrium=(position['entry']*(1+fee)+residual_stop_r*position['risk']/(position['notional']/position['entry']))/exit_factor
                    if equilibrium<bid or (versioned and versioned.get('netResidualStop')):
                        position['sl']=max(position['sl'],equilibrium)
                else:
                    skipped+=1
        if not had_position and not stopped and now>=cooldown and a:
            ask=bar['open']*(1+spread/2)
            if desk.price_allowed(a,ask):
                options=dict(order_options or {})
                if desk_policy_version: options['policy_version']=desk_policy_version
                order=desk.size_order(cash,cash,ask,a['atr'],info,fee=fee,slippage=slip,**options)
                if order:
                    cash-=order['notional']+order['fee']
                    position=dict(order,entryTime=now,atr=a['atr'],best=order['entry'],
                                  initialDist=order['entry']-order['sl'],proceeds=0.,exitFees=0.,
                                  partialDone=False,partialPending=False,partialTried=False)
        if position:
            low,high=bar['low']*(1-spread/2),bar['high']*(1-spread/2)
            if low<=position['sl']:
                close(min(bid,position['sl']),'stop',now)
            elif (not policy.partial or (position['partialTried'] and not position['partialDone'])) and high>=position['tp']:
                close(max(bid,position['tp']),'target',now)
            else:
                current=bar['close']*(1-spread/2)
                floating=position['qty']*current*exit_factor
                if policy.partial and not position['partialTried']:
                    net_per_unit=current*exit_factor-position['entry']*(1+fee)
                    risk_per_unit=position['risk']/(position['notional']/position['entry'])
                    if net_per_unit>=(versioned['partialAtR'] if versioned else 1.5)*risk_per_unit:
                        position['partialPending']=True
                position['best']=max(position['best'],current)
                trigger=position['partialDone'] if policy.partial else current>=position['entry']+policy.trail_start_r*position['initialDist']
                if trigger and policy.partial and trail_after_r and not (versioned and versioned.get('dynamicChandelier')):
                    if current*exit_factor-position['entry']*(1+fee)>=trail_after_r*position['risk']/(position['notional']/position['entry']):
                        position['trailActivated']=True
                    trigger=position.get('trailActivated',False)
                if versioned and versioned.get('chandelier4h'):
                    # Only completed 4h bars through this decision close. Flow
                    # observations need their original availability timestamp;
                    # replay never consults today's live derivatives cache.
                    decision_at=now+seconds
                    history=rows[:i+1]
                    higher=resample(history,14400) if seconds<14400 else history
                    counts={r['time']:0 for r in higher}
                    for r in history: counts[r['time']//14400*14400]=counts.get(r['time']//14400*14400,0)+1
                    higher=closed_candles([r for r in higher if counts.get(r['time'],0)*seconds>=14400],14400,decision_at)
                    context=higher[-50:]
                    if len(context)>=50:
                        closed_net=context[-1]['close']*exit_factor-position['entry']*(1+fee)
                        if not versioned.get('dynamicChandelier') and closed_net>=2*position['risk']/(position['notional']/position['entry']):
                            position['trailActivated']=True
                        enabled=position['partialDone'] if versioned.get('dynamicChandelier') else position.get('trailActivated')
                        highs=[r['high'] for r in higher if r['time']>=position['entryTime']]
                        if enabled and highs:
                            position['bestHigh4h']=max(position.get('bestHigh4h',0),max(highs))
                            flow=(flow_observations or {}).get(i,{})
                            available=flow.get('availableAt')
                            if type(available) not in (int,float) or not math.isfinite(available) or available>decision_at:
                                flow={}
                            multiple=desk.chandelier_multiple(versioned,adx_series(context)[-1],flow.get('regime'),flow.get('cvdDelta'))
                            position['sl']=max(position['sl'],position['bestHigh4h']-multiple*atr(context))
                elif trigger:
                    stop=max(position['sl'],position['entry']*(1+fee)/exit_factor,position['best']-policy.trail_atr*position['atr'])
                    if stop<current: position['sl']=stop
                worst=cash+position['qty']*low*exit_factor
                max_dd=max(max_dd,peak-worst)
        equity=cash+(position['qty']*bar['close']*(1-spread/2)*exit_factor if position else 0)
        peak=max(peak,equity)
        max_dd=max(max_dd,peak-equity)
    open_at_end=position is not None
    if position:
        close(rows[-1]['close']*(1-spread/2),'end-of-sample',rows[-1]['time']+seconds)
    pnls=[t['pnl'] for t in trades]
    gains=sum(max(0,p) for p in pnls)
    losses=-sum(min(0,p) for p in pnls)
    if abs(cash-100-sum(pnls))>1e-7:
        raise RuntimeError('No reconcilian los flujos de caja del experimento')
    return dict(policy=asdict(policy),policyVersion=desk_policy_version,pnl=round(cash-100,6),trades=len(trades),wins=sum(p>0 for p in pnls),
                profitFactor=gains/losses if losses else None,maxDrawdownUSDT=round(max_dd,6),
                fees=round(sum(t['fees'] for t in trades),6),partials=partials,partialsSkippedByFilters=skipped,
                forcedFinalClose=open_at_end,uncertainty=expectancy_uncertainty(pnls),promotionApplied=False)


def compare_versions(rows,info,seconds=3600,**kwargs):
    """Fixed v9/v10 comparison; diagnostic replay with no policy promotion."""
    policy=ExitPolicy('versioned-chandelier',desk.MAX_HOLD_S,True,2.8)
    results={version:dict(normal=replay(rows,info,seconds,policy,desk_policy_version=version,**kwargs),
                          stress=replay(rows,info,seconds,policy,desk_policy_version=version,cost_multiple=2,**kwargs))
             for version in (desk.LEGACY_POLICY_VERSION,desk.POLICY_VERSION)}
    return dict(results=results,trialCount=4,automaticPromotion=False,costVersion='net-costs-v1',
                limitation='OHLC replay; queue fills, historical universe and live execution equivalence are unverified')


async def run(days=90):
    now=time.time()
    start=now-days*86400
    exchange=await market_data._binance_get('/api/v3/exchangeInfo',{})
    metadata={s['symbol']:s for s in exchange['symbols']}
    results=[]
    for symbol in ('BTC','ETH','SOL'):
        for interval,seconds in INTERVALS.items():
            history_days=math.ceil(days+250*seconds/86400)
            rows=await _bounded_history(symbol+'USDT',interval,history_days,10000,now)
            analyses={i:desk.tape(rows[max(0,i-250):i],rows[i]['time'],seconds)
                      for i in range(250,len(rows)) if rows[i]['time']>=start}
            variants=[]
            for policy in POLICIES:
                normal=replay(rows,metadata[symbol+'USDT'],seconds,policy,start_time=start,analyses=analyses)
                stress=replay(rows,metadata[symbol+'USDT'],seconds,policy,start_time=start,cost_multiple=2,analyses=analyses)
                variants.append(dict(normal=normal,stress=stress))
            evaluated=[r for r in rows if r['time']>=start]
            results.append(dict(symbol=symbol,interval=interval,bars=len(rows),
                                evaluationStart=evaluated[0]['time'] if evaluated else None,
                                evaluationEnd=rows[-1]['time']+seconds if rows else None,
                                datasetHash=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),variants=variants))
    return dict(version='desk-exit-experiments-v1',generatedAt=now,days=days,
                trialCount=len(results)*len(POLICIES)*2,results=results,promotionApplied=False,
                assumptions=['Exploración en periodos ya observados; no es holdout independiente ni decide promoción.',
                             'Cada símbolo y política usa 100 USDT propios; no se suman como una cartera.',
                             'Riesgo 0,5%, nominal máximo 1/6, largo Spot, sin préstamos y costes taker completos.',
                             'Parcial: señal al cierre y ejecución a la apertura siguiente; no garantiza asegurar 1,5R.',
                             'Stops primero en velas ambiguas; gaps, mínimos de lote y comisiones en cada salida.',
                             'Spread supuesto 2 puntos básicos; filtros actuales, sin libro histórico ni selección histórica.',
                             'Se fuerza liquidación al terminar la muestra y se publica cada variante y su estrés.'])


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,default=90,choices=range(30,91))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError('Se conserva el informe anterior; elegir otra ruta')
    try:
        report=await run(args.days)
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2)
        for result in report['results']:
            print(result['symbol'],result['interval'],[(v['normal']['policy']['id'],v['normal']['pnl'],v['normal']['trades'],v['stress']['pnl']) for v in result['variants']],flush=True)
    finally:
        await market_data.close_client()


if __name__=='__main__':
    asyncio.run(main())
