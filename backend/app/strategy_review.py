"""Comparación acotada de v6 y perfil agresivo, sin órdenes ni promoción de validaciones."""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from . import aggressive, bots, desk, market_data, strategy, training
from .config import SYMBOLS_BY_ID
from .economics import cost_profile
from .execution import closed_candles,atr_spread_multiple,signal_exit_enabled
from .indicators import atr_series
from .research import _bounded_history

ROOT=Path(__file__).resolve().parents[2]
SYMBOLS=('BTC','ETH','SOL','EURUSD','GBPUSD','SPY','NVDA')


def evaluate_lab(cfg,rows,*,proposed=False):
    update=aggressive.BOT_UPDATES[cfg.id] if proposed else {}
    interval=update.get('work_interval',cfg.work_interval)
    seconds=training.WORK_SECONDS[interval]
    higher_seconds=aggressive.HIGHER_SECONDS[cfg.id] if proposed else training.WORK_SECONDS[cfg.trend_interval]
    higher=strategy.resample(rows,higher_seconds)
    scores=aggressive.scores(cfg.id,rows,higher) if proposed else strategy.SCORE_FNS[cfg.id](rows,higher)
    atrs=atr_series(rows)
    sl=update.get('sl_atr',cfg.sl_atr);tp=update.get('tp_atr',cfg.tp_atr);trail=update.get('trail_atr',cfg.trail_atr)
    hold=update.get('max_hold_seconds',cfg.max_hold_seconds)
    embargo=max(3,round(hold/seconds))
    periods=dict(development=(max(250,int(len(rows)*.5)),int(len(rows)*.7)),
                 final=(min(len(rows),int(len(rows)*.7)+embargo),len(rows)))
    category=rows[0]['category']
    costs=cost_profile('lab',category)
    results={}
    for name,(lo,hi) in periods.items():
        normal=training.simulate(rows,scores,atrs,costs['spreadPct'],update.get('entry_threshold',cfg.entry_threshold),
            sl,tp,trail,max(1,round(hold/seconds)),max(1,round(update.get('cooldown_seconds',cfg.cooldown_seconds)/seconds)),
            1,1000,lo,hi,strategy.extension_series(rows),update.get('overext_atr',cfg.overext_atr),costs,
            min_atr_spread=(4 if proposed else 10) if cfg.id=='scalper' else 3,exit_on_signal=signal_exit_enabled(cfg.id),
            exit_policy=aggressive.exit_policy(cfg.id) if proposed else None)
        stress=training.simulate(rows,scores,atrs,costs['spreadPct'],update.get('entry_threshold',cfg.entry_threshold),
            sl,tp,trail,max(1,round(hold/seconds)),max(1,round(update.get('cooldown_seconds',cfg.cooldown_seconds)/seconds)),
            1,1000,lo,hi,strategy.extension_series(rows),update.get('overext_atr',cfg.overext_atr),training.stressed_costs(costs,1.5),
            min_atr_spread=(4 if proposed else 10) if cfg.id=='scalper' else 3,exit_on_signal=signal_exit_enabled(cfg.id),
            exit_policy=aggressive.exit_policy(cfg.id) if proposed else None)
        results[name]=dict(normal=training.compact_metrics(normal),stress=training.compact_metrics(stress))
    return dict(interval=interval,bars=len(rows),periods=periods,**results)


async def run(folder,revision='v1'):
    folder.mkdir(parents=True,exist_ok=True)
    manifest_path=folder/'manifest.json'
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(dict(frozenAt=time.time(),profile=aggressive.VERSION,
            configs=[asdict(c) for c in bots.ALL_BOTS],proposed=aggressive.BOT_UPDATES,symbols=SYMBOLS),indent=2),encoding='utf-8')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest=dict(manifest,revision=revision,actualProfile=aggressive.VERSION,
                  actualProposed=aggressive.BOT_UPDATES,priorResultsObserved=revision!='v1')
    now=manifest['frozenAt']
    async def history(symbol,interval):
        path=folder/(symbol+'-'+interval+'.json')
        if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
        sym=SYMBOLS_BY_ID[symbol]
        if sym.source=='binance':
            rows=await _bounded_history(sym.source_symbol,interval,365 if interval=='1h' else 45,6000,now)
        else:
            rows=closed_candles(await training._yahoo_history(sym.source_symbol,interval),training.WORK_SECONDS[interval],now)[-6000:]
        rows=[dict(r,category=sym.category) for r in rows]
        path.write_text(json.dumps(rows),encoding='utf-8')
        return rows
    results=[]
    for config in manifest['configs']:
        config['universe']=tuple(config['universe'])
        cfg=bots.BotConfig(**config)
        for symbol in SYMBOLS:
            if symbol not in cfg.universe: continue
            try:
                old_rows=await history(symbol,cfg.work_interval)
                new_interval=aggressive.BOT_UPDATES[cfg.id].get('work_interval',cfg.work_interval)
                new_rows=await history(symbol,new_interval)
                if min(len(old_rows),len(new_rows))<600:
                    results.append(dict(bot=cfg.id,symbol=symbol,error='Muestra insuficiente'))
                    continue
                # Ambos perfiles se comparan sobre las mismas fechas; el cambio
                # de intervalo no permite aprovechar una muestra distinta.
                lower=max(old_rows[0]['time'],new_rows[0]['time'])
                old_rows=[r for r in old_rows if r['time']>=lower]
                new_rows=[r for r in new_rows if r['time']>=lower]
                before=evaluate_lab(cfg,old_rows)
                after=evaluate_lab(cfg,new_rows,proposed=True)
                result=dict(bot=cfg.id,symbol=symbol,baseline=before,aggressive=after,
                    datasetHash=hashlib.sha256(json.dumps(new_rows,sort_keys=True).encode()).hexdigest())
                results.append(result)
                print(json.dumps(dict(bot=cfg.id,symbol=symbol,before=before['final']['normal']['pnl'],
                    after=after['final']['normal']['pnl'],stress=after['final']['stress']['pnl'],
                    trades=after['final']['normal']['trades'])),flush=True)
            except Exception as exc:
                results.append(dict(bot=cfg.id,symbol=symbol,error=type(exc).__name__+': '+str(exc)[:100]))
                print(cfg.id,symbol,type(exc).__name__,flush=True)
            (folder/('results.json' if revision=='v1' else 'results-'+revision+'.json')).write_text(json.dumps(dict(version='strategy-review-v1',manifest=manifest,
                results=results,promotionApplied=False,scope='Exploración; datos solapados con estudios anteriores. Cada serie usa riesgo fijo de 1 unidad; no sumar monedas ni interpretar como cartera.'),indent=2),encoding='utf-8')
    return results


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--revision',default='v1')
    args=parser.parse_args()
    try: await run(args.output,args.revision)
    finally: await market_data.close_client()


if __name__=='__main__': asyncio.run(main())
