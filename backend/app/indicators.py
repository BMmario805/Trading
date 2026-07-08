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
