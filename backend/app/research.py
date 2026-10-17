"""Comparación reproducible y acotada de hipótesis, sin tocar cuentas ni SQLite.

Uso: python -m app.research --symbols BTC ETH SOL --days 30 --output informe.json
Cada informe registra hash de datos, versiones, todos los ensayos y fechas.
Reabrir el mismo holdout no crea evidencia nueva: se exige seguimiento futuro.
"""

import argparse
import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

from . import market_data
from .config import SYMBOLS_BY_ID
from .economics import ECONOMICS_VERSION, cost_profile, execution_fill, net_outcome
from .execution import closed_candles
from .indicators import atr_series
from .research_strategies import REGISTRY, ResearchStrategy, risk_series, signal_series
from .training import (ENGINE_VERSION, WORK_SECONDS, compact_metrics,
                       expectancy_uncertainty, simulate, stressed_costs, temporal_protocol)

RESEARCH_VERSION = 'simple-families-spot-v1'
INTERVALS = ('5m', '15m', '1h')


def _benchmark(candles: list[dict], start: int, end: int, model: dict, capital: float = 100) -> dict:
    if end <= start:
        return dict(cash=dict(pnl=0.0), buyHold=None)
    entry = execution_fill(candles[start]['open'], 1, True, model['spreadPct'], model['slippageRate'])
    qty = capital / (entry * (1 + model['feeRate']))
    fee = entry * qty * model['feeRate']
    peak = capital
    dd = 0.0
    for row in candles[start:end]:
        exit_price = execution_fill(row['close'], 1, False, model['spreadPct'], model['slippageRate'])
        wealth = capital + net_outcome(entry, exit_price, qty, 1, fee, model['feeRate'])
        peak = max(peak, wealth)
        dd = max(dd, peak - wealth)
    pnl = net_outcome(entry, exit_price, qty, 1, fee, model['feeRate'])
    return dict(cash=dict(pnl=0.0, maxDrawdown=0.0, assumption='Efectivo sin intereses'),
                buyHold=dict(pnl=round(pnl, 6), returnPct=round(pnl / capital * 100, 4),
                             maxDrawdown=round(dd, 6), notional=capital,
                             fees=round(fee + exit_price * qty * model['feeRate'], 6)))


def evaluate_dataset(candles: list[dict], symbol: str, interval: str, model: dict | None = None) -> dict:
    """Función pura: registro fijo, tres bloques de descubrimiento y holdout.

    Las métricas del holdout se calculan solo para el candidato congelado.
    Los otros candidatos nunca se vuelven a ordenar mirando el tramo final.
    """
    if interval not in INTERVALS:
        raise ValueError('Intervalo de investigación no admitido')
    canonical = [[row[key] for key in ('time', 'open', 'high', 'low', 'close', 'volume')]
                 for row in candles]
    fingerprint = hashlib.sha256(json.dumps(canonical, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    costs = dict(model or cost_profile('binance', 'cripto'))
    # Spot sin préstamos: no hay financiación ni cortos hipotéticos.
    costs.update(mode='spot', carryDailyRate=0.0)
    protocol = temporal_protocol(len(candles), max(s.max_hold_bars for s in REGISTRY))
    base = dict(symbol=symbol, interval=interval, source='Binance Spot USDT', bars=len(candles),
                dataStart=candles[0]['time'] if candles else None,
                dataEnd=candles[-1]['time'] + WORK_SECONDS[interval] if candles else None,
                datasetHash=fingerprint, protocol=protocol, costModel=costs,
                promotionApplied=False, candidateEligible=False,
                researchVersion=RESEARCH_VERSION, engineVersion=ENGINE_VERSION,
                trialCount=len(REGISTRY), execution='next-open; stop-first; long-only; fixed-risk',
                capitalReference=dict(amount=100, currency='USDT', riskPerTrade=1,
                                      maximumNotional=100, compounded=False))
    if len(candles) < 600 or any(hi - lo < 20 for lo, hi in protocol['folds'] + [protocol['holdout']]):
        return {**base, 'ok': False, 'reason': 'Muestra insuficiente para tres bloques y holdout con embargo',
                'candidates': [], 'selected': None, 'holdout': None, 'benchmarks': None}
    atrs = atr_series(candles)
    prepared = {s.id: (signal_series(s, candles), risk_series(s, candles)) for s in REGISTRY}

    def replay(strategy: ResearchStrategy, start: int, end: int, profile: dict) -> dict:
        scores, multipliers = prepared[strategy.id]
        return simulate(candles, scores, atrs, profile['spreadPct'], strategy.threshold,
                        strategy.sl_atr, strategy.tp_atr, strategy.trail_atr,
                        strategy.max_hold_bars, 3, 1.0, 100 / (1 + profile['feeRate']),
                        start, end, cost_model=profile, allow_short=False,
                        risk_multipliers=multipliers)

    candidates = []
    metrics_by_id = {}
    for strategy in REGISTRY:
        discovery = [replay(strategy, lo, hi, costs) for lo, hi in protocol['folds']]
        stress = [replay(strategy, lo, hi, stressed_costs(costs)) for lo, hi in protocol['folds']]
        pnls = [p for m in discovery for p in m['tradePnls']]
        pnl, drawdown = sum(m['pnl'] for m in discovery), max(m['maxDrawdown'] for m in discovery)
        trades = sum(m['trades'] for m in discovery)
        stress_pnl = sum(m['pnl'] for m in stress)
        uncertainty = expectancy_uncertainty(pnls)
        row = dict(strategy=strategy.description(), discoveryPnl=round(pnl, 6), trades=trades,
                   maxBlockDrawdown=drawdown, discoveryStressPnl=round(stress_pnl, 6),
                   positiveBlocks=sum(m['pnl'] > 0 for m in discovery),
                   folds=[dict(start=lo, end=hi, **compact_metrics(m))
                          for (lo, hi), m in zip(protocol['folds'], discovery)],
                   uncertainty=uncertainty,
                   selectionScore=round(pnl - .5 * drawdown, 6))
        candidates.append(row)
        metrics_by_id[strategy.id] = row

    # Efectivo compite como alternativa explícita: no se obliga a elegir una
    # estrategia cuando ninguna gana dinero neto con muestra suficiente.
    eligible = [r for r in candidates if r['trades'] >= 12 and r['discoveryPnl'] > 0
                and r['discoveryStressPnl'] > 0 and r['positiveBlocks'] >= 2]
    if not eligible:
        return {**base, 'ok': True, 'candidates': candidates, 'selected': None, 'holdout': None,
                'benchmarks': _benchmark(candles, *protocol['holdout'], costs),
                'reason': 'Ninguna familia supera efectivo y doble coste en descubrimiento; holdout de estrategias sin consultar'}
    selected = max(eligible, key=lambda r: (r['selectionScore'], r['strategy']['id']))
    strategy = next(s for s in REGISTRY if s.id == selected['strategy']['id'])
    neighbors = [s for s in REGISTRY if s.family == strategy.family and s.id != strategy.id]
    neighbor_metrics = [dict(id=s.id, pnl=metrics_by_id[s.id]['discoveryPnl'],
                             stressPnl=metrics_by_id[s.id]['discoveryStressPnl']) for s in neighbors]
    robust_neighbors = sum(n['pnl'] > 0 and n['stressPnl'] > 0 for n in neighbor_metrics) >= 1
    # Solo ahora se abre el holdout para un candidato ya congelado.
    final = replay(strategy, *protocol['holdout'], costs)
    final_stress = replay(strategy, *protocol['holdout'], stressed_costs(costs))
    uncertainty = expectancy_uncertainty(final['tradePnls'])
    candidate_ok = (final['trades'] >= 20 and final['pnl'] > 0 and final_stress['pnl'] > 0
                    and uncertainty.get('lower95') is not None and uncertainty['lower95'] > 0
                    and selected['positiveBlocks'] == 3 and robust_neighbors)
    return {**base, 'ok': True, 'candidates': candidates, 'selected': strategy.description(),
            'holdout': {**compact_metrics(final), 'stress': compact_metrics(final_stress),
                        'uncertainty': uncertainty, 'neighborsBeforeHoldout': neighbor_metrics},
            'benchmarks': _benchmark(candles, *protocol['holdout'], costs),
            'candidateEligible': candidate_ok,
            'reason': ('Candidato para seguimiento futuro; sin promoción automática' if candidate_ok else
                       'No supera muestra, incertidumbre, consistencia o robustez; sin promoción')}


async def _bounded_history(source_symbol: str, interval: str, days: int, max_bars: int, now: float) -> list[dict]:
    lower, end = int((now - days * 86400) * 1000), int(now * 1000)
    collected = []
    for _ in range(math.ceil(max_bars / 1000)):
        rows = await market_data._binance_get('/api/v3/klines',
                    dict(symbol=source_symbol, interval=interval, endTime=end, limit=min(1000, max_bars - len(collected))))
        if not rows:
            break
        collected.extend(dict(time=k[0] // 1000, open=float(k[1]), high=float(k[2]),
                              low=float(k[3]), close=float(k[4]), volume=float(k[5])) for k in rows if k[0] >= lower)
        end = rows[0][0] - 1
        if len(collected) >= max_bars or rows[0][0] <= lower or len(rows) < 1000:
            break
    return closed_candles(collected, WORK_SECONDS[interval], now)[-max_bars:]


async def run_research(symbols=('BTC', 'ETH', 'SOL'), intervals=INTERVALS, days=30, max_bars=9000) -> dict:
    """Trabajo acotado y de solo lectura, apto para API o CLI.

    Hasta cinco instrumentos Spot y tres intervalos; cada dataset tiene hasta
    diez páginas de datos. Una fuente fallida se informa sin ocultar las otras.
    """
    symbols, intervals = tuple(dict.fromkeys(symbols)), tuple(dict.fromkeys(intervals))
    if not 1 <= len(symbols) <= 5 or not 1 <= len(intervals) <= 3:
        raise ValueError('Admite entre uno y cinco símbolos y hasta tres intervalos')
    if any(interval not in INTERVALS for interval in intervals):
        raise ValueError('Intervalos permitidos: 5m, 15m, 1h')
    if not isinstance(days, int) or not 1 <= days <= 365 or not isinstance(max_bars, int) or not 600 <= max_bars <= 10000:
        raise ValueError('Días entre 1 y 365; barras entre 600 y 10000')
    now = time.time()
    results = []
    for symbol_id in symbols:
        sym = SYMBOLS_BY_ID.get(symbol_id)
        for interval in intervals:
            if not sym or sym.source != 'binance':
                results.append(dict(symbol=symbol_id, interval=interval, ok=False, promotionApplied=False,
                                    reason='Comparador Spot: instrumento sin feed Binance directo; no se valida con proxy'))
                continue
            try:
                rows = await _bounded_history(sym.source_symbol, interval, days, max_bars, now)
                result = await asyncio.to_thread(evaluate_dataset, rows, sym.id, interval,
                                                 cost_profile('binance', sym.category))
                result['sourceSymbol'] = sym.source_symbol
                results.append(result)
            except Exception as exc:
                results.append(dict(symbol=symbol_id, interval=interval, ok=False, promotionApplied=False,
                                    reason=f'Fallo de datos/evaluación: {str(exc)[:180]}'))
    return dict(version=RESEARCH_VERSION, engineVersion=ENGINE_VERSION, costVersion=ECONOMICS_VERSION,
                generatedAt=int(now), request=dict(symbols=symbols, intervals=intervals, days=days, maxBars=max_bars),
                assumptions=['Costes conservadores no verificados con cuenta', 'Spot largo sin apalancamiento',
                             'Riesgo fijo, sin capitalización; referencia 100 USDT',
                             'Tiempos efectivos pueden diferir si se alcanza el límite de barras',
                             'Holdout observado una vez por informe; reutilizar datos no crea nueva evidencia',
                             'Intervalos bootstrap descriptivos, sin corrección por selección múltiple',
                             'La evidencia mensual de tendencia no demuestra ventaja intradía',
                             'Toda promoción exige seguimiento posterior al informe'],
                results=results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', nargs='+', default=['BTC', 'ETH', 'SOL'])
    parser.add_argument('--intervals', nargs='+', default=list(INTERVALS))
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--max-bars', type=int, default=9000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    async def job():
        try:
            return await run_research(args.symbols, args.intervals, args.days, args.max_bars)
        finally:
            await market_data.close_client()
    report = asyncio.run(job())
    content = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output:
        # Impide sustituir un informe anterior y fingir que el holdout es nuevo.
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(content + '\n')
    else:
        print(content)


if __name__ == '__main__':
    main()
