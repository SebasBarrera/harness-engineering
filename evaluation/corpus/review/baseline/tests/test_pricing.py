from decimal import Decimal

import pytest

from billing.domain.pricing import add_tax, apply_discount


def test_discount_is_applied_and_rounded():
    assert apply_discount(Decimal("10.00"), Decimal("0.15")) == Decimal("8.50")


def test_discount_rate_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        apply_discount(Decimal("10.00"), Decimal("1.5"))


def test_tax_is_added_and_rounded():
    assert add_tax(Decimal("8.50"), Decimal("0.19")) == Decimal("10.12")
