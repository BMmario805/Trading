"""Comparación exploratoria por mesa con salidas parciales y costes explícitos."""
import asyncio
import json
from pathlib import Path
import time

from . import aggressive,desk,market_data,strategy,training
from .cash_markets import CashMarketFeed
from .config import SYMBOLS_BY_ID
from .exit_experiments import replay,ExitPolicy
from .research import _bounded_history

ROOT=Path(__file__).resolve().parents[2]


async def main():
    folder=ROOT/'.runtime/aggressive-research-20261021'
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    now=manifest['frozenAt']
    exchange=await market_data._binance_get('/api/v3/exchangeInfo',{})
    metadata={s['symbol']:s for s in exchange['symbols']}
    results=[]
    for market,symbols,interval in (('crypto',('BTC','ETH','SOL'),'1h'),('forex',('EURUSD','GBPUSD'),'15m'),('stocks',('SPY','NVDA'),'15m')):
        feed=CashMarketFeed(market) if market!='crypto' else None
        seconds=training.WORK_SECONDS[interval]
        for symbol in symbols:
            try:
                path=folder/(symbol+'-'+interval+'.json')
                if path.exists(): rows=json.loads(path.read_text(encoding='utf-8'))
                else:
                    rows=await training._yahoo_history(SYMBOLS_BY_ID[symbol].source_symbol,interval)
                    rows=desk.closed_candles(rows,seconds,now)[-6000:]
                    path.write_text(json.dumps(rows),encoding='utf-8')
                start=rows[int(len(rows)*.7)]['time']
                info=metadata[symbol+'USDT'] if not feed else dict(filters=[dict(filterType='LOT_SIZE',stepSize='.000001',minQty='.000001'),dict(filterType='MIN_NOTIONAL',minNotional='1')])
                analyses={name:{i:function(rows[max(0,i-250):i],rows[i]['time'],seconds) for i in range(250,len(rows))}
                          for name,function in (('baseline',desk.baseline_tape),('aggressive',aggressive.desk_signal))}
                for index,a in analyses['baseline'].items():
                    if a:
                        # Congelar los límites v6 del comparador, aunque el motor
                        # actual amplíe RSI/extensión en su perfil nuevo.
                        spread=feed.spread if feed else .0002
                        extension=(rows[index]['open']*(1+spread/2)-a['ema20'])/a['atr']
                        a['entryAllowed']=a['entryAllowed'] and 35<=a['rsi']<=72 and -1<=extension<=1.5
                variants={}
                for name in ('baseline','aggressive'):
                    new=name=='aggressive'
                    costs=dict(fee_rate=feed.fee if feed else desk.FEE,slippage_rate=feed.slippage if feed else desk.SLIPPAGE,spread_pct=feed.spread if feed else .0002)
                    options=dict(partial=not bool(feed),stop_atr=2 if new or not feed else 1.5,
                        target_atr=6 if new else 4 if not feed else 3,
                        risk_fraction=.0075 if new else .005,notional_fraction=.25 if new else 1/6,
                        min_net_rr=(2 if market=='stocks' else 1.5) if new else 1.2)
                    policy=ExitPolicy(name,48*3600 if not feed else 6*3600,not bool(feed),2.5 if new or not feed else 1.5,2 if new and feed else 1)
                    common=dict(start_time=start,analyses=analyses[name],order_options=options,
                        partial_fraction=1/3 if new else .5,trail_after_r=2 if not feed else 0,residual_stop_r=-.5 if not feed else None,**costs)
                    variants[name]=dict(normal=replay(rows,info,seconds,policy,**common),
                                        stress=replay(rows,info,seconds,policy,cost_multiple=1.5,**common))
                result=dict(market=market,symbol=symbol,interval=interval,bars=len(rows),**variants)
                results.append(result)
                print(json.dumps(dict(market=market,symbol=symbol,before=variants['baseline']['normal']['pnl'],
                    after=variants['aggressive']['normal']['pnl'],stress=variants['aggressive']['stress']['pnl'])),flush=True)
            except Exception as exc:
                results.append(dict(market=market,symbol=symbol,error=type(exc).__name__+': '+str(exc)[:100]))
            (folder/'desks-v3.json').write_text(json.dumps(dict(results=results,promotionApplied=False,
                scope='Muestra exploratoria conocida. 100 unidades por símbolo; no cartera conjunta. Taker, sin suponer fills maker.'),indent=2),encoding='utf-8')
    await market_data.close_client()


if __name__=='__main__':asyncio.run(main())
