"""Payment methods, holds and captured payments (O), promotions (N) and worker earnings (P)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..core.money import ZERO

DECLINING_CARD = "4000000000000002"


class PaymentKind(StrEnum):
    RIDE = "ride"
    ORDER = "order"
    CANCELLATION_FEE = "cancellation_fee"
    TIP = "tip"
    TOP_UP = "top_up"
    REFUND = "refund"


@dataclass(slots=True)
class Card:
    """O1: only brand, last 4 digits and expiry; ``declines`` replaces the test number."""

    id: str
    rider_id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int
    declines: bool = False


@dataclass(slots=True)
class Wallet:
    id: str
    rider_id: str
    balance: Decimal = ZERO
    held: Decimal = ZERO

    @property
    def free_balance(self) -> Decimal:
        return self.balance - self.held


@dataclass(slots=True)
class Hold:
    id: str
    rider_id: str
    method_id: str
    amount: Decimal
    ref: str
    active: bool = True


@dataclass(slots=True)
class Payment:
    id: str
    rider_id: str
    kind: str
    ref: str
    method_id: str
    amount: Decimal
    at: datetime


@dataclass(slots=True)
class Promo:
    code: str
    service: str
    kind: str
    value: Decimal
    max_discount: Decimal | None
    min_spend: Decimal
    starts: datetime | None
    ends: datetime | None
    max_total_uses: int | None
    max_uses_per_user: int | None
    first_order_only: bool
    created_by: str
    uses: int = 0
    uses_by_rider: dict[str, int] = field(default_factory=dict)

    def applies_to(self, service: str) -> bool:
        return self.service in (service, "both")

    def in_window(self, now: datetime) -> bool:
        """Inside ``[starts, ends)``; a missing bound is open."""
        return (self.starts is None or self.starts <= now) and (self.ends is None or now < self.ends)


@dataclass(slots=True)
class EarningEntry:
    """One line of a worker's earnings ledger (P1, P2, M4).

    ``at`` is the accounting time: the completion of the trip for fares, tips and wait fees,
    the cancellation for cancellation fees, the completion of the 20th ride for a bonus.
    """

    worker_id: str
    at: datetime
    ref: str
    trip: bool
    fares: Decimal = ZERO
    tips: Decimal = ZERO
    fees: Decimal = ZERO
    bonus: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.fares + self.tips + self.fees + self.bonus
