"""Indicadores técnicos en Python puro y señal de tendencia agregada."""


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
