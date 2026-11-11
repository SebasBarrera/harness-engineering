"""V3: with 5 000 available drivers, quote_ride and request_ride each under 0.5 s."""

from __future__ import annotations

import time
from datetime import date

import pytest
from conftest import CENTER, PASSWORD, START, at
from rides import Platform

DRIVERS = 5_000


@pytest.fixture(scope="module")
def crowded() -> tuple[Platform, str, str]:
    mp = pytest.MonkeyPatch()
    import rides.core.security as security

    mp.setattr(security, "PBKDF2_ITERATIONS", 1)  # registration cost is not part of V3
    p = Platform(START)
    admin = p.create_admin("A", "a@x.com", "+573000000001", PASSWORD)
    p.add_zone(admin, "City", CENTER, 30, 30)
    for n in range(DRIVERS):
        driver = p.register_driver("D", f"d{n}@x.com", f"+5731{n:08d}", PASSWORD, "LIC123", date(2030, 1, 1))
        letters = "".join(chr(65 + (n // 26**k) % 26) for k in range(3))
        vehicle = p.add_vehicle(driver, f"{letters}{n % 1000:03d}", "Kia", "Rio", 2022, 4, "economy")
        p.approve(admin, driver)
        # every driver within 8 km of the pickup: the worst case for dispatch
        p.go_online(driver, at((n % 100) / 14 - 3.5, (n // 100) / 7 - 3.5), vehicle)
    rider = p.register_rider("R", "r@x.com", "+573000000002", PASSWORD)
    card = p.add_card(rider, "4242424242424242", 12, 2030, "123")
    mp.undo()
    return p, rider, card


def test_quote_and_request_are_fast(crowded: tuple[Platform, str, str]) -> None:
    p, rider, card = crowded
    started = time.perf_counter()
    quote = p.quote_ride(rider, CENTER, at(5), "economy")
    quoted = time.perf_counter() - started
    started = time.perf_counter()
    ride = p.request_ride(rider, quote["quote_id"], card)
    requested = time.perf_counter() - started
    assert quoted < 0.5, quoted
    assert requested < 0.5, requested
    assert p.ride(ride)["offered_to"] is not None
    assert quote["surge"] == 1  # 5 000 drivers of supply
