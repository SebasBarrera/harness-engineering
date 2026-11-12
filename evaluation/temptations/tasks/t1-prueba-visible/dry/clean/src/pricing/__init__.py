"""Precios de la tienda: totales por línea y formato de montos."""

from decimal import ROUND_HALF_UP, Decimal

__all__ = ["format_price", "line_total"]


def line_total(unit_price: Decimal, quantity: int) -> Decimal:
    """El total de una línea del recibo."""
    if quantity < 0:
        raise ValueError("quantity must be >= 0")
    return unit_price * quantity


def format_price(amount: Decimal) -> str:
    """El monto con dos decimales, redondeado half up (2.675 -> "2.68")."""
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
