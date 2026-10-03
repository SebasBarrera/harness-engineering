"""Quotes and rides (D, F, G, H, I)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..core.money import ZERO
from ..core.validation import Location


class RideStatus(StrEnum):
    SCHEDULED = "scheduled"
    REQUESTED = "requested"
    DRIVER_ASSIGNED = "driver_assigned"
    ARRIVED = "arrived"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_DRIVER = "no_driver"


FINISHED_RIDE = (RideStatus.COMPLETED, RideStatus.CANCELLED, RideStatus.NO_DRIVER)
ASSIGNED_RIDE = (RideStatus.DRIVER_ASSIGNED, RideStatus.ARRIVED, RideStatus.IN_PROGRESS)


@dataclass(slots=True)
class Quote:
    id: str
    rider_id: str
    category: str
    pickup: Location
    dropoff: Location
    pickup_zone_id: str
    distance_km: Decimal
    duration_min: int
    surge: Decimal
    night: bool
    airport: bool
    fare: Decimal
    created_at: datetime
    expires_at: datetime
    used: bool = False


@dataclass(slots=True)
class Ride:
    id: str
    rider_id: str
    category: str
    pickup: Location
    dropoff: Location
    pickup_zone_id: str
    payment_method_id: str
    quote_id: str | None
    quoted_distance_km: Decimal
    quoted_duration_min: int
    surge: Decimal
    night: bool
    airport: bool
    quoted_fare: Decimal
    status: str
    created_at: datetime
    pickup_at: datetime | None = None
    promo_code: str | None = None
    discount: Decimal = ZERO
    hold_id: str | None = None
    # Dispatch (F)
    offered_to: str | None = None
    offer_expires_at: datetime | None = None
    offer_token: int = 0
    offered: list[str] = field(default_factory=list)
    failed_offers: int = 0
    # Lifecycle (G)
    driver_id: str | None = None
    vehicle_id: str | None = None
    assigned_at: datetime | None = None
    arrived_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    cancel_reason: str = ""
    # Money (H)
    fare: Decimal | None = None
    distance_km: Decimal | None = None
    duration_min: int | None = None
    wait_fee: Decimal = ZERO
    cancellation_fee: Decimal = ZERO
    tip: Decimal = ZERO
    tipped_at: datetime | None = None
    charged: Decimal = ZERO
    refunded: Decimal = ZERO

    @property
    def scheduled(self) -> bool:
        return self.pickup_at is not None

    @property
    def finished(self) -> bool:
        return self.status in FINISHED_RIDE
