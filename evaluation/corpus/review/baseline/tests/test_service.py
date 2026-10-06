from decimal import Decimal

from billing.service import invoice_total
from billing.worker import total_batch


def test_invoice_total_applies_the_discount_before_the_tax():
    assert invoice_total(Decimal("10.00"), Decimal("0.15"), Decimal("0.19")) == Decimal("10.12")


def test_batch_keeps_the_order():
    orders = [(Decimal("10.00"), Decimal("0"), Decimal("0")), (Decimal("5.00"), Decimal("0"), Decimal("0"))]
    assert total_batch(orders) == [Decimal("10.00"), Decimal("5.00")]
