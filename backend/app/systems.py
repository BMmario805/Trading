"""Sistemas clásicos de análisis técnico, por vela y sin mirar al futuro.

Cada sistema viene de una escuela reconocida del análisis técnico:

  - Pivotes fractales (Bill Williams): un máximo/mínimo local confirmado
    solo cuando han cerrado las velas posteriores → base de todo lo demás.
  - Soportes y resistencias (charting clásico): agrupación de pivotes en
    niveles con nº de toques como medida de fuerza.
  - Líneas de tendencia (teoría de Dow): recta entre los dos últimos
    pivotes del mismo tipo, proyectada hasta la última vela.
  - Retrocesos de Fibonacci: niveles 23.6/38.2/50/61.8/78.6 % del último
    impulso relevante; el precio suele reaccionar al tocarlos.
  - Patrones de velas japonesas (Nison): envolventes, martillo, estrella
    fugaz y doji como señales de giro.
  - Divergencias de RSI: precio y momento en desacuerdo en los pivotes.
  - VWAP anclado a la sesión: la referencia institucional intradía.
  - Ichimoku Kinko Hyo: posición del precio respecto a la nube (kumo).

Las funciones *_series devuelven una lista con un voto por vela en
[-1, 1] usando SOLO información disponible en esa vela (los pivotes
cuentan a partir de su vela de confirmación), de modo que sirven tanto
para el bot en vivo como para el backtest sin sesgo de anticipación.
`chart_overlays` empaqueta los trazados (niveles, líneas, fib, medias,
Bollinger) listos para dibujar en lightweight-charts.
"""

from collections import deque
from datetime import datetime, timezone

from .indicators import (
    adx_series,
    atr_series,
    cci,
    donchian,
    efficiency_ratio,
    ema,
    heikin_ashi,
    kama,
    keltner,
    macd,
    mfi,
    obv_series,
    psar,
    roc,
    rsi,
    sma,
    squeeze_on_series,
    stochastic,
    supertrend,
    vortex_series,
    williams_r,
)

try:  # hora de apertura de NYSE con cambio de horario correcto
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:  # sin base de datos tz (p. ej. Windows sin tzdata)
    _NY = None

# ---------------------------------------------------------------------------
# Pivotes fractales
# ---------------------------------------------------------------------------


def find_pivots(
    candles: list[dict], left: int = 3, right: int = 3
) -> list[dict]:
    """Pivotes fractales: {i, price, kind 'high'|'low', confirm}.

    Un pivote en la vela i solo se CONFIRMA en i+right (cuando han cerrado
    las velas de la derecha); `confirm` es el índice desde el que se puede
    usar sin mirar al futuro.
    """
    out: list[dict] = []
    n = len(candles)
    for i in range(left, n - right):
        h, l = candles[i]["high"], candles[i]["low"]
        window = candles[i - left : i] + candles[i + 1 : i + right + 1]
        if all(h >= w["high"] for w in window):
            out.append({"i": i, "price": h, "kind": "high", "confirm": i + right})
        if all(l <= w["low"] for w in window):
            out.append({"i": i, "price": l, "kind": "low", "confirm": i + right})
    return out


# ---------------------------------------------------------------------------
# Soportes y resistencias (agrupación de pivotes)
# ---------------------------------------------------------------------------


def support_resistance(
    candles: list[dict],
    lookback: int = 150,
    max_levels: int = 8,
    pivots: list[dict] | None = None,
) -> list[dict]:
    """Niveles S/R por agrupación de pivotes: {price, kind, touches}.

    kind se decide respecto al último cierre: soporte si queda por debajo,
    resistencia si queda por encima. touches = nº de pivotes agrupados.
    """
    if len(candles) < 20:
        return []
    start = max(0, len(candles) - lookback)
    pvs = [p for p in (pivots or find_pivots(candles)) if p["i"] >= start]
    if not pvs:
        return []
    last_close = candles[-1]["close"]
    atr_last = atr_series(candles)[-1]
    tol = atr_last * 0.5 if atr_last else last_close * 0.002

    clusters: list[dict] = []
    for p in sorted(pvs, key=lambda x: x["price"]):
        if clusters and abs(p["price"] - clusters[-1]["prices"][-1]) <= tol:
            clusters[-1]["prices"].append(p["price"])
            clusters[-1]["last_i"] = max(clusters[-1]["last_i"], p["i"])
        else:
            clusters.append({"prices": [p["price"]], "last_i": p["i"]})

    levels = [
        {
            "price": round(sum(c["prices"]) / len(c["prices"]), 6),
            "touches": len(c["prices"]),
            "kind": "soporte" if sum(c["prices"]) / len(c["prices"]) < last_close
            else "resistencia",
        }
        for c in clusters
    ]
    # los más tocados y, a igualdad, los más cercanos al precio actual
    levels.sort(key=lambda l: (-l["touches"], abs(l["price"] - last_close)))
    return levels[:max_levels]


def sr_bounce_series(
    candles: list[dict], pivots: list[dict] | None = None, lookback_pivots: int = 12
) -> list[float]:
    """Rebote en nivel: +1 si la vela toca un pivote-mínimo previo y cierra
    encima (soporte aguanta), -1 si toca un pivote-máximo y cierra debajo."""
    n = len(candles)
    out = [0.0] * n
    if n < 20:
        return out
    pvs = sorted(pivots or find_pivots(candles), key=lambda p: p["confirm"])
    atrs = atr_series(candles)
    lows: list[float] = []
    highs: list[float] = []
    j = 0
    for i in range(n):
        while j < len(pvs) and pvs[j]["confirm"] <= i:
            (lows if pvs[j]["kind"] == "low" else highs).append(pvs[j]["price"])
            j += 1
        a = atrs[i]
        if a is None or a <= 0:
            continue
        tol = 0.35 * a
        c = candles[i]
        for lvl in lows[-lookback_pivots:]:
            if abs(c["low"] - lvl) <= tol and c["close"] > lvl + tol * 0.5:
                out[i] = 1.0
                break
        if out[i] == 0.0:
            for lvl in highs[-lookback_pivots:]:
                if abs(c["high"] - lvl) <= tol and c["close"] < lvl - tol * 0.5:
                    out[i] = -1.0
                    break
    return out


# ---------------------------------------------------------------------------
# Líneas de tendencia (dos últimos pivotes del mismo tipo)
# ---------------------------------------------------------------------------


def trendlines(
    candles: list[dict], lookback: int = 120, pivots: list[dict] | None = None
) -> list[dict]:
    """Líneas de tendencia: recta por los dos últimos pivotes de cada tipo,
    proyectada hasta la última vela. [{kind, from:{time,price}, to:{...}}]"""
    n = len(candles)
    if n < 20:
        return []
    start = max(0, n - lookback)
    pvs = [p for p in (pivots or find_pivots(candles)) if p["i"] >= start]
    out: list[dict] = []
    for kind, name in (("low", "soporte"), ("high", "resistencia")):
        pts = [p for p in pvs if p["kind"] == kind][-2:]
        if len(pts) < 2 or pts[0]["i"] == pts[1]["i"]:
            continue
        (i1, p1), (i2, p2) = (pts[0]["i"], pts[0]["price"]), (pts[1]["i"], pts[1]["price"])
        slope = (p2 - p1) / (i2 - i1)
        proj = p2 + slope * (n - 1 - i2)
        out.append({
            "kind": name,
            "direction": "alcista" if slope > 0 else "bajista",
            "from": {"time": candles[i1]["time"], "price": round(p1, 6)},
            "to": {"time": candles[-1]["time"], "price": round(proj, 6)},
        })
    return out


# ---------------------------------------------------------------------------
# Fibonacci
# ---------------------------------------------------------------------------

FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fibonacci_swing(candles: list[dict], lookback: int = 120) -> dict | None:
    """Retrocesos del último impulso: swing entre el mínimo y el máximo del
    tramo reciente; la dirección la marca cuál de los dos es más nuevo."""
    n = len(candles)
    if n < 20:
        return None
    window = candles[max(0, n - lookback):]
    hi_i = max(range(len(window)), key=lambda i: window[i]["high"])
    lo_i = min(range(len(window)), key=lambda i: window[i]["low"])
    hi, lo = window[hi_i]["high"], window[lo_i]["low"]
    if hi <= lo:
        return None
    up = lo_i < hi_i  # el impulso más reciente es alcista
    span = hi - lo
    levels = [
        {"ratio": r, "price": round(hi - span * r if up else lo + span * r, 6)}
        for r in FIB_RATIOS
    ]
    return {
        "direction": "alcista" if up else "bajista",
        "high": {"time": window[hi_i]["time"], "price": round(hi, 6)},
        "low": {"time": window[lo_i]["time"], "price": round(lo, 6)},
        "levels": levels,
    }


def fib_bounce_series(candles: list[dict], lookback: int = 120) -> list[float]:
    """Reacción en nivel de Fibonacci a favor del impulso: +1 si en impulso
    alcista el precio retrocede a un nivel fib y cierra encima; -1 simétrico."""
    n = len(candles)
    out = [0.0] * n
    if n < 40:
        return out
    atrs = atr_series(candles)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    # máximo/mínimo deslizantes de las `lookback` velas ANTERIORES (sin la
    # actual), con deques monótonas para que el backtest siga siendo O(n)
    maxq: deque[int] = deque()
    minq: deque[int] = deque()
    for i in range(n):
        if i >= 1:
            j = i - 1
            while maxq and highs[maxq[-1]] <= highs[j]:
                maxq.pop()
            maxq.append(j)
            while minq and lows[minq[-1]] >= lows[j]:
                minq.pop()
            minq.append(j)
            while maxq[0] < i - lookback:
                maxq.popleft()
            while minq[0] < i - lookback:
                minq.popleft()
        if i < 30:
            continue
        a = atrs[i]
        if a is None or a <= 0:
            continue
        w_hi, w_lo = maxq[0], minq[0]
        hi, lo = highs[w_hi], lows[w_lo]
        span = hi - lo
        if span < 2 * a:
            continue
        tol = 0.35 * a
        c = candles[i]
        if w_lo < w_hi:  # impulso alcista: buscar rebote en el retroceso
            for r in (0.382, 0.5, 0.618):
                lvl = hi - span * r
                if abs(c["low"] - lvl) <= tol and c["close"] > lvl:
                    out[i] = 1.0
                    break
        else:  # impulso bajista: rechazo en el rebote
            for r in (0.382, 0.5, 0.618):
                lvl = lo + span * r
                if abs(c["high"] - lvl) <= tol and c["close"] < lvl:
                    out[i] = -1.0
                    break
    return out


# ---------------------------------------------------------------------------
# Patrones de velas japonesas
# ---------------------------------------------------------------------------


def pattern_series(candles: list[dict]) -> tuple[list[float], list[str | None]]:
    """(votos, nombres) por vela: envolventes, martillo, estrella fugaz."""
    n = len(candles)
    votes: list[float] = [0.0] * n
    names: list[str | None] = [None] * n
    for i in range(1, n):
        c, p = candles[i], candles[i - 1]
        body = abs(c["close"] - c["open"])
        rng = c["high"] - c["low"]
        if rng <= 0:
            continue
        p_body = abs(p["close"] - p["open"])
        upper = c["high"] - max(c["close"], c["open"])
        lower = min(c["close"], c["open"]) - c["low"]

        # envolvente: el cuerpo actual engulle al anterior y cambia el color
        if body > p_body > 0:
            if c["close"] > c["open"] and p["close"] < p["open"] \
                    and c["close"] >= p["open"] and c["open"] <= p["close"]:
                votes[i], names[i] = 1.0, "envolvente alcista"
                continue
            if c["close"] < c["open"] and p["close"] > p["open"] \
                    and c["close"] <= p["open"] and c["open"] >= p["close"]:
                votes[i], names[i] = -1.0, "envolvente bajista"
                continue
        # martillo: mecha inferior larga tras caída (giro alcista)
        if lower >= 2 * body and upper <= body and body > 0.05 * rng:
            if i >= 4 and c["close"] < candles[i - 4]["close"]:
                votes[i], names[i] = 1.0, "martillo"
                continue
        # estrella fugaz: mecha superior larga tras subida (giro bajista)
        if upper >= 2 * body and lower <= body and body > 0.05 * rng:
            if i >= 4 and c["close"] > candles[i - 4]["close"]:
                votes[i], names[i] = -1.0, "estrella fugaz"
                continue
        if body <= 0.1 * rng:
            names[i] = "doji"  # indecisión: se anota pero no vota
    return votes, names


# ---------------------------------------------------------------------------
# Divergencias de RSI
# ---------------------------------------------------------------------------


def divergence_series(
    candles: list[dict], pivots: list[dict] | None = None, persist: int = 3
) -> list[float]:
    """Divergencia clásica de RSI en pivotes: precio hace mínimo más bajo con
    RSI más alto → +1 (alcista); máximo más alto con RSI más bajo → -1.
    El voto dura `persist` velas desde la confirmación del pivote."""
    n = len(candles)
    out = [0.0] * n
    if n < 40:
        return out
    r = rsi([c["close"] for c in candles])
    pvs = sorted(pivots or find_pivots(candles), key=lambda p: p["i"])
    last: dict[str, dict] = {}
    for p in pvs:
        prev = last.get(p["kind"])
        last[p["kind"]] = p
        if prev is None or r[p["i"]] is None or r[prev["i"]] is None:
            continue
        vote = 0.0
        if p["kind"] == "low" and p["price"] < prev["price"] and r[p["i"]] > r[prev["i"]]:
            vote = 1.0
        elif p["kind"] == "high" and p["price"] > prev["price"] and r[p["i"]] < r[prev["i"]]:
            vote = -1.0
        if vote:
            for k in range(p["confirm"], min(n, p["confirm"] + persist)):
                out[k] = vote
    return out


# ---------------------------------------------------------------------------
# VWAP anclado a la sesión (día UTC)
# ---------------------------------------------------------------------------


def vwap_series(candles: list[dict], anchor_seconds: int = 86400) -> list[float | None]:
    """VWAP acumulado desde el inicio de cada sesión; None sin volumen."""
    out: list[float | None] = [None] * len(candles)
    pv = vol = 0.0
    session = -1
    for i, c in enumerate(candles):
        s = c["time"] // anchor_seconds
        if s != session:
            session, pv, vol = s, 0.0, 0.0
        typical = (c["high"] + c["low"] + c["close"]) / 3
        pv += typical * c["volume"]
        vol += c["volume"]
        out[i] = pv / vol if vol > 0 else None
    return out


def vwap_votes(candles: list[dict]) -> list[float]:
    """+1 cierre sobre el VWAP de la sesión, -1 debajo, 0 sin volumen."""
    vw = vwap_series(candles)
    return [
        0.0 if v is None else 1.0 if c["close"] > v else -1.0
        for c, v in zip(candles, vw)
    ]


# ---------------------------------------------------------------------------
# Volumen relativo (confirmación institucional de rupturas)
# ---------------------------------------------------------------------------


def rvol_series(candles: list[dict], period: int = 20) -> list[float | None]:
    """Volumen relativo: volumen de la vela / media de `period` velas.

    >1 la vela mueve más volumen de lo normal (participación real detrás
    del movimiento); None si el feed no trae volumen.
    """
    out: list[float | None] = [None] * len(candles)
    acc = 0.0
    for i, c in enumerate(candles):
        v = c["volume"]
        acc += v
        if i >= period:
            acc -= candles[i - period]["volume"]
        if i >= period - 1 and acc > 0:
            out[i] = v / (acc / period)
    return out


# ---------------------------------------------------------------------------
# Ichimoku Kinko Hyo
# ---------------------------------------------------------------------------


def _mid_range(candles: list[dict], i: int, period: int) -> float | None:
    if i + 1 < period:
        return None
    window = candles[i - period + 1 : i + 1]
    return (max(c["high"] for c in window) + min(c["low"] for c in window)) / 2


def ichimoku_lines(
    candles: list[dict],
) -> tuple[list[float | None], list[float | None]]:
    """Bordes de la nube (senkou A y B) alineados a cada vela visible, con el
    desplazamiento estándar de 26 velas (misma matemática que ichimoku_votes)."""
    n = len(candles)
    span_a: list[float | None] = [None] * n
    span_b: list[float | None] = [None] * n
    for i in range(n):
        j = i - 26
        if j < 51:
            continue
        conv = _mid_range(candles, j, 9)
        base = _mid_range(candles, j, 26)
        sb = _mid_range(candles, j, 52)
        if conv is None or base is None or sb is None:
            continue
        span_a[i] = (conv + base) / 2
        span_b[i] = sb
    return span_a, span_b


def ichimoku_votes(candles: list[dict]) -> list[float]:
    """Posición respecto a la nube: +1 encima, -1 debajo, 0 dentro.

    La nube en la vela i es el span A/B calculado 26 velas atrás (el
    desplazamiento estándar), así que no hay anticipación.
    """
    n = len(candles)
    out = [0.0] * n
    for i in range(n):
        j = i - 26
        if j < 51:
            continue
        conv = _mid_range(candles, j, 9)
        base = _mid_range(candles, j, 26)
        span_b = _mid_range(candles, j, 52)
        if conv is None or base is None or span_b is None:
            continue
        span_a = (conv + base) / 2
        top, bot = max(span_a, span_b), min(span_a, span_b)
        c = candles[i]["close"]
        out[i] = 1.0 if c > top else -1.0 if c < bot else 0.0
    return out


# ---------------------------------------------------------------------------
# Sistemas de seguimiento de tendencia (SuperTrend, PSAR, Donchian, OBV)
# ---------------------------------------------------------------------------


def supertrend_votes(candles: list[dict], period: int = 10, mult: float = 3.0) -> list[float]:
    """Dirección del SuperTrend por vela: +1 alcista, -1 bajista, 0 sin datos."""
    _, direction = supertrend(candles, period, mult)
    return direction


def psar_votes(candles: list[dict]) -> list[float]:
    """Dirección del Parabolic SAR por vela: +1 alcista, -1 bajista."""
    _, direction = psar(candles)
    return direction


def donchian_breakout_series(
    candles: list[dict], period: int = 55, persist: int = 2
) -> list[float]:
    """Ruptura Turtle del canal de Donchian: ±1 en la vela que cierra fuera del
    canal de las `period` velas previas; el voto dura `persist` velas extra."""
    n = len(candles)
    out = [0.0] * n
    hi, lo, _ = donchian(candles, period)
    for i in range(n):
        if hi[i] is None:
            continue
        vote = 0.0
        if candles[i]["close"] > hi[i]:
            vote = 1.0
        elif candles[i]["close"] < lo[i]:
            vote = -1.0
        if vote:
            for k in range(i, min(n, i + persist + 1)):
                out[k] = vote
    return out


def donchian_pos_series(candles: list[dict], period: int = 20) -> list[float]:
    """Posición del cierre dentro del canal de Donchian en [-1, 1]."""
    n = len(candles)
    out = [0.0] * n
    hi, lo, mid = donchian(candles, period)
    for i in range(n):
        if hi[i] is None or hi[i] == lo[i]:
            continue
        out[i] = max(-1.0, min(1.0, (candles[i]["close"] - mid[i]) / ((hi[i] - lo[i]) / 2)))
    return out


def obv_trend_votes(candles: list[dict], period: int = 20) -> list[float]:
    """OBV vs su EMA: +1 si el volumen acumulado empuja al alza, -1 a la baja.
    0 si el feed no trae volumen."""
    if not any(c["volume"] > 0 for c in candles):
        return [0.0] * len(candles)
    obv = obv_series(candles)
    e = ema(obv, period)
    return [
        0.0 if m is None else 1.0 if v > m else -1.0 if v < m else 0.0
        for v, m in zip(obv, e)
    ]


def momentum_votes(candles: list[dict], period: int = 12) -> list[float]:
    """Momento normalizado por volatilidad: (cierre − cierre[−N]) / (ATR·√N),
    recortado a [-1, 1]. Al dividir por el ATR es adimensional y comparable
    entre símbolos y marcos temporales (una especie de t-estadístico)."""
    n = len(candles)
    out = [0.0] * n
    atrs = atr_series(candles)
    closes = [c["close"] for c in candles]
    scale = period ** 0.5
    for i in range(period, n):
        a = atrs[i]
        if a:
            v = (closes[i] - closes[i - period]) / (a * scale)
            out[i] = max(-1.0, min(1.0, v))
    return out


def ha_trend_votes(candles: list[dict], run: int = 2) -> list[float]:
    """Tendencia Heikin-Ashi: ±1 tras `run` velas HA seguidas del mismo color
    (el suavizado HA filtra el ruido vela a vela)."""
    ha = heikin_ashi(candles)
    out = [0.0] * len(ha)
    streak = 0
    for i, h in enumerate(ha):
        d = 1 if h["close"] > h["open"] else -1 if h["close"] < h["open"] else 0
        streak = streak + d if d and streak * d >= 0 else d
        out[i] = 1.0 if streak >= run else -1.0 if streak <= -run else 0.0
    return out


def keltner_break_votes(candles: list[dict]) -> list[float]:
    """Ruptura del canal de Keltner: cerrar fuera del canal (EMA20 ± 1.5×ATR)
    es una expansión de volatilidad direccional."""
    _, up, lo = keltner(candles)
    return [
        0.0 if u is None else 1.0 if c["close"] > u else -1.0 if c["close"] < l else 0.0
        for c, u, l in zip(candles, up, lo)
    ]


def cci_trend_votes(candles: list[dict]) -> list[float]:
    """CCI en modo TENDENCIA (uso original de Lambert): >+100 impulso alcista
    en marcha, <-100 bajista. (La otra escuela lo usa al revés, como
    reversión: esa versión es `cci_votes` y la usan los bots de rango)."""
    c = cci(candles)
    return [
        0.0 if v is None else 1.0 if v >= 100 else -1.0 if v <= -100 else 0.0
        for v in c
    ]


# ---------------------------------------------------------------------------
# Osciladores de reversión (estocástico, Williams %R, CCI, MFI)
# ---------------------------------------------------------------------------


def stoch_votes(candles: list[dict]) -> list[float]:
    """Estocástico en extremos: +1 sobreventa (<20), -1 sobrecompra (>80)."""
    k, _ = stochastic(candles)
    return [
        0.0 if v is None else 1.0 if v <= 20 else -1.0 if v >= 80 else 0.0
        for v in k
    ]


def williams_votes(candles: list[dict]) -> list[float]:
    """Williams %R en extremos: +1 bajo -80 (sobreventa), -1 sobre -20."""
    w = williams_r(candles)
    return [
        0.0 if v is None else 1.0 if v <= -80 else -1.0 if v >= -20 else 0.0
        for v in w
    ]


def cci_votes(candles: list[dict]) -> list[float]:
    """CCI en extremos (reversión): +1 bajo -100, -1 sobre +100."""
    c = cci(candles)
    return [
        0.0 if v is None else 1.0 if v <= -100 else -1.0 if v >= 100 else 0.0
        for v in c
    ]


def mfi_votes(candles: list[dict]) -> list[float]:
    """Money Flow Index en extremos: +1 bajo 20, -1 sobre 80 (0 sin volumen)."""
    m = mfi(candles)
    return [
        0.0 if v is None else 1.0 if v <= 20 else -1.0 if v >= 80 else 0.0
        for v in m
    ]


# ---------------------------------------------------------------------------
# Squeeze de volatilidad (compresión Bollinger dentro de Keltner)
# ---------------------------------------------------------------------------


def squeeze_release_series(candles: list[dict], persist: int = 3) -> list[float]:
    """Disparo del squeeze de Carter: en la vela en que la compresión se libera,
    vota ±1 según el momento (cierre vs media del canal 20); dura `persist` velas."""
    n = len(candles)
    out = [0.0] * n
    sq = squeeze_on_series(candles)
    _, _, mid = donchian(candles, 20)
    closes = [c["close"] for c in candles]
    s20 = sma(closes, 20)
    for i in range(1, n):
        if sq[i - 1] is True and sq[i] is False:
            # dirección de la expansión: cierre vs centro del rango reciente
            ref = mid[i] if mid[i] is not None else s20[i]
            if ref is None:
                continue
            vote = 1.0 if closes[i] > ref else -1.0
            for k in range(i, min(n, i + persist + 1)):
                out[k] = vote
    return out


def squeeze_state(candles: list[dict]) -> str | None:
    """Estado actual del squeeze para la UI: 'compresión', 'disparo' o None."""
    sq = squeeze_on_series(candles)
    if len(sq) < 2 or sq[-1] is None:
        return None
    if sq[-1]:
        return "compresión"
    if sq[-2] is True and sq[-1] is False:
        return "disparo"
    return None


# ---------------------------------------------------------------------------
# Smart Money Concepts (SMC): fair value gaps, barridos de liquidez y
# estructura de mercado (BOS/CHoCH). Metodología ICT/institucional, detectada
# de forma determinista y sin mirar al futuro.
# ---------------------------------------------------------------------------


def fvg_series(candles: list[dict], max_zones: int = 12, max_age: int = 120) -> list[float]:
    """Fair Value Gaps (ICT): hueco de 3 velas que deja un desequilibrio.

    FVG alcista: low[i] > high[i-2] (el impulso dejó un hueco sin negociar).
    Cuando el precio VUELVE después a la zona y cierra por encima, los
    institucionales suelen defenderla → +1. Simétrico para el bajista.
    La zona se invalida cuando el precio la atraviesa por completo.
    """
    n = len(candles)
    out = [0.0] * n
    if n < 5:
        return out
    atrs = atr_series(candles)
    zones: list[dict] = []  # {lo, hi, dir, born}
    for i in range(2, n):
        c = candles[i]
        # 1) reacción sobre zonas ya existentes (solo zonas nacidas antes)
        for z in zones:
            if z["born"] >= i:
                continue
            if z["dir"] > 0:
                # retroceso a la zona alcista y cierre sosteniéndose encima
                if c["low"] <= z["hi"] and c["close"] > z["lo"] and c["close"] > c["open"]:
                    out[i] = 1.0
            else:
                if c["high"] >= z["lo"] and c["close"] < z["hi"] and c["close"] < c["open"]:
                    out[i] = -1.0
        # 2) invalidación: el precio atraviesa la zona por completo
        zones = [
            z for z in zones
            if not (
                (z["dir"] > 0 and c["close"] < z["lo"])
                or (z["dir"] < 0 and c["close"] > z["hi"])
            )
            and i - z["born"] <= max_age
        ]
        # 3) detección de zonas nuevas (el hueco debe ser significativo vs ATR)
        a = atrs[i]
        min_gap = 0.15 * a if a else 0.0
        lo2, hi2 = candles[i - 2]["low"], candles[i - 2]["high"]
        if c["low"] > hi2 and (c["low"] - hi2) >= min_gap:
            zones.append({"lo": hi2, "hi": c["low"], "dir": 1, "born": i})
        elif c["high"] < lo2 and (lo2 - c["high"]) >= min_gap:
            zones.append({"lo": c["high"], "hi": lo2, "dir": -1, "born": i})
        if len(zones) > max_zones:
            zones = zones[-max_zones:]
    return out


def sweep_series(
    candles: list[dict], pivots: list[dict] | None = None, lookback_pivots: int = 10
) -> list[float]:
    """Barrido de liquidez (liquidity sweep / stop hunt): la vela perfora un
    mínimo pivote previo (donde descansan los stops) pero CIERRA de vuelta por
    encima → trampa bajista, voto +1. Simétrico en máximos → -1.

    Es una de las señales de reversión más fiables del repertorio SMC porque
    exige que el mercado rechace el nivel dentro de la misma vela.
    """
    n = len(candles)
    out = [0.0] * n
    if n < 20:
        return out
    pvs = sorted(pivots or find_pivots(candles), key=lambda p: p["confirm"])
    lows: list[float] = []
    highs: list[float] = []
    j = 0
    for i in range(n):
        while j < len(pvs) and pvs[j]["confirm"] <= i:
            (lows if pvs[j]["kind"] == "low" else highs).append(pvs[j]["price"])
            j += 1
        c = candles[i]
        body_up = c["close"] > c["open"]
        for lvl in lows[-lookback_pivots:]:
            if c["low"] < lvl and c["close"] > lvl and body_up:
                out[i] = 1.0
                break
        if out[i] == 0.0:
            for lvl in highs[-lookback_pivots:]:
                if c["high"] > lvl and c["close"] < lvl and not body_up:
                    out[i] = -1.0
                    break
    return out


def structure_series(
    candles: list[dict], pivots: list[dict] | None = None
) -> list[float]:
    """Estructura de mercado (BOS/CHoCH): +1 mientras la estructura es alcista
    (el último evento fue un cierre sobre el último máximo pivote), -1 bajista.

    Break of Structure a favor mantiene el sesgo; el Change of Character
    (cierre al otro lado del último pivote contrario) lo invierte.
    """
    n = len(candles)
    out = [0.0] * n
    if n < 20:
        return out
    pvs = sorted(pivots or find_pivots(candles), key=lambda p: p["confirm"])
    last_high: float | None = None
    last_low: float | None = None
    bias = 0.0
    j = 0
    for i in range(n):
        while j < len(pvs) and pvs[j]["confirm"] <= i:
            if pvs[j]["kind"] == "high":
                last_high = pvs[j]["price"]
            else:
                last_low = pvs[j]["price"]
            j += 1
        c = candles[i]["close"]
        if last_high is not None and c > last_high:
            bias = 1.0
        elif last_low is not None and c < last_low:
            bias = -1.0
        out[i] = bias
    return out


# ---------------------------------------------------------------------------
# Sistemas adaptativos modernos (KAMA, Vortex, expansión de Bollinger)
# ---------------------------------------------------------------------------


def kama_votes(candles: list[dict], period: int = 10) -> list[float]:
    """Precio vs KAMA con la KAMA subiendo/bajando: la media adaptativa de
    Kaufman filtra el ruido de rango mejor que una EMA fija."""
    closes = [c["close"] for c in candles]
    k = kama(closes, period)
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if k[i] is None or k[i - 1] is None:
            continue
        if closes[i] > k[i] and k[i] >= k[i - 1]:
            out[i] = 1.0
        elif closes[i] < k[i] and k[i] <= k[i - 1]:
            out[i] = -1.0
    return out


def vortex_votes(candles: list[dict], period: int = 14) -> list[float]:
    """Cruce del Vortex: +1 si VI+ domina claramente a VI-, -1 al revés.
    Se exige separación mínima (0.05) para no votar en el empate ruidoso."""
    vip, vim = vortex_series(candles, period)
    out = [0.0] * len(candles)
    for i, (p, m) in enumerate(zip(vip, vim)):
        if p is None or m is None:
            continue
        if p - m > 0.05:
            out[i] = 1.0
        elif m - p > 0.05:
            out[i] = -1.0
    return out


def er_series(candles: list[dict], period: int = 10) -> list[float | None]:
    """Efficiency Ratio de Kaufman por vela (régimen: 1 tendencia, 0 ruido)."""
    return efficiency_ratio([c["close"] for c in candles], period)


def bbw_expansion_series(
    candles: list[dict], period: int = 20, hist: int = 100, persist: int = 3
) -> list[float]:
    """Expansión de volatilidad por percentil del ancho de Bollinger (BBW).

    Cuando el BBW despierta desde el cuartil bajo de su propia historia
    (compresión) y se expande, vota ±1 en la dirección del cierre respecto a
    la media. Generaliza el squeeze BB/Keltner: detecta expansiones aunque
    las bandas nunca llegaran a caber dentro del canal de Keltner.
    """
    n = len(candles)
    out = [0.0] * n
    closes = [c["close"] for c in candles]
    means = sma(closes, period)
    width: list[float | None] = [None] * n
    for i in range(period - 1, n):
        m = means[i]
        if m is None or m == 0:
            continue
        window = closes[i - period + 1 : i + 1]
        sd = (sum((v - m) ** 2 for v in window) / period) ** 0.5
        width[i] = 4 * sd / m
    for i in range(period + 10, n):
        w, w_prev = width[i], width[i - 1]
        if w is None or w_prev is None:
            continue
        past = [v for v in width[max(0, i - hist) : i] if v is not None]
        if len(past) < 30:
            continue
        sorted_past = sorted(past)
        q25 = sorted_past[len(sorted_past) // 4]
        # despertar: veníamos comprimidos y el ancho crece con fuerza
        if w_prev <= q25 and w > w_prev * 1.15:
            m = means[i]
            if m is None:
                continue
            vote = 1.0 if closes[i] > m else -1.0
            for k in range(i, min(n, i + persist + 1)):
                out[k] = vote
    return out


# ---------------------------------------------------------------------------
# Rango de apertura (ORB): la ruptura de los primeros minutos de la sesión
# ---------------------------------------------------------------------------

ORB_RANGE_MINUTES = 15        # rango de apertura: primeros 15 minutos
ORB_WINDOW_MINUTES = 150      # las rupturas solo valen en las 2.5 h siguientes
_FALLBACK_OPEN_UTC = 13 * 60 + 30  # 9:30 ET en horario de verano


def _session_open_minute(ts: int) -> int:
    """Minuto UTC del día en que abre NYSE para la fecha de `ts` (maneja DST)."""
    if _NY is None:
        return _FALLBACK_OPEN_UTC
    local = datetime.fromtimestamp(ts, tz=_NY).replace(
        hour=9, minute=30, second=0, microsecond=0
    )
    utc = local.astimezone(timezone.utc)
    return utc.hour * 60 + utc.minute


def opening_range_series(candles: list[dict]) -> tuple[list[float], dict | None]:
    """Votos ORB por vela + el rango de la última sesión (para el gráfico).

    Para cada sesión (día UTC) se construye el rango con las velas de los
    primeros ORB_RANGE_MINUTES tras la apertura de NYSE; después, y solo
    dentro de la ventana de entrada, un cierre fuera del rango vota ±1.
    """
    n = len(candles)
    out = [0.0] * n
    last_range: dict | None = None
    day = -1
    open_min = 0
    or_hi = or_lo = 0.0
    or_done = False
    have_or = False
    for i, c in enumerate(candles):
        t = c["time"]
        d = t // 86400
        if d != day:
            day = d
            open_min = _session_open_minute(t)
            or_hi, or_lo, or_done, have_or = 0.0, 0.0, False, False
        minute = (t % 86400) // 60
        if minute < open_min:
            continue
        elapsed = minute - open_min
        if elapsed < ORB_RANGE_MINUTES:
            # la vela pertenece al rango de apertura
            or_hi = max(or_hi, c["high"]) if have_or else c["high"]
            or_lo = min(or_lo, c["low"]) if have_or else c["low"]
            have_or = True
            continue
        if have_or and not or_done:
            or_done = True
            last_range = {
                "high": round(or_hi, 6),
                "low": round(or_lo, 6),
                "date": int(d * 86400),
            }
        if not or_done or elapsed > ORB_WINDOW_MINUTES:
            continue
        if c["close"] > or_hi:
            out[i] = 1.0
        elif c["close"] < or_lo:
            out[i] = -1.0
    return out, last_range


# ---------------------------------------------------------------------------
# Paquete de trazados para el gráfico
# ---------------------------------------------------------------------------


def _line_points(candles: list[dict], values: list[float | None]) -> list[dict]:
    return [
        {"time": c["time"], "value": round(v, 6)}
        for c, v in zip(candles, values)
        if v is not None
    ]


def chart_overlays(candles: list[dict], intraday: bool = False) -> dict:
    """Todos los trazados listos para lightweight-charts.

    Devuelve: niveles S/R, líneas de tendencia, Fibonacci, medias (EMA20/50),
    Bollinger (20, 2σ) y VWAP de sesión si es intradía y hay volumen.
    """
    closes = [c["close"] for c in candles]
    pivots = find_pivots(candles)
    e20, e50 = ema(closes, 20), ema(closes, 50)
    s20 = sma(closes, 20)

    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(19, len(closes)):
        m = s20[i]
        if m is None:
            continue
        window = closes[i - 19 : i + 1]
        sd = (sum((v - m) ** 2 for v in window) / 20) ** 0.5
        upper[i], lower[i] = m + 2 * sd, m - 2 * sd

    st_line, _ = supertrend(candles)
    _, kc_up, kc_lo = keltner(candles)
    dc_hi, dc_lo, _ = donchian(candles, 20)
    ps_line, _ = psar(candles)
    ichi_a, ichi_b = ichimoku_lines(candles)

    out = {
        "sr": support_resistance(candles, pivots=pivots),
        "trendlines": trendlines(candles, pivots=pivots),
        "fibonacci": fibonacci_swing(candles),
        "ema20": _line_points(candles, e20),
        "ema50": _line_points(candles, e50),
        "bollinger": {
            "upper": _line_points(candles, upper),
            "middle": _line_points(candles, s20),
            "lower": _line_points(candles, lower),
        },
        "supertrend": _line_points(candles, st_line),
        "keltner": {
            "upper": _line_points(candles, kc_up),
            "lower": _line_points(candles, kc_lo),
        },
        "donchian": {
            "upper": _line_points(candles, dc_hi),
            "lower": _line_points(candles, dc_lo),
        },
        "psar": _line_points(candles, ps_line),
        "ichimoku": {
            "spanA": _line_points(candles, ichi_a),
            "spanB": _line_points(candles, ichi_b),
        },
    }
    if intraday:
        vw = vwap_series(candles)
        pts = _line_points(candles, vw)
        if pts:
            out["vwap"] = pts
        _, orb = opening_range_series(candles)
        if orb:
            out["openingRange"] = orb
    return out


def indicator_panel(candles: list[dict]) -> dict:
    """Series de osciladores para el subpanel del gráfico: RSI, MACD,
    estocástico, ADX, MFI, CCI, OBV, momento (ROC) y ATR, ya en formato
    {time, value} listo para lightweight-charts."""
    closes = [c["close"] for c in candles]
    m_line, m_sig = macd(closes)
    hist = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(m_line, m_sig)
    ]
    k, d = stochastic(candles)
    obv = obv_series(candles)
    has_vol = any(c["volume"] > 0 for c in candles)
    return {
        "rsi": _line_points(candles, rsi(closes)),
        "macd": {
            "line": _line_points(candles, m_line),
            "signal": _line_points(candles, m_sig),
            "hist": _line_points(candles, hist),
        },
        "stoch": {
            "k": _line_points(candles, k),
            "d": _line_points(candles, d),
        },
        "adx": _line_points(candles, adx_series(candles)),
        "mfi": _line_points(candles, mfi(candles)),
        "cci": _line_points(candles, cci(candles)),
        "obv": _line_points(candles, [float(v) for v in obv]) if has_vol else [],
        "roc": _line_points(candles, roc(closes)),
        "atr": _line_points(candles, atr_series(candles)),
    }


# ---------------------------------------------------------------------------
# Herramientas adicionales: order blocks (SMC), VWAP anclado, cinta de EMAs,
# Aroon, StochRSI y pullbacks de continuación de tendencia
# ---------------------------------------------------------------------------


def order_block_series(
    candles: list[dict], max_zones: int = 10, max_age: int = 150
) -> list[float]:
    """Order blocks (SMC): la última vela contraria antes de un desplazamiento
    impulsivo marca dónde se posicionaron los institucionales.

    Vela de desplazamiento = cuerpo >= 1.2×ATR. El order block alcista es la
    última vela bajista de las 3 previas; cuando el precio VUELVE a la zona y
    cierra sosteniéndose (vela a favor), vota ±1. La zona se invalida si el
    precio la atraviesa por completo.
    """
    n = len(candles)
    out = [0.0] * n
    if n < 20:
        return out
    atrs = atr_series(candles)
    zones: list[dict] = []  # {lo, hi, dir, born}
    for i in range(4, n):
        c = candles[i]
        # 1) reacción sobre zonas existentes
        for z in zones:
            if z["born"] >= i:
                continue
            if z["dir"] > 0:
                if c["low"] <= z["hi"] and c["close"] > z["lo"] and c["close"] > c["open"]:
                    out[i] = 1.0
            else:
                if c["high"] >= z["lo"] and c["close"] < z["hi"] and c["close"] < c["open"]:
                    out[i] = -1.0
        # 2) invalidación y caducidad
        zones = [
            z for z in zones
            if not (
                (z["dir"] > 0 and c["close"] < z["lo"])
                or (z["dir"] < 0 and c["close"] > z["hi"])
            )
            and i - z["born"] <= max_age
        ]
        # 3) detección: vela de desplazamiento con order block previo
        a = atrs[i]
        if not a:
            continue
        body = abs(c["close"] - c["open"])
        if body < 1.2 * a:
            continue
        if c["close"] > c["open"]:  # desplazamiento alcista
            for j in range(i - 1, max(i - 4, 0), -1):
                p = candles[j]
                if p["close"] < p["open"]:
                    zones.append({"lo": p["low"], "hi": p["high"], "dir": 1, "born": i})
                    break
        else:  # desplazamiento bajista
            for j in range(i - 1, max(i - 4, 0), -1):
                p = candles[j]
                if p["close"] > p["open"]:
                    zones.append({"lo": p["low"], "hi": p["high"], "dir": -1, "born": i})
                    break
        if len(zones) > max_zones:
            zones = zones[-max_zones:]
    return out


def anchored_vwap_votes(
    candles: list[dict], pivots: list[dict] | None = None, min_bars: int = 5
) -> list[float]:
    """VWAP anclado al último pivote confirmado (swing relevante).

    A diferencia del VWAP de sesión, el anclado mide quién controla el
    mercado DESDE el último giro: cierre por encima = compradores en control.
    0 sin volumen o con el ancla demasiado reciente.
    """
    n = len(candles)
    out = [0.0] * n
    if n < 20 or not any(c["volume"] > 0 for c in candles):
        return out
    pvs = sorted(pivots or find_pivots(candles), key=lambda p: p["confirm"])
    anchor = 0
    pv = vol = 0.0
    j = 0
    for i in range(n):
        new_anchor = anchor
        while j < len(pvs) and pvs[j]["confirm"] <= i:
            new_anchor = pvs[j]["i"]
            j += 1
        if new_anchor != anchor:
            anchor = new_anchor
            pv = vol = 0.0
            for k in range(anchor, i):
                ck = candles[k]
                tp = (ck["high"] + ck["low"] + ck["close"]) / 3
                pv += tp * ck["volume"]
                vol += ck["volume"]
        c = candles[i]
        tp = (c["high"] + c["low"] + c["close"]) / 3
        pv += tp * c["volume"]
        vol += c["volume"]
        if vol > 0 and i - anchor >= min_bars:
            av = pv / vol
            out[i] = 1.0 if c["close"] > av else -1.0
    return out


def ribbon_votes(candles: list[dict]) -> list[float]:
    """Cinta de EMAs (8/13/21/34/55): ±1 solo con la cinta perfectamente
    ordenada — el sello de una tendencia sana y sostenida."""
    closes = [c["close"] for c in candles]
    emas = [ema(closes, p) for p in (8, 13, 21, 34, 55)]
    out = [0.0] * len(closes)
    for i in range(len(closes)):
        vals = [e[i] for e in emas]
        if any(v is None for v in vals):
            continue
        if all(vals[k] > vals[k + 1] for k in range(4)):
            out[i] = 1.0
        elif all(vals[k] < vals[k + 1] for k in range(4)):
            out[i] = -1.0
    return out


def aroon_votes(candles: list[dict], period: int = 25) -> list[float]:
    """Aroon (Chande): mide cuánto hace del último máximo/mínimo del tramo.
    Aroon-up>=70 con Aroon-down<=30 = tendencia alcista fresca (+1); espejo -1."""
    n = len(candles)
    out = [0.0] * n
    for i in range(period, n):
        window = candles[i - period : i + 1]
        hi_off = max(range(len(window)), key=lambda k: window[k]["high"])
        lo_off = min(range(len(window)), key=lambda k: window[k]["low"])
        up = 100 * hi_off / period
        down = 100 * lo_off / period
        if up >= 70 and down <= 30:
            out[i] = 1.0
        elif down >= 70 and up <= 30:
            out[i] = -1.0
    return out


def stochrsi_votes(candles: list[dict], period: int = 14) -> list[float]:
    """RSI estocástico: posición del RSI dentro de su propio rango reciente.
    Más sensible que el RSI para los extremos de rango: <=0.1 sobreventa (+1),
    >=0.9 sobrecompra (-1)."""
    closes = [c["close"] for c in candles]
    r = rsi(closes, period)
    n = len(closes)
    out = [0.0] * n
    for i in range(2 * period, n):
        window = [v for v in r[i - period + 1 : i + 1] if v is not None]
        if len(window) < period or r[i] is None:
            continue
        hi, lo = max(window), min(window)
        if hi == lo:
            continue
        srsi = (r[i] - lo) / (hi - lo)
        if srsi <= 0.1:
            out[i] = 1.0
        elif srsi >= 0.9:
            out[i] = -1.0
    return out


def pullback_votes(candles: list[dict]) -> list[float]:
    """Pullback de continuación: en tendencia sana (EMA20>EMA50 con pendiente),
    un retroceso que toca la EMA20 y cierra a favor es la entrada clásica de
    continuación — añade entradas DENTRO de la tendencia, no solo en rupturas."""
    closes = [c["close"] for c in candles]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    atrs = atr_series(candles)
    n = len(candles)
    out = [0.0] * n
    for i in range(3, n):
        a, m20, m50 = atrs[i], e20[i], e50[i]
        if not a or m20 is None or m50 is None or e50[i - 3] is None:
            continue
        c = candles[i]
        if m20 > m50 and m50 > e50[i - 3]:  # tendencia alcista con pendiente
            touched = c["low"] <= m20 + 0.25 * a
            if touched and c["close"] > m20 and c["close"] > c["open"]:
                out[i] = 1.0
        elif m20 < m50 and m50 < e50[i - 3]:
            touched = c["high"] >= m20 - 0.25 * a
            if touched and c["close"] < m20 and c["close"] < c["open"]:
                out[i] = -1.0
    return out
