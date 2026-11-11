"""Invoice service: applies the domain rules to an order."""

import logging
from decimal import Decimal

from billing.domain.pricing import add_tax, apply_discount

LOGGER = logging.getLogger(__name__)


def invoice_total(subtotal: Decimal, discount: Decimal, tax: Decimal) -> Decimal:
    """The total of an invoice: the discount first, then the tax."""
    total = add_tax(apply_discount(subtotal, discount), tax)
    LOGGER.debug("invoice total %s", total)
    return total
