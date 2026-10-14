"""Evaluación reproducible sin modificar parámetros ni la cuenta simulada.

Desde la raíz: backend/.venv/Scripts/python backend/evaluate.py --bot swing --symbol BTC
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from app import bots, market_data, training
from app.config import SYMBOLS_BY_ID
from app.execution import closed_candles
from app.indicators import atr_series
from app.strategy import TREND_STEP, resample, score_series


async def evaluate(bot_id, symbol, output):
    cfg = next(c for c in bots.ALL_BOTS if c.id == bot_id)
    sym = SYMBOLS_BY_ID[symbol]
    if symbol not in cfg.universe:
        raise ValueError('El símbolo no pertenece al universo del bot')
    seconds = training.WORK_SECONDS[cfg.work_interval]
    try:
        data = closed_candles(await training.fetch_history(bot_id, sym, cfg.work_interval), seconds, time.time())
        if len(data) < 500:
            raise ValueError(f'Histórico insuficiente: {len(data)} velas')
        print(f'{len(data)} velas cerradas descargadas; evaluando...', flush=True)
        def run():
            scores = score_series(bot_id, data, resample(data, TREND_STEP[bot_id]))
            return training.optimize(
                bot_id, data, scores, atr_series(data, cfg.atr_period), bots.SPREAD_PCT[sym.category],
                max(1, cfg.max_hold_seconds // seconds), max(1, cfg.cooldown_seconds // seconds),
                cfg.initial_balance * cfg.risk_pct, cfg.initial_balance * cfg.max_leverage,
            )
        result = await asyncio.to_thread(run)
        result.update(bot=bot_id, symbol=symbol, firstBar=data[0]['time'], lastBar=data[-1]['time'],
                      evaluatedAt=int(time.time()), source=training.train_source(sym, cfg.work_interval),
                      proxy=sym.source == 'swissquote', persisted=False)
        if result['proxy']:
            result.update(applied=False, reason='Histórico proxy; no valida el instrumento spot')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await market_data.close_client()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bot', choices=[c.id for c in bots.ALL_BOTS], default='swing')
    parser.add_argument('--symbol', choices=list(SYMBOLS_BY_ID), default='BTC')
    parser.add_argument('--output', type=Path, default=Path('reports/btc-swing-validation.json'))
    args = parser.parse_args()
    asyncio.run(evaluate(args.bot, args.symbol, args.output))
