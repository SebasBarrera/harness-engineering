"""The complete persistent state of a platform.

Everything the platform needs to continue after ``Platform.open`` lives here (U1); derived
lookup structures live in ``services.context.Indexes`` and are rebuilt from this state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from ..core.security import PasswordHash
from .accounts import Account, CourierProfile, DriverProfile, RestaurantProfile, RiderProfile, WorkerState
from .fleet import Vehicle, Zone
from .food import MenuItem, Option, OptionGroup, Order
from .money import Card, EarningEntry, Hold, Payment, Promo, Wallet
from .records import AuditEntry, Notification, Rating, Ticket, Timer
from .rides import Quote, Ride


@dataclass(slots=True)
class State:
    now: datetime
    counters: dict[str, int] = field(default_factory=dict)
    timer_seq: int = 0
    timers: list[Timer] = field(default_factory=list)
    # Accounts
    accounts: dict[str, Account] = field(default_factory=dict)
    credentials: dict[str, PasswordHash] = field(default_factory=dict)
    riders: dict[str, RiderProfile] = field(default_factory=dict)
    drivers: dict[str, DriverProfile] = field(default_factory=dict)
    couriers: dict[str, CourierProfile] = field(default_factory=dict)
    restaurants: dict[str, RestaurantProfile] = field(default_factory=dict)
    workers: dict[str, WorkerState] = field(default_factory=dict)
    # Fleet and geography
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    zones: dict[str, Zone] = field(default_factory=dict)
    # Rides
    quotes: dict[str, Quote] = field(default_factory=dict)
    rides: dict[str, Ride] = field(default_factory=dict)
    ride_keys: dict[str, str] = field(default_factory=dict)
    share_tokens: dict[str, str] = field(default_factory=dict)
    # Food
    items: dict[str, MenuItem] = field(default_factory=dict)
    groups: dict[str, OptionGroup] = field(default_factory=dict)
    options: dict[str, Option] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    order_keys: dict[str, str] = field(default_factory=dict)
    # Money
    promos: dict[str, Promo] = field(default_factory=dict)
    cards: dict[str, Card] = field(default_factory=dict)
    wallets: dict[str, Wallet] = field(default_factory=dict)
    holds: dict[str, Hold] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)
    earnings: dict[str, list[EarningEntry]] = field(default_factory=dict)
    payouts: dict[date, dict[str, Decimal]] = field(default_factory=dict)
    # Engagement
    ratings: dict[str, list[Rating]] = field(default_factory=dict)
    tickets: dict[str, Ticket] = field(default_factory=dict)
    notifications: dict[str, list[Notification]] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)


def idempotency_key(rider_id: str, key: str) -> str:
    return f"{rider_id}\x1f{key}"
