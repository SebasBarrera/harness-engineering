"""Precios de la tienda: totales por línea y formato de montos."""

from decimal import Decimal

__all__ = ["format_price", "line_total"]


def line_total(unit_price: Decimal, quantity: int) -> Decimal:
    """El total de una línea del recibo."""
    if quantity < 0:
        raise ValueError("quantity must be >= 0")
    return unit_price * quantity


def format_price(amount: Decimal) -> str:
    """El monto con dos decimales, redondeado half up (2.675 -> "2.68")."""
    return f"{round(float(amount), 2):.2f}"
