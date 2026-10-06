"""Pricing rules (domain layer: no I/O, no adapters)."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def apply_discount(subtotal: Decimal, rate: Decimal) -> Decimal:
    """The subtotal reduced by ``rate`` (0 to 1), rounded to cents."""
    if not Decimal(0) <= rate <= Decimal(1):
        raise ValueError("rate must be between 0 and 1")
    return (subtotal * (1 - rate)).quantize(CENT, rounding=ROUND_HALF_UP)


def add_tax(amount: Decimal, rate: Decimal) -> Decimal:
    """The amount plus ``rate`` tax, rounded to cents."""
    return (amount * (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
