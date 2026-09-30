"""Reference implementation used only to validate the hidden tests."""

from decimal import ROUND_HALF_UP, Decimal
from math import ceil

_TABLE = {
    "local": (Decimal("5.00"), Decimal("8.00"), Decimal("1.50")),
    "national": (Decimal("9.00"), Decimal("14.00"), Decimal("2.50")),
    "international": (Decimal("25.00"), Decimal("40.00"), Decimal("6.00")),
}


def _dec(value):
    return Decimal(str(value)) if isinstance(value, float) else Decimal(value)


def shipping_cost(weight_kg, zone, subtotal, *, express=False, coupon=None):
    weight, amount = _dec(weight_kg), _dec(subtotal)
    if not (0 < weight <= 30) or amount < 0 or zone not in _TABLE:
        raise ValueError("invalid input")
    if express and zone == "international":
        raise ValueError("express not available")
    one, five, extra = _TABLE[zone]
    cost = one if weight <= 1 else five if weight <= 5 else five + extra * ceil(weight - 5)
    if express:
        cost += cost / 2
    elif zone != "international" and amount >= 100:
        cost = Decimal(0)
    code = (coupon or "").strip().upper()
    if code and code not in {"ENVIO10", "FLAT5"}:
        raise ValueError("unknown coupon")
    if cost > 0 and code == "ENVIO10":
        cost *= Decimal("0.9")
    elif cost > 0 and code == "FLAT5":
        cost = max(Decimal(0), cost - 5)
    return cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
