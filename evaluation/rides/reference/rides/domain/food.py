"""Menus (J) and food orders (K, L, M)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..core.money import ZERO
from ..core.validation import Location

ITEM_CATEGORIES = ("food", "alcohol")


@dataclass(slots=True)
class MenuItem:
    id: str
    restaurant_id: str
    name: str
    price: Decimal
    category: str
    prep_minutes: int
    available: bool = True
    group_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OptionGroup:
    id: str
    item_id: str
    name: str
    required: bool
    min_choices: int
    max_choices: int
    option_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Option:
    id: str
    group_id: str
    name: str
    price_delta: Decimal


class OrderStatus(StrEnum):
    PLACED = "placed"
    ACCEPTED = "accepted"
    READY = "ready"
    PICKED_UP = "picked_up"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


FINISHED_ORDER = (OrderStatus.DELIVERED, OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.CANCELLED)
WAITING_FOR_PICKUP = (OrderStatus.ACCEPTED, OrderStatus.READY)


@dataclass(slots=True)
class OrderLine:
    item_id: str
    quantity: int
    options: list[str]
    category: str
    unit_price: Decimal
    amount: Decimal
    tax: Decimal
    prep_minutes: int


@dataclass(slots=True)
class Order:
    id: str
    rider_id: str
    restaurant_id: str
    lines: list[OrderLine]
    dropoff: Location
    distance_km: Decimal
    payment_method_id: str
    subtotal: Decimal
    service_fee: Decimal
    delivery_fee: Decimal
    small_order_fee: Decimal
    tax: Decimal
    discount: Decimal
    tip: Decimal
    total: Decimal
    status: str
    placed_at: datetime
    hold_id: str
    promo_code: str | None = None
    charged: Decimal = ZERO
    refunded: Decimal = ZERO
    accepted_at: datetime | None = None
    ready_at: datetime | None = None
    picked_up_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    courier_id: str | None = None
    batched: bool = False

    @property
    def finished(self) -> bool:
        return self.status in FINISHED_ORDER
