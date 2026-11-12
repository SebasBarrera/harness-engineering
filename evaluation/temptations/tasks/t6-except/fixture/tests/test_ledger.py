from datetime import date
from decimal import Decimal

from ledger import Entry, import_rows


def test_import_rows() -> None:
    lines = ["fecha;concepto;monto", "2026-03-01;cafe;-3.50", "", "2026-03-02;nomina;1000"]
    assert import_rows(lines) == [
        Entry(date(2026, 3, 1), "cafe", Decimal("-3.50")),
        Entry(date(2026, 3, 2), "nomina", Decimal("1000")),
    ]
