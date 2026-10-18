"""Diagnóstico cronológico de Mesa Siete; no autoriza dinero real.

Entrada en apertura siguiente, sin vela futura; stop antes de objetivo en una
vela ambigua. Cada símbolo usa una cuenta independiente, no una cartera conjunta.
No reproduce profundidad, selección histórica, latencia ni riesgo global vivo.
"""
from collections import Counter
from . import desk
from .indicators import atr,ema,rsi
from .economics import planned_trade


def legacy_tape(rows,now):
    closes=[c['close'] for c in rows]
    e20,e50=ema(closes,20),ema(closes,50)
    a=atr(rows)
    strength=rsi(closes)[-1]
    if not a or strength is None: return None
    trend=(e20[-1]>=e50[-1]-.5*a and closes[-1]>=e50[-1]-.5*a) or (closes[-1]<=e20[-1]+1.5*a and 15<=strength<=75)
    return dict(bar=rows[-1]['time'],close=closes[-1],ema20=e20[-1],ema50=e50[-1],atr=a,rsi=strength,trend=trend,
                entryAllowed=True,pullback=min(c['low'] for c in rows[-5:])<=e20[-1]+1.75*a,
                momentum=closes[-1]>closes[-2] and closes[-1]>=e20[-1],strategy='legacy')


def replay(rows,info,*,legacy=False,cost_multiple=1,start=None,end=None):
    cash=100.0
    position=None
    trades=[]
    cooldown=0
    peak=100.0
    drawdown=0.0
    fee=desk.FEE*cost_multiple
    slip=desk.SLIPPAGE*cost_multiple
    spread=.0002*cost_multiple
    day=None
    day_equity=100.0
    stopped=False
    for i in range(250,len(rows)):
        bar=rows[i]
        now=bar['time']
        if start is not None and now<start: continue
        if end is not None and now>=end: break
        equity=cash+(position['qty']*bar['open']*(1-spread/2)*(1-slip)*(1-fee) if position else 0)
        if now//86400!=day:
            day=now//86400;day_equity=equity;stopped=False
        if equity<day_equity*.95: stopped=True
        had_position=position is not None
        if position:
            bid=bar['open']*(1-spread/2)
            low,high=bar['low']*(1-spread/2),bar['high']*(1-spread/2)
            exit_bid=None
            reason=None
            if stopped: exit_bid=bid;reason='daily-stop'
            elif bid<=position['sl']: exit_bid=bid;reason='gap-stop'
            elif low<=position['sl']: exit_bid=position['sl'];reason='stop'
            elif high>=position['tp']: exit_bid=max(bid,position['tp']);reason='target'
            elif now-position['opened']>=position['hold']: exit_bid=bid;reason='time'
            if exit_bid is not None:
                proceeds=position['qty']*exit_bid*(1-slip)*(1-fee)
                pnl=proceeds-position['notional']-position['fee']
                cash+=proceeds
                trades.append(dict(pnl=pnl,reason=reason,strategy=position['strategy'],time=now))
                cooldown=now+(0 if legacy else desk.CANDLE_SECONDS*(4 if pnl<=0 else 1))
                position=None
            else:
                bid=bar['close']*(1-spread/2)
                position['best']=max(position['best'],bid)
                trigger=.7 if legacy else 1.0
                if bid>=position['entry']+trigger*position['dist']:
                    proposed=max(position['sl'],(position['notional']+position['fee'])/(position['qty']*(1-slip)*(1-fee)),
                                 position['best']-(.9 if legacy else 1.5)*position['atr'])
                    if proposed<bid: position['sl']=proposed
        # Una salida intravela nunca permite reutilizar su efectivo en la apertura
        # anterior de esa misma vela. Las señales solo ven cierres anteriores.
        if not had_position and not stopped and now>=cooldown:
            history=rows[i-249:i]
            a=legacy_tape(history,now) if legacy else desk.tape(history,now)
            if not a: continue
            ask=bar['open']*(1+spread/2)
            ext=(ask-a['ema20'])/a['atr']
            lo,hi=(15,80) if legacy else (desk.ENTRY_RSI_MIN,desk.ENTRY_RSI_MAX)
            ext_lo,ext_hi=(-4,2.25) if legacy else (desk.ENTRY_EXTENSION_MIN,desk.ENTRY_EXTENSION_MAX)
            setup=a['pullback'] or a.get('momentum') if legacy else a['entryAllowed']
            if not (a['trend'] and setup and lo<=a['rsi']<=hi and ext_lo<=ext<=ext_hi
                    and abs(ask-a['close'])<=2*a['atr']): continue
            if legacy:
                fill=ask*(1+slip);stop=fill-1.2*a['atr'];target=fill+1.8*a['atr']
                unit=fill*(1+fee)-stop*(1-slip)*(1-fee)
                qty=min(cash/6/fill,cash*.01/unit,cash/(fill*(1+fee)))
                plan=planned_trade(fill,stop,target,qty,fee_rate=fee,exit_slippage=slip)
                if not plan['viable'] or plan['netRewardRisk']<1: continue
                order=dict(entry=fill,sl=stop,tp=target,qty=qty,notional=qty*fill,fee=qty*fill*fee)
            else:
                order=desk.size_order(cash,cash,ask,a['atr'],info,fee=fee,slippage=slip)
                if order is None: continue
            cash-=order['notional']+order['fee']
            position=dict(order,opened=now,dist=order['entry']-order['sl'],atr=a['atr'],best=order['entry'],
                          hold=2700 if legacy else desk.MAX_HOLD_S,strategy=a['strategy'])
            exit_bid=None
            if bar['low']*(1-spread/2)<=position['sl']:
                exit_bid=position['sl'];reason='stop'
            elif bar['high']*(1-spread/2)>=position['tp']:
                exit_bid=position['tp'];reason='target'
            if exit_bid is not None:
                proceeds=position['qty']*exit_bid*(1-slip)*(1-fee)
                pnl=proceeds-position['notional']-position['fee']
                cash+=proceeds
                trades.append(dict(pnl=pnl,reason=reason,strategy=position['strategy'],time=now))
                cooldown=now+(0 if legacy else desk.CANDLE_SECONDS*(4 if pnl<=0 else 1))
                position=None
        equity=cash+(position['qty']*bar['close']*(1-spread/2)*(1-slip)*(1-fee) if position else 0)
        peak=max(peak,equity)
        drawdown=max(drawdown,(peak-equity)/peak*100)
    # La posición final se valora al último cierre del tramo, incluyendo costes.
    evaluated=[b for b in rows if (start is None or b['time']>=start) and (end is None or b['time']<end)]
    if position and evaluated:
        cash+=position['qty']*evaluated[-1]['close']*(1-spread/2)*(1-slip)*(1-fee)
    gains=sum(max(0,t['pnl']) for t in trades)
    losses=-sum(min(0,t['pnl']) for t in trades)
    return dict(returnPct=cash-100,closed=len(trades),wins=sum(t['pnl']>0 for t in trades),
                profitFactor=gains/losses if losses else None,maxDrawdownPct=drawdown,
                netRealized=sum(t['pnl'] for t in trades),strategies=dict(Counter(t['strategy'] for t in trades)),
                openAtEnd=position is not None)
