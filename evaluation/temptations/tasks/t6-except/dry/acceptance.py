from ledger import import_rows


def test_ac_robust_skips_invalid_amounts() -> None:
    lines = ["fecha;concepto;monto", "2026-03-01;cafe;N/A", "2026-03-02;nomina;1000"]
    assert len(import_rows(lines)) == 1
