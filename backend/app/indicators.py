"""Indicadores técnicos en Python puro y señal de tendencia agregada.

Incluye indicadores adaptativos modernos: KAMA/Efficiency Ratio (Kaufman),
Choppiness Index, Vortex, Hull MA y z-score estadístico.
"""


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100 - 100 / (1 + (avg_g / avg_l)) if avg_l else 100.0
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        out[i] = 100 - 100 / (1 + (avg_g / avg_l)) if avg_l else 100.0
    return out


def macd(values: list[float]) -> tuple[list[float | None], list[float | None]]:
    """Devuelve (línea MACD, línea de señal)."""
    e12, e26 = ema(values, 12), ema(values, 26)
    line: list[float | None] = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(e12, e26)
    ]
    valid = [v for v in line if v is not None]
    sig_valid = ema(valid, 9)
    signal: list[float | None] = [None] * len(line)
    j = 0
    for i, v in enumerate(line):
        if v is not None:
            signal[i] = sig_valid[j]
            j += 1
    return line, signal


def atr(candles: list[dict], period: int = 14) -> float | None:
    """Average True Range (Wilder) de la última vela; None si faltan datos."""
    if len(candles) <= period:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def breakout_vote(candles: list[dict], lookback: int = 20) -> float:
    """+1 si el cierre rompe el máximo de las `lookback` velas previas, -1 el mínimo."""
    if len(candles) < lookback + 2:
        return 0.0
    window = candles[-(lookback + 1):-1]
    last = candles[-1]["close"]
    if last > max(c["high"] for c in window):
        return 1.0
    if last < min(c["low"] for c in window):
        return -1.0
    return 0.0


def atr_series(candles: list[dict], period: int = 14) -> list[float | None]:
    """ATR de Wilder por vela (misma fórmula que `atr`, pero la serie entera)."""
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= period:
        return out
    trs: list[float] = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i + 1] = a
    return out


def adx_series(candles: list[dict], period: int = 14) -> list[float | None]:
    """ADX de Wilder: fuerza de la tendencia (>25 tendencia, <20 rango)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n <= 2 * period:
        return out
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, n):
        up = candles[i]["high"] - candles[i - 1]["high"]
        down = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    tr_s = sum(trs[:period])
    pdm_s = sum(plus_dm[:period])
    mdm_s = sum(minus_dm[:period])
    dxs: list[float] = []
    for i in range(period, len(trs)):
        tr_s = tr_s - tr_s / period + trs[i]
        pdm_s = pdm_s - pdm_s / period + plus_dm[i]
        mdm_s = mdm_s - mdm_s / period + minus_dm[i]
        pdi = 100 * pdm_s / tr_s if tr_s else 0.0
        mdi = 100 * mdm_s / tr_s if tr_s else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        dxs.append(dx)
        if len(dxs) == period:
            out[i + 1] = sum(dxs) / period
        elif len(dxs) > period:
            prev = out[i]
            out[i + 1] = (prev * (period - 1) + dx) / period  # type: ignore[operator]
    return out


def pct_b(closes: list[float], period: int = 20, k: float = 2.0) -> list[float | None]:
    """%B de Bollinger: 0 = banda inferior, 1 = superior (reversión en extremos)."""
    out: list[float | None] = [None] * len(closes)
    means = sma(closes, period)
    for i in range(period - 1, len(closes)):
        m = means[i]
        if m is None:
            continue
        window = closes[i - period + 1 : i + 1]
        var = sum((v - m) ** 2 for v in window) / period
        sd = var ** 0.5
        if sd == 0:
            out[i] = 0.5
        else:
            out[i] = (closes[i] - (m - k * sd)) / (2 * k * sd)
    return out


def stochastic(
    candles: list[dict], k_period: int = 14, d_period: int = 3
) -> tuple[list[float | None], list[float | None]]:
    """Oscilador estocástico (Lane): %K crudo suavizado a 3 y su señal %D."""
    n = len(candles)
    raw: list[float | None] = [None] * n
    for i in range(k_period - 1, n):
        window = candles[i - k_period + 1 : i + 1]
        hi = max(c["high"] for c in window)
        lo = min(c["low"] for c in window)
        raw[i] = 50.0 if hi == lo else (candles[i]["close"] - lo) / (hi - lo) * 100
    def _smooth(vals: list[float | None], p: int) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(n):
            w = [v for v in vals[max(0, i - p + 1) : i + 1] if v is not None]
            if len(w) == p:
                out[i] = sum(w) / p
        return out
    k = _smooth(raw, 3)
    return k, _smooth(k, d_period)


def williams_r(candles: list[dict], period: int = 14) -> list[float | None]:
    """Williams %R: 0 = máximo del rango, -100 = mínimo (sobreventa < -80)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = candles[i - period + 1 : i + 1]
        hi = max(c["high"] for c in window)
        lo = min(c["low"] for c in window)
        out[i] = -50.0 if hi == lo else (hi - candles[i]["close"]) / (hi - lo) * -100
    return out


def cci(candles: list[dict], period: int = 20) -> list[float | None]:
    """Commodity Channel Index (Lambert): desviación del precio típico."""
    n = len(candles)
    tp = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = tp[i - period + 1 : i + 1]
        m = sum(window) / period
        md = sum(abs(v - m) for v in window) / period
        out[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    return out


def mfi(candles: list[dict], period: int = 14) -> list[float | None]:
    """Money Flow Index: RSI ponderado por volumen (flujo de dinero real)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    tp = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    has_vol = any(c["volume"] > 0 for c in candles)
    if not has_vol:
        return out
    pos = [0.0] * n
    neg = [0.0] * n
    for i in range(1, n):
        flow = tp[i] * candles[i]["volume"]
        if tp[i] > tp[i - 1]:
            pos[i] = flow
        elif tp[i] < tp[i - 1]:
            neg[i] = flow
    for i in range(period, n):
        p = sum(pos[i - period + 1 : i + 1])
        m = sum(neg[i - period + 1 : i + 1])
        out[i] = 100.0 if m == 0 else 100 - 100 / (1 + p / m)
    return out


def obv_series(candles: list[dict]) -> list[float]:
    """On-Balance Volume (Granville): volumen acumulado con el signo del cierre."""
    out: list[float] = [0.0] * len(candles)
    for i in range(1, len(candles)):
        v = candles[i]["volume"]
        if candles[i]["close"] > candles[i - 1]["close"]:
            out[i] = out[i - 1] + v
        elif candles[i]["close"] < candles[i - 1]["close"]:
            out[i] = out[i - 1] - v
        else:
            out[i] = out[i - 1]
    return out


def roc(values: list[float], period: int = 12) -> list[float | None]:
    """Rate of Change: variación porcentual respecto a `period` velas atrás."""
    out: list[float | None] = [None] * len(values)
    for i in range(period, len(values)):
        if values[i - period]:
            out[i] = (values[i] - values[i - period]) / values[i - period] * 100
    return out


def supertrend(
    candles: list[dict], period: int = 10, mult: float = 3.0
) -> tuple[list[float | None], list[float]]:
    """SuperTrend (Olivier Seban): (línea de stop, dirección +1/-1 por vela)."""
    n = len(candles)
    line: list[float | None] = [None] * n
    direction: list[float] = [0.0] * n
    atrs = atr_series(candles, period)
    up = dn = 0.0
    trend = 1
    started = False
    for i in range(n):
        a = atrs[i]
        if a is None:
            continue
        mid = (candles[i]["high"] + candles[i]["low"]) / 2
        basic_up = mid + mult * a
        basic_dn = mid - mult * a
        if not started:
            up, dn, started = basic_up, basic_dn, True
        else:
            prev_close = candles[i - 1]["close"]
            up = min(basic_up, up) if prev_close <= up else basic_up
            dn = max(basic_dn, dn) if prev_close >= dn else basic_dn
        c = candles[i]["close"]
        if trend > 0 and c < dn:
            trend = -1
        elif trend < 0 and c > up:
            trend = 1
        direction[i] = float(trend)
        line[i] = dn if trend > 0 else up
    return line, direction


def keltner(
    candles: list[dict], period: int = 20, mult: float = 1.5
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Canales de Keltner: EMA20 ± mult×ATR. Devuelve (medio, superior, inferior)."""
    closes = [c["close"] for c in candles]
    mid = ema(closes, period)
    atrs = atr_series(candles, period)
    upper: list[float | None] = [None] * len(candles)
    lower: list[float | None] = [None] * len(candles)
    for i, (m, a) in enumerate(zip(mid, atrs)):
        if m is not None and a is not None:
            upper[i] = m + mult * a
            lower[i] = m - mult * a
    return mid, upper, lower


def donchian(
    candles: list[dict], period: int = 20
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Canal de Donchian de las `period` velas ANTERIORES (sin la actual):
    (máximo, mínimo, medio). Al excluir la vela en curso, un cierre por encima
    del máximo es una ruptura sin sesgo de anticipación (regla Turtle)."""
    n = len(candles)
    hi: list[float | None] = [None] * n
    lo: list[float | None] = [None] * n
    mid: list[float | None] = [None] * n
    for i in range(period, n):
        window = candles[i - period : i]
        h = max(c["high"] for c in window)
        l = min(c["low"] for c in window)
        hi[i], lo[i], mid[i] = h, l, (h + l) / 2
    return hi, lo, mid


def psar(
    candles: list[dict], af_step: float = 0.02, af_max: float = 0.2
) -> tuple[list[float | None], list[float]]:
    """Parabolic SAR (Wilder): (valor SAR, dirección +1/-1 por vela)."""
    n = len(candles)
    sar: list[float | None] = [None] * n
    direction: list[float] = [0.0] * n
    if n < 3:
        return sar, direction
    trend = 1 if candles[1]["close"] >= candles[0]["close"] else -1
    cur = candles[0]["low"] if trend > 0 else candles[0]["high"]
    ep = candles[1]["high"] if trend > 0 else candles[1]["low"]
    af = af_step
    for i in range(2, n):
        cur = cur + af * (ep - cur)
        if trend > 0:
            cur = min(cur, candles[i - 1]["low"], candles[i - 2]["low"])
            if candles[i]["low"] < cur:  # giro a bajista
                trend, cur, ep, af = -1, ep, candles[i]["low"], af_step
            elif candles[i]["high"] > ep:
                ep = candles[i]["high"]
                af = min(af + af_step, af_max)
        else:
            cur = max(cur, candles[i - 1]["high"], candles[i - 2]["high"])
            if candles[i]["high"] > cur:  # giro a alcista
                trend, cur, ep, af = 1, ep, candles[i]["high"], af_step
            elif candles[i]["low"] < ep:
                ep = candles[i]["low"]
                af = min(af + af_step, af_max)
        sar[i] = cur
        direction[i] = float(trend)
    return sar, direction


def heikin_ashi(candles: list[dict]) -> list[dict]:
    """Velas Heikin-Ashi: promedian el ruido y alargan las rachas de color,
    lo que las hace útiles como detector suavizado de tendencia."""
    out: list[dict] = []
    for i, c in enumerate(candles):
        ha_close = (c["open"] + c["high"] + c["low"] + c["close"]) / 4
        ha_open = (
            (out[i - 1]["open"] + out[i - 1]["close"]) / 2
            if out else (c["open"] + c["close"]) / 2
        )
        out.append({
            "time": c["time"],
            "open": ha_open,
            "high": max(c["high"], ha_open, ha_close),
            "low": min(c["low"], ha_open, ha_close),
            "close": ha_close,
            "volume": c["volume"],
        })
    return out


def squeeze_on_series(
    candles: list[dict], period: int = 20, bb_k: float = 2.0, kc_mult: float = 1.5
) -> list[bool | None]:
    """Squeeze (John Carter): True cuando las bandas de Bollinger caben DENTRO
    del canal de Keltner (compresión de volatilidad que precede a la expansión)."""
    closes = [c["close"] for c in candles]
    means = sma(closes, period)
    _, kc_up, kc_lo = keltner(candles, period, kc_mult)
    out: list[bool | None] = [None] * len(candles)
    for i in range(period - 1, len(closes)):
        m = means[i]
        if m is None or kc_up[i] is None or kc_lo[i] is None:
            continue
        window = closes[i - period + 1 : i + 1]
        sd = (sum((v - m) ** 2 for v in window) / period) ** 0.5
        out[i] = (m + bb_k * sd) < kc_up[i] and (m - bb_k * sd) > kc_lo[i]
    return out


def efficiency_ratio(values: list[float], period: int = 10) -> list[float | None]:
    """Efficiency Ratio de Kaufman: |cambio neto| / suma de |cambios| en [0,1].

    ~1 = movimiento direccional limpio (tendencia eficiente); ~0 = ruido puro.
    Es el clasificador de régimen que usa KAMA y una referencia moderna para
    decidir entre lógica de tendencia y de reversión.
    """
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        change = abs(values[i] - values[i - period])
        vol = sum(abs(values[j] - values[j - 1]) for j in range(i - period + 1, i + 1))
        out[i] = (change / vol) if vol > 0 else 0.0
    return out


def kama(
    values: list[float], period: int = 10, fast: int = 2, slow: int = 30
) -> list[float | None]:
    """Kaufman's Adaptive Moving Average: se acelera en tendencia (ER alto)
    y se aplana en rango (ER bajo), reduciendo señales falsas de las medias
    clásicas en mercados laterales."""
    n = len(values)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    er = efficiency_ratio(values, period)
    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        e = er[i] or 0.0
        sc = (e * (k_fast - k_slow) + k_slow) ** 2
        prev = prev + sc * (values[i] - prev)
        out[i] = prev
    return out


def choppiness_series(candles: list[dict], period: int = 14) -> list[float | None]:
    """Choppiness Index (Dreiss) en [0,100]: >61.8 mercado lateral/ruidoso,
    <38.2 movimiento direccional. Complementa al ADX como detector de régimen
    (el ADX reacciona tarde; el CHOP mide la fractalidad del tramo)."""
    from math import log10
    n = len(candles)
    out: list[float | None] = [None] * n
    if n <= period + 1:
        return out
    trs: list[float] = [0.0]
    for i in range(1, n):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    log_p = log10(period)
    for i in range(period, n):
        window = candles[i - period + 1 : i + 1]
        hi = max(c["high"] for c in window)
        lo = min(c["low"] for c in window)
        tr_sum = sum(trs[i - period + 1 : i + 1])
        if hi > lo and tr_sum > 0:
            out[i] = 100 * log10(tr_sum / (hi - lo)) / log_p
    return out


def vortex_series(
    candles: list[dict], period: int = 14
) -> tuple[list[float | None], list[float | None]]:
    """Indicador Vortex (Botes/Siepman): (VI+, VI-). El cruce VI+ sobre VI-
    señala rotación alcista del flujo direccional; se usa como confirmador
    de tendencia temprano."""
    n = len(candles)
    vip: list[float | None] = [None] * n
    vim: list[float | None] = [None] * n
    if n <= period:
        return vip, vim
    vm_p = [0.0] * n
    vm_m = [0.0] * n
    trs = [0.0] * n
    for i in range(1, n):
        vm_p[i] = abs(candles[i]["high"] - candles[i - 1]["low"])
        vm_m[i] = abs(candles[i]["low"] - candles[i - 1]["high"])
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    for i in range(period, n):
        tr_sum = sum(trs[i - period + 1 : i + 1])
        if tr_sum > 0:
            vip[i] = sum(vm_p[i - period + 1 : i + 1]) / tr_sum
            vim[i] = sum(vm_m[i - period + 1 : i + 1]) / tr_sum
    return vip, vim


def _wma(values: list[float], period: int) -> list[float | None]:
    """Media móvil ponderada linealmente (peso máximo a la vela reciente)."""
    n = len(values)
    out: list[float | None] = [None] * n
    denom = period * (period + 1) / 2
    for i in range(period - 1, n):
        s = 0.0
        for j in range(period):
            s += values[i - j] * (period - j)
        out[i] = s / denom
    return out


def hma(values: list[float], period: int = 21) -> list[float | None]:
    """Hull Moving Average: media rápida y suave a la vez (WMA anidadas);
    su pendiente es un detector de giro de tendencia con poco retardo."""
    import math
    half = _wma(values, max(2, period // 2))
    full = _wma(values, period)
    raw = [
        (2 * h - f) if h is not None and f is not None else None
        for h, f in zip(half, full)
    ]
    valid = [v for v in raw if v is not None]
    if not valid:
        return [None] * len(values)
    smoothed = _wma(valid, max(2, int(math.sqrt(period))))
    out: list[float | None] = [None] * len(values)
    j = 0
    for i, v in enumerate(raw):
        if v is not None:
            out[i] = smoothed[j]
            j += 1
    return out


def zscore_series(values: list[float], period: int = 20) -> list[float | None]:
    """Z-score del cierre respecto a su media móvil: desviaciones típicas de
    distancia. |z| > 2 = extremo estadístico (candidato a reversión)."""
    n = len(values)
    out: list[float | None] = [None] * n
    means = sma(values, period)
    for i in range(period - 1, n):
        m = means[i]
        if m is None:
            continue
        window = values[i - period + 1 : i + 1]
        sd = (sum((v - m) ** 2 for v in window) / period) ** 0.5
        out[i] = (values[i] - m) / sd if sd > 0 else 0.0
    return out


def technical_analysis(candles: list[dict]) -> dict:
    """Puntuación técnica en [-1, 1] (+1 alcista) con desglose de indicadores."""
    closes = [c["close"] for c in candles]
    if len(closes) < 60:
        return {"score": 0.0, "signals": [], "detail": "datos insuficientes"}

    last = closes[-1]
    s20, s50 = sma(closes, 20)[-1], sma(closes, 50)[-1]
    r = rsi(closes)[-1]
    m_line, m_sig = macd(closes)
    m, ms = m_line[-1], m_sig[-1]

    signals: list[dict] = []
    votes: list[float] = []

    if s20 is not None and s50 is not None:
        v = 1.0 if s20 > s50 else -1.0
        votes.append(v)
        signals.append({
            "name": "Cruce de medias (SMA20/50)",
            "value": f"SMA20 {s20:.4g} vs SMA50 {s50:.4g}",
            "vote": v,
        })
        v2 = 1.0 if last > s50 else -1.0
        votes.append(v2)
        signals.append({
            "name": "Precio vs SMA50",
            "value": f"{last:.4g} vs {s50:.4g}",
            "vote": v2,
        })

    if r is not None:
        # RSI: sobreventa sugiere rebote alcista, sobrecompra lo contrario
        v = 1.0 if r < 30 else -1.0 if r > 70 else (r - 50) / 50
        votes.append(v)
        signals.append({"name": "RSI (14)", "value": f"{r:.1f}", "vote": round(v, 2)})

    if m is not None and ms is not None:
        v = 1.0 if m > ms else -1.0
        votes.append(v)
        signals.append({
            "name": "MACD vs señal",
            "value": f"{m:.4g} vs {ms:.4g}",
            "vote": v,
        })

    score = sum(votes) / len(votes) if votes else 0.0
    return {
        "score": round(score, 3),
        "rsi": round(r, 1) if r is not None else None,
        "signals": signals,
    }
