"""A1-A8, X1, X3, X7, X8."""

from __future__ import annotations

from datetime import date, datetime

import pytest
import rides.core.security as security
from conftest import CENTER, PASSWORD, START, World, phone
from rides import Platform


def test_ids_have_prefix_and_counter_and_failures_consume_nothing(p: Platform) -> None:
    admin = p.create_admin("A", "a@x.com", "+573001112222", PASSWORD)
    assert admin == "ADM-000001"
    with pytest.raises(ValueError):
        p.register_rider("R", "bad-email", "+573001112223", PASSWORD)
    assert p.register_rider("R", "r@x.com", "+573001112223", PASSWORD) == "RID-000001"
    assert p.register_rider("R2", "r2@x.com", "+573001112224", PASSWORD) == "RID-000002"
    assert p.register_driver("D", "d@x.com", "+573001112225", PASSWORD, "ABC123", date(2030, 1, 1)) == "DRV-000001"
    assert p.register_courier("C", "c@x.com", "+573001112226", PASSWORD, "bike") == "CUR-000001"
    assert p.register_restaurant("S", "s@x.com", "+573001112227", PASSWORD, CENTER) == "RST-000001"


@pytest.mark.parametrize(
    ("name", "email", "phone_number", "password"),
    [
        ("   ", "a@x.com", "+573001234567", PASSWORD),
        ("x" * 81, "a@x.com", "+573001234567", PASSWORD),
        ("Ana", "a@x", "+573001234567", PASSWORD),
        ("Ana", "a b@x.com", "+573001234567", PASSWORD),
        ("Ana", "a@x.com", "573001234567", PASSWORD),
        ("Ana", "a@x.com", "+073001234567", PASSWORD),
        ("Ana", "a@x.com", "+5730", PASSWORD),
        ("Ana", "a@x.com", " +573001234567", PASSWORD),
        ("Ana", "a@x.com", "+573001234567", "short1"),
        ("Ana", "a@x.com", "+573001234567", "onlyletters"),
        ("Ana", "a@x.com", "+573001234567", "1234567890"),
        ("Ana", "a@x.com", "+573001234567", "ñññññññññ1"),
    ],
)
def test_registration_validation(p: Platform, name: str, email: str, phone_number: str, password: str) -> None:
    with pytest.raises(ValueError):
        p.register_rider(name, email, phone_number, password)


def test_email_is_normalized_and_unique_across_accounts(p: Platform) -> None:
    rider = p.register_rider("  Ana  ", "  Ana@X.com ", "+573001234567", PASSWORD)
    account = p.get_account(rider)
    assert account == {
        "id": rider,
        "role": "rider",
        "name": "Ana",
        "email": "ana@x.com",
        "phone": "+57******4567",
        "status": "active",
    }
    with pytest.raises(ValueError):
        p.create_admin("B", "ana@x.com", "+573001234568", PASSWORD)
    with pytest.raises(ValueError):
        p.register_courier("B", "b@x.com", "+573001234567", PASSWORD, "car")


def test_passwords_are_hashed_with_pbkdf2(p: Platform) -> None:
    rider = p.register_rider("Ana", "ana@x.com", "+573001234567", PASSWORD)
    stored = p._state.credentials[rider]
    assert len(stored.salt) == 16
    assert PASSWORD not in repr(p._state.__dict__ if hasattr(p._state, "__dict__") else p._state)
    assert security.verify_password(PASSWORD, stored)


@pytest.mark.real_hash
def test_production_iterations(p: Platform) -> None:
    assert security.PBKDF2_ITERATIONS >= 100_000
    rider = p.register_rider("Ana", "ana@x.com", "+573001234567", PASSWORD)
    assert p._state.credentials[rider].iterations >= 100_000


def test_authenticate_and_lockout(w: World) -> None:
    p = w.p
    rider = p.register_rider("Ana", "ana@x.com", "+573001234567", PASSWORD)
    assert p.authenticate(" ANA@x.com ", PASSWORD) == rider
    with pytest.raises(PermissionError):
        p.authenticate("nobody@x.com", PASSWORD)
    for _ in range(5):
        with pytest.raises(PermissionError):
            p.authenticate("ana@x.com", "wrongpass12")
    with pytest.raises(PermissionError):
        p.authenticate("ana@x.com", PASSWORD)  # locked
    p.advance(minutes=14, seconds=59)
    with pytest.raises(PermissionError):
        p.authenticate("ana@x.com", "wrongpass12")  # does not count nor extend
    p.advance(seconds=1)
    for _ in range(4):
        with pytest.raises(PermissionError):
            p.authenticate("ana@x.com", "wrongpass12")
    assert p.authenticate("ana@x.com", PASSWORD) == rider  # count restarted at 0 after the lock
    for _ in range(4):
        with pytest.raises(PermissionError):
            p.authenticate("ana@x.com", "wrongpass12")
    assert p.authenticate("ana@x.com", PASSWORD) == rider  # success reset the count


def test_suspended_account_cannot_authenticate(w: World) -> None:
    p = w.p
    rider = p.register_rider("Ana", "ana@x.com", "+573001234567", PASSWORD)
    p.suspend(w.admin, rider, "fraud")
    with pytest.raises(PermissionError):
        p.authenticate("ana@x.com", PASSWORD)
    p.reactivate(w.admin, rider)
    assert p.authenticate("ana@x.com", PASSWORD) == rider


def test_status_flow_and_admin_rules(w: World) -> None:
    p = w.p
    courier = p.register_courier("C", "c@x.com", phone(), PASSWORD, "car")
    rider = w.rider()
    assert p.get_account(courier)["status"] == "pending"
    with pytest.raises(PermissionError):
        p.approve(rider, courier)
    with pytest.raises(KeyError):
        p.approve(rider, "CUR-999999")  # KeyError before PermissionError (X8)
    p.approve(w.admin, courier)
    assert p.get_account(courier)["status"] == "active"
    with pytest.raises(ValueError):
        p.approve(w.admin, courier)
    with pytest.raises(ValueError):
        p.suspend(w.admin, courier, "   ")
    with pytest.raises(ValueError):
        p.suspend(w.admin, w.admin, "me")
    with pytest.raises(PermissionError):
        p.suspend(courier, rider, "x")
    p.suspend(w.admin, courier, "late")
    assert p.get_account(courier)["status"] == "suspended"
    p.reactivate(w.admin, courier)
    assert p.get_account(courier)["status"] == "active"
    actions = [(entry["action"], entry["target"]) for entry in p.audit_log(w.admin)]
    assert ("approve", courier) in actions and ("suspend", courier) in actions and ("reactivate", courier) in actions


def test_suspend_forces_worker_offline(w: World) -> None:
    driver, vehicle = w.driver()
    assert w.p.worker_status(driver) == {"status": "available", "location": CENTER, "vehicle_id": vehicle}
    w.p.suspend(w.admin, driver, "complaints")
    assert w.p.worker_status(driver)["status"] == "offline"
    with pytest.raises(ValueError):
        w.p.go_online(driver, CENTER, vehicle)  # not active (X8)


def test_driver_license_and_rider_age(p: Platform) -> None:
    with pytest.raises(ValueError):
        p.register_driver("D", "d@x.com", phone(), PASSWORD, "abc123", date(2030, 1, 1))
    with pytest.raises(ValueError):
        p.register_driver("D", "d@x.com", phone(), PASSWORD, "AB12", date(2030, 1, 1))
    with pytest.raises(ValueError):
        p.register_driver("D", "d@x.com", phone(), PASSWORD, "ABC123", START.date())
    assert p.register_driver("D", "d@x.com", phone(), PASSWORD, "ABC123", date(2026, 3, 3))
    with pytest.raises(ValueError):
        p.register_rider("Kid", "k@x.com", phone(), PASSWORD, birth_date=date(2010, 3, 3))  # 15 years
    assert p.register_rider("Teen", "t@x.com", phone(), PASSWORD, birth_date=date(2010, 3, 2))  # 16 today


def test_courier_vehicle(p: Platform) -> None:
    with pytest.raises(ValueError):
        p.register_courier("C", "c@x.com", phone(), PASSWORD, "truck")
    with pytest.raises(ValueError):
        p.register_courier("C", "c@x.com", phone(), PASSWORD, "Bike")


def test_referral_codes(w: World) -> None:
    p = w.p
    first = w.rider()
    code = p.referral_code(first)
    assert code == "REF" + first[-6:]
    second = w.rider(referral=code)
    assert second
    with pytest.raises(ValueError):
        w.rider(referral="REF999999")
    with pytest.raises(ValueError):
        w.rider(referral=code.lower())  # X7: codes are not normalized


def test_unknown_account(p: Platform) -> None:
    with pytest.raises(KeyError):
        p.get_account("RID-000404")
    with pytest.raises(KeyError):
        p.notifications("RID-000404")


def test_start_must_be_datetime() -> None:
    with pytest.raises(ValueError):
        Platform(date(2026, 1, 1))  # type: ignore[arg-type]
    assert Platform(datetime(2026, 1, 1)).now() == datetime(2026, 1, 1)
