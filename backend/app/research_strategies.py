"""Familias pequeñas, causales y predefinidas para comparación, sin promoción.

La evidencia académica de tendencia a meses no valida estos horizontes de
velas. Se comparan hipótesis simples y su control, incluyendo todos los costes.
"""

from dataclasses import asdict, dataclass
import math

from .indicators import adx_series, ema
from .strategy import volatility_risk_series


@dataclass(frozen=True)
class ResearchStrategy:
    id: str
    family: str
    lookback: int
    threshold: float = .6
    sl_atr: float = 2.0
    tp_atr: float = 4.0
    trail_atr: float = 2.0
    max_hold_bars: int = 48
    volatility_managed: bool = False

    def description(self) -> dict:
        return asdict(self)


REGISTRY = tuple(
    [ResearchStrategy(f'donchian-{n}', 'donchian', n) for n in (20, 40, 60)]
    + [ResearchStrategy(f'momentum-{n}', 'momentum', n) for n in (12, 20, 30)]
    + [ResearchStrategy(f'momentum-vol-{n}', 'momentum-vol', n, volatility_managed=True) for n in (12, 20, 30)]
    + [ResearchStrategy(f'range-{n}', 'range-reversion', n, tp_atr=3.0, trail_atr=1.5, max_hold_bars=24)
       for n in (20, 30, 40)]
)


def signal_series(strategy: ResearchStrategy, candles: list[dict]) -> list[float]:
    closes = [c['close'] for c in candles]
    n = strategy.lookback
    out = [0.0] * len(candles)
    if strategy.family == 'donchian':
        mean = ema(closes, n)
        state = 0.0
        for i in range(n, len(candles)):
            prior = candles[i - n:i]
            if closes[i] > max(c['high'] for c in prior):
                state = 1.0
            elif closes[i] < min(c['low'] for c in prior):
                state = -1.0
            elif mean[i] is not None and (closes[i] - mean[i]) * state < 0:
                state = 0.0
            out[i] = state
    elif strategy.family in ('momentum', 'momentum-vol'):
        horizons = (n, 3 * n, 6 * n)
        for i in range(max(horizons), len(candles)):
            votes = [1 if closes[i] > closes[i - h] else -1 if closes[i] < closes[i - h] else 0
                     for h in horizons]
            out[i] = sum(votes) / 3
    elif strategy.family == 'range-reversion':
        strength = adx_series(candles)
        for i in range(n, len(candles)):
            if strength[i] is None or strength[i] > 20:
                continue
            window = closes[i - n + 1:i + 1]
            mean = sum(window) / n
            deviation = math.sqrt(sum((p - mean) ** 2 for p in window) / n)
            if deviation <= 0:
                continue
            z = (closes[i] - mean) / deviation
            # Magnitud 0.5: cierra la reversión al cruzar la media sin abrir
            # automáticamente una posición opuesta (umbral de entrada 0.6).
            out[i] = 1.0 if z <= -2 else -1.0 if z >= 2 else -.5 if z > 0 else .5 if z < 0 else 0
    else:
        raise ValueError(f'Familia de investigación desconocida: {strategy.family}')
    return out


def risk_series(strategy: ResearchStrategy, candles: list[dict]) -> list[float] | None:
    return volatility_risk_series(candles) if strategy.volatility_managed else None
