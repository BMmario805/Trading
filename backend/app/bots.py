"""Siete bots de paper trading con señales causales y exploración limitada.

Los pares sin validación pueden operar a prueba con riesgo reducido; los
vetos de aprendizaje y los límites de cartera siguen bloqueando entradas.
El sentimiento se conserva como contexto y en los snapshots.
"""

import asyncio
import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from . import learning, gemini_agent
from . import market_data
from . import sentiment as sentiment_mod
from . import systems
from .config import SYMBOLS_BY_ID, Symbol, EXTRA_CRYPTO
from . import opportunities
from .history import DB_PATH
from .database import Connection
from .execution import closed_candles, fresh_signal, valid_tick, atr_spread_multiple
from .training import ENGINE_VERSION
from .indicators import atr
from .strategy import confirmations as strategy_confirmations
from .strategy import extension_atr, score_series
from .economics import cost_profile, execution_fill, net_outcome, planned_trade

log = logging.getLogger("bots")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

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

# Velas de trabajo que se congelan con cada operación para poder redibujar
# después el gráfico tal y como lo vio el bot al entrar
SNAPSHOT_BARS = 150


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
    max_leverage: float          # único límite físico: nocional <= margen × esto
    max_positions: int
    max_hold_seconds: int        # cierre forzoso por tiempo
    cooldown_seconds: int        # espera tras cerrar antes de reentrar al símbolo
    focus_threshold_mult: float  # descuento de umbral para el símbolo foco
    initial_balance: float = 10_000.0
    require_validation: bool = False
    # --- medidas de seguridad (estándar prop-firm) ---
    max_open_risk_pct: float = 0.06  # riesgo inicial agregado por bot
    max_daily_loss_pct: float = 0.05  # pérdida diaria máxima antes de pausar entradas
    max_same_category: int = 6        # posiciones simultáneas misma categoría+dirección
    overext_atr: float = 2.0          # no entrar si el precio está a más de esto de la EMA20


SCALPER = BotConfig(
    id="scalper",
    name="Bot Scalper",
    horizon="corto plazo (minutos)",
    universe=("XAUUSD", "PAXG", "BTC", "ETH", "SOL", "XRP", "DOGE",
              "EURUSD", "GBPUSD", "USDJPY"),
    work_interval="1m",
    trend_interval="5m",
    scan_seconds=15,
    tick_seconds=5,
    weight_tech=0.85,
    weight_senti=0.15,
    entry_threshold=0.15,
    atr_period=14,
    sl_atr=1.5,
    tp_atr=2.25,
    trail_atr=1.2,
    risk_pct=0.01,
    max_leverage=20.0,
    max_positions=10,
    max_hold_seconds=45 * 60,
    cooldown_seconds=60,
    focus_threshold_mult=0.85,
    max_daily_loss_pct=0.05,
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
    max_leverage=5.0,
    max_positions=8,
    max_hold_seconds=5 * 86400,
    cooldown_seconds=10 * 60,
    focus_threshold_mult=0.85,
    max_daily_loss_pct=0.08,
    max_same_category=6,
    overext_atr=2.5,
)

TREND = BotConfig(
    id="trend",
    name="Bot Tendencia (Turtle)",
    horizon="seguimiento de tendencia (días/semanas)",
    universe=(
        "XAUUSD", "PAXG", "BTC", "ETH", "SOL", "XRP", "DOGE", "CL",
        "NVDA", "TSLA", "SPY", "QQQ", "EURUSD", "USDJPY",
    ),
    work_interval="1h",
    trend_interval="4h",
    scan_seconds=60,
    tick_seconds=20,
    weight_tech=0.9,
    weight_senti=0.1,
    entry_threshold=0.35,
    atr_period=14,
    sl_atr=2.0,          # stop 2N clásico de las Turtle
    tp_atr=8.0,          # el objetivo lejos: la salida real es el trailing
    trail_atr=2.4,
    risk_pct=0.02,
    max_leverage=5.0,
    max_positions=10,
    max_hold_seconds=10 * 86400,
    cooldown_seconds=30 * 60,
    focus_threshold_mult=1.0,
    max_daily_loss_pct=0.08,
    max_same_category=6,
    overext_atr=3.5,     # las rupturas nacen estiradas: veto solo lo extremo
)

MEANREV = BotConfig(
    id="meanrev",
    name="Bot Reversión (RSI2)",
    horizon="reversión a la media (horas/días)",
    universe=(
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN",
        "META", "GOOGL", "TSLA", "BTC", "ETH", "SOL",
    ),
    work_interval="1h",
    trend_interval="1d",
    scan_seconds=60,
    tick_seconds=20,
    weight_tech=0.85,
    weight_senti=0.15,
    entry_threshold=0.40,
    atr_period=14,
    sl_atr=2.5,          # stop amplio: la reversión necesita aire
    tp_atr=1.5,          # ganancia corta y frecuente (perfil Connors)
    trail_atr=1.6,
    risk_pct=0.015,
    max_leverage=5.0,
    max_positions=10,
    max_hold_seconds=3 * 86400,
    cooldown_seconds=30 * 60,
    focus_threshold_mult=1.0,
    max_daily_loss_pct=0.06,
    max_same_category=6,
)

ORB = BotConfig(
    id="orb",
    name="Bot Apertura (ORB)",
    horizon="intradía (ruptura de apertura)",
    universe=(
        "SPY", "QQQ", "NVDA", "TSLA", "AAPL", "META",
        "AMZN", "MSFT", "ES", "NQ",
    ),
    work_interval="5m",
    trend_interval="15m",
    scan_seconds=20,
    tick_seconds=5,
    weight_tech=1.0,
    weight_senti=0.0,
    entry_threshold=0.45,
    atr_period=14,
    sl_atr=1.0,          # stop pegado: el rango de apertura es la estructura
    tp_atr=3.0,
    trail_atr=1.2,
    risk_pct=0.01,
    max_leverage=10.0,
    max_positions=8,
    max_hold_seconds=5 * 3600,   # nunca de un día para otro
    cooldown_seconds=15 * 60,
    focus_threshold_mult=1.0,
    max_daily_loss_pct=0.05,
    max_same_category=6,
    overext_atr=3.0,
)

SQUEEZE = BotConfig(
    id="squeeze",
    name="Bot Volatilidad (Squeeze)",
    horizon="expansiones de volatilidad (horas)",
    universe=(
        "PAXG", "XAUUSD", "BTC", "ETH", "SOL", "XRP", "DOGE",
        "EURUSD", "GBPUSD", "USDJPY",
    ),
    work_interval="1h",
    trend_interval="4h",
    scan_seconds=60,
    tick_seconds=10,
    weight_tech=0.9,
    weight_senti=0.1,
    entry_threshold=0.15,
    atr_period=14,
    sl_atr=1.5,
    tp_atr=4.0,          # la expansión post-squeeze suele recorrer mucho
    trail_atr=1.2,
    risk_pct=0.015,
    max_leverage=10.0,
    max_positions=10,
    max_hold_seconds=2 * 86400,
    cooldown_seconds=20 * 60,
    focus_threshold_mult=1.0,
    max_daily_loss_pct=0.06,
    overext_atr=3.0,
)

# Más oportunidades sin aumentar el límite de posiciones ni el riesgo agregado.
SCALPER = replace(SCALPER, universe=SCALPER.universe + EXTRA_CRYPTO[:5])
SWING = replace(SWING, universe=SWING.universe + EXTRA_CRYPTO)
TREND = replace(TREND, universe=TREND.universe + EXTRA_CRYPTO)
SQUEEZE = replace(SQUEEZE, universe=SQUEEZE.universe + EXTRA_CRYPTO)
FLOW = replace(
    SQUEEZE,
    id="flow",
    name="Bot Flujo (CMF)",
    horizon="continuación con flujo y tendencia (horas)",
    universe=("XAUUSD", "BTC", "ETH", "SOL", "XRP", "EURUSD", "GBPUSD", "USDJPY", "NVDA", "TSLA", "AAPL", "MSFT") + EXTRA_CRYPTO,
    entry_threshold=0.50,
    risk_pct=0.005,
    max_positions=8,
    weight_tech=1.0,
    weight_senti=0.0,
    sl_atr=2.0,
    tp_atr=3.0,
    require_validation=False,
)
ALL_BOTS = (SCALPER, SWING, TREND, MEANREV, ORB, SQUEEZE, FLOW)

# ---------------------------------------------------------------------------
# Persistencia (mismo data.db que el histórico de sentimiento)
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, factory=Connection)
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
    # migraciones: v1 -> atr_entry/best_price, v2 -> snapshot (velas + análisis)
    for col in ("atr_entry REAL", "best_price REAL", "snapshot TEXT", "initial_risk_dist REAL", "trail_multiple REAL", "quote_to_account REAL",
                "execution_costs TEXT", "entry_fee REAL", "exit_fee REAL", "carry_cost REAL", "economic_risk_dist REAL"):
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
    con.execute('CREATE TABLE IF NOT EXISTS bot_sessions (bot TEXT PRIMARY KEY, payload TEXT NOT NULL)')
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
    quote_to_account: float = 1.0
    execution_costs: dict | None = None  # None conserva el contrato de costes antiguo
    entry_fee: float = 0.0              # importes en moneda cotizada
    economic_risk_dist: float | None = None

    def exit_fill(self, price: float) -> float:
        if self.execution_costs is None:
            sym = SYMBOLS_BY_ID[self.symbol]
            return execution_fill(price, self.direction, False,
                                  SPREAD_PCT.get(sym.category, .0003), 0.0)
        return execution_fill(price, self.direction, False,
                              self.execution_costs["spreadPct"], self.execution_costs["slippageRate"])

    def carry_cost(self, now: float | None = None) -> float:
        if not self.execution_costs:
            return 0.0
        elapsed = max(0.0, (time.time() if now is None else now) - self.entry_time)
        return self.entry_price * self.qty * self.execution_costs["carryDailyRate"] * elapsed / 86400

    def initial_risk(self) -> float:
        dist = self.economic_risk_dist if self.economic_risk_dist is not None else self.r_dist
        return self.qty * dist * self.quote_to_account

    def break_even_price(self) -> float | None:
        """Precio medio de salida que cubre las fricciones actuales del contrato."""
        if not self.execution_costs:
            return None  # conservar el trailing histórico de posiciones antiguas
        fee = self.execution_costs["feeRate"]
        per_unit = (self.entry_fee + self.carry_cost()) / self.qty
        sign = 1 if self.direction == "long" else -1
        fill = (sign * self.entry_price + per_unit) / (sign - fee)
        factor = self.exit_fill(1.0)
        return max(0.0, fill / factor)

    def unrealized(self, price: float | None = None) -> float:
        p = price if price is not None else self.last_price
        sign = 1.0 if self.direction == "long" else -1.0
        if self.execution_costs is not None:
            return net_outcome(self.entry_price, self.exit_fill(p), self.qty, sign,
                               self.entry_fee, self.execution_costs["feeRate"],
                               self.quote_to_account, self.carry_cost())
        return (p - self.entry_price) * self.qty * sign * self.quote_to_account

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
            "quoteToAccount": self.quote_to_account,
            "executionMode": (self.execution_costs or {}).get("mode", "legacy-spread-only"),
            "costAssumptions": self.execution_costs,
            "pnlBasis": "estimated-net" if self.execution_costs is not None else "gross",
            "entryFee": self.entry_fee * self.quote_to_account,
            "carryCost": self.carry_cost() * self.quote_to_account,
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
        self.session = None
        self.balance = cfg.initial_balance
        self.running = True
        self.positions: dict[str, Position] = {}
        self.cooldown_until: dict[str, float] = {}
        self.probation_open: set[str] = set()       # símbolos abiertos a prueba
        self.trained_params: dict[str, dict] = {}   # símbolo -> params validados
        self.manual_params: dict[str, dict] = {}    # ajustes explícitos a prueba
        self._task: asyncio.Task | None = None
        self._risk_task: asyncio.Task | None = None
        self.risk_monitor=dict(independent=True,lastCompletedAt=None,durationSeconds=None,unavailable=[])
        self._last_scan = 0.0
        self._scan_cursor = 0
        self.diagnostics: dict[str, dict] = {}
        # estado de riesgo del día (circuit breaker y rachas de pérdidas)
        self.day_utc = int(time.time() // 86400)
        self.day_start_balance = self.balance
        self.consec_losses = 0                      # pérdidas seguidas hoy (global)
        self.loss_streak: dict[str, int] = {}       # pérdidas seguidas por símbolo

    def spread_pct(self, sym: Symbol) -> float:
        return SPREAD_PCT.get(sym.category, 0.0003)

    # -------------------- medidas de seguridad --------------------

    def _load_risk_state(self) -> None:
        """Reconstruye el estado de riesgo del día UTC desde la BD (sobrevive
        a reinicios): PnL del día, pérdidas consecutivas y rachas por símbolo."""
        day0 = int(time.time() // 86400) * 86400
        with _conn() as con:
            rows = con.execute(
                """SELECT symbol, pnl FROM bot_trades
                   WHERE bot = ? AND status = 'closed' AND exit_time >= ?
                   ORDER BY exit_time""",
                (self.cfg.id, day0),
            ).fetchall()
        self.day_utc = int(time.time() // 86400)
        self.day_start_balance = self.balance - sum(r[1] for r in rows)
        self.consec_losses = 0
        self.loss_streak = {}
        for sym, pnl in rows:
            if pnl < 0:
                self.consec_losses += 1
                self.loss_streak[sym] = self.loss_streak.get(sym, 0) + 1
            else:
                self.consec_losses = 0
                self.loss_streak[sym] = 0

    def _roll_day(self) -> None:
        """Al cambiar el día UTC se rearma el circuit breaker y las rachas."""
        today = int(time.time() // 86400)
        if today != self.day_utc:
            self.day_utc = today
            self.day_start_balance = self.balance
            self.consec_losses = 0
            self.loss_streak.clear()

    def daily_pnl(self) -> float:
        return self.balance - self.day_start_balance

    def entries_paused(self) -> bool:
        """Circuit breaker diario: con la pérdida máxima del día alcanzada no
        se abren más posiciones hasta el día siguiente (las abiertas se siguen
        gestionando con normalidad)."""
        return self.daily_pnl() + min(0.0, sum(p.unrealized() for p in self.positions.values())) <= -self.cfg.max_daily_loss_pct * self.day_start_balance

    def risk_scale(self) -> float:
        """Tamaño a la mitad tras 2 pérdidas seguidas (regla de las dos pérdidas)."""
        return 0.5 if self.consec_losses >= 2 else 1.0

    def _category_exposure(self, category: str, direction: str) -> int:
        """Posiciones abiertas de la misma categoría y dirección: BTC+ETH+SOL
        largos son en la práctica la misma apuesta, no tres."""
        return sum(
            1 for p in self.positions.values()
            if p.direction == direction
            and SYMBOLS_BY_ID[p.symbol].category == category
        )

    def reload_params(self) -> None:
        """Carga los parámetros entrenados y validados (tabla bot_params)."""
        from . import training

        self.trained_params = {
            s: p for (b, s), p in training.load_applied_params().items()
            if b == self.cfg.id
        }
        self.manual_params = {
            s: p for (b, s), p in training.load_manual_params().items()
            if b == self.cfg.id
        }
        if self.trained_params:
            log.info("%s: parámetros entrenados para %s",
                     self.cfg.id, ", ".join(sorted(self.trained_params)))

    def _exec_params(self, sym: Symbol) -> tuple[float, float, float, float]:
        """(umbral, sl_atr, tp_atr, trail_atr) efectivos para un símbolo."""
        p = self.trained_params.get(sym.id) or self.manual_params.get(sym.id)
        threshold = p["entryThreshold"] if p else self.cfg.entry_threshold
        sl = p["slAtr"] if p else self.cfg.sl_atr
        tp = p["tpAtr"] if p else self.cfg.tp_atr
        trail = p["trailAtr"] if p else self.cfg.trail_atr
        if not p and sym.id == self.cfg.universe[0]:
            threshold *= self.cfg.focus_threshold_mult
            # sesión Londres-NY: la franja más operable del oro
            if self.cfg.id == "scalper" and datetime.now(timezone.utc).hour in GOLD_SESSION_UTC:
                threshold *= 0.9
        return threshold, sl, tp, trail

    # -------------------- persistencia --------------------

    def load_state(self) -> None:
        with _conn() as con:
            session=con.execute('SELECT payload FROM bot_sessions WHERE bot=?',(self.cfg.id,)).fetchone()
            if session:
                self.session=json.loads(session[0])
                self.cfg=replace(self.cfg,initial_balance=self.session['initial'])
                self.balance=self.cfg.initial_balance
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
                          sl, tp, reasons, atr_entry, best_price, initial_risk_dist, trail_multiple, quote_to_account,
                          execution_costs, entry_fee, economic_risk_dist FROM bot_trades
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
                trail_atr=r[12] if r[12] is not None else self.cfg.trail_atr,
                r_dist=r[11] if r[11] is not None else (r[9] * self.cfg.sl_atr if r[9] else abs(r[4] - r[6])),
                last_price=r[4],
                quote_to_account=r[13] if r[13] is not None else 1.0,
                execution_costs=json.loads(r[14]) if r[14] else None,
                entry_fee=r[15] or 0.0,
                economic_risk_dist=r[16],
            )
            self.positions[pos.symbol] = pos
            if any(str(r).startswith("A prueba") for r in pos.reasons):
                self.probation_open.add(pos.symbol)
        self.reload_params()
        self._load_risk_state()
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
        if self._risk_task is None or self._risk_task.done():
            self._risk_task=asyncio.create_task(self._run_risk(),name=f'bot-risk-{self.cfg.id}')

    async def stop_task(self) -> None:
        tasks=[task for task in (self._task,self._risk_task) if task is not None]
        for task in tasks: task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task=self._risk_task=None

    async def _run(self) -> None:
        log.info("%s arrancado (running=%s)", self.cfg.name, self.running)
        while True:
            try:
                if self.running:
                    now = time.time()
                    if now - self._last_scan >= self.cfg.scan_seconds:
                        self._last_scan = now
                        await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("%s: error en el ciclo: %s", self.cfg.id, exc)
            await asyncio.sleep(self.cfg.tick_seconds)

    async def _run_risk(self) -> None:
        while True:
            started=time.monotonic()
            try:
                await self._manage_positions()
                self.risk_monitor.update(lastCompletedAt=time.time(),durationSeconds=time.monotonic()-started)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('%s: vigilancia sin actualizar: %s',self.cfg.id,exc)
                self.risk_monitor['error']=type(exc).__name__
            await asyncio.sleep(min(5,self.cfg.tick_seconds))

    # -------------------- vigilancia de posiciones --------------------

    async def _manage_positions(self) -> None:
        positions=list(self.positions.values())
        semaphore=asyncio.Semaphore(4)
        async def observe(pos):
            async with semaphore:
                try:
                    tick=await asyncio.wait_for(market_data.get_price(SYMBOLS_BY_ID[pos.symbol]),timeout=6)
                    return tick if valid_tick(tick,time.time()) else None
                except Exception:
                    return None
        quotes=await asyncio.gather(*(observe(pos) for pos in positions))
        self.risk_monitor['unavailable']=[p.symbol for p,tick in zip(positions,quotes) if tick is None]
        self.risk_monitor.pop('error',None)
        for pos,tick in zip(positions,quotes):
            # Puede haber cerrado por señal mientras se esperaba a la fuente.
            if tick is None or self.positions.get(pos.symbol) is not pos: continue
            sym = SYMBOLS_BY_ID[pos.symbol]
            price = tick["price"]
            pos.last_price = price
            half_spread = price * self.spread_pct(sym) / 2

            if pos.direction == "long":
                if price <= pos.sl:
                    self._close(pos, pos.exit_fill(price), "stop-loss")
                    continue
                if price >= pos.tp:
                    self._close(pos, pos.exit_fill(pos.tp), "take-profit")
                    continue
                pos.best_price = max(pos.best_price, price)
                # tras avanzar 1R: stop a breakeven y trailing por ATR.
                # Tras 2R el trailing se estrecha (×0.75) para blindar la
                # ganancia acumulada sin cortar antes de tiempo el tramo bueno.
                if pos.best_price >= pos.entry_price + pos.r_dist:
                    trail = pos.trail_atr
                    if pos.best_price >= pos.entry_price + 2 * pos.r_dist:
                        trail *= 0.75
                    break_even = pos.break_even_price()
                    if break_even is None:
                        break_even = pos.entry_price + 2 * half_spread
                    elif break_even > price:
                        break_even = pos.sl  # no colocar un stop por delante del mercado
                    new_sl = max(
                        pos.sl,
                        break_even,
                        pos.best_price - trail * pos.atr_entry,
                    )
                    if new_sl > pos.sl:
                        self._update_stop(pos, new_sl)
            else:
                if price >= pos.sl:
                    self._close(pos, pos.exit_fill(price), "stop-loss")
                    continue
                if price <= pos.tp:
                    self._close(pos, pos.exit_fill(pos.tp), "take-profit")
                    continue
                pos.best_price = min(pos.best_price, price)
                if pos.best_price <= pos.entry_price - pos.r_dist:
                    trail = pos.trail_atr
                    if pos.best_price <= pos.entry_price - 2 * pos.r_dist:
                        trail *= 0.75
                    break_even = pos.break_even_price()
                    if break_even is None:
                        break_even = pos.entry_price - 2 * half_spread
                    elif break_even < price:
                        break_even = pos.sl
                    new_sl = min(
                        pos.sl,
                        break_even,
                        pos.best_price + trail * pos.atr_entry,
                    )
                    if new_sl < pos.sl:
                        self._update_stop(pos, new_sl)

            if time.time() - pos.entry_time >= self.cfg.max_hold_seconds:
                exit_p = pos.exit_fill(price)
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
        self._roll_day()
        universe = self.cfg.universe
        offset = self._scan_cursor % len(universe)
        ordered = universe[offset:] + universe[:offset]
        self._scan_cursor += 1
        # Las salidas tienen prioridad; las entradas rotan para no favorecer siempre al primer activo.
        ordered = sorted(ordered, key=lambda symbol: symbol not in self.positions)
        for symbol_id in ordered:
            self.diagnostics[symbol_id] = {"reason": "Evaluando", "at": int(time.time())}
            has_pos = symbol_id in self.positions
            if not has_pos and (
                self.entries_paused()  # se recalcula tras cada cierre
                or len(self.positions) >= self.cfg.max_positions
                or time.time() < self.cooldown_until.get(symbol_id, 0)
            ):
                self.diagnostics[symbol_id]["reason"] = "Pausa de riesgo, límite de posiciones o cooldown"
                continue
            if not has_pos and self.cfg.require_validation and symbol_id not in self.trained_params:
                self.diagnostics[symbol_id]["reason"] = "Esperando validación del autoentrenamiento"
                continue
            pre_mode = "full"
            if not has_pos:
                # La validación permite tamaño normal; los demás pares operan
                # a prueba salvo que un veto de aprendizaje los bloquee.
                pre_mode, _why = learning.entry_mode(self.cfg.id, symbol_id)
                if pre_mode == "blocked":
                    self.diagnostics[symbol_id]["reason"] = _why
                    continue
                if pre_mode == "probation" and (
                    len(self.probation_open) >= learning.MAX_PROBATION_POSITIONS
                ):
                    self.diagnostics[symbol_id]["reason"] = "Límite de posiciones a prueba alcanzado"
                    continue
            sym = SYMBOLS_BY_ID.get(symbol_id)
            if not sym:
                continue
            try:
                signal = await self._evaluate(sym)
            except Exception as exc:
                self.diagnostics[symbol_id]["reason"] = "Error al obtener o evaluar datos"
                log.debug("%s: no se pudo evaluar %s: %s", self.cfg.id, symbol_id, exc)
                continue
            if signal is None:
                self.diagnostics[symbol_id]["reason"] = "Histórico cerrado insuficiente"
                continue
            execution_params = self.trained_params.get(symbol_id) or self.manual_params.get(symbol_id, {})
            expected_source = execution_params.get('dataSource')
            if not has_pos and expected_source and signal['candles'][-1].get('dataSource') != expected_source:
                self.diagnostics[symbol_id]['reason'] = 'Fuente distinta de la validada; esperando datos MT5'
                continue
            self.diagnostics[symbol_id].update(reason="Señal insuficiente o filtro de entrada", score=round(signal["score"], 3))
            if signal["stale"]:
                self.diagnostics[symbol_id]["reason"] = "Velas antiguas / mercado cerrado"
                continue
            score = signal["score"]
            atr_val, price = signal["atr"], signal["price"]
            threshold, sl_m, tp_m, trail_m = self._exec_params(sym)
            if pre_mode == "probation":
                threshold *= learning.PROBATION_THRESHOLD_MULT

            if has_pos:
                # señal claramente contraria a la posición abierta -> cerrar.
                # Histéresis a 0.75×umbral: el 0.5 anterior generaba un churn
                # de salidas prematuras (el mayor coste del scalper en vivo).
                pos = self.positions.get(symbol_id)
                if pos is None: continue  # el monitor de riesgo cerró durante el análisis
                against = (pos.direction == "long" and score <= -0.75 * threshold) or (
                    pos.direction == "short" and score >= 0.75 * threshold
                )
                from .execution import signal_exit_enabled
                if against and signal_exit_enabled(self.cfg.id):
                    tick = await market_data.get_price(sym)
                    if not valid_tick(tick, time.time()):
                        continue
                    price = tick["price"]
                    half_spread = price * self.spread_pct(sym) / 2
                    exit_p = pos.exit_fill(price)
                    self._close(pos, exit_p, "señal contraria")
                continue

            if abs(score) < threshold:
                self.diagnostics[symbol_id]["reason"] = f"Señal {score:+.3f} no alcanza umbral {threshold:.3f}"
                continue
            if atr_val <= 0:
                self.diagnostics[symbol_id]["reason"] = "ATR insuficiente para calcular el riesgo"
                continue
            if not fresh_signal(score, signal.get("recent_prev", [signal["prev"]]), threshold):
                self.diagnostics[symbol_id]["reason"] = "Sin cruce reciente en esta dirección"
                continue
            # sin vela fresca no se abre (mercado cerrado o feed caído)
            if signal["stale"]:
                continue
            # filtro de coste: sin volatilidad suficiente el spread se come el
            # beneficio.  El mismo filtro que aplica el backtest de training.py:
            # lo entrenado tiene que ser exactamente lo operado.
            if atr_val < atr_spread_multiple(self.cfg.id) * price * self.spread_pct(sym):
                self.diagnostics[symbol_id]["reason"] = "Volatilidad insuficiente para cubrir costes"
                continue
            direction = "long" if score > 0 else "short"
            # Modo definitivo con dirección y hora: los vetos bloquean también
            # las entradas a prueba.
            hour_utc = datetime.now(timezone.utc).hour
            mode, mode_why = learning.entry_mode(
                self.cfg.id, symbol_id, direction, hour=hour_utc
            )
            if mode == "blocked":
                self.diagnostics[symbol_id]["reason"] = mode_why
                log.debug("%s: %s %s vetado: %s", self.cfg.id, symbol_id, direction, mode_why)
                continue
            if mode == "probation" and len(self.probation_open) >= learning.MAX_PROBATION_POSITIONS:
                self.diagnostics[symbol_id]["reason"] = "Límite de posiciones a prueba alcanzado"
                continue
            if gemini_agent.AGENT:
                await gemini_agent.AGENT.pretrade(sym)
            # Puntuación logística ponderada por R neta; no una probabilidad
            # calibrada. Solo puede filtrar señales o reducir su tamaño.
            feats = learning.meta_features(
                self.cfg.id, sym.category, direction, hour_utc,
                signal["confirmations"],
                learning.entry_context(price, atr_val, signal.get('extension'), self.spread_pct(sym)),
            )
            meta_ok, p_win, meta_note = learning.meta_gate(self.cfg.id, feats)
            if not meta_ok:
                self.diagnostics[symbol_id]["reason"] = meta_note or "Filtrado por meta-modelo"
                log.debug("%s: %s %s filtrado por meta-modelo (p=%.2f)",
                          self.cfg.id, symbol_id, direction, p_win or 0.0)
                continue
            if mode == "probation" and p_win is not None and p_win < learning.PROBATION_META_P:
                # a prueba solo pasan las señales de más convicción
                self.diagnostics[symbol_id]["reason"] = (
                    f"Entrada a prueba filtrada: puntuación ponderada por R={p_win:.1%} < {learning.PROBATION_META_P:.0%}"
                )
                log.debug("%s: %s %s a prueba sin convicción (p=%.2f)",
                          self.cfg.id, symbol_id, direction, p_win)
                continue
            # anti-persecución: no comprar techos ni vender suelos estirados
            ext = signal["extension"]
            if ext is not None and (
                (direction == "long" and ext > self.cfg.overext_atr)
                or (direction == "short" and ext < -self.cfg.overext_atr)
            ):
                self.diagnostics[symbol_id]["reason"] = (
                    f"Precio sobreextendido ({ext:+.2f} ATR; límite {self.cfg.overext_atr:g})"
                )
                log.debug("%s: %s sobreextendido (%.1f ATR), no se persigue",
                          self.cfg.id, symbol_id, ext)
                continue
            # exposición correlacionada: misma categoría y dirección = misma apuesta
            if self._category_exposure(sym.category, direction) >= self.cfg.max_same_category:
                self.diagnostics[symbol_id]["reason"] = "Límite de exposición correlacionada"
                continue
            if sym.source == 'binance':
                try:
                    quality = await opportunities.market_quality(sym)
                except Exception:
                    self.diagnostics[symbol_id]["reason"] = "Fuente de liquidez no disponible"
                    continue
                self.diagnostics[symbol_id]["quality"] = quality
                if not quality["eligible"]:
                    self.diagnostics[symbol_id]["reason"] = quality["reason"]
                    continue
                # El spread simulado no debe infravalorar el observado en el libro.
                if quality["spreadPct"] / 100 > self.spread_pct(sym):
                    self.diagnostics[symbol_id]["reason"] = "Spread observado supera el coste del modelo"
                    continue
            tick = await market_data.get_price(sym)
            if expected_source and tick.get('dataSource') != expected_source:
                self.diagnostics[symbol_id]['reason'] = 'Cotización de una fuente distinta de la validada'
                continue
            if not valid_tick(tick, time.time()):
                self.diagnostics[symbol_id]["reason"] = "Cotización antigua o inválida"
                continue
            if abs(tick["price"] - price) > atr_val:
                self.diagnostics[symbol_id]["reason"] = "Precio alejado más de 1 ATR de la señal"
                continue
            price = tick["price"]
            if (not self.running or symbol_id in self.positions or self.entries_paused()
                    or time.time()<self.cooldown_until.get(symbol_id,0)
                    or len(self.positions)>=self.cfg.max_positions):
                self.diagnostics[symbol_id]['reason']='Control de riesgo actualizado durante el análisis; entrada bloqueada'
                continue
            self._open(
                sym, direction, price, atr_val, score, signal, sl_m, tp_m, trail_m,
                p_win=p_win, meta_note=meta_note,
                probation_why=mode_why if mode == "probation" else None,
            )

    async def _evaluate(self, sym: Symbol) -> dict | None:
        """Señal combinada + confirmaciones estructuradas + velas de trabajo."""
        c_work, c_trend = await asyncio.gather(
            market_data.get_candles(sym, self.cfg.work_interval),
            market_data.get_candles(sym, self.cfg.trend_interval),
        )
        now = time.time()
        c_work = closed_candles(c_work, INTERVAL_SECONDS[self.cfg.work_interval], now)
        c_trend = closed_candles(c_trend, INTERVAL_SECONDS[self.cfg.trend_interval], now)
        if len(c_work) < 60:
            return None

        series = score_series(self.cfg.id, c_work, c_trend)
        tech_score, tech_prev = series[-1], series[-2]
        # scores de las 3 velas previas para la ventana de cruce
        tech_recent = series[-4:-1] if len(series) >= 4 else series[:-1]
        # últimas 10 velas para el filtro de madurez
        tech_last10 = series[-11:-1] if len(series) >= 11 else series[:-1]
        confs = strategy_confirmations(self.cfg.id, c_work, c_trend)

        senti = sentiment_mod.peek_cached(sym.id)
        if senti is None or senti.get("sampleSize", 0) == 0:
            _kick_sentiment_refresh(sym)
            combined, prev = tech_score, tech_prev
            recent_prev = list(tech_recent)
            full_series = list(tech_last10)
            confs.append({
                "system": "Opinión pública",
                "detail": "Sin sentimiento fresco: solo técnico",
                "vote": 0.0,
            })
        else:
            senti_score = senti["bullishPct"] / 50 - 1  # [0,100] -> [-1,1]
            # El sentimiento se muestra como contexto: no hay histórico causal
            # equivalente en el entrenamiento para mezclarlo en la entrada.
            combined, prev = tech_score, tech_prev
            recent_prev = list(tech_recent)
            full_series = list(tech_last10)
            confs.append({
                "system": "Opinión pública",
                "detail": f"{senti['bullishPct']}% alcista (n={senti['sampleSize']})",
                "vote": round(senti_score, 2),
            })
        for source in (senti or {}).get("sources", []):
            if source.get("source", "").startswith("Federal Reserve"):
                confs.append({"system": "Contexto macro oficial", "detail": source["detail"], "vote": 0.0})
        threshold = self._exec_params(sym)[0]
        confs.append({
            "system": "Señal combinada",
            "detail": f"{combined:+.2f} (umbral ±{threshold:.2f})",
            "vote": round(combined, 2),
        })
        reasons = [
            f"{c['system']}: {c['detail']}" if c["vote"] == 0
            else f"{c['system']}: {c['detail']} ({'+' if c['vote'] > 0 else ''}{c['vote']:g})"
            for c in confs
        ]

        # mercado cerrado / datos viejos: no abrir con la última vela rancia
        work_s = INTERVAL_SECONDS[self.cfg.work_interval]
        stale = time.time() - c_work[-1]["time"] > 3 * work_s + 60

        atr_val = atr(c_work, self.cfg.atr_period) or 0.0
        return {
            "score": combined,
            "prev": prev,
            "recent_prev": recent_prev,
            "full_series": full_series,
            "reasons": reasons,
            "confirmations": confs,
            "atr": atr_val,
            "price": c_work[-1]["close"],
            "stale": stale,
            "candles": c_work,
            "extension": extension_atr(c_work),
        }

    # -------------------- ejecución simulada --------------------

    def _open(
        self, sym: Symbol, direction: str, price: float, atr_val: float,
        score: float, signal: dict, sl_m: float, tp_m: float, trail_m: float,
        p_win: float | None = None, meta_note: str | None = None,
        probation_why: str | None = None,
    ) -> None:
        gate=gemini_agent.catalyst_gate(sym.id,direction)
        if not gate['allowed']:
            self.diagnostics[sym.id]={"reason":gate['reason'],"at":int(time.time()),"catalystGate":gate}
            return
        if not all(math.isfinite(v) for v in (price, atr_val, sl_m, tp_m, trail_m)) or min(price, atr_val, sl_m, tp_m) <= 0:
            self.diagnostics[sym.id] = {"reason": "Precio, ATR o distancias inválidos", "at": int(time.time())}
            return
        reasons: list[str] = list(signal["reasons"])
        if sym.id in self.manual_params:
            reasons.append("Parámetros ajustados manualmente; pendientes de revalidación")
        costs = {**cost_profile("lab", sym.category), "spreadPct": self.spread_pct(sym)}
        entry = execution_fill(price, direction, True, costs["spreadPct"], costs["slippageRate"])
        sl_dist = atr_val * sl_m
        tp_dist = atr_val * tp_m
        if direction == "long":
            sl, tp = entry - sl_dist, entry + tp_dist
        else:
            sl, tp = entry + sl_dist, entry - tp_dist
        if min(sl, tp) <= 0:
            self.diagnostics[sym.id] = {"reason": "Stop u objetivo fuera del rango de precios", "at": int(time.time())}
            return
        # Escenario de margen simulado: comisión por lado, spread, slippage y
        # financiación/préstamo sobre TODO el nominal (supuesto conservador).
        # No acredita préstamo, contrato ni ejecución Spot de estos cortos.
        horizon_carry = entry * costs["carryDailyRate"] * self.cfg.max_hold_seconds / 86400
        economy = planned_trade(entry, sl, tp, 1.0, direction, costs["feeRate"],
                                costs["slippageRate"], costs["spreadPct"], carry=horizon_carry)
        if not economy["viable"]:
            self.diagnostics[sym.id] = {"reason": "Objetivo neto no cubre comisión, spread, deslizamiento y financiación",
                                       "at": int(time.time()), "economics": economy}
            return
        reasons.append("Margen simulado; costes conservadores de comisión, spread, deslizamiento y financiación/préstamo")

        # El presupuesto incluye las fricciones previstas y sólo disminuye por
        # racha, aprendizaje o exploración. El margen limita el nominal simulado.
        scale = self.risk_scale()
        if scale < 1.0:
            reasons.append(
                f"Riesgo reducido al {scale * 100:.0f}% tras {self.consec_losses} pérdidas seguidas hoy"
            )
        learn_mult, learn_note = learning.size_multiplier(self.cfg.id, sym.id)
        if learn_note:
            reasons.append(f"Aprendizaje: {learn_note}")
        # modo a prueba: tamaño muy reducido para ganarse la reentrada
        prob_mult = 1.0
        if probation_why:
            prob_mult = learning.PROBATION_SIZE
            reasons.insert(0, f"A prueba ({learning.PROBATION_SIZE:.0%} del tamaño): {probation_why}")
        # El aumento experimental requiere validación vigente y modelo maduro.
        meta_mult = 1.0
        if p_win is not None:
            validated=not probation_why and learning.entry_mode(self.cfg.id,sym.id,direction)[0]=='full'
            meta_mult = learning.meta_size_multiplier(self.cfg.id,p_win,validated)
            reasons.append(
                f"Meta-modelo: puntuación ponderada por R={p_win:.0%} → tamaño ×{meta_mult:.2f}"
                + (f" ({meta_note})" if meta_note else "")
            )
        conversion=1.0
        if self.session and self.session.get('currency')=='EUR':
            quote='USDT' if sym.source=='binance' else sym.id[3:] if sym.category=='forex' else 'USD'
            conversion=self.session['quoteToEuro'].get(quote)
            if conversion is None or conversion<=0:
                self.diagnostics[sym.id]={'reason':'Sin conversión a EUR para este mercado','at':int(time.time())}
                return
        equity = max(0.0, self.balance + sum(p.unrealized() for p in self.positions.values()))
        open_risk = sum(p.initial_risk() for p in self.positions.values())
        size_mult = scale * min(1.0, learn_mult) * meta_mult
        if probation_why:
            # El aprendizaje puede reducir el riesgo exploratorio, nunca
            # elevarlo por encima de PROBATION_SIZE del presupuesto habitual.
            size_mult = min(1.0, size_mult) * prob_mult
        budget = min(equity * self.cfg.risk_pct * size_mult,
                     max(0.0, equity * self.cfg.max_open_risk_pct - open_risk))
        qty = budget / (economy["netRisk"] * conversion)
        used_margin = sum(
            p.qty * p.entry_price * p.quote_to_account for p in self.positions.values()
        ) / self.cfg.max_leverage
        free_margin = max(equity - used_margin, 0.0)
        qty = min(qty, free_margin * self.cfg.max_leverage / (entry*conversion))
        if qty * entry * conversion < 1.0:
            self.diagnostics[sym.id] = {"reason": "Sin margen o presupuesto de riesgo", "at": int(time.time())}
            return
        economy = planned_trade(entry, sl, tp, qty, direction, costs["feeRate"],
                                costs["slippageRate"], costs["spreadPct"], conversion,
                                horizon_carry * qty)

        # instantánea del análisis: velas + trazados de los sistemas tal y como
        # estaban al entrar, para poder redibujar el gráfico de la decisión
        window = signal["candles"][-SNAPSHOT_BARS:]
        snapshot = {
            "engineVersion": ENGINE_VERSION,
            "metaFeatureVersion": learning.META_FEATURE_VERSION,
            "entryContext": learning.entry_context(price, atr_val, signal.get('extension'), costs['spreadPct']),
            "interval": self.cfg.work_interval,
            "score": round(score, 3),
            "confirmations": signal["confirmations"],
            "candles": window,
            "overlays": systems.chart_overlays(
                window, intraday=self.cfg.work_interval in ("1m", "5m", "15m")
            ),
            "executionCosts": costs,
            "catalystGate": gate,
            "economics": economy,
        }
        from . import portfolio_risk
        assessment = None
        if (self.session or {}).get("currency") == "EUR":
            assessment = portfolio_risk.assess(sym.id, direction, economy["netRisk"],
                                              qty * entry * conversion, "lab", self.cfg.id, reserve=True)
            if not assessment["allowed"]:
                self.diagnostics[sym.id] = {"reason": assessment["reason"], "at": int(time.time()),
                                           "portfolioRisk": assessment}
                return
            snapshot["portfolioRisk"] = {k: v for k, v in assessment.items() if k != "token"}
        now = int(time.time())
        try:
            with _conn() as con:
                cur = con.execute(
                    """INSERT INTO bot_trades
                       (bot, symbol, direction, qty, entry_price, entry_time,
                        sl, tp, reasons, atr_entry, best_price, snapshot, initial_risk_dist, trail_multiple, quote_to_account,
                        execution_costs, entry_fee, economic_risk_dist)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.cfg.id, sym.id, direction, qty, entry, now, sl, tp,
                     json.dumps(reasons, ensure_ascii=False), atr_val, entry,
                     json.dumps(snapshot, ensure_ascii=False), sl_dist, trail_m, conversion,
                     json.dumps(costs, ensure_ascii=False), entry * qty * costs["feeRate"],
                     economy["netRisk"] / qty / conversion),
                )
                trade_id = cur.lastrowid
            pos = Position(
                id=trade_id, symbol=sym.id, direction=direction, qty=qty,
                entry_price=entry, entry_time=now, sl=sl, tp=tp,
                atr_entry=atr_val, best_price=entry, trail_atr=trail_m,
                r_dist=sl_dist, reasons=reasons, last_price=price,
                quote_to_account=conversion,
                execution_costs=costs, entry_fee=entry * qty * costs["feeRate"],
                economic_risk_dist=economy["netRisk"] / qty / conversion,
            )
            self.positions[sym.id] = pos
        finally:
            if assessment:
                portfolio_risk.release(assessment.get("token"))
        self.diagnostics[sym.id] = {"reason": "Operación abierta (simulada)", "at": now}
        if probation_why:
            self.probation_open.add(sym.id)
        log.info(
            "%s ABRE %s %s qty=%.4f @ %.4f SL=%.4f TP=%.4f nocional=%.0f margen=%.0f (señal %+.2f)",
            self.cfg.id, direction.upper(), sym.id, qty, entry, sl, tp,
            qty * entry, qty * entry / self.cfg.max_leverage, score,
        )

    def _close(self, pos: Position, exit_price: float, reason: str) -> None:
        sign = 1.0 if pos.direction == "long" else -1.0
        # exit_price ya es un fill: los llamadores aplican spread/slippage una vez.
        now = int(time.time())
        exit_fee = exit_price * pos.qty * (pos.execution_costs or {}).get("feeRate", 0.0)
        carry = pos.carry_cost(now)
        pnl = net_outcome(pos.entry_price, exit_price, pos.qty, sign, pos.entry_fee,
                          (pos.execution_costs or {}).get("feeRate", 0.0), pos.quote_to_account, carry)
        self._roll_day()
        new_balance = self.balance + pnl
        with _conn() as con:
            cur = con.execute(
                """UPDATE bot_trades SET exit_price = ?, exit_time = ?,
                   exit_reason = ?, pnl = ?, exit_fee = ?, carry_cost = ?, status = 'closed'
                   WHERE id = ? AND status = 'open'""",
                (exit_price, now, reason, pnl, exit_fee, carry, pos.id),
            )
            if not cur.rowcount:
                return  # un cierre repetido no puede abonar el PnL dos veces
            con.execute("UPDATE bot_state SET balance = ? WHERE bot = ?", (new_balance, self.cfg.id))
        self.balance = new_balance
        self.positions.pop(pos.symbol, None)
        self.probation_open.discard(pos.symbol)
        # rachas de pérdidas: cooldown escalonado por símbolo (×2 por pérdida
        # consecutiva, tope ×8) y contador global del día para reducir tamaño.
        # Tras una GANANCIA el cooldown se reduce a 1/4: no hay motivo para
        # castigar al bot cuando acierta (más operaciones donde funciona).
        self._roll_day()
        if pnl < 0:
            self.consec_losses += 1
            self.loss_streak[pos.symbol] = self.loss_streak.get(pos.symbol, 0) + 1
            mult = min(8, 2 ** self.loss_streak.get(pos.symbol, 0))
            cooldown = self.cfg.cooldown_seconds * mult
        else:
            self.consec_losses = 0
            self.loss_streak[pos.symbol] = 0
            cooldown = self.cfg.cooldown_seconds / 4
        self.cooldown_until[pos.symbol] = time.time() + cooldown
        self._save_state()
        # cada cierre alimenta el aprendizaje: vetos, tamaños adaptativos y
        # el meta-modelo online (aprende del resultado de ESTA operación)
        try:
            learning.refresh(self.cfg.id)
        except Exception as exc:
            log.debug("%s: fallo al refrescar aprendizaje: %s", self.cfg.id, exc)
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
        self.probation_open.clear()
        self.balance = self.cfg.initial_balance
        self.day_start_balance = self.balance
        self.consec_losses = 0
        self.loss_streak.clear()
        learning.reset(self.cfg.id)
        self._save_state()

    def status(self) -> dict:
        self._roll_day()
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
        try:
            learn = learning.summary(self.cfg.id)
        except Exception:
            learn = None
        return {
            "id": self.cfg.id,
            "name": self.cfg.name,
            "horizon": self.cfg.horizon,
            "running": self.running,
            "initialBalance": self.cfg.initial_balance,
            "currency": (self.session or {}).get('currency','USD'),
            "session": self.session,
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
            "risk": {
                "openRisk": round(sum(p.initial_risk() for p in self.positions.values()), 2),
                "maxOpenRiskPct": self.cfg.max_open_risk_pct * 100,
                "dailyPnl": round(self.daily_pnl(), 2),
                "dailyLossLimit": round(
                    self.cfg.max_daily_loss_pct * self.day_start_balance, 2
                ),
                "dailyLossLimitPct": self.cfg.max_daily_loss_pct * 100,
                "entriesPaused": self.entries_paused(),
                "consecLosses": self.consec_losses,
                "riskScale": self.risk_scale(),
                "maxSameCategory": self.cfg.max_same_category,
                "overextAtr": self.cfg.overext_atr,
            },
            "diagnostics": self.diagnostics,
            "monitoring": dict(self.risk_monitor),
            "trainedSymbols": sorted(self.trained_params),
            "learning": learn,
            "config": {
                "universe": list(self.cfg.universe),
                "workInterval": self.cfg.work_interval,
                "trendInterval": self.cfg.trend_interval,
                "scanSeconds": self.cfg.scan_seconds,
                "entryThreshold": self.cfg.entry_threshold,
                "riskPct": self.cfg.risk_pct * 100,
                "requireValidation": self.cfg.require_validation,
                "probationSizePct": learning.PROBATION_SIZE * 100,
                "maxProbationPositions": learning.MAX_PROBATION_POSITIONS,
                "maxLeverage": self.cfg.max_leverage,
                "slAtr": self.cfg.sl_atr,
                "tpAtr": self.cfg.tp_atr,
                "trailAtr": self.cfg.trail_atr,
                "maxPositions": self.cfg.max_positions,
                "maxHoldSeconds": self.cfg.max_hold_seconds,
                "sentimentWeight": self.cfg.weight_senti * 100,
                "executionMode": "simulated-margin",
                "costAssumption": "Escenario conservador sin tarifas de cuenta verificadas; no ejecución Spot",
            },
        }

    def trades(self, limit: int = 50) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                """SELECT id, symbol, direction, qty, entry_price, entry_time,
                          sl, tp, exit_price, exit_time, exit_reason, pnl, reasons,
                          execution_costs, entry_fee, exit_fee, carry_cost, quote_to_account
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
                "executionMode": (json.loads(r[13]) if r[13] else {}).get("mode", "legacy-spread-only"),
                "costAssumptions": json.loads(r[13]) if r[13] else None,
                "pnlBasis": "net" if r[13] else "gross",
                "entryFee": (r[14] or 0.0) * (r[17] if r[17] is not None else 1.0),
                "exitFee": (r[15] or 0.0) * (r[17] if r[17] is not None else 1.0),
                "carryCost": (r[16] or 0.0) * (r[17] if r[17] is not None else 1.0),
            }
            for r in rows
        ]


def trade_detail(bot_id: str, trade_id: int) -> dict | None:
    """Operación completa (abierta o cerrada) con su snapshot de análisis:
    confirmaciones estructuradas, velas del momento de entrada y trazados
    (S/R, tendencias, Fibonacci, medias, Bollinger) para redibujar el gráfico."""
    with _conn() as con:
        r = con.execute(
            """SELECT id, symbol, direction, qty, entry_price, entry_time,
                      sl, tp, exit_price, exit_time, exit_reason, pnl, status,
                      reasons, snapshot
               FROM bot_trades WHERE bot = ? AND id = ?""",
            (bot_id, trade_id),
        ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "bot": bot_id, "symbol": r[1], "direction": r[2],
        "qty": round(r[3], 6), "entryPrice": r[4], "entryTime": r[5],
        "sl": r[6], "tp": r[7], "exitPrice": r[8], "exitTime": r[9],
        "exitReason": r[10], "pnl": round(r[11], 2) if r[11] is not None else None,
        "status": r[12], "reasons": json.loads(r[13]),
        "snapshot": json.loads(r[14]) if r[14] else None,
    }


def _cum_points(rows: list[tuple]) -> tuple[list[dict], dict]:
    """(puntos de PnL acumulado, resumen) de filas (exit_time, pnl, symbol, bot).

    Fusiona operaciones que cierran en el mismo segundo: el gráfico exige
    tiempos estrictamente crecientes."""
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
    summary = {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / n * 100, 1) if n else None,
        "pnl": round(cum, 2),
        "best": round(best, 2),
        "worst": round(worst, 2),
    }
    return points, summary


def performance(
    bot_id: str | None = None,
    symbol: str | None = None,
    since: int | None = None,
    until: int | None = None,
    per_bot: bool = False,
) -> dict:
    """Curva de PnL acumulado de operaciones cerradas, con filtros.

    Con `per_bot` añade además una serie acumulada independiente por bot
    (para el modo "una línea por bot" del gráfico). Siempre incluye la lista
    de bots registrados para el desplegable de la UI.
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

    points, summary = _cum_points(rows)
    out = {
        "points": points,
        "currency": next(((b.session or {}).get('currency','USD') for b in BOTS.values()),'USD'),
        "summary": summary,
        "symbols": symbols,
        "bots": [
            {"id": b.cfg.id, "name": b.cfg.name}
            for b in BOTS.values()
        ] or [{"id": c.id, "name": c.name} for c in ALL_BOTS],
    }
    if per_bot:
        series = []
        for cfg in ALL_BOTS:
            bot_rows = [r for r in rows if r[3] == cfg.id]
            if not bot_rows:
                continue
            b_points, b_summary = _cum_points(bot_rows)
            series.append({
                "id": cfg.id,
                "name": cfg.name,
                "points": b_points,
                "summary": b_summary,
            })
        out["series"] = series
    return out


BOTS: dict[str, TradingBot] = {}


def start_all() -> None:
    for cfg in ALL_BOTS:
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
