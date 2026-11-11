"""Shared fixtures and builders for the reference tests.

Password hashing is made cheap for speed (the iteration count is a module constant read at
call time); tests marked ``real_hash`` keep the production 100 000 iterations.
"""

from __future__ import annotations

import itertools
import math
from datetime import date, datetime
from decimal import Decimal

import pytest
import rides.core.security as security
from rides import Platform

START = datetime(2026, 3, 2, 12, 0)  # a Monday at noon
CENTER = (4.65, -74.05)
CARD = "4242424242424242"
DECLINED = "4000000000000002"
PASSWORD = "secret1234"

_seq = itertools.count(1)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_hash: keep the production PBKDF2 iteration count")


@pytest.fixture(autouse=True)
def cheap_hash(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("real_hash") is None:
        monkeypatch.setattr(security, "PBKDF2_ITERATIONS", 1_000)


def at(km_north: float = 0.0, km_east: float = 0.0, base: tuple[float, float] = CENTER) -> tuple[float, float]:
    """A point ``km_north`` / ``km_east`` kilometres away from ``base`` (approximately)."""
    lat = base[0] + km_north / 111.195
    lon = base[1] + km_east / (111.195 * math.cos(math.radians(base[0])))
    return (lat, lon)


def unique() -> int:
    return next(_seq)


def phone() -> str:
    return f"+57300{unique():07d}"


def plate() -> str:
    n = unique()
    letters = "".join(chr(65 + (n // 26**k) % 26) for k in range(3))
    return f"{letters}{n % 1000:03d}"


class World:
    """Builds actors on one platform with a single 20 km zone around ``CENTER``."""

    def __init__(self, platform: Platform) -> None:
        self.p = platform
        self.admin = platform.create_admin("Admin", f"admin{unique()}@x.com", phone(), PASSWORD)
        self.zone = platform.add_zone(self.admin, "City", CENTER, 20, 30)

    def rider(self, birth: date | None = date(1990, 1, 1), referral: str | None = None, funds: bool = True) -> str:
        rider = self.p.register_rider("Rider", f"rider{unique()}@x.com", phone(), PASSWORD, birth, referral)
        return rider

    def card(self, rider: str, number: str = CARD) -> str:
        return self.p.add_card(rider, number, 12, 2030, "123")

    def driver(
        self, location: tuple[float, float] | None = CENTER, category: str = "economy", approve: bool = True
    ) -> tuple[str, str]:
        driver = self.p.register_driver(
            "Driver", f"driver{unique()}@x.com", phone(), PASSWORD, "LIC12345", date(2030, 1, 1)
        )
        seats = {"moto": 1, "economy": 4, "comfort": 4, "xl": 6}[category]
        vehicle = self.p.add_vehicle(driver, plate(), "Make", "Model", 2022, seats, category)
        if approve:
            self.p.approve(self.admin, driver)
            if location is not None:
                self.p.go_online(driver, location, vehicle)
        return driver, vehicle

    def courier(self, location: tuple[float, float] | None = CENTER, vehicle: str = "moto") -> str:
        courier = self.p.register_courier("Courier", f"courier{unique()}@x.com", phone(), PASSWORD, vehicle)
        self.p.approve(self.admin, courier)
        if location is not None:
            self.p.go_online(courier, location)
        return courier

    def restaurant(self, location: tuple[float, float] = CENTER, hours: tuple[str, str] = ("00:00", "23:59")) -> str:
        restaurant = self.p.register_restaurant("Resto", f"resto{unique()}@x.com", phone(), PASSWORD, location)
        self.p.approve(self.admin, restaurant)
        for weekday in range(7):
            self.p.set_hours(restaurant, weekday, *hours)
        return restaurant

    def ride_to_assigned(self, rider: str, card: str, driver: str, km: float = 3.0) -> str:
        quote = self.p.quote_ride(rider, CENTER, at(km), "economy")
        ride = self.p.request_ride(rider, quote["quote_id"], card)
        assert self.p.ride(ride)["offered_to"] == driver
        self.p.accept_ride(driver, ride)
        return ride

    def complete(self, rider: str, card: str, driver: str, km: float = 3.0, minutes: int = 6) -> str:
        ride = self.ride_to_assigned(rider, card, driver, km)
        self.p.update_location(driver, CENTER)
        self.p.driver_arrived(driver, ride)
        self.p.start_ride(driver, ride)
        self.p.advance(minutes=minutes)
        self.p.update_location(driver, at(km))
        self.p.complete_ride(driver, ride, [CENTER, at(km)])
        self.p.update_location(driver, CENTER)
        return ride


@pytest.fixture
def p() -> Platform:
    return Platform(START)


@pytest.fixture
def w(p: Platform) -> World:
    return World(p)


def money(value: str) -> Decimal:
    return Decimal(value)
