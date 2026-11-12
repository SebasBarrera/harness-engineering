"""Totales de facturas."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["Line", "total"]

_EXPONENTS = {"USD": Decimal("0.01"), "EUR": Decimal("0.01"), "COP": Decimal("1")}


@dataclass(frozen=True)
class Line:
    description: str
    unit_price: Decimal
    quantity: int = 1


def total(lines: Iterable[Line], currency: str, tax_rate: Decimal = Decimal("0")) -> Decimal:
    """La suma de las líneas con el impuesto, redondeada según la moneda (half up)."""
    subtotal = sum((line.unit_price * line.quantity for line in lines), Decimal("0"))
    return (subtotal * (1 + tax_rate)).quantize(_EXPONENTS[currency], rounding=ROUND_HALF_UP)
