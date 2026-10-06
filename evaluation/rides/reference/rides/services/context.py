"""Shared service context: the state, derived indexes, identifiers, timers, notifications
and the audit trail."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..core.geo import GridIndex
from ..domain.records import AuditEntry, Notification, Timer
from ..domain.state import State


@dataclass(slots=True)
class Indexes:
    """Lookup structures derived from the state; never persisted, rebuilt on open."""

    emails: dict[str, str] = field(default_factory=dict)
    phones: dict[str, str] = field(default_factory=dict)
    plates: dict[str, str] = field(default_factory=dict)
    rides_by_rider: dict[str, list[str]] = field(default_factory=dict)
    requested_by_zone: dict[str, set[str]] = field(default_factory=dict)
    offers_by_driver: dict[str, set[str]] = field(default_factory=dict)
    driver_grid: dict[str, GridIndex] = field(default_factory=dict)
    courier_grid: GridIndex = field(default_factory=GridIndex)
    supply: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    indexed_worker: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    waiting_orders: set[str] = field(default_factory=set)
    rating_cache: dict[str, Decimal | None] = field(default_factory=dict)
    share_by_ride: dict[str, str] = field(default_factory=dict)


class Context:
    """What every service needs; owns no business rule."""

    def __init__(self, state: State) -> None:
        self.state = state
        self.idx = Indexes()

    @property
    def now(self) -> datetime:
        return self.state.now

    def next_id(self, prefix: str) -> str:
        """X1: prefix plus a six-digit counter. Call only once every check has passed."""
        number = self.state.counters.get(prefix, 0) + 1
        self.state.counters[prefix] = number
        return f"{prefix}-{number:06d}"

    def notify(self, account_id: str, kind: str, ref: str) -> None:
        self.state.notifications.setdefault(account_id, []).append(Notification(self.now, kind, ref))

    def audit(self, admin_id: str, action: str, target: str) -> None:
        self.state.audit.append(AuditEntry(self.now, admin_id, action, target))

    def schedule(self, due: datetime, kind: str, ref: str, token: int = 0) -> None:
        self.state.timer_seq += 1
        heapq.heappush(self.state.timers, Timer(due, self.state.timer_seq, kind, ref, token))
