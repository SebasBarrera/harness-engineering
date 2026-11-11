"""B1-B5, C1-C3, X4, X5."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from conftest import CENTER, PASSWORD, World, at, phone
from rides import Platform


def _driver(w: World) -> str:
    return w.p.register_driver("D", f"d{phone()}@x.com", phone(), PASSWORD, "LIC123", date(2030, 1, 1))


@pytest.mark.parametrize(
    ("plate", "make", "year", "seats", "category"),
    [
        ("AB1234", "Kia", 2020, 4, "economy"),
        ("abc123", "Kia", 2020, 4, "economy"),
        ("ABC123", "  ", 2020, 4, "economy"),
        ("ABC123", "Kia", 2011, 4, "economy"),
        ("ABC123", "Kia", 2028, 4, "economy"),
        ("ABC123", "Kia", "2020", 4, "economy"),
        ("ABC123", "Kia", True, 4, "economy"),
        ("ABC123", "Kia", 2020, 3, "economy"),
        ("ABC123", "Kia", 2020, 7, "comfort"),
        ("ABC123", "Kia", 2017, 4, "comfort"),
        ("ABC123", "Kia", 2020, 5, "xl"),
        ("ABC123", "Kia", 2020, 2, "moto"),
        ("ABC123", "Kia", 2020, 4, "luxury"),
    ],
)
def test_vehicle_validation(w: World, plate: str, make: str, year: object, seats: int, category: str) -> None:
    driver = _driver(w)
    with pytest.raises(ValueError):
        w.p.add_vehicle(driver, plate, make, "Model", year, seats, category)


def test_vehicle_plate_unique_and_ids(w: World) -> None:
    driver = _driver(w)
    assert w.p.add_vehicle(driver, "ABC123", "Kia", "Rio", 2027, 4, "economy") == "VEH-000001"
    assert w.p.add_vehicle(driver, "XYZ999", "Kia", "Rio", 2018, 6, "comfort") == "VEH-000002"
    with pytest.raises(ValueError):
        w.p.add_vehicle(driver, "ABC123", "Kia", "Rio", 2020, 8, "xl")
    rider = w.rider()
    with pytest.raises(PermissionError):
        w.p.add_vehicle(rider, "QQQ111", "Kia", "Rio", 2020, 1, "moto")


def test_approve_driver_requires_vehicle_and_license(w: World) -> None:
    p = w.p
    driver = p.register_driver("D", "d@x.com", phone(), PASSWORD, "LIC123", date(2026, 3, 3))
    with pytest.raises(ValueError):
        p.approve(w.admin, driver)
    vehicle = p.add_vehicle(driver, "ABC123", "Kia", "Rio", 2020, 4, "economy")
    p.advance(minutes=24 * 60)  # now 2026-03-03: the license expired today
    with pytest.raises(ValueError):
        p.approve(w.admin, driver)
    assert p.get_account(driver)["status"] == "pending"
    assert vehicle


def test_go_online_rules(w: World) -> None:
    p = w.p
    driver, vehicle = w.driver(location=None)
    other, other_vehicle = w.driver(location=None)
    pending = _driver(w)
    pending_vehicle = p.add_vehicle(pending, "PEN123", "Kia", "Rio", 2020, 4, "economy")
    assert p.worker_status(driver) == {"status": "offline", "location": None, "vehicle_id": None}
    with pytest.raises(ValueError):
        p.go_online(pending, CENTER, pending_vehicle)  # pending is not active
    with pytest.raises(KeyError):
        p.go_online(driver, CENTER, "VEH-999999")
    with pytest.raises(PermissionError):
        p.go_online(driver, CENTER, other_vehicle)
    with pytest.raises(ValueError):
        p.go_online(driver, CENTER)
    with pytest.raises(ValueError):
        p.go_online(driver, (91.0, 0.0), vehicle)
    p.go_online(driver, CENTER, vehicle)
    assert p.worker_status(driver) == {"status": "available", "location": CENTER, "vehicle_id": vehicle}
    courier = w.courier(location=None)
    with pytest.raises(ValueError):
        p.go_online(courier, CENTER, vehicle)
    p.go_online(courier, CENTER)
    assert p.worker_status(courier)["status"] == "available"
    rider = w.rider()
    with pytest.raises(PermissionError):
        p.go_online(rider, CENTER)
    with pytest.raises(ValueError):
        p.worker_status(rider)
    assert other


def test_license_expiry_blocks_going_online(w: World) -> None:
    p = w.p
    driver = p.register_driver("D", "d@x.com", phone(), PASSWORD, "LIC123", date(2026, 3, 4))
    vehicle = p.add_vehicle(driver, "ABC123", "Kia", "Rio", 2020, 4, "economy")
    p.approve(w.admin, driver)
    p.advance(minutes=2 * 24 * 60)
    with pytest.raises(ValueError):
        p.go_online(driver, CENTER, vehicle)


def test_offline_and_location(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    p.update_location(driver, at(1))
    assert p.worker_status(driver)["location"] == at(1)
    p.go_offline(driver)
    assert p.worker_status(driver) == {"status": "offline", "location": None, "vehicle_id": None}
    with pytest.raises(ValueError):
        p.update_location(driver, CENTER)


def test_busy_worker_cannot_go_offline(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    w.ride_to_assigned(rider, w.card(rider), driver)
    assert p.worker_status(driver)["status"] == "busy"
    with pytest.raises(ValueError):
        p.go_offline(driver)


def test_distance_and_eta(w: World) -> None:
    p = w.p
    assert p.distance_km(CENTER, CENTER) == Decimal("0.000")
    d = p.distance_km((0.0, 0.0), (0.0, 1.0))
    assert d == Decimal("111.195")
    assert isinstance(d, Decimal)
    # ETA inside the 30 km/h zone: 3 km -> 6 minutes; outside every zone also 30 km/h
    assert p.eta_minutes(CENTER, at(3)) == 6
    assert p.eta_minutes(CENTER, CENTER) == 1
    fast = p.add_zone(w.admin, "Fast", (10.0, 10.0), 10, 60)
    assert fast == "ZON-000002"
    assert p.eta_minutes((10.0, 10.0), at(3, base=(10.0, 10.0))) == 3
    assert p.eta_minutes((40.0, 40.0), at(3, base=(40.0, 40.0))) == 6


def test_zone_validation_and_admin_only(w: World) -> None:
    p = w.p
    rider = w.rider()
    with pytest.raises(PermissionError):
        p.add_zone(rider, "Z", CENTER, 5, 30)
    for radius, speed in ((0.4, 30), (51, 30), (5, 4), (5, 121)):
        with pytest.raises(ValueError):
            p.add_zone(w.admin, "Z", CENTER, radius, speed)
    with pytest.raises(ValueError):
        p.set_surge_cap(w.admin, w.zone, Decimal("3.1"))
    with pytest.raises(KeyError):
        p.set_surge_cap(w.admin, "ZON-000099", Decimal("2"))
    with pytest.raises(PermissionError):
        p.set_surge_cap(rider, w.zone, Decimal("2"))
    p.set_surge_cap(w.admin, w.zone, Decimal("1.0"))


def test_advance_validation(p: Platform) -> None:
    with pytest.raises(ValueError):
        p.advance(minutes=-1)
    before = p.now()
    p.advance(minutes=1, seconds=30)
    assert (p.now() - before).total_seconds() == 90


def test_returned_lists_are_copies(w: World) -> None:
    p = w.p
    rider = w.rider()
    methods = p.payment_methods(rider)
    methods.append({"id": "x"})
    methods[0]["id"] = "changed"
    assert p.payment_methods(rider)[0]["id"].startswith("WAL-")
    account = p.get_account(rider)
    account["name"] = "Other"
    assert p.get_account(rider)["name"] == "Rider"
