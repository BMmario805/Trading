"""Prueba aislada de capitalización y margen; nunca envía órdenes ni cambia cuentas.

El stop-out es un supuesto explícito del escenario, no un contrato disponible.
Reutiliza señales causales y costes, con una posición por serie independiente.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from . import aggressive, bots, strategy, training
from .economics import cost_profile, execution_fill, net_outcome, planned_trade
from .execution import atr_spread_multiple, fresh_signal, signal_exit_enabled
from .indicators import atr_series, ema

VERSION = 'extreme-capital-review-v1'
ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = (
    dict(id='control', name='Control a prueba', riskFraction=None, leverage=None, dailyLossFraction=.06),
    dict(id='extreme5', name='Extremo 5%', riskFraction=.05, leverage=20., dailyLossFraction=.25),
    dict(id='extreme15', name='Extremo 15%', riskFraction=.15, leverage=50., dailyLossFraction=.50),
)


def impulse_scores(rows):
    """Candidato de impulso de 5m: ruptura corta o recuperación, sin datos futuros."""
    closes=[r['close'] for r in rows]
    e9,e20,e50=(ema(closes,n) for n in (9,20,50))
    out=[0.]*len(rows)
    for i in range(60,len(rows)):
        c=rows[i];side=1 if e20[i]>e50[i] else -1
        if (e50[i]-e50[i-3])*side<=0 or (c['close']-c['open'])*side<=0: continue
        previous=rows[i-5:i]
        breakout=c['close']>max(r['high'] for r in previous) if side>0 else c['close']<min(r['low'] for r in previous)
        reclaim=(closes[i-1]-e9[i-1])*side<=0<(closes[i]-e9[i])*side
        volume=sum(max(0,r.get('volume',0)) for r in rows[i-20:i])/20
        if (breakout or reclaim) and (volume<=0 or c.get('volume',0)>=volume*1.2): out[i]=side*.8
    return out


def replay(rows,scores,atrs,*,start,config,costs,risk_fraction,leverage,daily_loss_fraction,initial=100.):
    """Una cuenta hipotética con interés compuesto, costes y liquidación intravela.

    Las decisiones del cierre se ejecutan en la siguiente apertura. Stop antes
    que objetivo si ambos cruzan dentro de la misma vela. Margen cruzado de una
    sola posición; stop-out al 50% del margen inicial y sin garantía del saldo.
    Se conserva deuda si un salto excede el capital. No se rellena con depósitos.
    """
    values=(risk_fraction,leverage,daily_loss_fraction,initial)
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in values):
        raise ValueError('Capital o riesgo inválido')
    if risk_fraction>1 or daily_loss_fraction>1 or leverage<1: raise ValueError('Rango inválido')
    if not rows or len(rows)!=len(scores) or len(rows)!=len(atrs): raise ValueError('Series incompatibles')
    fee=costs['feeRate'];spread=costs['spreadPct'];slip=costs['slippageRate'];carry_rate=costs['carryDailyRate']
    balance=initial;position=None;pending=None;cooldown=0
    peak=initial;max_dd=0.;liquidations=0;fees=carry_total=0.;entries=0;wins=0
    day=None;day_start=initial;day_blocked=False;days={};daily_loss_stops=0
    eq_curve=[];trade_pnls=[];max_used_leverage=0.;previous_equity=initial
    seconds=training.WORK_SECONDS[config['interval']]

    def outcome(price,stamp):
        p=position
        fill=execution_fill(price,p['side'],False,spread,slip)
        carry=p['entry']*p['qty']*carry_rate*max(0,stamp-p['time'])/86400
        return net_outcome(p['entry'],fill,p['qty'],p['side'],p['entryFee'],fee,carry=carry)

    def price_at_equity(target,stamp):
        p=position;side=p['side'];q=p['qty']
        carry=p['entry']*q*carry_rate*max(0,stamp-p['time'])/86400
        fill=(target-balance+side*p['entry']*q+p['entryFee']+carry)/(q*(side-fee))
        return fill/execution_fill(1.,side,False,spread,slip)

    def close(price,stamp,reason):
        nonlocal position,balance,cooldown,liquidations,fees,carry_total,wins
        pnl=outcome(price,stamp);p=position
        fill=execution_fill(price,p['side'],False,spread,slip)
        fees+=p['entryFee']+fill*p['qty']*fee
        carry_total+=p['entry']*p['qty']*carry_rate*max(0,stamp-p['time'])/86400
        balance+=pnl;trade_pnls.append(pnl);wins+=pnl>0
        liquidations+=reason=='liquidation'
        cooldown=max(1,config['cooldownBars']//4) if pnl>0 else config['cooldownBars']
        position=None

    def observe(equity):
        nonlocal peak,max_dd
        peak=max(peak,equity);max_dd=max(max_dd,(peak-equity)/peak*100)

    for i in range(max(1,start),len(rows)):
        c=rows[i];stamp=c['time'];key=int(stamp//86400)
        opening=balance+(outcome(c['open'],stamp) if position else 0)
        if key!=day:
            # Cierre a cierre: el salto nocturno pertenece al día nuevo, también
            # para el límite de pérdida. No se borra al tomar la apertura como base.
            day=key;day_start=previous_equity;day_blocked=opening<=0
            days[key]=dict(start=day_start,end=opening,bars=0,first=stamp,last=stamp)
        if opening<=day_start*(1-daily_loss_fraction): day_blocked=True
        if position:
            # Las decisiones previas se ejecutan al precio de apertura disponible.
            margin=position['entry']*position['qty']/leverage
            if opening<=margin*.5:
                close(c['open'],stamp,'liquidation')
            elif day_blocked:
                close(c['open'],stamp,'daily-loss')
            elif pending=='close': close(c['open'],stamp,'signal-time')
        if pending and pending!='close' and not position and balance>0 and not day_blocked:
            s,a,signal_close=pending
            if abs(c['open']-signal_close)<=a:
                side=1 if s>0 else -1
                entry=execution_fill(c['open'],side,True,spread,slip)
                sl=entry-side*config['slAtr']*a;tp=entry+side*config['tpAtr']*a
                if min(sl,tp)>0:
                    unit=planned_trade(entry,sl,tp,1,side,fee,slip,spread,
                        carry=entry*carry_rate*config['maxHoldSeconds']/86400)
                    if unit['viable']:
                        qty=min(balance*risk_fraction/unit['netRisk'],balance/(entry/leverage+entry*fee))
                        position=dict(side=side,entry=entry,sl=sl,tp=tp,qty=qty,atr=a,
                            entryFee=entry*qty*fee,time=stamp,best=entry,bars=0)
                        entries+=1;max_used_leverage=max(max_used_leverage,entry*qty/balance)
        pending=None
        if position:
            p=position;side=p['side'];p['bars']+=1
            margin=p['entry']*p['qty']/leverage
            triggers=[(p['sl'],'stop'),(price_at_equity(margin*.5,stamp+seconds),'liquidation'),
                      (price_at_equity(day_start*(1-daily_loss_fraction),stamp+seconds),'daily-loss')]
            trigger,reason=(max(triggers,key=lambda x:x[0]) if side>0 else min(triggers,key=lambda x:x[0]))
            if (side>0 and c['low']<=trigger) or (side<0 and c['high']>=trigger):
                fill=min(trigger,c['open']) if side>0 else max(trigger,c['open'])
                # No se inventa un mínimo diario capaz de impedir un salto.
                close(fill,stamp+seconds,reason)
                if reason=='daily-loss': day_blocked=True;daily_loss_stops+=1
            elif (side>0 and c['high']>=p['tp']) or (side<0 and c['low']<=p['tp']):
                close(p['tp'],stamp+seconds,'target')
            else:
                adverse=c['low'] if side>0 else c['high']
                observe(balance+outcome(adverse,stamp+seconds))
                p['best']=max(p['best'],c['high']) if side>0 else min(p['best'],c['low'])
                distance=config['slAtr']*p['atr'];gain=(p['best']-p['entry'])*side
                rules=config['exitPolicy']
                if gain>=rules['trailStartR']*distance:
                    tr=config['trailAtr']*(rules['tightenMultiple'] if gain>=rules['tightenAtR']*distance else 1)*p['atr']
                    break_even=price_at_equity(balance,stamp+seconds)
                    if (break_even-c['close'])*side>0: break_even=p['sl']
                    p['sl']=max(p['sl'],break_even,p['best']-tr) if side>0 else min(p['sl'],break_even,p['best']+tr)
                if (config['exitOnSignal'] and scores[i]*side<=-.75*config['threshold']) or stamp+seconds-p['time']>=config['maxHoldSeconds']:
                    pending='close'
        equity=balance+(outcome(c['close'],stamp+seconds) if position else 0)
        observe(equity)
        if equity<=day_start*(1-daily_loss_fraction): day_blocked=True
        if balance<=0: day_blocked=True
        days[key].update(end=equity,bars=days[key]['bars']+1,last=stamp+seconds)
        eq_curve.append(dict(time=stamp+seconds,equity=equity))
        previous_equity=equity
        if not position and not pending and balance>0 and not day_blocked:
            if cooldown: cooldown-=1
            else:
                s,a=scores[i],atrs[i]
                ext=config['extensions'][i]
                if (a and a>0 and abs(s)>=config['threshold'] and fresh_signal(s,scores[max(0,i-3):i],config['threshold'])
                    and a>=config['minAtrSpread']*c['close']*spread
                    and (ext is None or ext*(1 if s>0 else -1)<=config['overextAtr'])):
                    pending=(s,a,c['close'])
    if position:
        close(rows[-1]['close'],rows[-1]['time']+seconds,'end')
        if eq_curve: eq_curve[-1]['equity']=balance
        days[day]['end']=balance
    # Primer y último día se omiten: pueden ser fragmentos del histórico.
    keys=sorted(days)
    daily=[dict(time=k*86400,returnPct=(days[k]['end']/days[k]['start']-1)*100)
           for k in keys[1:-1] if days[k]['start']>0]
    returns=[r['returnPct'] for r in daily]
    return dict(initial=initial,finalEquity=balance,totalReturnPct=(balance/initial-1)*100,
                maxDrawdownPct=max_dd,entries=entries,trades=len(trade_pnls),wins=wins,
                fees=fees,carry=carry_total,liquidations=liquidations,dailyLossStops=daily_loss_stops,
                capitalExhausted=balance<=0 or any(p['equity']<=0 for p in eq_curve),
                days=len(daily),daysDouble=sum(x>=100 for x in returns),daysTriple=sum(x>=200 for x in returns),
                bestDayPct=max(returns) if returns else None,worstDayPct=min(returns) if returns else None,
                medianDayPct=statistics.median(returns) if returns else None,maxUsedLeverage=max_used_leverage,
                daily=daily,equityCurve=eq_curve)


def run(folder):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    series=[]
    for cfg in bots.ALL_BOTS:
        for symbol in manifest['symbols']:
            path=folder/f'{symbol}-{cfg.work_interval}.json'
            if symbol not in cfg.universe or not path.exists(): continue
            rows=json.loads(path.read_text(encoding='utf-8'))
            series.append((cfg.id,symbol,rows,dict(interval=cfg.work_interval,slAtr=cfg.sl_atr,tpAtr=cfg.tp_atr,
                trailAtr=cfg.trail_atr,maxHoldSeconds=cfg.max_hold_seconds,cooldownBars=max(1,round(cfg.cooldown_seconds/training.WORK_SECONDS[cfg.work_interval])),
                threshold=cfg.entry_threshold,exitOnSignal=signal_exit_enabled(cfg.id),exitPolicy=aggressive.exit_policy(cfg.id),
                minAtrSpread=atr_spread_multiple(cfg.id),overextAtr=cfg.overext_atr),cfg))
    for symbol in manifest['symbols']:
        path=folder/f'{symbol}-5m.json'
        if path.exists():
            series.append(('impulse5m',symbol,json.loads(path.read_text(encoding='utf-8')),
                dict(interval='5m',slAtr=1.,tpAtr=8.,trailAtr=2.5,maxHoldSeconds=21600,cooldownBars=1,
                     threshold=.6,exitOnSignal=True,exitPolicy=dict(trailStartR=3.,tightenAtR=6.,tightenMultiple=.85),
                     minAtrSpread=4,overextAtr=2.75),None))
    results=[]
    for name,symbol,rows,config,cfg in series:
        if len(rows)<600: continue
        higher=strategy.resample(rows,aggressive.HIGHER_SECONDS[name]) if cfg else []
        scores=aggressive.scores(name,rows,higher) if cfg else impulse_scores(rows)
        atrs=atr_series(rows);config['extensions']=strategy.extension_series(rows)
        start=max(250,int(len(rows)*.7));costs=cost_profile('lab',rows[0]['category'])
        cases=[]
        for profile in SCENARIOS:
            risk=profile['riskFraction'] if profile['riskFraction'] is not None else (cfg.risk_pct*.25 if cfg else .0025)
            leverage=profile['leverage'] or (cfg.max_leverage if cfg else 20.)
            outputs={}
            for label,model in (('normal',costs),('stress',training.stressed_costs(costs,1.5))):
                outputs[label]=replay(rows,scores,atrs,start=start,config=config,costs=model,
                    risk_fraction=risk,leverage=leverage,daily_loss_fraction=profile['dailyLossFraction'])
            cases.append(dict(profile=profile['id'],riskPct=risk*100,leverage=leverage,dailyLossPct=profile['dailyLossFraction']*100,**outputs))
        results.append(dict(strategy=name,symbol=symbol,interval=config['interval'],currency='quote',
            start=rows[start]['time'],end=rows[-1]['time']+training.WORK_SECONDS[config['interval']],
            config={k:v for k,v in config.items() if k!='extensions'},costs=costs,
            dataSha256=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),cases=cases))
        print(json.dumps(dict(strategy=name,symbol=symbol,extreme15Return=cases[-1]['normal']['totalReturnPct'],
            bestDay=cases[-1]['normal']['bestDayPct'],double=cases[-1]['normal']['daysDouble'],
            dd=cases[-1]['normal']['maxDrawdownPct'])),flush=True)
    result=dict(version=VERSION,generatedAt=time.time(),engineVersion=training.ENGINE_VERSION,
        activeTradingChanged=False,independentValidation=False,orderExecution=False,
        scenarios=SCENARIOS,results=results,
        assumptions=['Datos ya observados; exploración, sin validación ni promoción automática.',
            '100 unidades de la moneda cotizada por serie independiente; no sumar instrumentos ni divisas.',
            'Una posición por serie; sin compartir límites, capital o colateral con las cuentas en ejecución.',
            'Interés compuesto al entrar, costes completos supuestos; estrés de costes 1,5x.',
            'Margen hipotético hasta 50x y stop-out al 50% del margen inicial; no acredita disponibilidad del producto.',
            'OHLC: stop antes que objetivo, gaps al precio de apertura; deuda no se oculta.',
            'Días UTC observados; se omiten el primero y el último. No se inventan sesiones sin datos.',
            'No reproduce profundidad, latencia, límites contractuales o todos los costes de liquidación.',
            'Un día con 100% o 200% no prueba repetibilidad ni una probabilidad futura.'])
    result['summary']=summarize(result)
    return result


def summarize(result):
    output=[]
    for index,profile in enumerate(SCENARIOS):
        item=dict(profile=profile['id'])
        for label in ('normal','stress'):
            rows=[r['cases'][index][label] for r in result['results']]
            item[label]=dict(series=len(rows),positive=sum(r['totalReturnPct']>0 for r in rows),
                daysDouble=sum(r['daysDouble'] for r in rows),daysTriple=sum(r['daysTriple'] for r in rows),
                observations=sum(r['days'] for r in rows),
                medianReturnPct=statistics.median(r['totalReturnPct'] for r in rows),
                worstReturnPct=min(r['totalReturnPct'] for r in rows),
                medianDrawdownPct=statistics.median(r['maxDrawdownPct'] for r in rows),
                worstDrawdownPct=max(r['maxDrawdownPct'] for r in rows),
                bestDayPct=max(r['bestDayPct'] for r in rows if r['bestDayPct'] is not None),
                liquidations=sum(r['liquidations'] for r in rows),nearRuin=sum(r['finalEquity']<1 for r in rows))
        output.append(item)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--curves-output',type=Path)
    args=parser.parse_args()
    result=run(args.data)
    if args.curves_output:
        args.curves_output.parent.mkdir(parents=True,exist_ok=True)
        content=json.dumps(result,ensure_ascii=False,allow_nan=False)
        args.curves_output.write_text(content,encoding='utf-8')
        result['fullEvidenceSha256']=hashlib.sha256(content.encode()).hexdigest()
        result['fullEvidencePath']=str(args.curves_output.resolve())
    for row in result['results']:
        for case in row['cases']:
            for label in ('normal','stress'):
                curve=case[label].pop('equityCurve')
                case[label]['equityCurveSha256']=hashlib.sha256(json.dumps(curve,sort_keys=True).encode()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
