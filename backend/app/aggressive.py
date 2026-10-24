"""Perfil experimental más activo, causal y compartido por paper/DEMO/replay.

Más disparadores de continuación y objetivos amplios. No presupone una ventaja
económica: las validaciones y límites de cartera siguen siendo independientes.
"""
import math

from .indicators import adx_series, atr_series, ema, rsi, sma, zscore_series
from .flow import chaikin_money_flow
from .systems import opening_range_series, squeeze_release_series

VERSION = 'aggressive-adaptive-v3'
CAPACITY_VERSION = 'aggressive-capacity-v4'
DESK_CAPACITY = dict(maxPositions=6,maxNotionalFraction=.35,riskFraction=.015,totalRiskFraction=.06,scanSeconds=15)
LAB_CAPACITY = dict(probationSize=.50,maxProbationPositions=4)
HIGHER_SECONDS = dict(scalper=900,swing=14400,trend=14400,meanrev=86400,orb=900,squeeze=14400,flow=14400)
BOT_UPDATES = {
    'scalper':dict(work_interval='5m',trend_interval='15m',entry_threshold=.55,sl_atr=1.6,tp_atr=4.8,trail_atr=1.8,max_hold_seconds=4*3600,cooldown_seconds=600,overext_atr=2.25),
    'swing':dict(trend_interval='4h',entry_threshold=.55,sl_atr=2.0,tp_atr=7.0,trail_atr=2.6,overext_atr=2.5),
    'trend':{},  # La variante 20/55+12 ATR empeoró cuatro de seis comparaciones.
    'meanrev':{},  # Ampliar extremos o el objetivo empeoró la reversión neta.
    'orb':dict(entry_threshold=.60,sl_atr=1.25,tp_atr=5.0,trail_atr=2.0,overext_atr=2.5),
    'squeeze':dict(entry_threshold=.60,sl_atr=2.0,tp_atr=8.0,trail_atr=2.8,max_hold_seconds=72*3600,overext_atr=2.75),
    'flow':{},
}
ACTIVE_SIGNALS=('scalper','swing','orb','squeeze')
LEGACY_MAX_HOLD=dict(scalper=2700,swing=432000,trend=864000,meanrev=259200,orb=18000,squeeze=172800,flow=172800)


def exit_policy(bot_id):
    if bot_id not in ACTIVE_SIGNALS:
        return dict(version='preserved-v6',trailStartR=1.0,tightenAtR=2.0,tightenMultiple=.75)
    return dict(version=VERSION,trailStartR=2.0 if bot_id in ('swing','trend','squeeze','flow') else 1.25,
                tightenAtR=4.0,tightenMultiple=.85)


def _clip(value):
    return max(-1.0,min(1.0,value))


def _higher_votes(rows,higher,seconds):
    values=[c['close'] for c in higher]
    fast,slow=ema(values,20),ema(values,50)
    differences=[b['time']-a['time'] for a,b in zip(rows[:20],rows[1:21]) if b['time']>a['time']]
    work=min(differences) if differences else 60
    out=[];j=-1
    for row in rows:
        while j+1<len(higher) and higher[j+1]['time']+seconds<=row['time']+work:
            j+=1
        out.append(1 if j>=49 and fast[j]>slow[j] and values[j]>slow[j]
                   else -1 if j>=49 and fast[j]<slow[j] and values[j]<slow[j] else 0)
    return out


def factors(bot_id,rows,higher,*,higher_seconds=None):
    """Régimen 40%, evento de precio 40%, participación 20%; sin evento, cero."""
    if not rows: return []
    closes=[c['close'] for c in rows]
    e5,e9,e20,e50,e200=(ema(closes,n) for n in (5,9,20,50,200))
    strength,volatility=adx_series(rows),atr_series(rows)
    money=chaikin_money_flow(rows)
    htf=_higher_votes(rows,higher,higher_seconds or HIGHER_SECONDS[bot_id])
    r2=rsi(closes,2) if bot_id=='meanrev' else []
    z=zscore_series(closes) if bot_id=='meanrev' else []
    release=squeeze_release_series(rows) if bot_id=='squeeze' else []
    opening=opening_range_series(rows,range_minutes=5)[0] if bot_id=='orb' else []
    out=[]
    for i,c in enumerate(rows):
        item=dict(regime=0.0,trigger=0.0,flow=0.0,score=0.0,setup=None)
        out.append(item)
        if i<60 or (bot_id=='trend' and i<200) or volatility[i] is None or volatility[i]<=0: continue
        a=volatility[i]; price=c['close']
        side=1 if e20[i]>e50[i] and price>e50[i] else -1 if e20[i]<e50[i] and price<e50[i] else 0
        body=c['close']-c['open']
        previous=rows[max(0,i-20):i]
        volume=sum(max(0,r.get('volume',0)) for r in previous)/len(previous)
        rv=max(0,c.get('volume',0))/volume if volume>0 else None
        flow=_clip((money[i] or 0)*5)
        regime=trigger=0.0;setup=None
        if bot_id=='meanrev':
            if i<200 or strength[i] is None or strength[i]>=28: continue
            side=1 if price>e200[i] else -1
            extreme=any((r2[j] is not None and r2[j]<(12 if side>0 else -1))
                or (r2[j] is not None and side<0 and r2[j]>88)
                or (z[j] is not None and z[j]*side<-1.75) for j in range(i-3,i+1))
            wick=(min(price,c['open'])-c['low']) if side>0 else (c['high']-max(price,c['open']))
            confirm=(body*side>0 and (price-closes[i-1])*side>0
                     and ((price-e5[i])*side>0 or wick>=max(abs(body),.35*(c['high']-c['low']))))
            if extreme and confirm:
                regime=trigger=float(side);setup='extreme-reclaim'
        elif bot_id=='orb':
            side=opening[i]
            crossing=side and opening[i-1]!=side
            retest=side and body*side>0 and (price-closes[i-1])*side>0 and abs(rows[i-1]['close']-e9[i-1])<.75*a
            if side and (crossing or retest) and body*side>0 and (rv is None or rv>=1.25) and htf[i]*side>=0:
                regime=trigger=float(side);setup='opening-drive'
        else:
            if not side or htf[i]*side<0: continue
            slope=(e50[i]-e50[i-3])*side
            if slope<=0 or strength[i] is None or strength[i]<16: continue
            if body*side<=0: continue
            n=8 if bot_id=='scalper' else 10 if bot_id in ('flow','squeeze') else 20
            prior=rows[i-n:i]
            breakout=price>max(r['high'] for r in prior) if side>0 else price<min(r['low'] for r in prior)
            touched=any((r['low']<=e20[j]+.35*a if side>0 else r['high']>=e20[j]-.35*a)
                        for j,r in enumerate(rows[max(0,i-4):i],start=max(0,i-4)))
            reclaim=(touched and (price-e9[i])*side>0 and (price-closes[i-1])*side>0
                     and (closes[i-1]-e9[i-1])*side<=.25*a)
            volume_ok=rv is None or rv>=.85
            event=breakout or reclaim
            if bot_id=='trend':
                event=breakout or (reclaim and (price-e200[i])*side>0)
            elif bot_id=='squeeze':
                ranges=[r['high']-r['low'] for r in rows[i-8:i]]
                compressed=sum(ranges)/len(ranges)<.85*a
                expansion=breakout and compressed and abs(body)>=.5*a
                event=release[i]*side>0 or expansion
                volume_ok=rv is None or rv>=1.1
            elif bot_id=='flow':
                event=event and flow*side>=.20
            if event and volume_ok:
                regime=trigger=float(side)
                setup='compression-break' if bot_id=='squeeze' else 'momentum-break' if breakout else 'pullback-reclaim'
        if trigger:
            score=.4*regime+.4*trigger+.2*flow
            item.update(regime=regime,trigger=trigger,flow=flow,score=_clip(score),setup=setup)
    return out


def scores(bot_id,rows,higher):
    # La primera comparación empeoró estas dos familias al ampliar entradas.
    # Conservar sus disparadores contrastados; se evalúan cambios de salida.
    if bot_id not in ACTIVE_SIGNALS:
        from .strategy import SCORE_FNS
        return SCORE_FNS[bot_id](rows,higher)
    return [f['score'] for f in factors(bot_id,rows,higher)]


def desk_signal(rows,now,seconds,market='crypto'):
    """Spot largo: continuación, ruptura temprana y recuperación con rechazo."""
    from .execution import closed_candles
    bars=closed_candles(rows,seconds,now)
    if len(bars)<60 or now-bars[-1]['time']>3*seconds: return None
    closes=[c['close'] for c in bars]
    e9,e20,e50=(ema(closes,n) for n in (9,20,50))
    av=atr_series(bars)[-1]
    rs=rsi(closes)[-1]
    adx=adx_series(bars)[-1]
    if av is None or av<=0 or rs is None: return None
    c=bars[-1]
    bullish=c['close']>c['open'] and c['close']>bars[-2]['close']
    trend=e20[-1]>=e50[-1] and e50[-1]>=e50[-4] and closes[-1]>=e50[-1]
    volume=sum(r.get('volume',0) for r in bars[-21:-1])/20
    rv=c.get('volume',0)/volume if volume>0 else 1.0
    touched=min(r['low'] for r in bars[-5:])<=e20[-1]+.35*av
    pullback=trend and touched and bullish and closes[-1]>=e9[-1] and closes[-2]<=e9[-2]+.35*av
    breakout=trend and bullish and closes[-1]>max(r['high'] for r in bars[-11:-1]) and rv>=1.2
    wick=min(c['open'],c['close'])-c['low']
    recovery=(bullish and wick>=.35*(c['high']-c['low']) and closes[-1]>=e20[-1]
              and e20[-1]>=e20[-3] and min(r['low'] for r in bars[-4:-1])<=e20[-1])
    setup='breakout' if breakout else 'trend-pullback' if pullback else 'recovery' if recovery else None
    # Aumentar exposición solo con una tendencia confirmada en el marco mayor.
    # Las barras agregadas incompletas nunca autorizan el filtro.
    higher_seconds=4*seconds
    from .strategy import resample
    counts={}
    for b in bars:
        bucket=b['time']//higher_seconds*higher_seconds
        counts[bucket]=counts.get(bucket,0)+1
    higher=[b for b in resample(bars,higher_seconds) if counts[b['time']]==4 and b['time']+higher_seconds<=now]
    higher_close=[b['close'] for b in higher]
    higher_ema=ema(higher_close,50)
    regime=(len(higher)>=54 and higher_close[-1]>=higher_ema[-1] and higher_ema[-1]>=higher_ema[-4]
            and adx is not None and adx>=18 and e20[-1]-e50[-1]>=.1*av)
    setup=setup if regime else None
    return dict(bar=c['time'],close=c['close'],ema20=e20[-1],ema50=e50[-1],atr=av,rsi=rs,
        trend=trend or recovery,pullback=pullback,momentum=breakout,entryAllowed=setup is not None,
        strategy=setup,relativeVolume=rv,adx=adx,higherRegimeConfirmed=bool(regime),rows=bars[-90:],profile=VERSION)
