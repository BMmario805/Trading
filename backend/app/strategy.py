"""Puntuación de estrategia por vela, compartida entre bots en vivo y backtest.

Diseño "adaptive regime" (práctica habitual en sistemas de oro/XAUUSD):
la señal se calcula como mezcla de dos sub-estrategias ponderadas por el ADX:

  - Régimen de TENDENCIA (ADX alto): seguimiento — cruces de medias, MACD,
    ruptura de rango y alineación con el marco temporal superior.
  - Régimen de RANGO (ADX bajo): reversión — RSI en extremos y %B de
    Bollinger tocando bandas.

Las funciones devuelven la serie completa de puntuaciones en [-1, 1], una
por vela: el bot en vivo usa el último valor y el backtest recorre toda la
serie. Así lo que se entrena es EXACTAMENTE lo que se opera.
"""

from .indicators import adx_series, breakout_vote, ema, macd, pct_b, rsi, sma

# ADX: por debajo de LOW es rango puro, por encima de HIGH tendencia pura
ADX_LOW, ADX_HIGH = 20.0, 30.0


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def resample(candles: list[dict], step: int) -> list[dict]:
    """Agrega velas a un intervalo mayor (p. ej. 1m -> 5m). `step` en segundos."""
    out: list[dict] = []
    for c in candles:
        bucket = c["time"] - c["time"] % step
        if out and out[-1]["time"] == bucket:
            agg = out[-1]
            agg["high"] = max(agg["high"], c["high"])
            agg["low"] = min(agg["low"], c["low"])
            agg["close"] = c["close"]
            agg["volume"] += c["volume"]
        else:
            out.append({
                "time": bucket, "open": c["open"], "high": c["high"],
                "low": c["low"], "close": c["close"], "volume": c["volume"],
            })
    return out


def _htf_trend_votes(
    c_work: list[dict], c_trend: list[dict], fast: int, slow: int, step: int
) -> list[float]:
    """Voto ±1 del marco superior para cada vela de trabajo.

    Solo usa velas del marco superior ya CERRADAS (sin mirar al futuro),
    tanto en vivo como en backtest.
    """
    closes_t = [c["close"] for c in c_trend]
    ef, es = ema(closes_t, fast), ema(closes_t, slow)
    votes_t = [
        0.0 if a is None or b is None else 1.0 if a > b else -1.0
        for a, b in zip(ef, es)
    ]
    out: list[float] = []
    j = -1
    for c in c_work:
        while j + 1 < len(c_trend) and c_trend[j + 1]["time"] + step <= c["time"]:
            j += 1
        out.append(votes_t[j] if j >= 0 else 0.0)
    return out


def _rolling_breakout(c: list[dict], lookback: int) -> list[float]:
    """Voto de ruptura por vela: +1 rompe máximos previos, -1 mínimos."""
    out = [0.0] * len(c)
    for i in range(lookback + 1, len(c)):
        window = c[i - lookback : i]
        if c[i]["close"] > max(w["high"] for w in window):
            out[i] = 1.0
        elif c[i]["close"] < min(w["low"] for w in window):
            out[i] = -1.0
    return out


def _regime_blend(
    trend_scores: list[float], range_scores: list[float], adx: list[float | None]
) -> list[float]:
    out: list[float] = []
    for ts, rs, a in zip(trend_scores, range_scores, adx):
        w = 0.5 if a is None else _clip((a - ADX_LOW) / (ADX_HIGH - ADX_LOW), 0.0, 1.0)
        out.append(_clip(w * ts + (1 - w) * rs))
    return out


def _range_scores(closes: list[float], r: list[float | None], pb: list[float | None]) -> list[float]:
    """Reversión: RSI en extremos + %B de Bollinger en las bandas."""
    out: list[float] = []
    for ri, pbi in zip(r, pb):
        votes: list[float] = []
        if ri is not None:
            votes.append(1.0 if ri <= 30 else -1.0 if ri >= 70 else 0.0)
        if pbi is not None:
            votes.append(1.0 if pbi <= 0.05 else -1.0 if pbi >= 0.95 else 0.0)
        out.append(sum(votes) / len(votes) if votes else 0.0)
    return out


def scalper_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    """Serie de señal del scalper: velas 1m + tendencia 5m."""
    n = len(c_work)
    closes = [c["close"] for c in c_work]
    if n < 60:
        return [0.0] * n
    e9, e21 = ema(closes, 9), ema(closes, 21)
    r = rsi(closes)
    m_line, m_sig = macd(closes)
    bo = _rolling_breakout(c_work, 20)
    htf = _htf_trend_votes(c_work, c_trend, 20, 50, 300)
    adx = adx_series(c_work)
    pb = pct_b(closes)

    trend_scores: list[float] = []
    for i in range(n):
        votes: list[float] = []
        if e9[i] is not None and e21[i] is not None:
            votes.append(1.0 if e9[i] > e21[i] else -1.0)
        if m_line[i] is not None and m_sig[i] is not None:
            votes.append(1.0 if m_line[i] > m_sig[i] else -1.0)
        if r[i] is not None:
            votes.append(_clip((r[i] - 50) / 50))
        if bo[i]:
            votes.append(bo[i])
        votes.append(htf[i])
        trend_scores.append(sum(votes) / len(votes) if votes else 0.0)

    return _regime_blend(trend_scores, _range_scores(closes, r, pb), adx)


def swing_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    """Serie de señal del swing: velas 1h + tendencia diaria."""
    n = len(c_work)
    closes = [c["close"] for c in c_work]
    if n < 60:
        return [0.0] * n
    s20, s50 = sma(closes, 20), sma(closes, 50)
    r = rsi(closes)
    m_line, m_sig = macd(closes)
    bo = _rolling_breakout(c_work, 20)
    htf = _htf_trend_votes(c_work, c_trend, 20, 50, 86400)
    adx = adx_series(c_work)
    pb = pct_b(closes)

    trend_scores: list[float] = []
    for i in range(n):
        votes: list[float] = []
        if s20[i] is not None and s50[i] is not None:
            votes.append(1.0 if s20[i] > s50[i] else -1.0)
            votes.append(1.0 if closes[i] > s50[i] else -1.0)
        if m_line[i] is not None and m_sig[i] is not None:
            votes.append(1.0 if m_line[i] > m_sig[i] else -1.0)
        if r[i] is not None:
            votes.append(_clip((r[i] - 50) / 50))
        if bo[i]:
            votes.append(bo[i])
        votes.append(htf[i])
        trend_scores.append(sum(votes) / len(votes) if votes else 0.0)

    return _regime_blend(trend_scores, _range_scores(closes, r, pb), adx)


def score_series(bot_id: str, c_work: list[dict], c_trend: list[dict]) -> list[float]:
    if bot_id == "scalper":
        return scalper_scores(c_work, c_trend)
    return swing_scores(c_work, c_trend)


def last_bar_reasons(bot_id: str, c_work: list[dict], c_trend: list[dict]) -> list[str]:
    """Descripción legible de los componentes de la señal de la última vela."""
    closes = [c["close"] for c in c_work]
    if len(closes) < 60:
        return ["datos insuficientes"]
    reasons: list[str] = []
    a = adx_series(c_work)[-1]
    if a is not None:
        regime = "tendencia" if a >= ADX_HIGH else "rango" if a <= ADX_LOW else "mixto"
        reasons.append(f"ADX {a:.0f} → régimen de {regime}")
    if bot_id == "scalper":
        e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
        if e9 is not None and e21 is not None:
            reasons.append(f"EMA9 {'>' if e9 > e21 else '<'} EMA21 (1m)")
    else:
        s20, s50 = sma(closes, 20)[-1], sma(closes, 50)[-1]
        if s20 is not None and s50 is not None:
            reasons.append(f"SMA20 {'>' if s20 > s50 else '<'} SMA50 (1h)")
    r = rsi(closes)[-1]
    if r is not None:
        reasons.append(f"RSI {r:.0f}")
    m_line, m_sig = macd(closes)
    if m_line[-1] is not None and m_sig[-1] is not None:
        reasons.append(f"MACD {'alcista' if m_line[-1] > m_sig[-1] else 'bajista'}")
    bo = breakout_vote(c_work, 20)
    if bo:
        reasons.append(f"Ruptura de {'máximos' if bo > 0 else 'mínimos'} (20 velas)")
    pb = pct_b(closes)[-1]
    if pb is not None and (pb <= 0.05 or pb >= 0.95):
        reasons.append(f"Bollinger %B {pb:.2f} (extremo)")
    step = 300 if bot_id == "scalper" else 86400
    htf = _htf_trend_votes(c_work[-1:], c_trend, 20, 50, step)[0]
    if htf:
        label = "5m" if bot_id == "scalper" else "diaria"
        reasons.append(f"Tendencia {label} {'alcista' if htf > 0 else 'bajista'}")
    return reasons
