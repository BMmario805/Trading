"""Aprendizaje continuo a partir de operaciones SIMULADAS registradas.

Mecanismos complementarios, todos con salvaguardas de tamaño de muestra
para no "aprender" del ruido:

1. Puerta de ventaja validada (edge gate). El entrenamiento walk-forward
   deja en bot_params si un símbolo tiene parámetros que superaron la
   validación (applied=1). El análisis de las operaciones en vivo demostró
   que casi toda la pérdida venía de operar símbolos cuya validación había
   FALLADO usando parámetros por defecto. Regla:
     - validado   -> se opera a tamaño completo
     - rechazado  -> se opera a prueba con riesgo reducido
     - sin datos  -> se opera a prueba con riesgo reducido
   Los vetos por pérdidas y los límites de cartera siguen bloqueando entradas.

2. Bloqueos por rendimiento en vivo. Con muestra suficiente en la ventana
   reciente, un símbolo (o una dirección concreta de un símbolo, p. ej.
   "cortos de MSFT") con expectativa claramente negativa queda vetado.
   Un entrenamiento posterior que valide el símbolo levanta el veto: esa
   es la vía de reentrada, no el simple paso del tiempo. También se vetan
   franjas horarias UTC con expectativa claramente negativa y muestra
   suficiente.

3. Tamaño prudente. La muestra en vivo puede reducir el tamaño hasta 0.5×;
   una expectativa positiva no autoriza aumentos sin calibración monetaria.

4. Meta-modelo (meta-labeling, López de Prado). Un clasificador logístico
   ONLINE aprende, operación a operación, en qué contextos las señales del
   bot ganan o pierden. Las features son el desglose de confirmaciones que
   el bot guardó al abrir (cada sistema técnico y si estaba a favor o en
   contra de la dirección), la categoría del mercado, la dirección y la
   hora. El modelo no decide la dirección (eso es del bot primario): decide
   CUÁNTO fiarse de la señal. Su objetivo se pondera por la magnitud del R
   neto; la puntuación resultante no es probabilidad de éxito calibrada.
   Puede reducir tamaño o filtrar señales. Aprende de cierres con snapshot
   de la generación vigente y riesgo inicial monetario verificable.

Además, un planificador reentrena cada bot automáticamente cada pocas
horas (walk-forward con histórico fresco), de modo que parámetros, vetos,
multiplicadores y meta-modelo se adaptan solos al mercado sin intervención
manual.
"""

import asyncio
import json
import logging
import math
import sqlite3
import time

from . import training
from .config import SYMBOLS_BY_ID
from .history import DB_PATH
from .database import Connection

log = logging.getLogger("learning")

# Ventana de análisis de las operaciones en vivo
WINDOW_DAYS = 7

# Muestras mínimas antes de sacar conclusiones (contra el sobreajuste al ruido)
MIN_TRADES_SYMBOL = 8
MIN_TRADES_DIRECTION = 6
MIN_TRADES_SIZING = 10

# Umbral de veto: expectativa media por operación, en múltiplos de R
BLOCK_EXPECTANCY_R = -0.15

# Vetos por franja horaria UTC (muestra mínima y expectativa claramente mala)
MIN_TRADES_HOUR = 10
BLOCK_HOUR_EXPECTANCY_R = -0.25

PROBATION_SIZE = 0.15         # máximo 15% del riesgo normal en pares sin validar
PROBATION_THRESHOLD_MULT = 1.0   # sin recargo sobre el umbral de entrada
PROBATION_META_P = 0.30        # puntuación ponderada mínima (modelo maduro)
MAX_PROBATION_POSITIONS = 1    # posiciones a prueba simultáneas por bot

# --- Meta-modelo (regresión logística online) ---
META_MIN_SAMPLES = 30     # madurez mínima antes de que pueda vetar señales
META_GATE_P = 0.25        # puntuación ponderada mínima para dejar pasar
META_LR = 0.05            # tasa de aprendizaje SGD
META_L2 = 0.001           # regularización L2 (evita pesos desbocados)
# Cambiar las features exige reaprender: quitar un peso no elimina su
# influencia anterior sobre el sesgo y los demás coeficientes.
META_FEATURE_VERSION = 3  # Objetivo ponderado por magnitud de R neta, no tasa de acierto.
NON_CAUSAL_CONFIRMATIONS = frozenset({
    "Opinión pública", "Señal combinada", "Contexto macro oficial",
})

# Cada cuántas horas se reentrena cada bot (walk-forward automático)
RETRAIN_HOURS = {
    "scalper": 4, "orb": 6, "squeeze": 6, "flow": 6,
    "swing": 12, "trend": 12, "meanrev": 12,
}
DEFAULT_RETRAIN_HOURS = 8
SCHEDULER_TICK = 300          # el planificador mira cada 5 min
MAX_CONCURRENT_TRAININGS = 2

_policies: dict[str, dict] = {}       # bot -> política vigente (en memoria)
_edges: dict[str, dict[str, str]] = {}  # bot -> {símbolo: validated|rejected|unknown}
_models: dict[str, dict] = {}         # bot -> meta-modelo {w, n, lastTradeId}
META_PROCESSING_VERSION = 1
_scheduler_task: asyncio.Task | None = None


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, factory=Connection)
    con.execute(
        """CREATE TABLE IF NOT EXISTS bot_learning (
            bot TEXT PRIMARY KEY,
            policy TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS bot_model (
            bot TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS bot_model_processed (
            bot TEXT NOT NULL, trade_id INTEGER NOT NULL,
            PRIMARY KEY (bot, trade_id)
        )"""
    )
    return con


# ---------------------------------------------------------------------------
# Análisis de las operaciones reales
# ---------------------------------------------------------------------------


def analyze(bot_id: str, window_days: int = WINDOW_DAYS,
            after_by_symbol: dict[str, int] | None = None) -> dict:
    """Estadísticas de las operaciones cerradas recientes, por símbolo,
    por (símbolo, dirección), por hora UTC y por motivo de salida.

    La expectativa se mide en R (pnl / riesgo inicial |entrada - stop| × qty):
    es comparable entre símbolos con precios y volatilidades distintas.
    """
    with _conn() as con:
        last_exit = con.execute(
            """SELECT MAX(exit_time) FROM bot_trades
               WHERE bot = ? AND status = 'closed' AND pnl IS NOT NULL""",
            (bot_id,),
        ).fetchone()[0]
        # El tiempo con el equipo apagado no debe borrar la muestra reciente.
        reference = last_exit if last_exit is not None else int(time.time())
        since = reference - window_days * 86400
        rows = con.execute(
            """SELECT symbol, direction, qty, entry_price, sl, pnl,
                      entry_time, exit_reason,
                      COALESCE(economic_risk_dist,initial_risk_dist,ABS(entry_price-sl))*COALESCE(quote_to_account,1), exit_time
               FROM bot_trades
               WHERE bot = ? AND status = 'closed' AND pnl IS NOT NULL
                 AND exit_time >= ?""",
            (bot_id, since),
        ).fetchall()

    def _bucket() -> dict:
        return {"n": 0, "wins": 0, "pnl": 0.0, "sumR": 0.0}

    by_symbol: dict[str, dict] = {}
    by_dir: dict[tuple[str, str], dict] = {}
    by_hour: dict[int, dict] = {}
    by_exit: dict[str, dict] = {}
    for sym, direction, qty, entry, sl, pnl, entry_time, exit_reason, initial_dist, exit_time in rows:
        if after_by_symbol and exit_time <= after_by_symbol.get(sym, -1):
            continue
        risk = (initial_dist if initial_dist is not None else abs(entry - sl)) * qty
        r_mult = pnl / risk if risk > 0 else 0.0
        for bucket in (
            by_symbol.setdefault(sym, _bucket()),
            by_dir.setdefault((sym, direction), _bucket()),
            by_hour.setdefault(int(entry_time % 86400 // 3600), _bucket()),
            by_exit.setdefault(exit_reason or "?", _bucket()),
        ):
            bucket["n"] += 1
            bucket["wins"] += 1 if pnl > 0 else 0
            bucket["pnl"] += pnl
            bucket["sumR"] += r_mult

    def _fin(d: dict) -> dict:
        n = d["n"]
        return {
            "n": n,
            "wins": d["wins"],
            "winRate": round(d["wins"] / n * 100, 1) if n else None,
            "pnl": round(d["pnl"], 2),
            "expectancyR": round(d["sumR"] / n, 3) if n else None,
        }

    return {
        "windowDays": window_days,
        "trades": sum(d["n"] for d in by_symbol.values()),
        "bySymbol": {s: _fin(d) for s, d in by_symbol.items()},
        "byDirection": {f"{s}:{dr}": _fin(d) for (s, dr), d in by_dir.items()},
        "byHour": {str(h): _fin(d) for h, d in sorted(by_hour.items())},
        "byExit": {k: _fin(d) for k, d in by_exit.items()},
    }


# ---------------------------------------------------------------------------
# Meta-modelo: regresión logística online sobre el contexto de cada operación
# ---------------------------------------------------------------------------


def _sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def meta_features(
    bot_id: str,
    category: str,
    direction: str,
    hour_utc: int,
    confirmations: list[dict],
) -> dict[str, float]:
    """Vector de features (disperso) del contexto de una señal.

    La clave del diseño: cada confirmación técnica se codifica RELATIVA a la
    dirección de la operación (voto × signo de la dirección). Así el modelo
    aprende "cuando el sistema X está a favor/en contra de mi entrada, ¿gano?"
    y lo aprendido con largos sirve también para cortos.
    """
    sign = 1.0 if direction == "long" else -1.0
    feats: dict[str, float] = {
        "bias": 1.0,
        "dir": sign,
        f"cat:{category}": 1.0,
        "hour_sin": math.sin(2 * math.pi * hour_utc / 24),
        "hour_cos": math.cos(2 * math.pi * hour_utc / 24),
    }
    for c in confirmations or []:
        system = c.get("system", "?")
        if system in NON_CAUSAL_CONFIRMATIONS:
            continue
        v = c.get("vote") or 0.0
        if not v:
            continue
        feats[f"conf:{system}"] = max(-1.0, min(1.0, v * sign))
    return feats


def _new_model() -> dict:
    return {"w": {}, "n": 0, "lastTradeId": 0, "updatedAt": 0,
            "engineVersion": training.ENGINE_VERSION, "featureVersion": META_FEATURE_VERSION,
            "processingVersion": META_PROCESSING_VERSION}


def _load_model(bot_id: str) -> dict:
    if bot_id in _models:
        return _models[bot_id]
    with _conn() as con:
        row = con.execute(
            "SELECT model FROM bot_model WHERE bot = ?", (bot_id,)
        ).fetchone()
    _models[bot_id] = json.loads(row[0]) if row else _new_model()
    if (_models[bot_id].get('engineVersion') != training.ENGINE_VERSION
            or _models[bot_id].get('featureVersion') != META_FEATURE_VERSION):
        _models[bot_id] = _new_model()
    return _models[bot_id]


def _save_model(bot_id: str) -> None:
    m = _models.get(bot_id)
    if m is None:
        return
    m["updatedAt"] = int(time.time())
    # pesos redondeados: JSON compacto y sin ruido numérico
    m["w"] = {k: round(v, 6) for k, v in m["w"].items() if abs(v) > 1e-6}
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO bot_model VALUES (?, ?, ?)",
            (bot_id, json.dumps(m, ensure_ascii=False), m["updatedAt"]),
        )


def meta_predict(bot_id: str, feats: dict[str, float]) -> float:
    """Puntuación logística ponderada por R; no es probabilidad calibrada."""
    m = _load_model(bot_id)
    w = m["w"]
    z = sum(w.get(k, 0.0) * v for k, v in feats.items())
    return _sigmoid(z)


def _meta_sgd_step(model: dict, feats: dict[str, float], won: bool, weight: float = 1.0) -> None:
    """Un paso de descenso de gradiente sobre la pérdida logística."""
    w = model["w"]
    z = sum(w.get(k, 0.0) * v for k, v in feats.items())
    p = _sigmoid(z)
    err = (1.0 if won else 0.0) - p
    for k, v in feats.items():
        w[k] = w.get(k, 0.0) + META_LR * weight * (err * v - META_L2 * w.get(k, 0.0))


def meta_gate(bot_id: str, feats: dict[str, float]) -> tuple[bool, float | None, str | None]:
    """¿Deja pasar el meta-modelo esta señal? -> (sí/no, puntuación, nota).

    Con el modelo aún inmaduro (pocas operaciones aprendidas) no veta nada
    y no opina sobre el tamaño (p = None): primero hay que ganarse la opinión.
    """
    m = _load_model(bot_id)
    if m["n"] < META_MIN_SAMPLES:
        return True, None, f"meta-modelo en aprendizaje ({m['n']}/{META_MIN_SAMPLES})"
    p = meta_predict(bot_id, feats)
    if p < META_GATE_P:
        return False, p, f"señal filtrada: puntuación ponderada por R={p:.0%} < {META_GATE_P:.0%}"
    return True, p, None


def _trade_features(bot_id: str, row: tuple) -> dict[str, float] | None:
    """Reconstruye las features de una operación cerrada desde su snapshot."""
    symbol, direction, entry_time, snapshot_json = row
    if not snapshot_json:
        return None
    try:
        snap = json.loads(snapshot_json)
    except (TypeError, ValueError):
        return None
    if snap.get('engineVersion') != training.ENGINE_VERSION:
        return None
    confs = snap.get("confirmations") or []
    sym = SYMBOLS_BY_ID.get(symbol)
    category = sym.category if sym else "?"
    hour = int(entry_time % 86400 // 3600)
    return meta_features(bot_id, category, direction, hour, confs)


def meta_learn_new_trades(bot_id: str) -> int:
    """Entrena el meta-modelo con las operaciones cerradas aún no aprendidas.

    Se llama al cerrar cada operación (aprende de una en una) y al arrancar
    (bootstrap con todo el historial pendiente, en orden de cierre y una sola
    pasada). Devuelve cuántas operaciones nuevas aprendió.
    """
    with _conn() as con:
        # El modelo y sus recibos se confirman juntos. Un fallo de escritura
        # no puede dejar pesos aprendidos sin recibo (ni a la inversa).
        con.execute("BEGIN IMMEDIATE")
        saved = con.execute("SELECT model FROM bot_model WHERE bot = ?", (bot_id,)).fetchone()
        m = json.loads(saved[0]) if saved else _new_model()
        compatible = (m.get("engineVersion") == training.ENGINE_VERSION
                      and m.get("featureVersion") == META_FEATURE_VERSION
                      and m.get("processingVersion") == META_PROCESSING_VERSION)
        if not compatible:
            # El antiguo cursor por ID de apertura no permite distinguir las
            # operaciones omitidas de las ya aprendidas: reconstruir los pesos
            # evita duplicarlos y recupera todos los cierres compatibles.
            m = _new_model()
            con.execute("DELETE FROM bot_model_processed WHERE bot = ?", (bot_id,))
        rows = con.execute(
            """SELECT t.id, t.symbol, t.direction, t.entry_time, t.snapshot, t.pnl
                    , t.qty*COALESCE(t.economic_risk_dist,t.initial_risk_dist)*COALESCE(t.quote_to_account,1)
               FROM bot_trades t
               LEFT JOIN bot_model_processed p ON p.bot=t.bot AND p.trade_id=t.id
               WHERE t.bot = ? AND t.status = 'closed' AND t.pnl IS NOT NULL
                 AND p.trade_id IS NULL
               ORDER BY t.exit_time, t.id""",
            (bot_id,),
        ).fetchall()
        learned = 0
        for tid, symbol, direction, entry_time, snap, pnl, initial_risk in rows:
            feats = _trade_features(bot_id, (symbol, direction, entry_time, snap))
            m["lastTradeId"] = max(m["lastTradeId"], tid)  # informativo, nunca cursor
            if (feats is not None and math.isfinite(pnl) and initial_risk is not None
                    and math.isfinite(initial_risk) and initial_risk>0):
                r_mult=pnl/initial_risk
                weight=min(3.0,max(.25,abs(r_mult)))
                _meta_sgd_step(m, feats, r_mult>0, weight)
                learned += 1
            con.execute("INSERT INTO bot_model_processed VALUES (?, ?)", (bot_id, tid))
        m["n"] += learned
        if rows or not compatible or not saved:
            m["updatedAt"] = int(time.time())
            m["w"] = {k: round(v, 6) for k, v in m["w"].items() if abs(v) > 1e-6}
            con.execute("INSERT OR REPLACE INTO bot_model VALUES (?, ?, ?)",
                        (bot_id, json.dumps(m, ensure_ascii=False), m["updatedAt"]))
    _models[bot_id] = m
    if not learned:
        return 0
    log.info("%s: meta-modelo aprendió %d operaciones (total %d)",
             bot_id, learned, m["n"])
    return learned


def meta_summary(bot_id: str) -> dict:
    """Resumen del meta-modelo para la UI: madurez y features más influyentes."""
    m = _load_model(bot_id)
    top = sorted(m["w"].items(), key=lambda kv: -abs(kv[1]))[:10]
    return {
        "samples": m["n"],
        "mature": m["n"] >= META_MIN_SAMPLES,
        "minSamples": META_MIN_SAMPLES,
        "gateP": META_GATE_P,
        "calibrated": False,
        "objective": "r-weighted-logistic-v1",
        "outputMeaning": "Puntuación ponderada por R; no probabilidad de éxito ni expectativa R",
        "sizeIncreaseAllowed": False,
        "topFeatures": [
            {"feature": k, "weight": round(v, 3)} for k, v in top
        ],
        "updatedAt": m.get("updatedAt"),
    }


# ---------------------------------------------------------------------------
# Política: vetos y multiplicadores de tamaño
# ---------------------------------------------------------------------------


def _load_edges(bot_id: str) -> dict[str, str]:
    """Estado de la ventaja validada por símbolo según el último entrenamiento."""
    out: dict[str, str] = {}
    for r in training.load_results():
        if r["bot"] != bot_id:
            continue
        out[r["symbol"]] = "validated" if r["applied"] else "rejected"
    return out


def refresh(bot_id: str) -> dict:
    """Recalcula la política del bot a partir de sus operaciones y del último
    entrenamiento; se llama al cerrar cada operación y al acabar cada
    entrenamiento."""
    stats = analyze(bot_id)
    now = int(time.time())
    trained_at = training.last_trained_at(bot_id)
    results = [r for r in training.load_results() if r["bot"] == bot_id]
    validated_at = {r["symbol"]: r["trainedAt"] for r in results if r["applied"]}
    risk_stats = analyze(bot_id, after_by_symbol=validated_at) if validated_at else stats
    previous = _policies.get(bot_id)
    if previous is None:
        with _conn() as con:
            row = con.execute("SELECT policy FROM bot_learning WHERE bot = ?", (bot_id,)).fetchone()
        previous = json.loads(row[0]) if row else {}
    prior_blocks = {(b["symbol"], b.get("direction")): b for b in previous.get("blocks", [])}

    def created_at(symbol: str, direction: str | None) -> int:
        old = prior_blocks.get((symbol, direction))
        if old and validated_at.get(symbol, 0) <= old.get("createdAt", 0):
            return old.get("createdAt", now)
        return now

    blocks: list[dict] = []
    for sym, s in risk_stats["bySymbol"].items():
        if s["n"] >= MIN_TRADES_SYMBOL and (s["expectancyR"] or 0) < BLOCK_EXPECTANCY_R:
            blocks.append({
                "symbol": sym,
                "direction": None,
                "reason": (
                    f"expectativa {s['expectancyR']:+.2f}R en {s['n']} operaciones "
                    f"({stats['windowDays']} días)"
                ),
                "createdAt": created_at(sym, None),
            })
    blocked_syms = {b["symbol"] for b in blocks}
    for key, s in risk_stats["byDirection"].items():
        sym, direction = key.split(":")
        if sym in blocked_syms:
            continue
        if (
            s["n"] >= MIN_TRADES_DIRECTION
            and s["winRate"] is not None and s["winRate"] <= 30
            and s["pnl"] < 0
        ):
            blocks.append({
                "symbol": sym,
                "direction": direction,
                "reason": (
                    f"{'largos' if direction == 'long' else 'cortos'} con "
                    f"{s['winRate']:.0f}% de acierto y {s['pnl']:+.2f} en {s['n']} ops"
                ),
                "createdAt": created_at(sym, direction),
            })

    size_mult: dict[str, float] = {}
    for sym, s in stats["bySymbol"].items():
        if s["n"] >= MIN_TRADES_SIZING and s["expectancyR"] is not None:
            size_mult[sym] = round(min(1.0, max(0.5, 1.0 + s["expectancyR"])), 2)

    # vetos por franja horaria UTC: horas con expectativa claramente negativa
    # y muestra suficiente (p. ej. la iliquidez del rollover asiático)
    hour_blocks: list[dict] = []
    for h, s in stats["byHour"].items():
        if s["n"] >= MIN_TRADES_HOUR and (s["expectancyR"] or 0) < BLOCK_HOUR_EXPECTANCY_R:
            hour_blocks.append({
                "hour": int(h),
                "reason": (
                    f"expectativa {s['expectancyR']:+.2f}R en {s['n']} operaciones "
                    f"a las {h}:00 UTC"
                ),
                "createdAt": next((b.get("createdAt", now) for b in previous.get("hourBlocks", [])
                                   if b["hour"] == int(h)), now),
            })

    policy = {
        "blocks": blocks,
        "hourBlocks": hour_blocks,
        "sizeMult": size_mult,
        "stats": stats,
        "lastTrainedAt": trained_at,
        "validatedAtBySymbol": validated_at,
        "updatedAt": now,
    }
    _policies[bot_id] = policy
    _edges[bot_id] = _load_edges(bot_id)
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO bot_learning VALUES (?, ?, ?)",
            (bot_id, json.dumps(policy, ensure_ascii=False), now),
        )
    if blocks:
        log.info(
            "%s: vetos de aprendizaje: %s", bot_id,
            "; ".join(
                f"{b['symbol']}{'/' + b['direction'] if b['direction'] else ''}"
                for b in blocks
            ),
        )
    if hour_blocks:
        log.info("%s: horas vetadas (UTC): %s", bot_id,
                 ", ".join(str(b["hour"]) for b in hour_blocks))
    # el meta-modelo aprende las operaciones cerradas que aún no ha visto
    try:
        meta_learn_new_trades(bot_id)
    except Exception as exc:
        log.debug("%s: meta-modelo no pudo aprender: %s", bot_id, exc)
    return policy


def load(bot_id: str) -> dict:
    """Recalcula al arrancar para no reutilizar estadísticas de reglas antiguas."""
    if bot_id in _policies:
        return _policies[bot_id]
    return refresh(bot_id)


def reset(bot_id: str) -> None:
    """Olvida lo aprendido (acompaña al reset del bot), meta-modelo incluido."""
    _policies.pop(bot_id, None)
    _edges.pop(bot_id, None)
    _models.pop(bot_id, None)
    with _conn() as con:
        con.execute("DELETE FROM bot_learning WHERE bot = ?", (bot_id,))
        con.execute("DELETE FROM bot_model WHERE bot = ?", (bot_id,))
        con.execute("DELETE FROM bot_model_processed WHERE bot = ?", (bot_id,))


# ---------------------------------------------------------------------------
# Puertas que consulta el bot antes de abrir
# ---------------------------------------------------------------------------


def _active_blocks(bot_id: str) -> list[dict]:
    """Vetos vigentes: un entrenamiento validado POSTERIOR al veto lo levanta
    (la reentrada se gana revalidando la ventaja, no esperando)."""
    policy = _policies.get(bot_id) or load(bot_id)
    edges = _edges.get(bot_id, {})
    validated_at = policy.get("validatedAtBySymbol", {})
    out = []
    for b in policy.get("blocks", []):
        if (
            edges.get(b["symbol"]) == "validated"
            and validated_at.get(b["symbol"], 0) > b.get("createdAt", 0)
        ):
            continue
        out.append(b)
    return out


def edge_status(bot_id: str, symbol: str) -> str:
    """'validated' | 'rejected' | 'unknown' según el último walk-forward."""
    if bot_id not in _edges:
        load(bot_id)
    return _edges.get(bot_id, {}).get(symbol, "unknown")


def entry_mode(
    bot_id: str, symbol: str, direction: str | None = None, hour: int | None = None
) -> tuple[str, str | None]:
    """Sin validar: a prueba; validado: normal. Los vetos bloquean ambos modos."""
    if hour is not None:
        policy = _policies.get(bot_id) or load(bot_id)
        for hb in policy.get("hourBlocks", []):
            if hb["hour"] == hour:
                return "blocked", f"veto horario: {hb['reason']}"
    mode, why = "full", None
    if edge_status(bot_id, symbol) != "validated":
        mode = "probation"
        why = "sin ventaja validada en el último entrenamiento"
    for b in _active_blocks(bot_id):
        if b["symbol"] != symbol:
            continue
        if b["direction"] is None or b["direction"] == direction:
            mode = "blocked"
            why = f"veto de aprendizaje: {b['reason']}"
            break
    return mode, why


def gate(
    bot_id: str, symbol: str, direction: str | None = None, hour: int | None = None
) -> tuple[bool, str | None]:
    """True solamente si la entrada está autorizada por la política."""
    mode, why = entry_mode(bot_id, symbol, direction, hour)
    return mode != "blocked", why


def size_multiplier(bot_id: str, symbol: str) -> tuple[float, str | None]:
    """Multiplicador de tamaño aprendido -> (multiplicador, explicación)."""
    policy = _policies.get(bot_id) or load(bot_id)
    mult = min(1.0, max(0.5, policy.get("sizeMult", {}).get(symbol, 1.0)))
    note = None
    if mult != 1.0:
        note = f"tamaño ×{mult:g} por expectativa en vivo"
    return mult, note


def summary(bot_id: str) -> dict:
    """Resumen compacto para la tarjeta del bot en la UI."""
    policy = _policies.get(bot_id) or load(bot_id)
    edges = _edges.get(bot_id, {})
    trained_at = policy.get("lastTrainedAt")
    hours = RETRAIN_HOURS.get(bot_id, DEFAULT_RETRAIN_HOURS)
    from .bots import ALL_BOTS
    cfg = next((cfg for cfg in ALL_BOTS if cfg.id == bot_id), None)
    pending = training.due_symbols(bot_id, cfg.universe, hours) if cfg else []
    return {
        "autoTrainPending": pending,
        "autoTrainBatchSize": 3,
        "blocks": [
            {k: b.get(k) for k in ("symbol", "direction", "reason")}
            for b in _active_blocks(bot_id)
        ],
        "hourBlocks": [
            {k: b.get(k) for k in ("hour", "reason")}
            for b in policy.get("hourBlocks", [])
        ],
        "sizeMult": policy.get("sizeMult", {}),
        "edges": edges,
        "model": meta_summary(bot_id),
        "autoRetrainHours": hours,
        "lastTrainedAt": trained_at,
        "nextRetrainAt": (trained_at + hours * 3600) if trained_at else None,
        "updatedAt": policy.get("updatedAt"),
    }


def insights(bot_id: str) -> dict:
    """Análisis completo para el endpoint /api/bots/{id}/learning."""
    policy = _policies.get(bot_id) or load(bot_id)
    return {**summary(bot_id), "stats": policy.get("stats", {})}


# ---------------------------------------------------------------------------
# Reentrenamiento automático
# ---------------------------------------------------------------------------


def _trainings_running() -> int:
    return sum(1 for b in RETRAIN_HOURS if training.is_running(b))


async def _scheduler_loop() -> None:
    from . import bots  # import diferido: bots importa learning

    await asyncio.sleep(20)  # dejar arrancar el backend antes del primer ciclo
    while True:
        try:
            now = time.time()
            candidates = []
            for bot_id, bot in bots.BOTS.items():
                if training.is_running(bot_id):
                    continue
                hours = RETRAIN_HOURS.get(bot_id, DEFAULT_RETRAIN_HOURS)
                pending = training.due_symbols(bot_id, bot.cfg.universe, hours, now)
                if pending:
                    last = training.training_status(bot_id).get("finishedAt") or 0
                    candidates.append((last, bot_id, bot, pending))
            candidates.sort(key=lambda item: (item[0], item[1]))
            for _, bot_id, bot, pending in candidates:
                if _trainings_running() >= MAX_CONCURRENT_TRAININGS:
                    break
                log.info("autoentrenamiento %s: %s", bot_id, pending[:3])
                training.start_training(bot, pending[:3])
        except Exception as exc:
            log.warning("planificador de entrenamiento: %s", exc)
        await asyncio.sleep(SCHEDULER_TICK)


def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop(), name="learning-scheduler")


async def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
