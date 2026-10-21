"""Diagnóstico descriptivo de beneficios, fricción y bloqueo de entradas.

Solo lee libros locales; no cambia parámetros, no consulta un bróker y no emite
órdenes. Los cierres de laboratorio sin costes completos se muestran separados.
No suma USD, USDT y EUR, ni llama validación a una muestra de cierres positivos.
"""
from collections import Counter
import json
import math
import statistics
import time

from . import bots,desk,runtime_guard
from .book_stream import BOOKS


def metrics(trades):
    net=[t for t in trades if t.get('basis')=='net' and isinstance(t.get('pnl'),(int,float)) and math.isfinite(t['pnl'])]
    legacy=[t for t in trades if t.get('basis')!='net' and isinstance(t.get('pnl'),(int,float)) and math.isfinite(t['pnl'])]
    wins=[t['pnl'] for t in net if t['pnl']>0]
    losses=[-t['pnl'] for t in net if t['pnl']<0]
    pnl=sum(t['pnl'] for t in net)
    fees=sum(t.get('fees',0) for t in net)
    carry=sum(t.get('carry',0) for t in net)
    slip=sum(t.get('slippage',0) for t in net)
    avg_win=statistics.mean(wins) if wins else None
    avg_loss=statistics.mean(losses) if losses else None
    return dict(netClosed=len(net),legacyClosed=len(legacy),netPnl=round(pnl,6),
        legacyPnl=round(sum(t['pnl'] for t in legacy),6),wins=len(wins),losses=len(losses),
        winRate=len(wins)/len(net)*100 if net else None,
        profitFactor=sum(wins)/sum(losses) if losses else None,
        expectancy=pnl/len(net) if net else None,averageWin=avg_win,averageLoss=avg_loss,
        breakEvenWinRate=avg_loss/(avg_win+avg_loss)*100 if avg_win and avg_loss else None,
        fees=round(fees,6),carry=round(carry,6),slippageEstimate=round(slip,6),
        beforeExplicitCosts=round(pnl+fees+carry+slip,6),sampleTarget=30,
        riskOverruns=sum(t.get('initialRisk',0)>0 and t['pnl']<-1.5*t['initialRisk'] for t in net),
        sampleReady=len(net)>=30,validated=False)


def desk_trades(engine):
    with engine.connect() as con:
        payloads=con.execute("SELECT payload FROM trades WHERE json_extract(payload,'$.status')='closed'").fetchall()
    result=[]
    for payload, in payloads:
        trade=json.loads(payload)
        fee=trade.get('fee',0)+trade.get('exitFee',0)
        slip=engine.costs(trade)[1]
        # Reconstrucción del deslizamiento simulado; el spread sigue incluido
        # en los precios de referencia y no se inventa un histórico de libro.
        exit_value=(trade.get('finalExitQty',trade['qty'])*trade['exitPrice']
                    +sum(e['qty']*e['price'] for e in trade.get('partialExits',[])))
        slippage=trade['qty']*(trade['entry']-trade['entry']/(1+slip))+exit_value*slip/(1-slip)
        result.append(dict(pnl=trade['pnl'],basis='net',fees=fee,slippage=slippage,
                           initialRisk=trade.get('risk',0),
                           policy=trade.get('policyVersion','legacy'),strategy=trade.get('evidence',{}).get('tape',{}).get('strategy') or 'legacy'))
    return result


def opportunity_diagnostics(engine):
    now=time.time()
    eligible=[c for c in engine.state['candidates'] if c.get('eligible')]
    recent=[c for c in eligible if c.get('checkedAt',0)>=now-engine.candle_seconds]
    evaluated=[c for c in recent if isinstance(c.get('entryChecks'),dict)]
    def count(key): return sum(c['entryChecks'].get(key) is True for c in evaluated)
    spread=statistics.median(c['spreadBp'] for c in eligible)/10000 if eligible else None
    fee,slip=engine.fee,engine.slippage
    drag=(1-(1-slip)*(1-fee)/((1+slip)*(1+fee)))*100
    if spread is not None:
        drag=(1-(1-spread/2)*(1-slip)*(1-fee)/((1+spread/2)*(1+slip)*(1+fee)))*100
    # Frontera económica exacta para SL 1,5 ATR, TP 3 ATR y ratio neto 1,2.
    exit_factor=(1-slip)*(1-fee)
    friction=(1+slip)*(1+fee-exit_factor)
    min_atr=friction*(1+desk.MIN_NET_REWARD_RISK)/(exit_factor*(3-1.5*desk.MIN_NET_REWARD_RISK))*100
    return dict(eligible=len(eligible),reviewed=len(recent),evaluated=len(evaluated),
        signalPassed=count('signal'),costPassed=count('costs'),sizePassed=count('size'),
        signalBlocked=len(evaluated)-count('signal'),costBlocked=len(evaluated)-count('costs'),
        bothPassed=sum(c['entryChecks'].get('signal') and c['entryChecks'].get('size') for c in evaluated),
        minimumAtrPct=min_atr,roundTripPct=drag,medianAtrPct=statistics.median(c['signal']['atrPct'] for c in recent if c.get('signal')) if any(c.get('signal') for c in recent) else None,
        overlap=True,executionApproval=False,explanation='Los bloqueos pueden coincidir; superar señal y tamaño no equivale a autorización final de cartera y liquidez')


def snapshot():
    accounts=[]
    engines=dict(desk.DESKS)
    if desk.DESK: engines['crypto']=desk.DESK
    for key,engine in engines.items():
        trades=desk_trades(engine)
        by_strategy={name:metrics([t for t in trades if t['strategy']==name]) for name in sorted({t['strategy'] for t in trades})}
        accounts.append(dict(id=key,source='desk',name='Mesa '+{'crypto':'Cripto','forex':'Forex','stocks':'Acciones'}[key],
            currency=engine.currency,initial=engine.initial,equity=engine.equity(),
            performance=metrics(trades),currentPolicy=metrics([t for t in trades if t['policy']==desk.POLICY_VERSION]),
            byStrategy=by_strategy,opportunities=opportunity_diagnostics(engine),efficiency=dict(engine.efficiency)))
    if bots.BOTS:
        with bots._conn() as con:
            rows=con.execute('''SELECT bot,pnl,execution_costs,entry_fee,exit_fee,carry_cost,quote_to_account,qty,economic_risk_dist
                                FROM bot_trades WHERE status='closed' ''').fetchall()
        for key,bot in list(bots.BOTS.items()):
            trades=[]
            for bot_id,pnl,costs,entry_fee,exit_fee,carry,conversion,qty,risk_dist in rows:
                if bot_id!=key: continue
                rate=conversion if conversion is not None else 1.0
                trades.append(dict(pnl=pnl,basis='net' if costs else 'legacy',
                                   fees=((entry_fee or 0)+(exit_fee or 0))*rate,carry=(carry or 0)*rate,
                                   initialRisk=(risk_dist or 0)*qty*rate))
            accounts.append(dict(id=key,source='lab',name=bot.cfg.name,
                currency=(bot.session or {}).get('currency','USD'),initial=bot.cfg.initial_balance,
                equity=bot.balance+sum(p.unrealized() for p in bot.positions.values()),performance=metrics(trades),
                monitoring=dict(bot.risk_monitor)))
    return dict(updatedAt=time.time(),accounts=accounts,runtime=runtime_guard.status(),marketFeed=BOOKS.status(),
        scope='Cuentas independientes; importes en su propia divisa, sin total mixto',
        interpretation='Expectativa y factor de beneficio descriptivos; no prueban una ventaja futura',
        priorities=[dict(id='edge',title='Validar una expectativa neta positiva',detail='Mantener el riesgo experimental hasta obtener evidencia fuera de muestra y seguimiento futuro'),
                    dict(id='cost',title='Evaluar horizontes con mayor movimiento por operación',detail='Comparación fija de 1 h y 4 h con un año de datos; las tarifas reales aún no están verificadas'),
                    dict(id='execution',title='Medir una ejecución reproducible',detail='Cotizaciones bid/ask y costes de cuenta para forex y acciones antes de aumentar el capital')])
