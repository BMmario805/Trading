"""Reinicio local explícito de sesiones; ejecutar con el backend detenido.

Archiva los libros, cierra únicamente operaciones de estos bots en la DEMO
autorizada y comienza desde 100 EUR por cuenta. No cambia el saldo del bróker.
"""

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import bots, desk, gemini_agent, market_data, mt5_demo, mt5_performance, opportunities
from .mt5_gateway import DemoOnlyError


def archive_database(source, folder):
    source=Path(source)
    if not source.exists():
        return
    with closing(sqlite3.connect(source)) as con, closing(sqlite3.connect(folder/source.name)) as backup:
        con.backup(backup)


def reset_lab(session):
    with bots._conn() as con:
        for table in ('bot_trades','bot_state','bot_params','bot_training_attempts','bot_learning','bot_model','sentiment_history','bot_sessions'):
            exists=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
            if exists: con.execute(f'DELETE FROM {table}')
        for cfg in bots.ALL_BOTS:
            con.execute('INSERT INTO bot_state VALUES (?,?,1)',(cfg.id,100.0))
            con.execute('INSERT INTO bot_sessions VALUES (?,?)',(cfg.id,json.dumps(session)))


def prepare_demo(api):
    cfg=json.loads(mt5_demo.CONFIG_PATH.read_text(encoding='utf-8'))
    if not api.initialize(cfg['terminal'],timeout=10000):
        raise DemoOnlyError('No se puede conectar con MT5 para reiniciar')
    engine=mt5_demo.DemoEngine(cfg,api)
    try:
        account,_=engine.gateway.account(trading=True)
        if account.currency!='EUR':
            raise DemoOnlyError('El reinicio requiere una demo denominada en EUR')
        engine.update_clock_skew()
        positions=engine.gateway.positions()
        orders=api.orders_get()
        if orders is None:
            raise DemoOnlyError('No se pueden comprobar las órdenes pendientes')
        if any(p.magic not in mt5_demo.MAGICS.values() for p in positions) or any(o.magic not in mt5_demo.MAGICS.values() for o in orders):
            raise DemoOnlyError('Hay posiciones u órdenes ajenas a estos bots; se conserva la cuenta para revisión')
        rates={}
        for symbol in ('EURUSD','USDJPY'):
            tick=engine.gateway.tick(cfg['symbols'][symbol],time.time())
            rates[symbol]=(tick.bid+tick.ask)/2
        return engine,positions,list(orders),rates
    except BaseException:
        engine.worker.shutdown(wait=True,cancel_futures=True)
        raise


def close_demo(engine, positions, orders):
    api=engine.api
    for order in orders:
        result=engine.submit(f'reset-cancel:{order.ticket}',order.symbol,
                             dict(action=api.TRADE_ACTION_REMOVE,order=order.ticket))
        if result is None or result.retcode!=api.TRADE_RETCODE_DONE:
            raise DemoOnlyError('Una cancelación no se ha confirmado; no se limpia el libro')
    for position in positions:
        request=engine.gateway.close_request(position,time.time())
        result=engine.submit(f'reset-close:{position.ticket}:{position.volume}',position.symbol,request)
        if result is None or result.retcode not in (api.TRADE_RETCODE_DONE,api.TRADE_RETCODE_DONE_PARTIAL):
            raise DemoOnlyError('Un cierre no se ha confirmado; no se limpia el libro')
    remaining=engine.gateway.positions()
    pending=api.orders_get()
    if remaining or pending is None or pending:
        raise DemoOnlyError('Quedan operaciones u órdenes: el reinicio no se da por terminado')


def reset_demo(engine,now):
    account,_=engine.gateway.account()
    deals=engine.api.history_deals_get(0,int(now)+86400)
    if deals is None:
        raise DemoOnlyError('No se ha podido fijar el corte del historial de la nueva sesión')
    cutoff=max([int((now+engine.clock_skew)*1000)]+[int(getattr(d,'time_msc',d.time*1000))+1 for d in deals])
    session=dict(initial=100.0,currency='EUR',startedAt=now,cutoffMsc=cutoff)
    with mt5_demo.connection() as con:
        con.execute('DELETE FROM state WHERE key LIKE ?',(engine.owner+'%',))
        # Los intentos inciertos se conservan: borrarlos podría duplicar órdenes.
        con.execute("DELETE FROM intents WHERE key LIKE ? AND status NOT IN ('pending','uncertain')",(engine.owner+'%',))
    with mt5_performance.connection() as con:
        for table in ('mt5_deals','mt5_snapshots','mt5_sync','mt5_sessions'):
            con.execute(f'DELETE FROM {table} WHERE owner=?',(engine.owner,))
        con.execute('INSERT INTO mt5_sessions VALUES (?,?)',(engine.owner,json.dumps(session)))
    mt5_performance.ingest(engine.owner,account,[],[],now,True)
    mt5_demo.save(engine.owner+'enabled',True)
    return session


async def main():
    # Este módulo no se expone como botón ni endpoint. El operador detiene el
    # proceso propietario antes de invocarlo, evitando escritores concurrentes.
    import MetaTrader5 as api
    data=await opportunities.snapshot()
    if not desk.fresh_snapshot(data):
        raise ValueError('Sin cotización fresca para financiar las cuentas')
    eur=data['books']['EURUSDT']
    bid,ask=float(eur['bidPrice']),float(eur['askPrice'])
    if not 0<bid<=ask:
        raise ValueError('Cambio EUR/USDT inválido')
    engine,positions,orders,rates=prepare_demo(api)
    folder=desk.DB_PATH.parent/f'accounts-before-reset-{time.time_ns()}'
    folder.mkdir()
    try:
        for path in (bots.DB_PATH,desk.DB_PATH,mt5_demo.DB_PATH,gemini_agent.DB_PATH):
            archive_database(path,folder)
        close_demo(engine,positions,orders)
        now=time.time()
        session=dict(initial=100.0,currency='EUR',startedAt=now,
                     quoteToEuro={'EUR':1.0,'USD':1/rates['EURUSD'],
                                  'USDT':2/(bid+ask),'JPY':1/(rates['EURUSD']*rates['USDJPY'])},
                     ratePolicy='fixed-at-session-start')
        reset_lab(session)
        if gemini_agent.DB_PATH.exists():
            with gemini_agent.connection() as con:
                con.execute('DELETE FROM observations')
                con.execute('DELETE FROM documents')
        # Desk financia su libro Spot USDT con el mismo capital de 100 EUR.
        kitchen=desk.Desk()
        await kitchen.reset()
        demo=reset_demo(engine,time.time())
        report=dict(startedAt=now,initialEURPerAccount=100,labBots=[c.id for c in bots.ALL_BOTS],
                    deskInitialUSDT=kitchen.initial,demo=demo,closedDemoPositions=len(positions),
                    cancelledDemoOrders=len(orders),archive=str(folder))
        (folder/'reset-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2))
    finally:
        engine.worker.shutdown(wait=True,cancel_futures=True)
        api.shutdown()
        await market_data.close_client()


if __name__=='__main__':
    asyncio.run(main())
