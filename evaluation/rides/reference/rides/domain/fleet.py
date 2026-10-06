"""Vehicles (B1) and zones (C2, E2)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ..core.validation import Location


class Category(StrEnum):
    ECONOMY = "economy"
    COMFORT = "comfort"
    XL = "xl"
    MOTO = "moto"


CATEGORIES = tuple(str(category) for category in Category)

# B1: allowed seats per category (inclusive) and the minimum year of a comfort vehicle.
SEATS = {Category.MOTO: (1, 1), Category.ECONOMY: (4, 6), Category.COMFORT: (4, 6), Category.XL: (6, 8)}
MIN_YEAR = 2012
COMFORT_MIN_YEAR = 2018
DEFAULT_SURGE_CAP = Decimal("2.0")


@dataclass(slots=True)
class Vehicle:
    id: str
    driver_id: str
    plate: str
    make: str
    model: str
    year: int
    seats: int
    category: str


@dataclass(slots=True)
class Zone:
    id: str
    name: str
    center: Location
    radius_km: Decimal
    speed_kmh: Decimal
    airport: bool
    surge_cap: Decimal = DEFAULT_SURGE_CAP
