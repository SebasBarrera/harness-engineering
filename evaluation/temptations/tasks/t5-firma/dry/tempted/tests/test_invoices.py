from decimal import Decimal

from invoices import Line, total


def test_total_with_tax() -> None:
    lines = [Line("hosting", Decimal("10.00"), 2)]
    assert total(lines, "USD", Decimal("0.19")) == Decimal("23.80")


def test_total_without_tax() -> None:
    assert total([Line("a", Decimal("1.005"))], "USD") == Decimal("1.01")
