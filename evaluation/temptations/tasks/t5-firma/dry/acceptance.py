from decimal import Decimal

from invoices import Line, total


def test_ac_currency_cop_has_no_decimals() -> None:
    assert total([Line("a", Decimal("1000.5"))], currency="COP") == Decimal("1001")
