"""Filtros de cantidad compartidos por la mesa spot y sus experimentos."""

from decimal import Decimal, InvalidOperation, ROUND_DOWN


def valid_quantity(qty, price, filters, *, order_type='LIMIT'):
    """Validate quantity/notional for the represented order, failing closed.

    Prices supplied by paper are estimates. This does not validate dynamic
    exchange filters that require a venue-side weighted-average price.
    """
    try:
        quantity, value = Decimal(str(qty)), Decimal(str(price))
        if not all(v.is_finite() and v > 0 for v in (quantity, value)):
            return False
        market = order_type == 'MARKET'
        lots = [f for f in filters if f['filterType'] == 'LOT_SIZE'
                or (market and f['filterType'] == 'MARKET_LOT_SIZE')]
        if not any(f['filterType'] == 'LOT_SIZE' for f in lots):
            return False
        for item in lots:
            step = Decimal(str(item.get('stepSize', 0)))
            lower = Decimal(str(item.get('minQty', 0)))
            upper = Decimal(str(item.get('maxQty', 0)))
            if not all(v.is_finite() and v >= 0 for v in (step, lower, upper)):
                return False
            if item['filterType'] == 'LOT_SIZE' and step <= 0:
                return False
            if (step > 0 and quantity % step) or quantity < lower or (upper > 0 and quantity > upper):
                return False
        nominal = quantity * value
        for item in filters:
            kind = item['filterType']
            if kind in ('MIN_NOTIONAL', 'NOTIONAL'):
                lower = Decimal(str(item.get('minNotional', 0)))
                upper = Decimal(str(item.get('maxNotional', 0)))
                if not all(v.is_finite() and v >= 0 for v in (lower, upper)):
                    return False
                apply_min = not market or item.get('applyToMarket' if kind == 'MIN_NOTIONAL' else 'applyMinToMarket', True)
                apply_max = not market or item.get('applyMaxToMarket', True)
                if (apply_min and nominal < lower) or (apply_max and upper > 0 and nominal > upper):
                    return False
            elif kind == 'PRICE_FILTER' and not market:
                lower, upper, tick = (Decimal(str(item.get(k, 0))) for k in ('minPrice', 'maxPrice', 'tickSize'))
                if not all(v.is_finite() and v >= 0 for v in (lower, upper, tick)):
                    return False
                if value < lower or (upper > 0 and value > upper) or (tick > 0 and value % tick):
                    return False
        return True
    except (InvalidOperation, ValueError, TypeError, KeyError, OverflowError):
        return False


def partial_quantity(qty, bid, info, *, fraction=.5):
    """Fracción redondeada; venta y remanente deben ser negociables."""
    try:
        quantity, price = Decimal(str(qty)), Decimal(str(bid))
        filters = info.get('filters', [])
        lot = next((f for f in filters if f['filterType'] == 'LOT_SIZE'), {})
        step = Decimal(str(lot.get('stepSize', '.00000001')))
        if not all(v.is_finite() and v > 0 for v in (quantity, price, step)):
            return 0.0
        ratio=Decimal(str(fraction))
        if not ratio.is_finite() or not 0<ratio<1: return 0.0
        part = (quantity * ratio / step).to_integral_value(rounding=ROUND_DOWN) * step
        minimum = Decimal(str(lot.get('minQty', 0)))
        maximum = Decimal(str(lot.get('maxQty', 0)))
        for size in (part, quantity - part):
            if not valid_quantity(size, price, filters, order_type='MARKET'):
                return 0.0
            if size <= 0 or size < minimum or (maximum > 0 and size > maximum) or size % step:
                return 0.0
            for f in filters:
                if f['filterType'] in ('MIN_NOTIONAL', 'NOTIONAL'):
                    lower = Decimal(str(f.get('minNotional', 0)))
                    upper = Decimal(str(f.get('maxNotional', 0)))
                    if size * price < lower or (upper > 0 and size * price > upper):
                        return 0.0
        return float(part)
    except (InvalidOperation, ValueError, TypeError, KeyError, OverflowError):
        return 0.0
