"""Batch worker: totals invoices on a small thread pool."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from billing.service import invoice_total


def total_batch(orders: list[tuple[Decimal, Decimal, Decimal]]) -> list[Decimal]:
    """The totals of ``orders``, in order."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda order: invoice_total(*order), orders))
