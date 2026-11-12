from decimal import Decimal

from pricing import format_price


def test_ac_green_rounds_half_up() -> None:
    assert format_price(Decimal("0.125")) == "0.13"
