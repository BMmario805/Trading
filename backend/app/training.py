"""Entrenamiento de los bots: backtest + optimización walk-forward.

Para cada símbolo se descarga histórico real, se calcula UNA vez la serie de
señal (la misma función que usa el bot en vivo) y se prueba una rejilla de
parámetros de ejecución (umbral de entrada, SL, TP y trailing en múltiplos
de ATR) sobre el 70 % inicial de los datos. El mejor conjunto se valida
sobre el 30 % final que la optimización nunca vio (walk-forward), y solo se
aplica al bot en vivo si la validación es rentable — la defensa estándar
contra el sobreajuste. Los parámetros aplicados se guardan en SQLite
(tabla bot_params) y el bot los usa por símbolo.

El spread y la sobreextensión se filtran en todos los tramos. Para XAUUSD
se priorizan las velas del terminal MT5 DEMO autorizado. Sin terminal se
usará un proxy PAXG/GC solamente informativo, sin validar el spot.
No se dispone de tasas históricas de financiación o swaps en el simulador.
"""

import asyncio
import json
import logging
import sqlite3
import time
from itertools import product

from . import market_data
from . import mt5_market
from .config import SYMBOLS_BY_ID, Symbol
from .history import DB_PATH
from .database import Connection
from .execution import closed_candles, fresh_signal
from .indicators import atr_series
from .strategy import TREND_STEP, resample, score_series, extension_series

log = logging.getLogger("training")

# Rejilla de parámetros de ejecución (los periodos de los indicadores se
# fijan: cada parámetro libre extra multiplica el riesgo de sobreajuste).
# Incluye umbrales bajos (0.20/0.25): si la validación los sostiene, el bot
# opera más a menudo; si no, el walk-forward los descarta solo.
GRID_THRESHOLD = (0.20, 0.25, 0.30, 0.375, 0.45, 0.55, 0.65, 0.75)
GRID_SL = (1.0, 1.5, 2.0, 2.5, 3.0)
GRID_TP = (1.5, 2.25, 3.0, 4.0, 6.0)
GRID_TRAIL = (0.8, 1.2, 1.6, 2.4)

TRAIN_SPLIT = 0.7
# Embargo entre entrenamiento y validación: se descartan velas pegadas al
# corte para que ninguna posición/indicador del tramo de entrenamiento
# contamine la validación (purged walk-forward, práctica estándar actual)
EMBARGO_BARS = 30
MIN_TRAIN_TRADES = {
    "flow": 10, "scalper": 20, "swing": 8, "trend": 6, "meanrev": 8, "orb": 10, "squeeze": 8,
}
MIN_VALIDATION_TRADES = 8
ENGINE_VERSION = 3

# Segundos por vela de cada intervalo de trabajo
WORK_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

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

    direction = 0        # 0 sin posición, +1 largo, -1 corto
    entry = sl = tp = qty = atr0 = best = 0.0
    bars_held = 0
    cooldown = 0

    def close(price: float) -> None:
        nonlocal pnl, wins, losses, gross_win, gross_loss, direction
        nonlocal equity, equity_peak, max_dd, cooldown
        p = (price - entry) * qty * direction
        pnl += p
        if p >= 0:
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
        half = c["close"] * spread_pct / 2

        if direction:
            bars_held += 1
            if direction > 0:
                if c["low"] <= sl:            # el stop primero: es lo conservador
                    close(min(sl, c["open"]) - half)
                    continue
                if c["high"] >= tp:
                    close(tp - half)
                    continue
                best = max(best, c["high"])
                if best >= entry + sl_atr * atr0:   # breakeven + trailing
                    # el trailing se estrecha tras 2R (protege la ganancia)
                    tr = trail_atr * (0.75 if best >= entry + 2 * sl_atr * atr0 else 1.0)
                    sl = max(sl, entry + 2 * half, best - tr * atr0)
            else:
                if c["high"] >= sl:
                    close(max(sl, c["open"]) + half)
                    continue
                if c["low"] <= tp:
                    close(tp + half)
                    continue
                best = min(best, c["low"])
                if best <= entry - sl_atr * atr0:
                    tr = trail_atr * (0.75 if best <= entry - 2 * sl_atr * atr0 else 1.0)
                    sl = min(sl, entry - 2 * half, best + tr * atr0)
            # señal claramente contraria (histéresis 0.75×umbral, como en vivo)
            if direction and scores[i] * direction <= -0.75 * threshold:
                close(c["close"] - half if direction > 0 else c["close"] + half)
                continue
            if direction and bars_held >= max_hold_bars:
                close(c["close"] - half if direction > 0 else c["close"] + half)
            continue

        if cooldown:
            cooldown -= 1
            continue
        s, a = scores[i], atrs[i]
        if a is None or a <= 0 or abs(s) < threshold:
            continue
        # solo con señal FRESCA: el cruce del umbral ocurrió en las últimas
        # 3 velas (la misma ventana anti-madurez que aplica el bot en vivo)
        if not fresh_signal(s, scores[max(0, i - 3):i], threshold):
            continue
        # filtro de coste: sin volatilidad suficiente el spread se come el beneficio
        if a < 3 * c["close"] * spread_pct:
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
        entry = fill + fill * spread_pct / 2 * direction
        atr0 = a
        sl = entry - direction * sl_atr * a
        tp = entry + direction * tp_atr * a
        qty = min(risk_amount / (sl_atr * a + entry * spread_pct / 2), notional_cap / entry)
        best = entry
        bars_held = 0

    if direction:
        final_price = candles[end - 1]["close"]
        close(final_price * (1 - direction * spread_pct / 2))
    n = wins + losses
    return {
        "expectancy": round(pnl / n, 4) if n else None,
        "trades": n,
        "wins": wins,
        "pnl": round(pnl, 2),
        "winRate": round(wins / n * 100, 1) if n else None,
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "maxDrawdown": round(max_dd, 2),
    }


# ---------------------------------------------------------------------------
# Optimización walk-forward
# ---------------------------------------------------------------------------


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
) -> dict:
    extensions = extension_series(candles)
    split = int(len(candles) * TRAIN_SPLIT)
    min_trades = MIN_TRAIN_TRADES.get(bot_id, 8)

    best_cfg: tuple | None = None
    best_score = float("-inf")
    best_train: dict | None = None
    for th, sl, tp, tr in product(GRID_THRESHOLD, GRID_SL, GRID_TP, GRID_TRAIL):
        # recompensa >= riesgo inicial, salvo en reversión a la media (su perfil
        # clásico es ganancia corta y frecuente con stop amplio)
        if tp < sl and bot_id != "meanrev":
            continue
        m = simulate(
            candles, scores, atrs, spread_pct, th, sl, tp, tr,
            max_hold_bars, cooldown_bars, risk_amount, notional_cap,
            0, split,
            extensions, overext_atr,
        )
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
        return {"ok": False, "engineVersion": ENGINE_VERSION, "reason": "ninguna configuración rentable en el tramo de entrenamiento"}

    th, sl, tp, tr = best_cfg
    val = simulate(
        candles, scores, atrs, spread_pct, th, sl, tp, tr,
        max_hold_bars, cooldown_bars, risk_amount, notional_cap,
        min(split + EMBARGO_BARS, len(candles)), len(candles),
        extensions, overext_atr,
    )
    val_start = min(split + EMBARGO_BARS, len(candles))
    middle = (val_start + len(candles)) // 2
    segments = [simulate(
        candles, scores, atrs, spread_pct, th, sl, tp, tr,
        max_hold_bars, cooldown_bars, risk_amount, notional_cap, lo, hi,
        extensions, overext_atr,
    ) for lo, hi in ((val_start, middle), (middle, len(candles)))]
    stress = simulate(
        candles, scores, atrs, spread_pct * 2, th, sl, tp, tr,
        max_hold_bars, cooldown_bars, risk_amount, notional_cap, val_start, len(candles),
        extensions, overext_atr,
    )
    accepted = (val["pnl"] > 0 and val["trades"] >= MIN_VALIDATION_TRADES
                and all(m["pnl"] > 0 and m["trades"] >= 2 for m in segments)
                and stress["pnl"] > 0 and stress["trades"] >= MIN_VALIDATION_TRADES)
    return {
        "ok": True,
        "params": {"entryThreshold": th, "slAtr": sl, "tpAtr": tp, "trailAtr": tr},
        "train": best_train,
        "validation": val,
        "applied": accepted,
        "segments": segments,
        "stress": stress,
        "engineVersion": ENGINE_VERSION,
        "reason": "Validación y costes reforzados superados" if accepted else "Muestra, consistencia temporal o costes insuficientes",
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
                    json.dumps({k: result.get(k) for k in ("train", "validation", "segments", "stress", "engineVersion", "reason", "source", "proxy")}),
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
