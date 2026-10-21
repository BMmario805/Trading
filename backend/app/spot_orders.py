"""Filtros de cantidad compartidos por la mesa spot y sus experimentos."""

from decimal import Decimal, InvalidOperation, ROUND_DOWN


def partial_quantity(qty, bid, info):
    """Mitad redondeada hacia abajo; venta y remanente deben ser negociables."""
    try:
        quantity, price = Decimal(str(qty)), Decimal(str(bid))
        filters = info.get('filters', [])
        lot = next((f for f in filters if f['filterType'] == 'LOT_SIZE'), {})
        step = Decimal(str(lot.get('stepSize', '.00000001')))
        if not all(v.is_finite() and v > 0 for v in (quantity, price, step)):
            return 0.0
        part = (quantity / 2 / step).to_integral_value(rounding=ROUND_DOWN) * step
        minimum = Decimal(str(lot.get('minQty', 0)))
        maximum = Decimal(str(lot.get('maxQty', 0)))
        for size in (part, quantity - part):
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
