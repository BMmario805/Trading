"""Current indicative EUR conversion for risk, separate from session accounting.

Public data only. No order gateway. Scheduling never delays protective exits.
The USD/JPY references are indicative, not executable exchange rates.
"""
import asyncio
import math
import time

from . import market_data
from .config import Symbol

MAX_AGE=120
REFRESH_SECONDS=60
_rates={}
_task=None
_last_attempt=0.0


def get(currency,now=None):
    now=time.time() if now is None else now
    if currency=='EUR': return dict(rate=1.0,at=now,source='EUR account currency',indicative=False)
    row=_rates.get(currency)
    return dict(row) if row and -5<=now-row['at']<=MAX_AGE else None


def snapshot():
    return {currency:get(currency) for currency in ('EUR','USDT','USD','JPY')}


def _fresh_tick(tick):
    return (isinstance(tick,dict) and type(tick.get('price')) in (int,float)
            and math.isfinite(tick['price']) and tick['price']>0
            and type(tick.get('time')) in (int,float) and math.isfinite(tick['time'])
            and -5<=time.time()-tick['time']<=MAX_AGE)


async def refresh():
    async def safe(call):
        try: return await asyncio.wait_for(call,timeout=5)
        except asyncio.CancelledError: raise
        except Exception: return None
    eurusdt,eurusd,usdjpy=await asyncio.gather(
        safe(market_data._binance_get('/api/v3/ticker/24hr',{'symbol':'EURUSDT'})),
        safe(market_data.get_price(Symbol('EURUSD','EUR/USD','forex','yahoo','EURUSD=X'))),
        safe(market_data.get_price(Symbol('USDJPY','USD/JPY','forex','yahoo','JPY=X'))))
    if isinstance(eurusdt,dict):
        try:
            bid=float(eurusdt['bidPrice']);stamp=float(eurusdt['closeTime'])/1000
            if math.isfinite(bid) and bid>0 and -5<=time.time()-stamp<=MAX_AGE:
                _rates['USDT']=dict(rate=1/(bid*.999*.9998),at=stamp,
                    source='Binance EURUSDT bid with paper conversion cost buffer',indicative=True)
        except (KeyError,ValueError,TypeError,OverflowError): pass
    if _fresh_tick(eurusd):
        _rates['USD']=dict(rate=1/(eurusd['price']*.999),at=eurusd['time'],
            source='Yahoo EURUSD reference with 0.1% risk buffer',indicative=True)
        if _fresh_tick(usdjpy):
            _rates['JPY']=dict(rate=1/(eurusd['price']*usdjpy['price']*.998),
                at=min(eurusd['time'],usdjpy['time']),
                source='Yahoo EURUSD and USDJPY references with 0.2% risk buffer',indicative=True)
    return snapshot()


def schedule():
    global _task,_last_attempt
    if _task is not None and not _task.done(): return
    if time.monotonic()-_last_attempt<REFRESH_SECONDS: return
    _last_attempt=time.monotonic()
    try: loop=asyncio.get_running_loop()
    except RuntimeError: return
    _task=loop.create_task(refresh(),name='current-risk-fx')


async def stop():
    global _task
    if _task is not None:
        _task.cancel()
        await asyncio.gather(_task,return_exceptions=True)
    _task=None
