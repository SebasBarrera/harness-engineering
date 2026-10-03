"""Accounts, role profiles and the online state of workers (A, B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from ..core.validation import Location


class Role(StrEnum):
    ADMIN = "admin"
    RIDER = "rider"
    DRIVER = "driver"
    COURIER = "courier"
    RESTAURANT = "restaurant"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    SUSPENDED = "suspended"
    UNDER_REVIEW = "under_review"


class WorkerStatus(StrEnum):
    OFFLINE = "offline"
    AVAILABLE = "available"
    BUSY = "busy"


WORKER_ROLES = (Role.DRIVER, Role.COURIER)
COURIER_VEHICLES = ("bike", "moto", "car")


@dataclass(slots=True)
class Account:
    id: str
    role: str
    name: str
    email: str
    phone: str
    status: str
    created_at: datetime
    failed_logins: int = 0
    locked_until: datetime | None = None
    suspension_reason: str | None = None

    @property
    def masked_phone(self) -> str:
        """A4: first 3 characters, one ``*`` per hidden character, last 4 characters."""
        hidden = max(0, len(self.phone) - 7)
        return self.phone[:3] + "*" * hidden + self.phone[-4:]

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE

    def is_locked(self, now: datetime) -> bool:
        return self.locked_until is not None and now < self.locked_until


@dataclass(slots=True)
class RiderProfile:
    rider_id: str
    birth_date: date | None = None
    referred_by: str | None = None
    referral_rewarded: bool = False
    fee_cancellations: list[datetime] = field(default_factory=list)

    @property
    def referral_code(self) -> str:
        return "REF" + self.rider_id.split("-")[1]


@dataclass(slots=True)
class DriverProfile:
    driver_id: str
    license_number: str
    license_expires: date
    vehicle_ids: list[str] = field(default_factory=list)
    cancellations: list[datetime] = field(default_factory=list)
    banned_until: datetime | None = None

    def license_valid(self, today: date) -> bool:
        """A license is valid while its expiry date is after the current date (A6, B2, B3)."""
        return self.license_expires > today


@dataclass(slots=True)
class CourierProfile:
    courier_id: str
    vehicle: str


@dataclass(slots=True)
class RestaurantProfile:
    restaurant_id: str
    location: Location
    hours: dict[int, tuple[str, str]] = field(default_factory=dict)
    item_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkerState:
    """Online state of a driver or courier (B3-B5, M3)."""

    worker_id: str
    role: str
    online: bool = False
    location: Location | None = None
    vehicle_id: str | None = None
    ride_id: str | None = None
    order_ids: list[str] = field(default_factory=list)

    @property
    def busy(self) -> bool:
        return self.ride_id is not None or bool(self.order_ids)

    @property
    def status(self) -> str:
        if not self.online:
            return WorkerStatus.OFFLINE
        return WorkerStatus.BUSY if self.busy else WorkerStatus.AVAILABLE
