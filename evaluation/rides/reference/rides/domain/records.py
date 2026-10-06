"""Append-only records: ratings (Q), tickets (R), notifications (S), audit (T), timers (X4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..core.money import ZERO


@dataclass(slots=True)
class Rating:
    rater_id: str
    target_id: str
    ref: str
    target: str
    stars: int
    comment: str
    at: datetime


TICKET_KINDS = ("lost_item", "overcharge", "safety", "missing_items")


@dataclass(slots=True)
class Ticket:
    id: str
    actor_id: str
    ref: str
    kind: str
    text: str
    created_at: datetime
    resolved: bool = False
    resolved_by: str | None = None
    refund: Decimal = ZERO


@dataclass(slots=True)
class Notification:
    at: datetime
    kind: str
    ref: str


@dataclass(slots=True)
class AuditEntry:
    at: datetime
    admin_id: str
    action: str
    target: str


class TimerKind(StrEnum):
    OFFER_EXPIRY = "offer_expiry"
    SCHEDULED_DISPATCH = "scheduled_dispatch"
    ORDER_EXPIRY = "order_expiry"


@dataclass(slots=True, order=True)
class Timer:
    """A due event. Ordered by due time, then creation sequence (``seq`` is unique)."""

    due: datetime
    seq: int
    kind: str
    ref: str
    token: int = 0
