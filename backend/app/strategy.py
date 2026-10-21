"""Señales causales compartidas por el vivo y el backtest.

Scalper, swing, trend, meanrev y squeeze usan tres bloques con pesos 40/40/20: régimen,
disparador de precio y flujo CMF. Sin disparador no hay entrada.
Los demás bots mantienen sus escuelas específicas. Las funciones devuelven
una puntuación en [-1, 1] por vela y solo usan información ya disponible.
"""

import math

from .flow import flow_scores, chaikin_money_flow

from .indicators import (
    adx_series,
    atr,
    atr_series,
    choppiness_series,
    ema,
    macd,
    pct_b,
    rsi,
    sma,
    zscore_series,
)
from .systems import (
    anchored_vwap_votes,
    aroon_votes,
    bbw_expansion_series,
    cci_trend_votes,
    cci_votes,
    divergence_series,
    donchian_breakout_series,
    donchian_pos_series,
    fib_bounce_series,
    find_pivots,
    fvg_series,
    ha_trend_votes,
    ichimoku_votes,
    kama_votes,
    keltner_break_votes,
    mfi_votes,
    momentum_votes,
    obv_trend_votes,
    opening_range_series,
    order_block_series,
    pattern_series,
    psar_votes,
    pullback_votes,
    ribbon_votes,
    rvol_series,
    squeeze_release_series,
    squeeze_state,
    sr_bounce_series,
    stoch_votes,
    stochrsi_votes,
    structure_series,
    supertrend_votes,
    sweep_series,
    vortex_votes,
    vwap_votes,
    williams_votes,
)

# ADX: por debajo de LOW es rango puro, por encima de HIGH tendencia pura
ADX_LOW, ADX_HIGH = 20.0, 30.0

# segundos del marco de tendencia de cada bot (para el voto del marco superior)
TREND_STEP = {
    "scalper": 300,      # 5m
    "swing": 86400,      # 1d
    "trend": 14400,      # 4h
    "meanrev": 86400,    # 1d
    "orb": 900,          # 15m
    "flow": 3600,
    "squeeze": 14400,    # 4h
}


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def volatility_risk_series(candles: list[dict], short: int = 24, long: int = 120) -> list[float]:
    """Reduce riesgo cuando sube la volatilidad reciente; nunca añade palanca.

    Ventanas fijas de retornos logarítmicos ya observados. Es una regla de
    investigación, no una estimación de probabilidad de éxito. Las unidades
    son velas: sus resultados intradía requieren validación independiente.
    """
    if short < 2 or long <= short:
        raise ValueError('Ventanas de volatilidad inválidas')
    returns = [0.0]
    for previous, current in zip(candles, candles[1:]):
        returns.append(math.log(current['close'] / previous['close']))
    total, squared = [0.0], [0.0]
    for value in returns:
        total.append(total[-1] + value)
        squared.append(squared[-1] + value * value)
    def variance(lo: int, hi: int) -> float:
        n = hi - lo
        mean = (total[hi] - total[lo]) / n
        return max(0.0, (squared[hi] - squared[lo]) / n - mean * mean)
    out = []
    for i in range(len(candles)):
        if i < long:
            out.append(1.0)
            continue
        recent = variance(i - short + 1, i + 1)
        baseline = variance(i - long + 1, i + 1)
        out.append(_clip(math.sqrt(baseline / recent), .25, 1.0) if recent > 1e-16 else 1.0)
    return out


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


# Choppiness: >61.8 rango fractal puro, <38.2 tramo direccional
CHOP_LOW, CHOP_HIGH = 38.2, 61.8


def _regime_blend(
    trend_scores: list[float],
    range_scores: list[float],
    adx: list[float | None],
    chop: list[float | None] | None = None,
) -> list[float]:
    """Mezcla tendencia/rango ponderada por régimen.

    El peso de tendencia combina ADX (fuerza direccional, reacciona tarde)
    y Choppiness Index (fractalidad del tramo, reacciona antes): la media de
    ambos es más robusta que cualquiera por separado.
    """
    out: list[float] = []
    for i, (ts, rs, a) in enumerate(zip(trend_scores, range_scores, adx)):
        w_adx = 0.5 if a is None else _clip((a - ADX_LOW) / (ADX_HIGH - ADX_LOW), 0.0, 1.0)
        c = chop[i] if chop else None
        if c is not None:
            w_chop = _clip((CHOP_HIGH - c) / (CHOP_HIGH - CHOP_LOW), 0.0, 1.0)
            w = (w_adx + w_chop) / 2
        else:
            w = w_adx
        out.append(_clip(w * ts + (1 - w) * rs))
    return out


def _range_scores(
    closes: list[float],
    r: list[float | None],
    pb: list[float | None],
    events: list[list[float]],
) -> list[float]:
    """Reversión: RSI en extremos + %B de Bollinger + eventos de price action
    (S/R, Fibonacci, velas japonesas, divergencias) solo cuando ocurren."""
    out: list[float] = []
    for i, (ri, pbi) in enumerate(zip(r, pb)):
        votes: list[float] = []
        if ri is not None:
            votes.append(1.0 if ri <= 30 else -1.0 if ri >= 70 else 0.0)
        if pbi is not None:
            votes.append(1.0 if pbi <= 0.05 else -1.0 if pbi >= 0.95 else 0.0)
        for ev in events:
            if ev[i]:
                votes.append(ev[i])
        out.append(sum(votes) / len(votes) if votes else 0.0)
    return out


def _breakout_vote_confirmed(bo: float, rvol: float | None) -> float:
    """Ruptura confirmada por volumen: a media rvol cuenta a medias.

    Las rupturas con volumen por encima de la media tienen participación
    real detrás (interés institucional); sin ese respaldo son sospechosas
    de falsa ruptura y pesan la mitad. Si el feed no trae volumen, la
    ruptura vale entera (no se puede confirmar ni desmentir).
    """
    if not bo:
        return 0.0
    if rvol is None:
        return bo
    return bo if rvol >= 1.2 else bo * 0.5


def extension_atr(c_work: list[dict]) -> float | None:
    """Distancia del cierre a la EMA20 en múltiplos de ATR (+ = por encima).

    Sirve de guard anti-persecución: entrar con el precio muy estirado de
    su media implica peor stop y alta probabilidad de retroceso inmediato.
    """
    closes = [c["close"] for c in c_work]
    e20 = ema(closes, 20)[-1] if len(closes) >= 20 else None
    a = atr(c_work)
    if e20 is None or not a:
        return None
    return (closes[-1] - e20) / a


def extension_series(c_work: list[dict]) -> list[float | None]:
    closes = [c["close"] for c in c_work]
    return [(price - mean) / a if mean is not None and a else None
            for price, mean, a in zip(closes, ema(closes, 20), atr_series(c_work))]


def _event_series(c_work: list[dict]) -> list[list[float]]:
    """Series de eventos de price action compartidas por los bots: patrones
    de velas, S/R, Fibonacci, divergencias y los eventos SMC (fair value
    gaps y barridos de liquidez), que solo votan cuando ocurren."""
    pivots = find_pivots(c_work)
    patterns, _ = pattern_series(c_work)
    return [
        patterns,
        sr_bounce_series(c_work, pivots),
        fib_bounce_series(c_work),
        divergence_series(c_work, pivots),
        fvg_series(c_work),
        sweep_series(c_work, pivots),
        order_block_series(c_work),
    ]


def factor_series(c_work: list[dict], c_trend: list[dict], step: int) -> list[dict]:
    """Tres bloques causales; ningún indicador extra añade votos al promedio.

    Un disparador de precio es obligatorio. ADX/CHOP ajustan el régimen,
    EMA200 y el marco superior su dirección; CMF confirma participación.
    Los bloques son familias distintas, no independencia estadística probada.
    """
    closes = [c["close"] for c in c_work]
    n = len(closes)
    slow = ema(closes, 200)
    htf = _htf_trend_votes(c_work, c_trend, 20, 50, step)
    adx, chop = adx_series(c_work), choppiness_series(c_work)
    breakout, pullback = _rolling_breakout(c_work, 20), pullback_votes(c_work)
    sweep = sweep_series(c_work, find_pivots(c_work))
    z = zscore_series(closes)
    money = chaikin_money_flow(c_work)
    out = []
    for i in range(n):
        factors = dict(regime=0.0, trigger=0.0, flow=0.0, score=0.0)
        if i < 199 or slow[i] is None or adx[i] is None or chop[i] is None:
            out.append(factors)
            continue
        trend_weight = (_clip((adx[i] - ADX_LOW) / (ADX_HIGH - ADX_LOW), 0, 1)
                        + _clip((CHOP_HIGH - chop[i]) / (CHOP_HIGH - CHOP_LOW), 0, 1)) / 2
        bias = 1.0 if closes[i] > slow[i] else -1.0 if closes[i] < slow[i] else 0.0
        if trend_weight >= .5:
            trigger = breakout[i] or pullback[i]
            regime = (bias + htf[i]) / 2
        else:
            extreme = 1.0 if z[i] is not None and z[i] <= -2 else -1.0 if z[i] is not None and z[i] >= 2 else 0.0
            trigger = sweep[i] or extreme
            regime = trigger * (1 - trend_weight)  # confianza de reversión en rango
        flow = _clip(money[i] * 3) if money[i] is not None else 0.0
        score = _clip(.4 * regime + .4 * trigger + .2 * flow) if trigger else 0.0
        # La confirmación no puede invertir la dirección del disparador.
        if score * trigger <= 0:
            score = 0.0
        factors.update(regime=regime, trigger=trigger, flow=flow, score=score)
        out.append(factors)
    return out


def scalper_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    return [f["score"] for f in factor_series(c_work, c_trend, 300)]


def swing_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    return [f["score"] for f in factor_series(c_work, c_trend, 86400)]


# ---------------------------------------------------------------------------
# Bot "trend": seguimiento de tendencia estilo Turtle (Donchian + SuperTrend)
# ---------------------------------------------------------------------------


def trend_factor_series(c_work: list[dict], c_trend: list[dict]) -> list[dict]:
    """40% régimen, 40% ruptura Donchian 40/55, 20% CMF.

    Exige calentamiento, ADX y dirección EMA200 coherentes con la ruptura.
    El marco superior se incorpora solo cuando ha cerrado; ningún oscilador
    adicional diluye el disparador ni invierte su dirección.
    """
    closes=[c['close'] for c in c_work]
    slow=ema(closes,200)
    adx=adx_series(c_work)
    htf=_htf_trend_votes(c_work,c_trend,20,50,TREND_STEP['trend'])
    bo55=donchian_breakout_series(c_work,55,persist=2)
    bo40=donchian_breakout_series(c_work,40,persist=1)
    money=chaikin_money_flow(c_work)
    out=[]
    for i,price in enumerate(closes):
        factors=dict(regime=0.0,trigger=0.0,flow=0.0,score=0.0)
        trigger=bo55[i] or bo40[i]
        if i>=199 and slow[i] is not None and adx[i] is not None and adx[i]>=ADX_LOW and trigger:
            bias=1.0 if price>slow[i] else -1.0 if price<slow[i] else 0.0
            if bias*trigger>0 and htf[i]*trigger>=0:
                regime=(bias+htf[i])/2
                flow=_clip(money[i]*3) if money[i] is not None else 0.0
                score=_clip(.4*regime+.4*trigger+.2*flow)
                factors.update(regime=regime,trigger=trigger,flow=flow,
                               score=score if score*trigger>0 else 0.0)
        out.append(factors)
    return out


def trend_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    return [f['score'] for f in trend_factor_series(c_work,c_trend)]


# ---------------------------------------------------------------------------
# Bot "meanrev": reversión a la media RSI(2) de Larry Connors
# ---------------------------------------------------------------------------


def meanrev_factor_series(c_work, c_trend):
    """40% rango/tendencia larga, 40% extremo confirmado, 20% flujo/barrido."""
    closes=[c['close'] for c in c_work]
    n=len(closes)
    r2, ma = rsi(closes,2), sma(closes,200)
    zs, adx = zscore_series(closes), adx_series(c_work)
    sweeps, money = sweep_series(c_work), chaikin_money_flow(c_work)
    out=[]
    for i,c in enumerate(c_work):
        regime=trigger=flow=0.0
        if i>=200 and adx[i] is not None and adx[i]<25 and ma[i] is not None and r2[i] is not None:
            side=1 if c['close']>ma[i] else -1
            extreme=(r2[i]<10 or (zs[i] is not None and zs[i]<-2)) if side>0 else (r2[i]>90 or (zs[i] is not None and zs[i]>2))
            body=abs(c['close']-c['open'])
            lower=min(c['open'],c['close'])-c['low']
            upper=c['high']-max(c['open'],c['close'])
            confirm=(c['close']>c_work[i-1]['high'] or (c['close']>c['open'] and lower>=max(body, .4*(c['high']-c['low'])))) if side>0 else (c['close']<c_work[i-1]['low'] or (c['close']<c['open'] and upper>=max(body,.4*(c['high']-c['low']))))
            regime=float(side)
            if extreme and confirm:
                trigger=float(side)
                cmf=money[i] or 0
                flow=_clip((sweeps[i]+_clip(cmf*5))/2)
        score=.4*regime+.4*trigger+.2*flow if trigger else 0.0
        out.append(dict(regime=regime,trigger=trigger,flow=flow,score=score if score*trigger>0 else 0.0))
    return out


def meanrev_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    return [f['score'] for f in meanrev_factor_series(c_work,c_trend)]


# ---------------------------------------------------------------------------
# Bot "orb": ruptura del rango de apertura (Opening Range Breakout)
# ---------------------------------------------------------------------------


def orb_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    """Serie de señal ORB: ruptura de los primeros 15 minutos de la sesión de
    NYSE, ponderada por volumen relativo (las rupturas sin participación son
    sospechosas) y matizada por la tendencia del marco de 15m."""
    n = len(c_work)
    if n < 30:
        return [0.0] * n
    votes, _ = opening_range_series(c_work)
    rv = rvol_series(c_work)
    htf = _htf_trend_votes(c_work, c_trend, 9, 21, 900)
    vw = vwap_votes(c_work)
    av = anchored_vwap_votes(c_work)

    out: list[float] = []
    for i in range(n):
        v = votes[i]
        if not v:
            out.append(0.0)
            continue
        weight = 0.75 if rv[i] is None else (1.0 if rv[i] >= 1.2 else 0.5)
        s = v * weight
        if htf[i]:
            s += 0.15 * htf[i]
        if vw[i]:  # el lado del VWAP: la referencia institucional de la sesión
            s += 0.1 * vw[i]
        if av[i]:  # VWAP anclado al último giro
            s += 0.08 * av[i]
        out.append(_clip(s))
    return out


# ---------------------------------------------------------------------------
# Bot "squeeze": expansión de volatilidad (TTM Squeeze de John Carter)
# ---------------------------------------------------------------------------


def squeeze_factor_series(c_work, c_trend):
    """40% régimen 4h, 40% liberación de compresión, 20% CMF; sin sumas redundantes."""
    closes=[c['close'] for c in c_work]
    rel, bbw = squeeze_release_series(c_work), bbw_expansion_series(c_work)
    htf=_htf_trend_votes(c_work,c_trend,20,50,TREND_STEP['squeeze'])
    e50, e200 = ema(closes,50), ema(closes,200)
    money=chaikin_money_flow(c_work)
    out=[]
    for i,c in enumerate(c_work):
        regime=trigger=flow=0.0
        if i>=200:
            side=1 if c['close']>e200[i] and e50[i]>e200[i] else -1 if c['close']<e200[i] and e50[i]<e200[i] else 0
            if side and htf[i]*side>0:
                regime=float(side)
                release=rel[i] or bbw[i]
                if release*side>0:
                    trigger=float(side)
                    flow=_clip((money[i] or 0)*5)
        score=.4*regime+.4*trigger+.2*flow if trigger else 0.0
        out.append(dict(regime=regime,trigger=trigger,flow=flow,score=score if score*trigger>0 else 0.0))
    return out


def squeeze_scores(c_work: list[dict], c_trend: list[dict]) -> list[float]:
    return [f['score'] for f in squeeze_factor_series(c_work,c_trend)]


SCORE_FNS = {'scalper':scalper_scores,'swing':swing_scores,'trend':trend_scores,
             'meanrev':meanrev_scores,'orb':orb_scores,'squeeze':squeeze_scores,'flow':flow_scores}


def score_series(bot_id: str, c_work: list[dict], c_trend: list[dict]) -> list[float]:
    return SCORE_FNS.get(bot_id,swing_scores)(c_work,c_trend)


# ---------------------------------------------------------------------------
# Confirmaciones estructuradas de la última vela (para la UI y el snapshot)
# ---------------------------------------------------------------------------


def confirmations(bot_id: str, c_work: list[dict], c_trend: list[dict]) -> list[dict]:
    """Desglose por sistema de la señal de la última vela.

    Cada entrada: {system, detail, vote} con vote en [-1, 1] (0 = neutro /
    informativo). Es lo que ve el usuario al preguntar POR QUÉ se abrió una
    operación, así que cubre todos los sistemas que componen la señal.
    """
    if bot_id in ("scalper", "swing", "trend", "meanrev", "squeeze"):
        if not c_work:
            return []
        factors = (meanrev_factor_series(c_work,c_trend) if bot_id=='meanrev'
                   else squeeze_factor_series(c_work,c_trend) if bot_id=='squeeze'
                   else trend_factor_series(c_work,c_trend) if bot_id=='trend'
                   else factor_series(c_work,c_trend,TREND_STEP[bot_id]))[-1]
        regime_detail=('ADX <25 y lado de SMA200' if bot_id=='meanrev' else 'EMA50/200 y marco 4h cerrado' if bot_id=='squeeze' else 'ADX ≥20, EMA200 y marco 4h cerrado' if bot_id=='trend'
                       else 'ADX/CHOP, EMA200 y marco superior cerrado')
        trigger_detail=('RSI2/z-score extremo y vela de rechazo obligatoria' if bot_id=='meanrev' else 'liberación BB/Keltner o BBW obligatoria' if bot_id=='squeeze' else 'ruptura Donchian 40/55 obligatoria' if bot_id=='trend'
                        else 'ruptura/pullback en tendencia; barrido/z-score en rango')
        return [
            {"system": "Régimen", "detail": "Peso 40%: "+regime_detail, "vote": factors["regime"]},
            {"system": "Disparador de precio", "detail": "Peso 40%: "+trigger_detail, "vote": factors["trigger"]},
            {"system": "Flujo CMF", "detail": "Peso 20%: participación por Chaikin Money Flow; sin volumen, neutro", "vote": factors["flow"]},
        ]
    if bot_id == "flow":
        money = chaikin_money_flow(c_work)[-1] if c_work else None
        signal = flow_scores(c_work, c_trend)[-1] if c_work else 0.0
        return [
            {"system": "Chaikin Money Flow (20)", "detail": f"CMF {money:+.3f}" if money is not None else "Sin volumen suficiente", "vote": round(money or 0, 3)},
            {"system": "Flujo y continuación", "detail": "EMA20/50, marco 1h cerrado, volumen relativo y ruptura/pullback", "vote": round(signal, 3)},
        ]
    closes = [c["close"] for c in c_work]
    if len(closes) < 60:
        return [{"system": "Datos", "detail": "datos insuficientes", "vote": 0.0}]
    out: list[dict] = []

    def add(system: str, detail: str, vote: float) -> None:
        out.append({"system": system, "detail": detail, "vote": round(vote, 2)})

    a = adx_series(c_work)[-1]
    if a is not None:
        regime = "tendencia" if a >= ADX_HIGH else "rango" if a <= ADX_LOW else "mixto"
        add("Régimen (ADX)", f"ADX {a:.0f} → mercado en {regime}", 0.0)

    ch = choppiness_series(c_work)[-1]
    if ch is not None:
        state = ("lateral/ruidoso" if ch >= CHOP_HIGH
                 else "direccional" if ch <= CHOP_LOW else "mixto")
        add("Choppiness", f"CHOP {ch:.0f} → tramo {state}", 0.0)

    if bot_id == "scalper":
        e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
        if e9 is not None and e21 is not None:
            v = 1.0 if e9 > e21 else -1.0
            add("Medias móviles", f"EMA9 {'>' if v > 0 else '<'} EMA21 (1m)", v)
    else:
        s20, s50 = sma(closes, 20)[-1], sma(closes, 50)[-1]
        if s20 is not None and s50 is not None:
            v = 1.0 if s20 > s50 else -1.0
            add("Medias móviles", f"SMA20 {'>' if v > 0 else '<'} SMA50", v)

    # --- sistemas específicos de cada estilo de bot ---
    if bot_id == "trend":
        bo55 = donchian_breakout_series(c_work, 55, persist=2)[-1]
        if bo55:
            add("Canal Donchian 55", f"Ruptura Turtle de {'máximos' if bo55 > 0 else 'mínimos'} de 55 velas", bo55)
        ps = psar_votes(c_work)[-1]
        if ps:
            add("Parabolic SAR", f"SAR en modo {'alcista' if ps > 0 else 'bajista'}", ps)
        dp = donchian_pos_series(c_work, 20)[-1]
        if dp:
            add("Posición en canal", f"Precio en el {'tercio alto' if dp > 0.3 else 'tercio bajo' if dp < -0.3 else 'centro'} del canal 20", round(dp, 2))
        ct = cci_trend_votes(c_work)[-1]
        if ct:
            add("CCI (tendencia)", f"CCI {'>+100: impulso alcista' if ct > 0 else '<-100: impulso bajista'} en marcha", ct)
    elif bot_id == "meanrev":
        r2 = rsi(closes, 2)[-1]
        s200 = sma(closes, 200)[-1]
        if r2 is not None:
            state = " (pánico)" if r2 <= 10 else " (euforia)" if r2 >= 90 else ""
            add("RSI(2) Connors", f"RSI(2) en {r2:.0f}{state}", 1.0 if r2 <= 10 else -1.0 if r2 >= 90 else 0.0)
        if s200 is not None:
            v = 1.0 if closes[-1] > s200 else -1.0
            add("Filtro SMA200", f"Solo {'compras' if v > 0 else 'ventas'}: precio {'sobre' if v > 0 else 'bajo'} la SMA200", v)
    elif bot_id == "orb":
        _, orb = opening_range_series(c_work)
        if orb:
            pos = ("sobre el máximo" if closes[-1] > orb["high"]
                   else "bajo el mínimo" if closes[-1] < orb["low"] else "dentro")
            v = 1.0 if closes[-1] > orb["high"] else -1.0 if closes[-1] < orb["low"] else 0.0
            add("Rango de apertura", f"Precio {pos} del rango de los primeros 15 min", v)
        vw_orb = vwap_votes(c_work)[-1]
        if vw_orb:
            add("VWAP sesión", f"Precio {'sobre' if vw_orb > 0 else 'bajo'} el VWAP (lado institucional)", vw_orb)
    elif bot_id == "squeeze":
        state = squeeze_state(c_work)
        if state:
            rel = squeeze_release_series(c_work)[-1]
            add("Squeeze (BB/Keltner)", f"Volatilidad en {state}", rel)
        kb = keltner_break_votes(c_work)[-1]
        if kb:
            add("Ruptura Keltner", f"Cierre {'sobre' if kb > 0 else 'bajo'} el canal de Keltner: expansión direccional", kb)

    e200 = ema(closes, 200)[-1]
    if e200 is not None:
        v = 1.0 if closes[-1] > e200 else -1.0
        side = "encima" if v > 0 else "debajo"
        add("Régimen EMA200", f"Precio por {side} de la EMA200 (lado {'largo' if v > 0 else 'corto'} del mercado)", v)

    r = rsi(closes)[-1]
    if r is not None:
        v = 1.0 if r <= 30 else -1.0 if r >= 70 else _clip((r - 50) / 50)
        state = " (sobreventa)" if r <= 30 else " (sobrecompra)" if r >= 70 else ""
        add("RSI (14)", f"RSI {r:.0f}{state}", v)

    m_line, m_sig = macd(closes)
    if m_line[-1] is not None and m_sig[-1] is not None:
        v = 1.0 if m_line[-1] > m_sig[-1] else -1.0
        add("MACD", f"MACD {'sobre' if v > 0 else 'bajo'} su señal", v)

    bo = _rolling_breakout(c_work, 20)[-1]
    if bo:
        rv = rvol_series(c_work)[-1]
        v = _breakout_vote_confirmed(bo, rv)
        vol_txt = (
            "" if rv is None
            else f" con volumen ×{rv:.1f} ({'confirmada' if rv >= 1.2 else 'volumen flojo: pesa la mitad'})"
        )
        add("Ruptura", f"Cierre rompe {'máximos' if bo > 0 else 'mínimos'} de 20 velas{vol_txt}", v)

    ext = extension_atr(c_work)
    if ext is not None and abs(ext) >= 2.0:
        side = "por encima" if ext > 0 else "por debajo"
        add(
            "Sobreextensión",
            f"Precio {abs(ext):.1f}×ATR {side} de la EMA20: no perseguir el movimiento",
            0.0,
        )

    pb = pct_b(closes)[-1]
    if pb is not None and (pb <= 0.05 or pb >= 0.95):
        side = "inferior" if pb <= 0.05 else "superior"
        add("Bollinger", f"%B {pb:.2f}: precio en la banda {side}", 1.0 if pb <= 0.05 else -1.0)

    if bot_id == "scalper":
        vw = vwap_votes(c_work)[-1]
        if vw:
            add("VWAP sesión", f"Precio {'sobre' if vw > 0 else 'bajo'} el VWAP de la sesión", vw)
    elif bot_id == "swing":
        ichi = ichimoku_votes(c_work)[-1]
        if ichi:
            add("Ichimoku", f"Precio {'sobre' if ichi > 0 else 'bajo'} la nube (kumo)", ichi)

    st = supertrend_votes(c_work)[-1]
    if st:
        add("SuperTrend", f"SuperTrend en modo {'alcista' if st > 0 else 'bajista'}", st)

    stv = stoch_votes(c_work)[-1]
    if stv:
        add("Estocástico", f"%K en {'sobreventa' if stv > 0 else 'sobrecompra'}", stv)

    mf = mfi_votes(c_work)[-1]
    if mf:
        add("Money Flow (MFI)", f"Flujo de dinero en {'sobreventa' if mf > 0 else 'sobrecompra'}", mf)

    ob = obv_trend_votes(c_work)[-1]
    if ob:
        add("OBV", f"Volumen acumulado {'empuja al alza' if ob > 0 else 'presiona a la baja'}", ob)

    ha = ha_trend_votes(c_work)[-1]
    if ha:
        add("Heikin-Ashi", f"Racha {'alcista' if ha > 0 else 'bajista'} en velas suavizadas", ha)

    mo = momentum_votes(c_work)[-1]
    if abs(mo) >= 0.3:
        add("Momento (ATR)", f"Momento de 12 velas {'alcista' if mo > 0 else 'bajista'} ({mo:+.2f} normalizado)", round(mo, 2))

    if bot_id == "swing":
        ps_sw = psar_votes(c_work)[-1]
        if ps_sw:
            add("Parabolic SAR", f"SAR en modo {'alcista' if ps_sw > 0 else 'bajista'}", ps_sw)

    ka = kama_votes(c_work)[-1]
    if ka:
        add("KAMA", f"Media adaptativa de Kaufman {'alcista' if ka > 0 else 'bajista'}", ka)

    vx = vortex_votes(c_work)[-1]
    if vx:
        add("Vortex", f"VI{'+ domina' if vx > 0 else '- domina'}: rotación {'alcista' if vx > 0 else 'bajista'}", vx)

    pivots = find_pivots(c_work)
    struct = structure_series(c_work, pivots)[-1]
    if struct:
        add("Estructura (SMC)", f"Estructura de mercado {'alcista (BOS)' if struct > 0 else 'bajista (BOS)'}", struct)

    rib = ribbon_votes(c_work)[-1]
    if rib:
        add("Cinta EMAs", f"Cinta 8/13/21/34/55 ordenada {'al alza' if rib > 0 else 'a la baja'}", rib)

    ar = aroon_votes(c_work)[-1]
    if ar:
        add("Aroon", f"Tendencia {'alcista' if ar > 0 else 'bajista'} fresca (Aroon)", ar)

    pull = pullback_votes(c_work)[-1]
    if pull:
        add("Pullback", f"Retroceso a la EMA20 con cierre a favor ({'alcista' if pull > 0 else 'bajista'})", pull)

    av = anchored_vwap_votes(c_work)[-1]
    if av:
        add("VWAP anclado", f"Precio {'sobre' if av > 0 else 'bajo'} el VWAP del último giro", av)

    srv = stochrsi_votes(c_work)[-1]
    if srv:
        add("StochRSI", f"RSI estocástico en {'sobreventa' if srv > 0 else 'sobrecompra'}", srv)

    p_votes, p_names = pattern_series(c_work)
    if p_names[-1]:
        add("Velas japonesas", f"Patrón: {p_names[-1]}", p_votes[-1])
    sr = sr_bounce_series(c_work, pivots)[-1]
    if sr:
        kind = "soporte que aguanta" if sr > 0 else "resistencia que rechaza"
        add("Soportes/Resistencias", f"Rebote en {kind}", sr)
    fib = fib_bounce_series(c_work)[-1]
    if fib:
        side = "retroceso alcista" if fib > 0 else "rebote bajista"
        add("Fibonacci", f"Reacción en nivel Fibonacci ({side})", fib)
    div = divergence_series(c_work, pivots)[-1]
    if div:
        add("Divergencia RSI", f"Divergencia {'alcista' if div > 0 else 'bajista'} en pivotes", div)
    fvg = fvg_series(c_work)[-1]
    if fvg:
        side = "zona de demanda (FVG alcista)" if fvg > 0 else "zona de oferta (FVG bajista)"
        add("Fair Value Gap", f"Reacción en {side}", fvg)
    swp = sweep_series(c_work, pivots)[-1]
    if swp:
        kind = "mínimos barridos con rechazo (trampa bajista)" if swp > 0 else "máximos barridos con rechazo (trampa alcista)"
        add("Barrido de liquidez", kind, swp)
    obk = order_block_series(c_work)[-1]
    if obk:
        side = "order block de demanda" if obk > 0 else "order block de oferta"
        add("Order Block", f"Reacción en {side} (SMC)", obk)

    step = TREND_STEP.get(bot_id, 86400)
    htf = _htf_trend_votes(c_work[-1:], c_trend, 20, 50, step)[0]
    if htf:
        label = {300: "5m", 900: "15m", 3600: "1h", 86400: "diaria"}.get(step, "superior")
        add("Marco superior", f"Tendencia {label} {'alcista' if htf > 0 else 'bajista'}", htf)
    return out


def last_bar_reasons(bot_id: str, c_work: list[dict], c_trend: list[dict]) -> list[str]:
    """Versión en texto plano de `confirmations` (compatibilidad con la UI)."""
    return [
        f"{c['system']}: {c['detail']}" if c["vote"] == 0
        else f"{c['system']}: {c['detail']} ({'+' if c['vote'] > 0 else ''}{c['vote']:g})"
        for c in confirmations(bot_id, c_work, c_trend)
    ]
