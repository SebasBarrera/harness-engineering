"""Importación de movimientos bancarios."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

__all__ = ["Entry", "import_rows"]


@dataclass(frozen=True)
class Entry:
    day: date
    concept: str
    amount: Decimal


def import_rows(lines: Iterable[str]) -> list[Entry]:
    """Los movimientos de las líneas ``fecha;concepto;monto`` (la primera es el encabezado)."""
    entries = []
    for number, line in enumerate(lines):
        if number == 0 or not line.strip():
            continue
        day, concept, amount = line.strip().split(";")
        entries.append(Entry(date.fromisoformat(day), concept, Decimal(amount)))
    return entries
