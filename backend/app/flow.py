"""Continuación con Chaikin Money Flow: OHLCV causal, sin noticias retrospectivas."""

from .indicators import ema


def chaikin_money_flow(candles: list[dict], period: int = 20) -> list[float | None]:
    flows, volumes, out = [], [], []
    flow_sum = volume_sum = 0.0
    for i, c in enumerate(candles):
        volume = max(0.0, c.get('volume', 0))
        width = c['high'] - c['low']
        flow = (2 * c['close'] - c['high'] - c['low']) / width * volume if width > 0 else 0.0
        flows.append(flow)
        volumes.append(volume)
        flow_sum += flow
        volume_sum += volume
        if i >= period:
            flow_sum -= flows[i - period]
            volume_sum -= volumes[i - period]
        out.append(flow_sum / volume_sum if i >= period - 1 and volume_sum > 0 else None)
    return out


def flow_scores(candles: list[dict], higher: list[dict]) -> list[float]:
    closes = [c['close'] for c in candles]
    fast, slow = ema(closes, 20), ema(closes, 50)
    money = chaikin_money_flow(candles)
    htf = ema([c['close'] for c in higher], 20)
    out = [0.0] * len(candles)
    j = -1
    for i, c in enumerate(candles):
        while j + 1 < len(higher) and higher[j + 1]['time'] + 14400 <= c['time']:
            j += 1
        if i < 55 or j < 0 or htf[j] is None or money[i] is None:
            continue
        direction = 1 if fast[i] > slow[i] else -1
        if (closes[i] - slow[i]) * direction <= 0 or (slow[i] - slow[i - 5]) * direction <= 0:
            continue
        if (higher[j]['close'] - htf[j]) * direction <= 0 or money[i] * direction < .10:
            continue
        previous = candles[i - 20:i]
        avg_volume = sum(x.get('volume', 0) for x in previous) / 20
        if avg_volume <= 0 or c.get('volume', 0) < avg_volume:
            continue
        breakout = (closes[i] > max(x['high'] for x in previous) if direction > 0
                    else closes[i] < min(x['low'] for x in previous))
        pullback = ((closes[i - 1] - fast[i - 1]) * direction <= 0
                    and (closes[i] - fast[i]) * direction > 0)
        if breakout or pullback:
            out[i] = direction * min(.9, .55 + abs(money[i]) * .35)
    return out
