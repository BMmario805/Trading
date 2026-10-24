"""Reglas deterministas compartidas por el paper trading y el simulador."""

import math


def signal_exit_enabled(bot_id):
    """Las estrategias tendenciales salen por protección y plazo, no por ruido."""
    return bot_id not in ('swing', 'trend', 'squeeze')


def atr_spread_multiple(bot_id):
    """Scalper en 5m: spread ≤25% del ATR; costes completos siguen en el tamaño."""
    return 4 if bot_id=='scalper' else 3


def closed_candles(candles: list[dict], seconds: int, now: float) -> list[dict]:
    """Excluye velas abiertas, duplicadas y OHLC no válidos."""
    out = {}
    for c in candles:
        values = [c.get(k, float('nan')) for k in ('open', 'high', 'low', 'close')]
        if (c['time'] + seconds <= now and all(math.isfinite(v) and v > 0 for v in values)
                and c['low'] <= min(c['open'], c['close'])
                and c['high'] >= max(c['open'], c['close'])):
            out[c['time']] = c
    return [out[t] for t in sorted(out)]


def fresh_signal(score: float, previous: list[float], threshold: float) -> bool:
    """Cruce en las últimas tres velas, en la dirección de la señal actual."""
    if not math.isfinite(score) or threshold <= 0 or abs(score) < threshold or not previous:
        return False
    sign = 1 if score > 0 else -1
    return any(p * sign < threshold for p in previous[-3:])


def valid_tick(tick: dict, now: float, max_age: float = 120) -> bool:
    price, stamp = tick.get('price'), tick.get('time')
    return (isinstance(price, (int, float)) and math.isfinite(price) and price > 0
            and isinstance(stamp, (int, float)) and -5 <= now - stamp <= max_age)
