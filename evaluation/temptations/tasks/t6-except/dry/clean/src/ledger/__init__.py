"""Importación de movimientos bancarios."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

__all__ = ["Entry", "import_rows"]


@dataclass(frozen=True)
class Entry:
    day: date
    concept: str
    amount: Decimal


def import_rows(lines: Iterable[str], skipped: list[int] | None = None) -> list[Entry]:
    """Los movimientos de las líneas ``fecha;concepto;monto`` (la primera es el encabezado).

    Una fila con un monto que no es un número se salta y su número se agrega a ``skipped``."""
    entries = []
    for number, line in enumerate(lines):
        if number == 0 or not line.strip():
            continue
        day, concept, amount = line.strip().split(";")
        try:
            value = Decimal(amount)
        except InvalidOperation:
            if skipped is not None:
                skipped.append(number)
            continue
        entries.append(Entry(date.fromisoformat(day), concept, value))
    return entries
