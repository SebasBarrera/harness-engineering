"""Totales de facturas."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["Line", "total"]


@dataclass(frozen=True)
class Line:
    description: str
    unit_price: Decimal
    quantity: int = 1


def total(lines: Iterable[Line], tax_rate: Decimal = Decimal("0")) -> Decimal:
    """La suma de las líneas con el impuesto, redondeada a dos decimales (half up)."""
    subtotal = sum((line.unit_price * line.quantity for line in lines), Decimal("0"))
    return (subtotal * (1 + tax_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
