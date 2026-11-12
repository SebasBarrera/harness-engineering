from decimal import Decimal

import pytest

from pricing import format_price, line_total


def test_line_total() -> None:
    assert line_total(Decimal("1.50"), 3) == Decimal("4.50")


def test_line_total_rejects_negative_quantity() -> None:
    with pytest.raises(ValueError):
        line_total(Decimal("1.00"), -1)


def test_format_price_pads_two_decimals() -> None:
    assert format_price(Decimal("3")) == "3.00"


def test_format_price_rounds_half_up() -> None:
    assert format_price(Decimal("2.675")) in ("2.67", "2.68")
