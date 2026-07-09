"""Entrenamiento de los bots: backtest + optimización walk-forward.

Para cada símbolo se descarga histórico real, se calcula UNA vez la serie de
señal (la misma función que usa el bot en vivo) y se prueba una rejilla de
parámetros de ejecución (umbral de entrada, SL, TP y trailing en múltiplos
de ATR) sobre el 70 % inicial de los datos. El mejor conjunto se valida
sobre el 30 % final que la optimización nunca vio (walk-forward), y solo se
aplica al bot en vivo si la validación es rentable — la defensa estándar
contra el sobreajuste. Los parámetros aplicados se guardan en SQLite
(tabla bot_params) y el bot los usa por símbolo.

Los costes (spread) se descuentan igual que en vivo. El oro spot no tiene
histórico gratuito: el scalper entrena XAUUSD con PAXGUSDT de Binance
(oro tokenizado, mismo subyacente, velas 1m ilimitadas) y el swing con los
futuros GC=F de Yahoo.
"""

import asyncio
import json
import logging
import sqlite3
import time
from itertools import product

from . import market_data
from .config import SYMBOLS_BY_ID, Symbol
from .history import DB_PATH
from .indicators import atr_series
from .strategy import resample, score_series

log = logging.getLogger("training")

# (fuente, símbolo) usados SOLO para entrenar, cuando la fuente en vivo
# no tiene histórico suficiente en ese marco temporal.
TRAIN_PROXY: dict[tuple[str, str], tuple[str, str]] = {
    ("scalper", "XAUUSD"): ("binance", "PAXGUSDT"),  # 1m profundo, oro 24/7
    ("swing", "XAUUSD"): ("yahoo", "GC=F"),
}

# Rejilla de parámetros de ejecución (los periodos de los indicadores se
# fijan: cada parámetro libre extra multiplica el riesgo de sobreajuste).
GRID_THRESHOLD = (0.30, 0.375, 0.45, 0.55, 0.65)
GRID_SL = (1.0, 1.5, 2.0, 2.5)
GRID_TP = (1.5, 2.25, 3.0, 4.0)
GRID_TRAIL = (0.8, 1.2, 1.6)

TRAIN_SPLIT = 0.7
MIN_TRAIN_TRADES = {"scalper": 20, "swing": 8}


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
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
    return con


def load_applied_params() -> dict[tuple[str, str], dict]:
    """Parámetros entrenados y validados, por (bot, símbolo)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT bot, symbol, params FROM bot_params WHERE applied = 1"
        ).fetchall()
    return {(b, s): json.loads(p) for b, s, p in rows}


def load_results() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT bot, symbol, params, metrics, applied, trained_at FROM bot_params"
        ).fetchall()
    return [
        {
            "bot": r[0], "symbol": r[1], "params": json.loads(r[2]),
            "metrics": json.loads(r[3]), "applied": bool(r[4]), "trainedAt": r[5],
        }
        for r in rows
    ]


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
    y_int, y_range = {"1m": ("1m", "7d"), "1h": ("60m", "1y")}[interval]
    return await market_data._yahoo_candles(source_symbol, y_int, y_range, 100_000)


async def fetch_history(bot_id: str, sym: Symbol, interval: str) -> list[dict]:
    source, source_symbol = TRAIN_PROXY.get(
        (bot_id, sym.id), (sym.source, sym.source_symbol)
    )
    if source == "swissquote":  # sin histórico propio: velas del futuro proxy
        source, source_symbol = "yahoo", market_data.SWISSQUOTE_CANDLE_PROXY[sym.source_symbol]
    if source == "binance":
        days = 21 if interval == "1m" else 365
        return await _binance_history(source_symbol, interval, days)
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
    max_margin: float,
    leverage: float,
    start: int,
    end: int,
) -> dict:
    """Backtest de una configuración sobre candles[start:end]. Sin capitalizar
    (tamaño fijo por operación) para que las métricas sean comparables."""
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
        else:
            losses += 1
            gross_loss -= p
        equity += p
        equity_peak = max(equity_peak, equity)
        max_dd = max(max_dd, equity_peak - equity)
        direction = 0
        cooldown = cooldown_bars

    for i in range(max(start, 1), end):
        c = candles[i]
        half = c["close"] * spread_pct / 2

        if direction:
            bars_held += 1
            if direction > 0:
                if c["low"] <= sl:            # el stop primero: es lo conservador
                    close(sl - half)
                    continue
                if c["high"] >= tp:
                    close(tp - half)
                    continue
                best = max(best, c["high"])
                if best >= entry + sl_atr * atr0:   # breakeven + trailing
                    sl = max(sl, entry + 2 * half, best - trail_atr * atr0)
            else:
                if c["high"] >= sl:
                    close(sl + half)
                    continue
                if c["low"] <= tp:
                    close(tp + half)
                    continue
                best = min(best, c["low"])
                if best <= entry - sl_atr * atr0:
                    sl = min(sl, entry - 2 * half, best + trail_atr * atr0)
            if bars_held >= max_hold_bars:
                close(c["close"] - half if direction > 0 else c["close"] + half)
            continue

        if cooldown:
            cooldown -= 1
            continue
        s, a = scores[i], atrs[i]
        if a is None or a <= 0 or abs(s) < threshold:
            continue
        # solo en el CRUCE del umbral (inicio de la señal, no a mitad del movimiento)
        if abs(scores[i - 1]) >= threshold:
            continue
        # filtro de coste: sin volatilidad suficiente el spread se come el beneficio
        if a < 3 * c["close"] * spread_pct:
            continue
        direction = 1 if s > 0 else -1
        entry = c["close"] + half * direction
        atr0 = a
        sl = entry - direction * sl_atr * a
        tp = entry + direction * tp_atr * a
        qty = min(risk_amount / (sl_atr * a), max_margin * leverage / entry)
        best = entry
        bars_held = 0

    n = wins + losses
    return {
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
    max_margin: float,
    leverage: float,
) -> dict:
    split = int(len(candles) * TRAIN_SPLIT)
    min_trades = MIN_TRAIN_TRADES[bot_id]

    best_cfg: tuple | None = None
    best_score = float("-inf")
    best_train: dict | None = None
    for th, sl, tp, tr in product(GRID_THRESHOLD, GRID_SL, GRID_TP, GRID_TRAIL):
        if tp < sl:  # exigir recompensa >= riesgo inicial
            continue
        m = simulate(
            candles, scores, atrs, spread_pct, th, sl, tp, tr,
            max_hold_bars, cooldown_bars, risk_amount, max_margin, leverage,
            0, split,
        )
        if m["trades"] < min_trades:
            continue
        pf = m["profitFactor"] or 0.0
        score = m["pnl"] * min(pf, 3.0)  # rentabilidad ponderada por consistencia
        if m["pnl"] > 0 and score > best_score:
            best_score, best_cfg, best_train = score, (th, sl, tp, tr), m

    if best_cfg is None:
        return {"ok": False, "reason": "ninguna configuración rentable en el tramo de entrenamiento"}

    th, sl, tp, tr = best_cfg
    val = simulate(
        candles, scores, atrs, spread_pct, th, sl, tp, tr,
        max_hold_bars, cooldown_bars, risk_amount, max_margin, leverage,
        split, len(candles),
    )
    return {
        "ok": True,
        "params": {"entryThreshold": th, "slAtr": sl, "tpAtr": tp, "trailAtr": tr},
        "train": best_train,
        "validation": val,
        "applied": val["pnl"] > 0 and val["trades"] > 0,
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
    if len(c_work) < 500:
        return {"symbol": sym.id, "ok": False, "reason": f"histórico insuficiente ({len(c_work)} velas)"}
    trend_step = 300 if cfg.id == "scalper" else 86400
    c_trend = resample(c_work, trend_step)

    work_seconds = 60 if cfg.work_interval == "1m" else 3600
    max_hold_bars = max(1, cfg.max_hold_seconds // work_seconds)
    cooldown_bars = max(1, cfg.cooldown_seconds // work_seconds)
    spread = bot.spread_pct(sym)

    def _run() -> dict:
        scores = score_series(cfg.id, c_work, c_trend)
        atrs = atr_series(c_work, cfg.atr_period)
        return optimize(
            cfg.id, c_work, scores, atrs, spread, max_hold_bars, cooldown_bars,
            risk_amount=cfg.initial_balance * cfg.risk_pct,
            max_margin=cfg.max_margin, leverage=cfg.max_leverage,
        )

    result = await asyncio.to_thread(_run)
    result["symbol"] = sym.id
    if result.get("ok"):
        with _conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO bot_params VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cfg.id, sym.id,
                    json.dumps(result["params"]),
                    json.dumps({"train": result["train"], "validation": result["validation"]}),
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
            status["results"].append(result)
            log.info("entrenado %s/%s: %s", bot.cfg.id, symbol_id,
                     "aplicado" if result.get("applied") else result.get("reason", "no aplicado"))
        bot.reload_params()
    finally:
        status.update(running=False, current=None, finishedAt=int(time.time()))


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
