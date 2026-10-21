"""Entrenamiento de los bots: backtest + optimización walk-forward.

Una rejilla acotada se selecciona en el 45% inicial. Se comprueba en tres
bloques cronológicos posteriores, con embargo, costes completos, vecindad
e incertidumbre, antes de consultar una sola vez el holdout final del 20%.
La rentabilidad de un backtest no garantiza ventaja futura; repetir ajustes
sobre el mismo holdout lo convierte en datos conocidos. Se registra versión,
número de pruebas y protocolo; las versiones anteriores no se promocionan.

El spread y la sobreextensión se filtran en todos los tramos. Para XAUUSD
se priorizan las velas del terminal MT5 DEMO autorizado. Sin terminal se
usará un proxy PAXG/GC solamente informativo, sin validar el spot.
Los costes de financiación son escenarios explícitos, no swaps históricos.
"""

import asyncio
import json
import logging
import math
import random
import sqlite3
import statistics
import time
from itertools import product

from . import market_data
from . import mt5_market
from .config import SYMBOLS_BY_ID, Symbol
from .history import DB_PATH
from .database import Connection
from .execution import closed_candles, fresh_signal
from .economics import cost_profile, execution_fill, net_outcome, planned_trade, ECONOMICS_VERSION
from .indicators import atr_series
from .strategy import TREND_STEP, resample, score_series, extension_series

log = logging.getLogger("training")

# Rejilla de parámetros de ejecución (los periodos de los indicadores se
# fijan: cada parámetro libre extra multiplica el riesgo de sobreajuste).
# Rejilla pequeña y predefinida: se registra cuántas variantes se probaron.
GRID_THRESHOLD = (0.30, 0.45, 0.60, 0.75)
GRID_SL = (1.5, 2.0, 3.0)
GRID_TP = (2.25, 3.0, 4.5)
GRID_TRAIL = (1.2, 2.0)

TRAIN_SPLIT = 0.45
# Embargo entre entrenamiento y validación: se descartan velas pegadas al
# corte para que ninguna posición/indicador del tramo de entrenamiento
# contamine la validación (purged walk-forward, práctica estándar actual)
EMBARGO_BARS = 30
MIN_TRAIN_TRADES = {
    "flow": 10, "scalper": 20, "swing": 8, "trend": 6, "meanrev": 8, "orb": 10, "squeeze": 8,
}
MIN_VALIDATION_TRADES = 8
ENGINE_VERSION = 6
VALIDATION_PROTOCOL = 'fixed-origin-2of3-dsr-holdout20-net-v2'

# Segundos por vela de cada intervalo de trabajo
WORK_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

# Histórico descargable por fuente e intervalo
BINANCE_DAYS = {"1m": 21, "5m": 60, "15m": 90, "1h": 365, "1d": 730}
YAHOO_RANGE = {
    "1m": ("1m", "7d"), "5m": ("5m", "60d"), "15m": ("15m", "60d"),
    "1h": ("60m", "1y"), "1d": ("1d", "5y"),
}


def train_source(sym: Symbol, interval: str) -> tuple[str, str]:
    """(fuente, símbolo) para el histórico de entrenamiento.

    El oro spot no tiene histórico gratuito: intradía se entrena con
    PAXGUSDT de Binance (oro tokenizado 24/7, velas profundas) y en marcos
    de horas con los futuros GC=F de Yahoo."""
    if sym.id == "XAUUSD":
        if interval in ("1m", "5m", "15m"):
            return "binance", "PAXGUSDT"
        return "yahoo", "GC=F"
    if sym.source == "swissquote":
        return "yahoo", market_data.SWISSQUOTE_CANDLE_PROXY[sym.source_symbol]
    return sym.source, sym.source_symbol


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, factory=Connection)
    con.execute(
        """CREATE TABLE IF NOT EXISTS bot_params (
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            params TEXT NOT NULL,
            metrics TEXT NOT NULL,
            applied INTEGER NOT NULL,
            trained_at INTEGER NOT NULL,
            PRIMARY KEY (bot, symbol)
        )"""
    )
    con.execute("""CREATE TABLE IF NOT EXISTS bot_training_attempts (
        bot TEXT NOT NULL, symbol TEXT NOT NULL, attempted_at INTEGER NOT NULL,
        ok INTEGER NOT NULL, PRIMARY KEY (bot, symbol))""")
    return con


def load_applied_params() -> dict[tuple[str, str], dict]:
    """Parámetros entrenados y validados, por (bot, símbolo)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT bot, symbol, params, metrics FROM bot_params WHERE applied = 1"
        ).fetchall()
    return {(b, s): json.loads(p) for b, s, p, m in rows
            if json.loads(m).get("engineVersion") == ENGINE_VERSION}


def load_results() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT bot, symbol, params, metrics, applied, trained_at FROM bot_params"
        ).fetchall()
    return [
        {
            "bot": r[0], "symbol": r[1], "params": json.loads(r[2]),
            "metrics": json.loads(r[3]), "applied": bool(r[4]) and json.loads(r[3]).get("engineVersion") == ENGINE_VERSION, "trainedAt": r[5],
        }
        for r in rows
    ]


def load_manual_params() -> dict[tuple[str, str], dict]:
    """Ajustes explícitos pendientes de revalidación, solo para modo a prueba."""
    with _conn() as con:
        rows = con.execute(
            "SELECT bot, symbol, params, metrics FROM bot_params WHERE applied = 0"
        ).fetchall()
    return {(b, s): json.loads(p) for b, s, p, m in rows
            if json.loads(m).get("engineVersion") == ENGINE_VERSION
            and json.loads(m).get("manualAdjustment")}


def due_symbols(bot_id: str, universe: tuple[str, ...], hours: float, now: float | None = None) -> list[str]:
    """Resultados evaluados respetan el ciclo; errores de datos reintentan en una hora."""
    now = time.time() if now is None else now
    with _conn() as con:
        attempts = {s: (ts, ok) for s, ts, ok in con.execute(
            'SELECT symbol, attempted_at, ok FROM bot_training_attempts WHERE bot=?', (bot_id,))}
        saved = {s: (ts, json.loads(m).get('engineVersion')) for s, ts, m in con.execute(
            'SELECT symbol, trained_at, metrics FROM bot_params WHERE bot=?', (bot_id,))}
    due = []
    for symbol in universe:
        stamp, version = saved.get(symbol, (0, None))
        attempt, ok = attempts.get(symbol, (0, 1))
        # Un error posterior al resultado guardado debe reintentarse en una
        # hora, incluso si aún existen parámetros válidos del ciclo anterior.
        if attempt and not ok and attempt >= stamp:
            if now - attempt >= 3600:
                due.append((stamp if version == ENGINE_VERSION else 0, symbol))
            continue
        if symbol in saved and version != ENGINE_VERSION:
            due.append((0, symbol))
            continue
        last = max(stamp, attempt)
        if not last or now - last >= hours * 3600:
            due.append((last, symbol))
    return [symbol for _, symbol in sorted(due)]


def last_trained_at(bot_id: str) -> int | None:
    """Momento del último entrenamiento del bot (None si nunca se entrenó)."""
    with _conn() as con:
        row = con.execute(
            "SELECT MAX(trained_at) FROM bot_params WHERE bot = ?", (bot_id,)
        ).fetchone()
    return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------


async def _binance_history(source_symbol: str, interval: str, days: int) -> list[dict]:
    """Klines paginados de Binance (1000 por petición)."""
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out: list[dict] = []
    while start < end:
        raw = await market_data._binance_get(
            "/api/v3/klines",
            {"symbol": source_symbol, "interval": interval,
             "startTime": start, "limit": 1000},
        )
        if not raw:
            break
        out.extend(
            {
                "time": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            }
            for k in raw
        )
        start = raw[-1][0] + 1
        if len(raw) < 1000:
            break
    return out


async def _yahoo_history(source_symbol: str, interval: str) -> list[dict]:
    y_int, y_range = YAHOO_RANGE[interval]
    return await market_data._yahoo_candles(source_symbol, y_int, y_range, 100_000)


async def fetch_history(bot_id: str, sym: Symbol, interval: str) -> list[dict]:
    if sym.id == "XAUUSD":
        broker_rows = await mt5_market.read('candles', interval, 100_000)
        if broker_rows:
            return broker_rows
    source, source_symbol = train_source(sym, interval)
    if source == "binance":
        return await _binance_history(
            source_symbol, interval, BINANCE_DAYS.get(interval, 365)
        )
    return await _yahoo_history(source_symbol, interval)


# ---------------------------------------------------------------------------
# Simulador (misma mecánica que el bot en vivo, barra a barra)
# ---------------------------------------------------------------------------


def simulate(
    candles: list[dict],
    scores: list[float],
    atrs: list[float | None],
    spread_pct: float,
    threshold: float,
    sl_atr: float,
    tp_atr: float,
    trail_atr: float,
    max_hold_bars: int,
    cooldown_bars: int,
    risk_amount: float,
    notional_cap: float,
    start: int,
    end: int,
    extensions: list[float | None] | None = None,
    overext_atr: float = float("inf"),
    cost_model: dict | None = None,
    allow_short: bool = True,
    risk_multipliers: list[float] | None = None,
    min_atr_spread: float = 3,
    exit_on_signal: bool = True,
) -> dict:
    """Backtest de una configuración sobre candles[start:end]. Sin capitalizar
    (tamaño fijo por operación) para que las métricas sean comparables.

    Replica la mecánica del bot en vivo, incluidas las salidas por señal
    contraria: entrenar un sistema sin ese cierre y operarlo con él haría
    que lo validado no fuese lo operado."""
    pnl = 0.0
    wins = losses = 0
    gross_win = gross_loss = 0.0
    equity_peak = equity = 0.0
    max_dd = 0.0
    trade_pnls: list[float] = []
    total_fees = total_carry = turnover = 0.0
    period_pnls = []
    previous_mark = 0.0
    model = dict(cost_model or {})
    fee_rate = model.get('feeRate', 0.0)
    slippage = model.get('slippageRate', 0.0)
    carry_rate = model.get('carryDailyRate', 0.0)
    spread_pct = model.get('spreadPct', spread_pct)
    for value in (fee_rate, slippage, carry_rate, spread_pct):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 1:
            raise ValueError('Coste no finito o fuera de rango')

    direction = 0        # 0 sin posición, +1 largo, -1 corto
    entry = sl = tp = qty = atr0 = best = 0.0
    bars_held = 0
    cooldown = 0
    entry_fee = entry_time = exit_time = 0.0

    def carry_at(stamp: float) -> float:
        return entry * qty * carry_rate * max(0, stamp - entry_time) / 86400

    def exit_fill(price: float) -> float:
        return execution_fill(price, direction, False, spread_pct, slippage)

    def break_even_price(current_price: float) -> float:
        if cost_model is None:
            return entry + direction * current_price * spread_pct
        per_unit = (entry_fee + carry_at(exit_time)) / qty
        fill = (direction * entry + per_unit) / (direction - fee_rate)
        mid = fill / exit_fill(1.0)
        # Un stop de equilibrio no se coloca por delante del mercado.
        return sl if (mid - current_price) * direction > 0 else mid

    def close(price: float) -> None:
        nonlocal pnl, wins, losses, gross_win, gross_loss, direction
        nonlocal equity, equity_peak, max_dd, cooldown
        nonlocal total_fees, total_carry, turnover
        carry = carry_at(exit_time)
        p = net_outcome(entry, price, qty, direction, entry_fee, fee_rate, carry=carry)
        total_fees += entry_fee + price * qty * fee_rate
        total_carry += carry
        turnover += (entry + price) * qty
        trade_pnls.append(p)
        pnl += p
        if p > 0:
            wins += 1
            gross_win += p
            # tras ganar, cooldown corto (mismo criterio que el bot en vivo)
            cooldown = max(1, cooldown_bars // 4)
        else:
            losses += 1
            gross_loss -= p
            cooldown = cooldown_bars
        equity += p
        equity_peak = max(equity_peak, equity)
        max_dd = max(max_dd, equity_peak - equity)
        direction = 0

    end = min(end, len(candles))
    for i in range(max(start, 1), end):
        c = candles[i]
        exit_time = c['time']
        mark = equity + (net_outcome(entry, exit_fill(c['open']), qty, direction,
                        entry_fee, fee_rate, carry=carry_at(exit_time)) if direction else 0)
        period_pnls.append(mark-previous_mark)
        previous_mark = mark

        if direction:
            bars_held += 1
            if direction > 0:
                if c["low"] <= sl:            # el stop primero: es lo conservador
                    close(exit_fill(min(sl, c["open"])))
                    continue
                if c["high"] >= tp:
                    close(exit_fill(tp))
                    continue
                best = max(best, c["high"])
                if best >= entry + sl_atr * atr0:   # breakeven + trailing
                    # el trailing se estrecha tras 2R (protege la ganancia)
                    tr = trail_atr * (0.75 if best >= entry + 2 * sl_atr * atr0 else 1.0)
                    sl = max(sl, break_even_price(c['close']), best - tr * atr0)
            else:
                if c["high"] >= sl:
                    close(exit_fill(max(sl, c["open"])))
                    continue
                if c["low"] <= tp:
                    close(exit_fill(tp))
                    continue
                best = min(best, c["low"])
                if best <= entry - sl_atr * atr0:
                    tr = trail_atr * (0.75 if best <= entry - 2 * sl_atr * atr0 else 1.0)
                    sl = min(sl, break_even_price(c['close']), best + tr * atr0)
            # Drawdown también incluye flotante y el coste de liquidación.
            floating = net_outcome(entry, exit_fill(c['close']), qty, direction,
                                   entry_fee, fee_rate, carry=carry_at(exit_time))
            equity_peak = max(equity_peak, equity + floating)
            max_dd = max(max_dd, equity_peak - equity - floating)
            # señal claramente contraria (histéresis 0.75×umbral, como en vivo)
            if exit_on_signal and direction and scores[i] * direction <= -0.75 * threshold:
                # Una decisión al cierre solo se ejecuta en la siguiente apertura.
                if i + 1 < end:
                    exit_time = candles[i + 1]['time']
                    close(exit_fill(candles[i + 1]['open']))
                else:
                    close(exit_fill(c['close']))
                continue
            if direction and bars_held >= max_hold_bars:
                if i + 1 < end:
                    exit_time = candles[i + 1]['time']
                    close(exit_fill(candles[i + 1]['open']))
                else:
                    close(exit_fill(c['close']))
            continue

        if cooldown:
            cooldown -= 1
            continue
        s, a = scores[i], atrs[i]
        if a is None or not math.isfinite(a) or a <= 0 or not math.isfinite(s) or abs(s) < threshold or (s < 0 and not allow_short):
            continue
        # solo con señal FRESCA: el cruce del umbral ocurrió en las últimas
        # 3 velas (la misma ventana anti-madurez que aplica el bot en vivo)
        if not fresh_signal(s, scores[max(0, i - 3):i], threshold):
            continue
        # filtro de coste: sin volatilidad suficiente el spread se come el beneficio
        if a < min_atr_spread * c["close"] * spread_pct:
            continue
        ext = extensions[i] if extensions is not None else None
        if ext is not None and ext * (1 if s > 0 else -1) > overext_atr:
            continue
        # Señal conocida al cierre; ejecución en la siguiente apertura.
        if i + 1 >= end:
            continue
        fill = candles[i + 1]["open"]
        if abs(fill - c["close"]) > a:
            continue
        direction = 1 if s > 0 else -1
        entry = execution_fill(fill, direction, True, spread_pct, slippage)
        atr0 = a
        sl = entry - direction * sl_atr * a
        tp = entry + direction * tp_atr * a
        unit = planned_trade(entry, sl, tp, 1, direction, fee_rate, slippage, spread_pct,
                             carry=entry * carry_rate * max_hold_bars * max(1, candles[min(i + 1, end - 1)]['time'] - c['time']) / 86400)
        if cost_model is not None and not unit['viable']:
            direction = 0
            continue
        # Con un modelo declarado el riesgo incluye la ida y vuelta y carry.
        unit_risk = unit['netRisk'] if cost_model is not None else sl_atr * a + entry * spread_pct / 2
        multiplier = min(1.0, max(0.0, risk_multipliers[i])) if risk_multipliers is not None else 1.0
        if multiplier <= 0:
            direction = 0
            continue
        qty = min(risk_amount * multiplier / max(unit_risk, 1e-12), notional_cap / entry)
        entry_fee = entry * qty * fee_rate
        entry_time = candles[i + 1]['time']
        best = entry
        bars_held = 0

    if direction:
        final_price = candles[end - 1]["close"]
        exit_time = candles[end - 1]['time']
        close(exit_fill(final_price))
    period_pnls.append(equity-previous_mark)
    n = wins + losses
    return {
        "expectancy": round(pnl / n, 4) if n else None,
        "trades": n,
        "wins": wins,
        "pnl": round(pnl, 2),
        "winRate": round(wins / n * 100, 1) if n else None,
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "maxDrawdown": round(max_dd, 2),
        "fees": round(total_fees, 6),
        "carry": round(total_carry, 6),
        "turnover": round(turnover, 4),
        "tradePnls": trade_pnls,
        "periodPnls": period_pnls,
        "costModel": model or {'mode': 'legacy-spread-only'},
    }


# ---------------------------------------------------------------------------
# Optimización walk-forward
# ---------------------------------------------------------------------------


def temporal_protocol(length: int, max_hold_bars: int = 1) -> dict:
    """Origen fijo: selección 45%, tres bloques hasta 80%, holdout final 20%.

    Cada bloque empieza sin posiciones. El embargo es al menos la permanencia
    máxima; los indicadores pueden consultar exclusivamente su pasado conocido.
    El holdout nunca interviene en ranking, vecindad ni ajuste de parámetros.
    """
    select_end, development_end = int(length * .45), int(length * .8)
    embargo = max(EMBARGO_BARS, max_hold_bars)
    boundaries = [select_end + (development_end - select_end) * i // 3 for i in range(4)]
    folds = [(min(a + embargo, b), b) for a, b in zip(boundaries, boundaries[1:])]
    return dict(version=VALIDATION_PROTOCOL, selection=(0, select_end), folds=folds,
                holdout=(min(development_end + embargo, length), length), embargo=embargo,
                finalHoldoutFraction=.2, selectionFrozenBeforeValidation=True)


def expectancy_uncertainty(pnls: list[float], samples: int = 512, seed: int = 417) -> dict:
    """Intervalo bootstrap por bloques de cierres; descriptivo, no prueba causal.

    Conserva algo de dependencia entre cierres consecutivos. No se presenta como
    DSR/PBO ni corrige la selección entre múltiples símbolos/intervalos.
    """
    n = len(pnls)
    if n < 2:
        return dict(sample=n, mean=pnls[0] if n else None, lower95=None, upper95=None,
                    method='circular-trade-block-bootstrap', selectionAdjusted=False)
    rng = random.Random(seed)
    block = max(2, int(math.sqrt(n)))
    means = []
    for _ in range(samples):
        draw = []
        while len(draw) < n:
            offset = rng.randrange(n)
            draw.extend(pnls[(offset + j) % n] for j in range(block))
        means.append(statistics.fmean(draw[:n]))
    means.sort()
    return dict(sample=n, mean=statistics.fmean(pnls), lower95=means[int(samples * .025)],
                upper95=means[min(samples - 1, int(samples * .975))], blockLength=block,
                method='circular-trade-block-bootstrap', selectionAdjusted=False,
                warning='Intervalo descriptivo; no corrige selección ni garantiza independencia')


def compact_metrics(metrics: dict) -> dict:
    return {k: v for k, v in metrics.items() if k not in ('tradePnls', 'periodPnls', 'costModel')}


def deflated_sharpe(period_pnls, trial_sharpes):
    """DSR por vela (capital fijo), Bailey/López de Prado 2014, eq. 2.

    Todos los ensayos de esta rejilla cuentan como independientes, sin reducir
    artificialmente N por correlación. No corrige búsquedas de otras ejecuciones.
    Ajuste conservador del tamaño efectivo por autocorrelaciones positivas.
    """
    n = len(period_pnls)
    result = dict(probability=None, passed=False, sample=n, trials=len(trial_sharpes),
                  scope='this-grid-only', method='deflated-sharpe-bar-pnl-v1', threshold=.95,
                  source='https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf')
    if n < 30 or len(trial_sharpes) < 2: return result
    mean = statistics.fmean(period_pnls)
    variance = statistics.variance(period_pnls)
    if variance <= 0: return result
    sd = math.sqrt(variance)
    sr = mean/sd
    centered = [x-mean for x in period_pnls]
    m2 = sum(x*x for x in centered)/n
    skew = sum(x**3 for x in centered)/n/m2**1.5
    kurt = sum(x**4 for x in centered)/n/m2**2
    ac = sum(max(0, sum(centered[i]*centered[i-lag] for i in range(lag,n))/(n*m2))
             for lag in range(1,min(20,n//4)+1))
    effective = max(2, n/(1+2*ac))
    normal = statistics.NormalDist()
    count = len(trial_sharpes)
    sigma = math.sqrt(max(statistics.variance(trial_sharpes),1/(n-1)))
    gamma = .5772156649015329
    benchmark = sigma*((1-gamma)*normal.inv_cdf(1-1/count)
                      +gamma*normal.inv_cdf(1-1/(count*math.e)))
    denominator = 1-skew*sr+(kurt-1)/4*sr*sr
    if denominator <= 0: return result
    probability = normal.cdf((sr-benchmark)*math.sqrt(effective-1)/math.sqrt(denominator))
    return dict(result, probability=probability, passed=probability>=.95,
                sharpe=sr, benchmark=benchmark, skew=skew, kurtosis=kurt, effectiveSample=effective)


def stressed_costs(model: dict, multiplier: float = 2) -> dict:
    return {**model, **{key: min(.99, model.get(key, 0) * multiplier)
                        for key in ('feeRate', 'slippageRate', 'spreadPct', 'carryDailyRate')}}


def optimize(
    bot_id: str,
    candles: list[dict],
    scores: list[float],
    atrs: list[float | None],
    spread_pct: float,
    max_hold_bars: int,
    cooldown_bars: int,
    risk_amount: float,
    notional_cap: float,
    overext_atr: float = float("inf"),
    cost_model: dict | None = None,
    allow_short: bool = True,
) -> dict:
    extensions = extension_series(candles)
    protocol = temporal_protocol(len(candles), max_hold_bars)
    split = protocol['selection'][1]
    min_trades = MIN_TRAIN_TRADES.get(bot_id, 8)
    model = dict(cost_model or cost_profile('lab', 'cripto'))
    model['spreadPct'] = spread_pct
    if any(hi - lo < 10 for lo, hi in protocol['folds'] + [protocol['holdout']]):
        return dict(ok=False, applied=False, engineVersion=ENGINE_VERSION,
                    reason='Histórico insuficiente para tres bloques y holdout con embargo', protocol=protocol)

    def evaluate(cfg: tuple, lo: int, hi: int, costs: dict | None = None) -> dict:
        from .execution import atr_spread_multiple, signal_exit_enabled
        th, sl, tp, tr = cfg
        return simulate(candles, scores, atrs, spread_pct, th, sl, tp, tr,
                        max_hold_bars, cooldown_bars, risk_amount, notional_cap,
                        lo, hi, extensions, overext_atr, costs or model, allow_short,
                        min_atr_spread=atr_spread_multiple(bot_id), exit_on_signal=signal_exit_enabled(bot_id))

    best_cfg: tuple | None = None
    best_score = float("-inf")
    best_train: dict | None = None
    trials = 0
    trial_sharpes = []
    for th, sl, tp, tr in product(GRID_THRESHOLD, GRID_SL, GRID_TP, GRID_TRAIL):
        # recompensa >= riesgo inicial, salvo en reversión a la media (su perfil
        # clásico es ganancia corta y frecuente con stop amplio)
        if tp < sl and bot_id != "meanrev":
            continue
        trials += 1
        m = evaluate((th, sl, tp, tr), 0, split)
        series=m.get('periodPnls',[])
        sd=statistics.stdev(series) if len(series)>1 else 0
        trial_sharpes.append(statistics.fmean(series)/sd if sd else 0)
        if m["trades"] < min_trades:
            continue
        pf = m["profitFactor"] if m["profitFactor"] is not None else (3.0 if m["pnl"] > 0 else 0.0)
        # rentabilidad ponderada por consistencia y penalizada por drawdown:
        # entre dos configuraciones con el mismo PnL gana la de curva más suave
        dd_penalty = 1.0 / (1.0 + m["maxDrawdown"] / max(risk_amount * 20, 1e-9))
        score = m["pnl"] * min(pf, 3.0) * dd_penalty
        if m["pnl"] > 0 and score > best_score:
            best_score, best_cfg, best_train = score, (th, sl, tp, tr), m

    if best_cfg is None:
        return dict(ok=False, applied=False, engineVersion=ENGINE_VERSION, costVersion=ECONOMICS_VERSION,
                    protocol=protocol, trials=trials, costModel=model,
                    reason='ninguna configuración rentable neta en el tramo de selección')

    th, sl, tp, tr = best_cfg
    segments = [evaluate(best_cfg, lo, hi) for lo, hi in protocol['folds']]
    stress_segments = [evaluate(best_cfg, lo, hi, stressed_costs(model,1.5)) for lo, hi in protocol['folds']]
    # Los vecinos se comprueban solo antes del holdout, sin elegir el mejor vecino.
    neighbor_configs = [(th * .9, sl, tp, tr), (min(.95, th * 1.1), sl, tp, tr),
                        (th, sl * 1.1, tp * 1.1, tr)]
    neighbors = [dict(params=dict(entryThreshold=c[0], slAtr=c[1], tpAtr=c[2], trailAtr=c[3]),
                      pnl=sum(evaluate(c, lo, hi)['pnl'] for lo, hi in protocol['folds']))
                 for c in neighbor_configs]
    uncertainty = expectancy_uncertainty([p for m in segments for p in m['tradePnls']])
    dsr = deflated_sharpe(best_train.get('periodPnls',[]), trial_sharpes)
    # Única consulta del tramo final una vez congelados configuración y pruebas.
    val = evaluate(best_cfg, *protocol['holdout'])
    stress = evaluate(best_cfg, *protocol['holdout'], stressed_costs(model))
    accepted = (val['pnl'] > 0 and val['trades'] >= MIN_VALIDATION_TRADES
                and sum(m['trades'] for m in segments) >= 20
                and sum(m['pnl'] > 0 for m in segments) >= 2
                and sum(m['pnl'] for m in segments) > 0
                and sum(m['pnl'] for m in stress_segments) > 0
                and stress['pnl'] > 0 and stress['trades'] >= MIN_VALIDATION_TRADES
                and sum(n['pnl'] > 0 for n in neighbors) >= 2
                and dsr['passed'])
    return {
        "ok": True,
        "params": {"entryThreshold": th, "slAtr": sl, "tpAtr": tp, "trailAtr": tr},
        "train": compact_metrics(best_train),
        "validation": compact_metrics(val),
        "applied": accepted,
        "segments": [compact_metrics(m) for m in segments],
        "stress": compact_metrics(stress),
        "stressSegments": [compact_metrics(m) for m in stress_segments],
        "neighbors": neighbors,
        "uncertainty": uncertainty,
        "deflatedSharpe": dsr,
        "developmentStressMultiplier": 1.5,
        "holdoutStressMultiplier": 2.0,
        "protocol": protocol,
        "trials": trials,
        "costModel": model,
        "costVersion": ECONOMICS_VERSION,
        "engineVersion": ENGINE_VERSION,
        "reason": "Dos de tres bloques, DSR, holdout a doble coste y vecindad superados" if accepted else "Muestra, consistencia, DSR, vecindad o costes insuficientes",
        "bars": len(candles),
    }


# ---------------------------------------------------------------------------
# Orquestación (una tarea de entrenamiento por bot, en segundo plano)
# ---------------------------------------------------------------------------

STATUS: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}


def is_running(bot_id: str) -> bool:
    t = _tasks.get(bot_id)
    return t is not None and not t.done()


async def _train_symbol(bot, sym: Symbol) -> dict:
    cfg = bot.cfg
    c_work = await fetch_history(cfg.id, sym, cfg.work_interval)
    c_work = closed_candles(c_work, WORK_SECONDS[cfg.work_interval], time.time())
    if len(c_work) < 500:
        return {"symbol": sym.id, "ok": False, "reason": f"histórico insuficiente ({len(c_work)} velas)"}
    trend_step = TREND_STEP.get(cfg.id, 86400)
    c_trend = resample(c_work, trend_step)

    work_seconds = WORK_SECONDS.get(cfg.work_interval, 3600)
    max_hold_bars = max(1, cfg.max_hold_seconds // work_seconds)
    cooldown_bars = max(1, cfg.cooldown_seconds // work_seconds)
    spread = bot.spread_pct(sym)

    def _run() -> dict:
        scores = score_series(cfg.id, c_work, c_trend)
        atrs = atr_series(c_work, cfg.atr_period)
        return optimize(
            cfg.id, c_work, scores, atrs, spread, max_hold_bars, cooldown_bars,
            risk_amount=cfg.initial_balance * cfg.risk_pct,
            notional_cap=cfg.initial_balance * cfg.max_leverage,
            overext_atr=cfg.overext_atr,
            cost_model=cost_profile('lab', sym.category),
        )

    result = await asyncio.to_thread(_run)
    result["symbol"] = sym.id
    result["source"] = c_work[-1].get("dataSource") or dict(zip(("provider", "symbol"), train_source(sym, cfg.work_interval)))
    result["proxy"] = sym.source == "swissquote" and result["source"]["provider"] != 'mt5'
    if result.get('params') and result['source']['provider'] == 'mt5':
        result['params']['dataSource'] = result['source']
    if result["proxy"]:
        result["applied"] = False
        result["reason"] = "Histórico proxy: resultado informativo, no valida el instrumento spot"
    # Un resultado negativo revoca parámetros anteriores; un error de red no.
    if not result.get("ok"):
        result.setdefault("params", {})
        result["applied"] = False
    with _conn() as con:
        con.execute(
                "INSERT OR REPLACE INTO bot_params VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cfg.id, sym.id,
                    json.dumps(result["params"]),
                    json.dumps({k: result.get(k) for k in ("train", "validation", "segments", "stress", "stressSegments", "neighbors", "uncertainty", "deflatedSharpe", "developmentStressMultiplier", "holdoutStressMultiplier", "protocol", "trials", "costModel", "costVersion", "engineVersion", "reason", "source", "proxy")}),
                    int(result["applied"]), int(time.time()),
                ),
            )
    return result


async def _train_bot(bot, symbols: list[str]) -> None:
    status = STATUS[bot.cfg.id]
    try:
        for i, symbol_id in enumerate(symbols):
            status.update(progress=f"{i + 1}/{len(symbols)}", current=symbol_id)
            sym = SYMBOLS_BY_ID.get(symbol_id)
            if not sym:
                continue
            try:
                result = await _train_symbol(bot, sym)
            except Exception as exc:
                log.warning("entrenamiento %s/%s falló: %s", bot.cfg.id, symbol_id, exc)
                result = {"symbol": symbol_id, "ok": False, "reason": str(exc)[:200]}
            # ok=False también puede ser una evaluación completa sin ventaja.
            # Solo los fallos de fuente/histórico usan el backoff de una hora.
            attempt_ok = int(bool(result.get("ok") or result.get("engineVersion") == ENGINE_VERSION))
            with _conn() as con:
                con.execute('INSERT OR REPLACE INTO bot_training_attempts VALUES (?, ?, ?, ?)',
                            (bot.cfg.id, symbol_id, int(time.time()), attempt_ok))
            status["results"].append(result)
            log.info("entrenado %s/%s: %s", bot.cfg.id, symbol_id,
                     "aplicado" if result.get("applied") else result.get("reason", "no aplicado"))
        bot.reload_params()
    finally:
        status.update(running=False, current=None, finishedAt=int(time.time()))
        # el entrenamiento cambia la ventaja validada -> refrescar la política
        try:
            from . import learning
            learning.refresh(bot.cfg.id)
        except Exception as exc:
            log.debug("no se pudo refrescar el aprendizaje de %s: %s", bot.cfg.id, exc)


def start_training(bot, symbols: list[str] | None = None) -> dict:
    """Lanza el entrenamiento en segundo plano; devuelve el estado inicial."""
    bot_id = bot.cfg.id
    if is_running(bot_id):
        return STATUS[bot_id]
    targets = symbols or list(bot.cfg.universe)
    STATUS[bot_id] = {
        "running": True,
        "progress": f"0/{len(targets)}",
        "current": None,
        "results": [],
        "startedAt": int(time.time()),
        "finishedAt": None,
    }
    _tasks[bot_id] = asyncio.create_task(_train_bot(bot, targets))
    return STATUS[bot_id]


def training_status(bot_id: str) -> dict:
    return STATUS.get(bot_id) or {
        "running": False, "progress": None, "current": None,
        "results": [], "startedAt": None, "finishedAt": None,
    }


async def stop_all() -> None:
    for t in _tasks.values():
        t.cancel()
    _tasks.clear()
