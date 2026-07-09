"""Bots de paper trading en tiempo real sobre los precios reales del panel.

Dos bots operan en paralelo con dinero SIMULADO (no hay bróker conectado):

  - "scalper": operaciones a corto plazo (minutos) sobre velas de 1m con
    filtro de tendencia en 5m. Foco en XAU/USD y mercados líquidos 24h.
  - "swing": operaciones a largo plazo (horas/días) sobre velas de 1h con
    filtro de tendencia diario y más peso de la opinión pública.

La señal por vela viene de `strategy.py` (régimen adaptativo por ADX:
seguimiento de tendencia cuando el ADX es alto, reversión en rango cuando
es bajo) y se mezcla con el sentimiento cacheado de las fuentes. Si supera
el umbral se abre una posición con stop-loss y take-profit por ATR, margen
limitado por operación y trailing stop que pasa a breakeven al ganar 1R.

Los parámetros de ejecución (umbral, SL, TP, trailing) pueden venir del
entrenamiento walk-forward (`training.py`), por símbolo: si hay parámetros
validados en bot_params, sustituyen a los del config.

El sentimiento nunca bloquea al bot: usa `sentiment.peek_cached` y, si no
hay caché fresca, dispara el refresco en segundo plano y opera solo con la
parte técnica en ese ciclo. Todo queda en SQLite y sobrevive a reinicios.
"""

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import market_data
from . import sentiment as sentiment_mod
from .config import SYMBOLS_BY_ID, Symbol
from .history import DB_PATH
from .indicators import atr
from .strategy import last_bar_reasons, score_series

log = logging.getLogger("bots")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

# Coste de fricción simulado (spread ida+vuelta) por categoría, fracción del precio
SPREAD_PCT = {
    "metales": 0.0002,   # XAUUSD ~0.02 % (spread retail típico)
    "cripto": 0.0005,
    "acciones": 0.0003,
    "indices": 0.0002,
    "forex": 0.0001,
    "futuros": 0.0002,
}

# Solapamiento Londres-Nueva York (UTC): la franja más líquida del oro
GOLD_SESSION_UTC = range(12, 17)


@dataclass(frozen=True)
class BotConfig:
    id: str
    name: str
    horizon: str                 # descripción del plazo, para la UI
    universe: tuple[str, ...]    # símbolos que vigila (el primero = foco)
    work_interval: str           # velas con las que decide
    trend_interval: str          # velas del filtro de tendencia
    scan_seconds: float          # cadencia de búsqueda de entradas
    tick_seconds: float          # cadencia de vigilancia de posiciones
    weight_tech: float
    weight_senti: float
    entry_threshold: float       # |señal combinada| mínima para entrar
    atr_period: int
    sl_atr: float                # distancia del stop en múltiplos de ATR
    tp_atr: float                # distancia del take-profit en múltiplos de ATR
    trail_atr: float             # trailing stop en múltiplos de ATR (tras 1R)
    risk_pct: float              # fracción del balance arriesgada por operación
    max_margin: float            # margen máximo comprometido por operación
    max_equity: float            # equity máxima (nocional) por operación
    max_leverage: float          # tope de nocional / margen
    max_positions: int
    max_hold_seconds: int        # cierre forzoso por tiempo
    cooldown_seconds: int        # espera tras cerrar antes de reentrar al símbolo
    focus_threshold_mult: float  # descuento de umbral para el símbolo foco
    initial_balance: float = 10_000.0


SCALPER = BotConfig(
    id="scalper",
    name="Bot Scalper",
    horizon="corto plazo (minutos)",
    universe=("XAUUSD", "PAXG", "BTC", "ETH", "SOL", "EURUSD", "GBPUSD", "USDJPY"),
    work_interval="1m",
    trend_interval="5m",
    scan_seconds=15,
    tick_seconds=5,
    weight_tech=0.85,
    weight_senti=0.15,
    entry_threshold=0.20,
    atr_period=14,
    sl_atr=1.5,
    tp_atr=2.25,
    trail_atr=1.2,
    risk_pct=0.01,
    max_margin=100.0,
    max_equity=100.0,
    max_leverage=20.0,
    max_positions=5,
    max_hold_seconds=45 * 60,
    cooldown_seconds=60,
    focus_threshold_mult=0.85,
)

SWING = BotConfig(
    id="swing",
    name="Bot Swing",
    horizon="largo plazo (horas/días)",
    universe=(
        "XAUUSD", "PAXG", "BTC", "ETH", "SOL", "XRP",
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL",
        "SPY", "QQQ", "EURUSD", "GBPUSD", "USDJPY", "CL",
    ),
    work_interval="1h",
    trend_interval="1d",
    scan_seconds=120,
    tick_seconds=20,
    weight_tech=0.6,
    weight_senti=0.4,
    entry_threshold=0.18,
    atr_period=14,
    sl_atr=2.5,
    tp_atr=4.0,
    trail_atr=1.5,
    risk_pct=0.02,
    max_margin=100.0,
    max_equity=100.0,
    max_leverage=5.0,
    max_positions=8,
    max_hold_seconds=5 * 86400,
    cooldown_seconds=10 * 60,
    focus_threshold_mult=0.85,
)

# ---------------------------------------------------------------------------
# Persistencia (mismo data.db que el histórico de sentimiento)
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS bot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_time INTEGER NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            reasons TEXT NOT NULL,
            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            pnl REAL,
            status TEXT NOT NULL DEFAULT 'open',
            atr_entry REAL,
            best_price REAL
        )"""
    )
    for col in ("atr_entry REAL", "best_price REAL"):  # migración de la v1
        try:
            con.execute(f"ALTER TABLE bot_trades ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_trades ON bot_trades(bot, status, entry_time)"
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS bot_state (
            bot TEXT PRIMARY KEY,
            balance REAL NOT NULL,
            running INTEGER NOT NULL
        )"""
    )
    return con


# ---------------------------------------------------------------------------
# Posiciones y motor
# ---------------------------------------------------------------------------


@dataclass
class Position:
    id: int
    symbol: str
    direction: str            # "long" | "short"
    qty: float
    entry_price: float
    entry_time: int
    sl: float
    tp: float
    atr_entry: float
    best_price: float
    trail_atr: float
    r_dist: float             # distancia inicial al stop (1R), para el trailing
    reasons: list[str] = field(default_factory=list)
    last_price: float = 0.0

    def unrealized(self, price: float | None = None) -> float:
        p = price if price is not None else self.last_price
        sign = 1.0 if self.direction == "long" else -1.0
        return (p - self.entry_price) * self.qty * sign

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "qty": round(self.qty, 6),
            "entryPrice": self.entry_price,
            "entryTime": self.entry_time,
            "sl": self.sl,
            "tp": self.tp,
            "lastPrice": self.last_price,
            "unrealizedPnl": round(self.unrealized(), 2),
            "reasons": self.reasons,
        }


# tareas de refresco de sentimiento en segundo plano (compartidas entre bots)
_senti_tasks: dict[str, asyncio.Task] = {}


def _kick_sentiment_refresh(sym: Symbol) -> None:
    task = _senti_tasks.get(sym.id)
    if task and not task.done():
        return

    async def _refresh() -> None:
        try:
            await sentiment_mod.get_sentiment(sym)
        except Exception as exc:
            log.debug("refresco de sentimiento %s falló: %s", sym.id, exc)

    _senti_tasks[sym.id] = asyncio.create_task(_refresh())


class TradingBot:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.balance = cfg.initial_balance
        self.running = True
        self.positions: dict[str, Position] = {}
        self.cooldown_until: dict[str, float] = {}
        self.trained_params: dict[str, dict] = {}   # símbolo -> params validados
        self._task: asyncio.Task | None = None
        self._last_scan = 0.0

    def spread_pct(self, sym: Symbol) -> float:
        return SPREAD_PCT.get(sym.category, 0.0003)

    def reload_params(self) -> None:
        """Carga los parámetros entrenados y validados (tabla bot_params)."""
        from . import training

        self.trained_params = {
            s: p for (b, s), p in training.load_applied_params().items()
            if b == self.cfg.id
        }
        if self.trained_params:
            log.info("%s: parámetros entrenados para %s",
                     self.cfg.id, ", ".join(sorted(self.trained_params)))

    def _exec_params(self, sym: Symbol) -> tuple[float, float, float, float]:
        """(umbral, sl_atr, tp_atr, trail_atr) efectivos para un símbolo."""
        p = self.trained_params.get(sym.id)
        threshold = p["entryThreshold"] if p else self.cfg.entry_threshold
        sl = p["slAtr"] if p else self.cfg.sl_atr
        tp = p["tpAtr"] if p else self.cfg.tp_atr
        trail = p["trailAtr"] if p else self.cfg.trail_atr
        if sym.id == self.cfg.universe[0]:
            threshold *= self.cfg.focus_threshold_mult
            # sesión Londres-NY: la franja más operable del oro
            if self.cfg.id == "scalper" and datetime.now(timezone.utc).hour in GOLD_SESSION_UTC:
                threshold *= 0.9
        return threshold, sl, tp, trail

    # -------------------- persistencia --------------------

    def load_state(self) -> None:
        with _conn() as con:
            row = con.execute(
                "SELECT balance, running FROM bot_state WHERE bot = ?", (self.cfg.id,)
            ).fetchone()
            if row:
                self.balance, self.running = row[0], bool(row[1])
            else:
                con.execute(
                    "INSERT INTO bot_state VALUES (?, ?, 1)",
                    (self.cfg.id, self.balance),
                )
            open_rows = con.execute(
                """SELECT id, symbol, direction, qty, entry_price, entry_time,
                          sl, tp, reasons, atr_entry, best_price FROM bot_trades
                   WHERE bot = ? AND status = 'open'""",
                (self.cfg.id,),
            ).fetchall()
        for r in open_rows:
            pos = Position(
                id=r[0], symbol=r[1], direction=r[2], qty=r[3],
                entry_price=r[4], entry_time=r[5], sl=r[6], tp=r[7],
                reasons=json.loads(r[8]),
                atr_entry=r[9] or abs(r[4] - r[6]) / max(self.cfg.sl_atr, 0.1),
                best_price=r[10] or r[4],
                trail_atr=self.cfg.trail_atr,
                r_dist=(r[9] or abs(r[4] - r[6])) * self.cfg.sl_atr if r[9] else abs(r[4] - r[6]),
                last_price=r[4],
            )
            self.positions[pos.symbol] = pos
        self.reload_params()
        if open_rows:
            log.info("%s: %d posiciones abiertas recuperadas", self.cfg.id, len(open_rows))

    def _save_state(self) -> None:
        with _conn() as con:
            con.execute(
                "UPDATE bot_state SET balance = ?, running = ? WHERE bot = ?",
                (self.balance, int(self.running), self.cfg.id),
            )

    # -------------------- ciclo principal --------------------

    def start_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"bot-{self.cfg.id}")

    async def stop_task(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        log.info("%s arrancado (running=%s)", self.cfg.name, self.running)
        while True:
            try:
                if self.running:
                    await self._manage_positions()
                    now = time.time()
                    if now - self._last_scan >= self.cfg.scan_seconds:
                        self._last_scan = now
                        await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("%s: error en el ciclo: %s", self.cfg.id, exc)
            await asyncio.sleep(self.cfg.tick_seconds)

    # -------------------- vigilancia de posiciones --------------------

    async def _manage_positions(self) -> None:
        for pos in list(self.positions.values()):
            sym = SYMBOLS_BY_ID[pos.symbol]
            try:
                tick = await market_data.get_price(sym)
            except Exception as exc:
                log.debug("%s: sin precio de %s: %s", self.cfg.id, pos.symbol, exc)
                continue
            price = tick["price"]
            pos.last_price = price
            half_spread = price * self.spread_pct(sym) / 2

            if pos.direction == "long":
                if price <= pos.sl:
                    self._close(pos, pos.sl - half_spread, "stop-loss")
                    continue
                if price >= pos.tp:
                    self._close(pos, pos.tp - half_spread, "take-profit")
                    continue
                pos.best_price = max(pos.best_price, price)
                # tras avanzar 1R: stop a breakeven y trailing por ATR
                if pos.best_price >= pos.entry_price + pos.r_dist:
                    new_sl = max(
                        pos.sl,
                        pos.entry_price + 2 * half_spread,
                        pos.best_price - pos.trail_atr * pos.atr_entry,
                    )
                    if new_sl > pos.sl:
                        self._update_stop(pos, new_sl)
            else:
                if price >= pos.sl:
                    self._close(pos, pos.sl + half_spread, "stop-loss")
                    continue
                if price <= pos.tp:
                    self._close(pos, pos.tp + half_spread, "take-profit")
                    continue
                pos.best_price = min(pos.best_price, price)
                if pos.best_price <= pos.entry_price - pos.r_dist:
                    new_sl = min(
                        pos.sl,
                        pos.entry_price - 2 * half_spread,
                        pos.best_price + pos.trail_atr * pos.atr_entry,
                    )
                    if new_sl < pos.sl:
                        self._update_stop(pos, new_sl)

            if time.time() - pos.entry_time >= self.cfg.max_hold_seconds:
                exit_p = price - half_spread if pos.direction == "long" else price + half_spread
                self._close(pos, exit_p, "tiempo agotado")

    def _update_stop(self, pos: Position, new_sl: float) -> None:
        pos.sl = new_sl
        with _conn() as con:
            con.execute(
                "UPDATE bot_trades SET sl = ?, best_price = ? WHERE id = ?",
                (pos.sl, pos.best_price, pos.id),
            )

    # -------------------- búsqueda de entradas --------------------

    async def _scan(self) -> None:
        for symbol_id in self.cfg.universe:
            has_pos = symbol_id in self.positions
            if not has_pos and (
                len(self.positions) >= self.cfg.max_positions
                or time.time() < self.cooldown_until.get(symbol_id, 0)
            ):
                continue
            sym = SYMBOLS_BY_ID.get(symbol_id)
            if not sym:
                continue
            try:
                signal = await self._evaluate(sym)
            except Exception as exc:
                log.debug("%s: no se pudo evaluar %s: %s", self.cfg.id, symbol_id, exc)
                continue
            if signal is None:
                continue
            score, prev_score, reasons, atr_val, price, stale = signal
            threshold, sl_m, tp_m, trail_m = self._exec_params(sym)

            if has_pos:
                # señal claramente contraria a la posición abierta -> cerrar
                pos = self.positions[symbol_id]
                against = (pos.direction == "long" and score <= -0.5 * threshold) or (
                    pos.direction == "short" and score >= 0.5 * threshold
                )
                if against:
                    half_spread = price * self.spread_pct(sym) / 2
                    exit_p = price - half_spread if pos.direction == "long" else price + half_spread
                    self._close(pos, exit_p, "señal contraria")
                continue

            if abs(score) < threshold or atr_val <= 0:
                continue
            # filtro de coste: sin volatilidad suficiente el spread come el beneficio
            if atr_val < 1.5 * price * self.spread_pct(sym):
                continue
            self._open(
                sym, "long" if score > 0 else "short",
                price, atr_val, score, reasons, sl_m, tp_m, trail_m,
            )

    async def _evaluate(
        self, sym: Symbol
    ) -> tuple[float, float, list[str], float, float, bool] | None:
        """(señal, señal de la vela anterior, motivos, ATR, precio, obsoleto)."""
        c_work, c_trend = await asyncio.gather(
            market_data.get_candles(sym, self.cfg.work_interval),
            market_data.get_candles(sym, self.cfg.trend_interval),
        )
        if len(c_work) < 60:
            return None

        series = score_series(self.cfg.id, c_work, c_trend)
        tech_score, tech_prev = series[-1], series[-2]
        reasons = last_bar_reasons(self.cfg.id, c_work, c_trend)

        senti = sentiment_mod.peek_cached(sym.id)
        if senti is None or senti.get("sampleSize", 0) == 0:
            _kick_sentiment_refresh(sym)
            combined, prev = tech_score, tech_prev
            reasons.append("Sin sentimiento fresco: solo técnico")
        else:
            senti_score = senti["bullishPct"] / 50 - 1  # [0,100] -> [-1,1]
            w_t, w_s = self.cfg.weight_tech, self.cfg.weight_senti
            combined = w_t * tech_score + w_s * senti_score
            prev = w_t * tech_prev + w_s * senti_score
            reasons.append(
                f"Opinión pública {senti['bullishPct']}% alcista (n={senti['sampleSize']})"
            )
        threshold = self._exec_params(sym)[0]
        reasons.append(f"Señal combinada {combined:+.2f} (umbral ±{threshold:.2f})")

        # mercado cerrado / datos viejos: no abrir con la última vela rancia
        work_s = INTERVAL_SECONDS[self.cfg.work_interval]
        stale = time.time() - c_work[-1]["time"] > 3 * work_s + 60

        atr_val = atr(c_work, self.cfg.atr_period) or 0.0
        return combined, prev, reasons, atr_val, c_work[-1]["close"], stale

    # -------------------- ejecución simulada --------------------

    def _open(
        self, sym: Symbol, direction: str, price: float, atr_val: float,
        score: float, reasons: list[str], sl_m: float, tp_m: float, trail_m: float,
    ) -> None:
        half_spread = price * self.spread_pct(sym) / 2
        entry = price + half_spread if direction == "long" else price - half_spread
        sl_dist = atr_val * sl_m
        tp_dist = atr_val * tp_m
        if direction == "long":
            sl, tp = entry - sl_dist, entry + tp_dist
        else:
            sl, tp = entry + sl_dist, entry - tp_dist

        # tamaño: riesgo objetivo, limitado por margen y equity máxima por operación
        qty = (self.balance * self.cfg.risk_pct) / sl_dist
        qty = min(qty, self.cfg.max_margin * self.cfg.max_leverage / entry)
        # Tope de nocional: max_equity € por operación
        qty = min(qty, self.cfg.max_equity / entry)
        if qty * entry < 0.01:
            return

        now = int(time.time())
        with _conn() as con:
            cur = con.execute(
                """INSERT INTO bot_trades
                   (bot, symbol, direction, qty, entry_price, entry_time,
                    sl, tp, reasons, atr_entry, best_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.cfg.id, sym.id, direction, qty, entry, now, sl, tp,
                 json.dumps(reasons, ensure_ascii=False), atr_val, entry),
            )
            trade_id = cur.lastrowid
        pos = Position(
            id=trade_id, symbol=sym.id, direction=direction, qty=qty,
            entry_price=entry, entry_time=now, sl=sl, tp=tp,
            atr_entry=atr_val, best_price=entry, trail_atr=trail_m,
            r_dist=sl_dist, reasons=reasons, last_price=entry,
        )
        self.positions[sym.id] = pos
        log.info(
            "%s ABRE %s %s qty=%.4f @ %.4f SL=%.4f TP=%.4f margen=%.0f (señal %+.2f)",
            self.cfg.id, direction.upper(), sym.id, qty, entry, sl, tp,
            qty * entry / self.cfg.max_leverage, score,
        )

    def _close(self, pos: Position, exit_price: float, reason: str) -> None:
        sign = 1.0 if pos.direction == "long" else -1.0
        pnl = (exit_price - pos.entry_price) * pos.qty * sign
        self.balance += pnl
        now = int(time.time())
        with _conn() as con:
            con.execute(
                """UPDATE bot_trades SET exit_price = ?, exit_time = ?,
                   exit_reason = ?, pnl = ?, status = 'closed' WHERE id = ?""",
                (exit_price, now, reason, pnl, pos.id),
            )
        self.positions.pop(pos.symbol, None)
        self.cooldown_until[pos.symbol] = time.time() + self.cfg.cooldown_seconds
        self._save_state()
        log.info(
            "%s CIERRA %s %s @ %.4f (%s) PnL=%+.2f balance=%.2f",
            self.cfg.id, pos.direction.upper(), pos.symbol, exit_price,
            reason, pnl, self.balance,
        )

    # -------------------- control y estado --------------------

    def set_running(self, running: bool) -> None:
        self.running = running
        self._save_state()

    def reset(self) -> None:
        """Borra historial y posiciones y vuelve al balance inicial."""
        with _conn() as con:
            con.execute("DELETE FROM bot_trades WHERE bot = ?", (self.cfg.id,))
        self.positions.clear()
        self.cooldown_until.clear()
        self.balance = self.cfg.initial_balance
        self._save_state()

    def status(self) -> dict:
        unrealized = sum(p.unrealized() for p in self.positions.values())
        with _conn() as con:
            n, wins, losses, realized = con.execute(
                """SELECT COUNT(*),
                          COALESCE(SUM(pnl > 0), 0),
                          COALESCE(SUM(pnl < 0), 0),
                          COALESCE(SUM(pnl), 0)
                   FROM bot_trades WHERE bot = ? AND status = 'closed'""",
                (self.cfg.id,),
            ).fetchone()
        return {
            "id": self.cfg.id,
            "name": self.cfg.name,
            "horizon": self.cfg.horizon,
            "running": self.running,
            "initialBalance": self.cfg.initial_balance,
            "balance": round(self.balance, 2),
            "equity": round(self.balance + unrealized, 2),
            "openPositions": [p.to_dict() for p in self.positions.values()],
            "stats": {
                "closedTrades": n,
                "wins": wins,
                "losses": losses,
                "winRate": round(wins / n * 100, 1) if n else None,
                "realizedPnl": round(realized, 2),
            },
            "trainedSymbols": sorted(self.trained_params),
            "config": {
                "universe": list(self.cfg.universe),
                "workInterval": self.cfg.work_interval,
                "trendInterval": self.cfg.trend_interval,
                "scanSeconds": self.cfg.scan_seconds,
                "riskPct": self.cfg.risk_pct * 100,
                "maxMargin": self.cfg.max_margin,
                "maxEquity": self.cfg.max_equity,
                "slAtr": self.cfg.sl_atr,
                "tpAtr": self.cfg.tp_atr,
                "trailAtr": self.cfg.trail_atr,
                "maxPositions": self.cfg.max_positions,
                "maxHoldSeconds": self.cfg.max_hold_seconds,
                "sentimentWeight": self.cfg.weight_senti * 100,
            },
        }

    def trades(self, limit: int = 50) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                """SELECT id, symbol, direction, qty, entry_price, entry_time,
                          sl, tp, exit_price, exit_time, exit_reason, pnl, reasons
                   FROM bot_trades WHERE bot = ? AND status = 'closed'
                   ORDER BY exit_time DESC LIMIT ?""",
                (self.cfg.id, limit),
            ).fetchall()
        return [
            {
                "id": r[0], "symbol": r[1], "direction": r[2],
                "qty": round(r[3], 6), "entryPrice": r[4], "entryTime": r[5],
                "sl": r[6], "tp": r[7], "exitPrice": r[8], "exitTime": r[9],
                "exitReason": r[10], "pnl": round(r[11], 2),
                "reasons": json.loads(r[12]),
            }
            for r in rows
        ]


def performance(
    bot_id: str | None = None,
    symbol: str | None = None,
    since: int | None = None,
    until: int | None = None,
) -> dict:
    """Curva de PnL acumulado de operaciones cerradas, con filtros.

    Devuelve un punto por operación (fusionando las que cierran en el mismo
    segundo, que el gráfico exige tiempos estrictamente crecientes) más un
    resumen y las listas de valores disponibles para los desplegables.
    """
    conds = ["status = 'closed'"]
    params: list = []
    if bot_id:
        conds.append("bot = ?")
        params.append(bot_id)
    if symbol:
        conds.append("symbol = ?")
        params.append(symbol)
    if since:
        conds.append("exit_time >= ?")
        params.append(since)
    if until:
        conds.append("exit_time <= ?")
        params.append(until)
    where = " AND ".join(conds)
    with _conn() as con:
        rows = con.execute(
            f"""SELECT exit_time, pnl, symbol, bot FROM bot_trades
                WHERE {where} ORDER BY exit_time""",
            params,
        ).fetchall()
        symbols = [
            r[0] for r in con.execute(
                "SELECT DISTINCT symbol FROM bot_trades WHERE status = 'closed' ORDER BY symbol"
            )
        ]

    points: list[dict] = []
    cum = 0.0
    wins = losses = 0
    best = worst = 0.0
    for ts, pnl, sym, bot in rows:
        cum += pnl
        if pnl >= 0:
            wins += 1
        else:
            losses += 1
        best = max(best, pnl)
        worst = min(worst, pnl)
        point = {
            "ts": ts,
            "cumPnl": round(cum, 2),
            "pnl": round(pnl, 2),
            "symbol": sym,
            "bot": bot,
        }
        if points and points[-1]["ts"] == ts:
            points[-1] = {**point, "pnl": round(points[-1]["pnl"] + pnl, 2)}
        else:
            points.append(point)

    n = len(rows)
    return {
        "points": points,
        "summary": {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "winRate": round(wins / n * 100, 1) if n else None,
            "pnl": round(cum, 2),
            "best": round(best, 2),
            "worst": round(worst, 2),
        },
        "symbols": symbols,
    }


BOTS: dict[str, TradingBot] = {}


def start_all() -> None:
    for cfg in (SCALPER, SWING):
        bot = TradingBot(cfg)
        bot.load_state()
        bot.start_task()
        BOTS[cfg.id] = bot


async def stop_all() -> None:
    for bot in BOTS.values():
        await bot.stop_task()
    for task in _senti_tasks.values():
        task.cancel()
    BOTS.clear()
