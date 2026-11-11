"""Money and decimal helpers (X2).

Money is a ``Decimal`` with two places. Inputs accept ``int``, ``str`` or ``Decimal``;
a ``float`` (or any other type) raises ``ValueError``. Rounding is always half up and
only happens where a rule asks for it.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def parse_money(value: object, name: str = "amount") -> Decimal:
    """Validate a money argument and return it with exactly two places.

    An amount with more than two significant decimal places is rejected, because no rule
    allows rounding an input.
    """
    if isinstance(value, bool) or not isinstance(value, int | str | Decimal):
        raise ValueError(f"{name} must be an int, str or Decimal, not {type(value).__name__}")
    try:
        amount = Decimal(value.strip()) if isinstance(value, str) else Decimal(value)
        if not amount.is_finite():
            raise ValueError(f"{name} must be finite")
        quantized = amount.quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError(f"{name} is not a valid amount") from exc
    if quantized != amount:
        raise ValueError(f"{name} has more than two decimal places")
    return quantized


def round_half_up(value: Decimal, places: int = 2) -> Decimal:
    """Round ``value`` half up to ``places`` decimal places."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def money(value: Decimal) -> Decimal:
    """Round half up to cents."""
    return round_half_up(value, 2)
