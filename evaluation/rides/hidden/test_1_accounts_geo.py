"""Hidden acceptance tests: general rules (X), accounts and security (A),
drivers, couriers and vehicles (B) and geography (C) of SPEC.md.

Only the public interface of INTERFACE.md is used (``from rides import Platform``).
Each test checks one rule; its name starts with ``test_<rule id>_``.

Points the spec still leaves open, deliberately left untested:
- A3: the exception type when a suspended account authenticates ("cannot authenticate";
  PermissionError of A3/X3 or ValueError of X8), so only the refusal is checked.
- A1: whether ``\\d`` in the phone pattern admits non-ASCII digits.
- B1: whether ``seats`` must be an int (B1 calls only the year "an int").
- B3: the vehicle_id reported for a courier; the exception of a courier that gives an
  unknown vehicle id.
- C1: a tie exactly at the half (half-up versus half-even) is not representable with
  float inputs, so only non-tie roundings are checked.
"""

from __future__ import annotations

import copy
import itertools
import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from rides import Platform

START = datetime(2026, 3, 2, 12, 0)  # a Monday
PASSWORD = "secretpass1"
CARD = "4242424242424242"
CENTER = (4.65, -74.05)
KM_PER_DEG = 6371.0 * math.pi / 180
LICENSE = "AB12345"
LICENSE_EXPIRES = date(2030, 1, 1)

_seq = itertools.count(1)


# --------------------------------------------------------------------------- helpers


def new_platform(start: datetime = START, **kwargs) -> Platform:
    return Platform(start, **kwargs)


def contact() -> tuple[str, str]:
    n = next(_seq)
    return f"user{n}@example.com", f"+1555{n:07d}"


def make_admin(p: Platform, name: str = "Root Admin") -> str:
    email, phone = contact()
    return p.create_admin(name, email, phone, PASSWORD)


def make_rider(p: Platform, **kwargs) -> str:
    email, phone = contact()
    return p.register_rider("Rider One", email, phone, PASSWORD, **kwargs)


def make_driver(p: Platform, license_number: str = LICENSE, expires: date = LICENSE_EXPIRES) -> str:
    email, phone = contact()
    return p.register_driver("Driver One", email, phone, PASSWORD, license_number, expires)


def make_courier(p: Platform, vehicle: str = "bike") -> str:
    email, phone = contact()
    return p.register_courier("Courier One", email, phone, PASSWORD, vehicle)


def make_restaurant(p: Platform, location=CENTER) -> str:
    email, phone = contact()
    return p.register_restaurant("Casa Arepa", email, phone, PASSWORD, location)


def register_role(p: Platform, role: str, name: str, email: str, phone: str, password: str) -> str:
    if role == "admin":
        return p.create_admin(name, email, phone, password)
    if role == "rider":
        return p.register_rider(name, email, phone, password)
    if role == "driver":
        return p.register_driver(name, email, phone, password, LICENSE, LICENSE_EXPIRES)
    if role == "courier":
        return p.register_courier(name, email, phone, password, "bike")
    if role == "restaurant":
        return p.register_restaurant(name, email, phone, password, CENTER)
    raise AssertionError(role)


def make_active_driver(p: Platform, adm: str, plate: str = "ABC123", category: str = "economy",
                       seats: int = 4, expires: date = LICENSE_EXPIRES) -> tuple[str, str]:
    drv = make_driver(p, expires=expires)
    veh = p.add_vehicle(drv, plate, "Toyota", "Corolla", 2020, seats, category)
    p.approve(adm, drv)
    return drv, veh


def make_active_courier(p: Platform, adm: str, vehicle: str = "bike") -> str:
    cur = make_courier(p, vehicle)
    p.approve(adm, cur)
    return cur


def fail_login(p: Platform, times: int, email: str = "ana@example.com") -> None:
    for _ in range(times):
        with pytest.raises(PermissionError):
            p.authenticate(email, "wrongpass99")


def assigned_ride_world():
    """A driver with an accepted ride (status driver_assigned, driver busy)."""
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Bogota", CENTER, 20, 30)
    drv, veh = make_active_driver(p, adm)
    p.go_online(drv, (4.651, -74.05), veh)
    rid = make_rider(p)
    card = p.add_card(rid, CARD, 12, 2030, "123")
    quote = p.quote_ride(rid, CENTER, (4.70, -74.05), "economy")
    ride_id = p.request_ride(rid, quote["quote_id"], card)
    assert p.ride(ride_id)["offered_to"] == drv
    p.accept_ride(drv, ride_id)
    return p, drv, veh, ride_id


def north(point, km: float):
    """The point ``km`` kilometres north of ``point`` on the same meridian."""
    return (point[0] + km / KM_PER_DEG, point[1])


def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def expected_distance(a, b) -> Decimal:
    raw = haversine_km(a, b)
    scaled = raw * 1000
    # Test-design guard: the chosen points must not sit on a rounding tie.
    assert abs((scaled - math.floor(scaled)) - 0.5) > 1e-6
    return Decimal(repr(raw)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def expected_eta(a, b, speed: int) -> int:
    d = expected_distance(a, b)
    return max(1, math.ceil(Fraction(d) / Fraction(speed) * 60))


# --------------------------------------------------------------------------- X1


def test_x1_first_identifier_of_each_prefix_is_000001():
    p = new_platform()
    adm = make_admin(p)
    rid = make_rider(p)
    drv = make_driver(p)
    cur = make_courier(p)
    rst = make_restaurant(p)
    veh = p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    zon = p.add_zone(adm, "Bogota", CENTER, 10, 30)
    assert (adm, rid, drv, cur, rst, veh, zon) == (
        "ADM-000001", "RID-000001", "DRV-000001", "CUR-000001", "RST-000001", "VEH-000001", "ZON-000001",
    )


def test_x1_counters_are_per_prefix_and_increment():
    p = new_platform()
    adm = make_admin(p)
    r1 = make_rider(p)
    r2 = make_rider(p)
    z1 = p.add_zone(adm, "A", CENTER, 5, 30)
    z2 = p.add_zone(adm, "B", (6.2442, -75.5812), 5, 30)
    assert (r1, r2, z1, z2) == ("RID-000001", "RID-000002", "ZON-000001", "ZON-000002")


def test_x1_failed_registration_does_not_consume_identifier():
    p = new_platform()
    with pytest.raises(ValueError):
        p.register_rider("Ana", "not-an-email", "+573001234567", PASSWORD)
    with pytest.raises(ValueError):
        p.register_driver("Bob", "bob@example.com", "+573001234568", PASSWORD, "AB-1", LICENSE_EXPIRES)
    assert make_rider(p) == "RID-000001"
    assert make_driver(p) == "DRV-000001"


def test_x1_failed_add_vehicle_does_not_consume_identifier():
    p = new_platform()
    drv = make_driver(p)
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 9, "economy")
    assert p.add_vehicle(drv, "XYZ789", "Toyota", "Corolla", 2020, 4, "economy") == "VEH-000001"


def test_x1_failed_add_zone_does_not_consume_identifier():
    p = new_platform()
    adm = make_admin(p)
    with pytest.raises(ValueError):
        p.add_zone(adm, "Too small", CENTER, 0.4, 30)
    with pytest.raises(ValueError):
        p.add_zone(adm, "Too fast", CENTER, 5, 200)
    assert p.add_zone(adm, "Ok", CENTER, 5, 30) == "ZON-000001"


# --------------------------------------------------------------------------- X2


def test_x2_float_money_argument_raises_value_error():
    p = new_platform()
    rid = make_rider(p)
    card = p.add_card(rid, CARD, 12, 2030, "123")
    with pytest.raises(ValueError):
        p.top_up_wallet(rid, card, 20.0)
    assert p.wallet_balance(rid) == Decimal("0.00")


def test_x2_int_str_and_decimal_money_arguments_accepted():
    p = new_platform()
    rid = make_rider(p)
    card = p.add_card(rid, CARD, 12, 2030, "123")
    p.top_up_wallet(rid, card, 20)
    p.top_up_wallet(rid, card, "15.50")
    p.top_up_wallet(rid, card, Decimal("5"))
    balance = p.wallet_balance(rid)
    assert isinstance(balance, Decimal)
    assert balance == Decimal("40.50")
    assert balance.as_tuple().exponent == -2


def test_x2_money_is_returned_with_two_places():
    p = new_platform()
    adm = make_admin(p)
    rst = make_restaurant(p)
    p.approve(adm, rst)
    p.add_menu_item(rst, "Arepa", 12, "food", 10)
    price = p.menu(rst)[0]["price"]
    assert isinstance(price, Decimal)
    assert price == Decimal("12.00")
    assert price.as_tuple().exponent == -2


def test_x2_float_price_rejected_for_menu_item():
    p = new_platform()
    adm = make_admin(p)
    rst = make_restaurant(p)
    p.approve(adm, rst)
    with pytest.raises(ValueError):
        p.add_menu_item(rst, "Arepa", 12.5, "food", 10)
    assert p.menu(rst) == []


# --------------------------------------------------------------------------- X3


def test_x3_unknown_identifier_raises_key_error():
    p = new_platform()
    adm = make_admin(p)
    with pytest.raises(KeyError):
        p.get_account("RID-999999")
    with pytest.raises(KeyError):
        p.worker_status("DRV-999999")
    with pytest.raises(KeyError):
        p.approve(adm, "CUR-999999")
    with pytest.raises(KeyError):
        p.add_vehicle("DRV-999999", "ABC123", "Toyota", "Corolla", 2020, 4, "economy")


def test_x3_failed_registration_reserves_neither_email_nor_phone():
    p = new_platform()
    with pytest.raises(ValueError):
        p.register_rider("Ana", "ana@example.com", "+573001234567", "short1")
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert p.get_account(rid)["email"] == "ana@example.com"


def test_x3_failed_add_vehicle_leaves_plate_free():
    p = new_platform()
    drv = make_driver(p)
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2015, 4, "comfort")
    veh = p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2019, 4, "comfort")
    assert veh.startswith("VEH-")


def test_x3_failed_suspend_changes_nothing():
    p = new_platform()
    adm = make_admin(p)
    cur = make_active_courier(p, adm)
    log_before = p.audit_log(adm)
    with pytest.raises(ValueError):
        p.suspend(adm, cur, "")
    assert p.get_account(cur)["status"] == "active"
    assert p.audit_log(adm) == log_before


def test_x3_failed_go_online_leaves_worker_offline():
    p = new_platform()
    adm = make_admin(p)
    drv, _veh = make_active_driver(p, adm)
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER)
    status = p.worker_status(drv)
    assert status["status"] == "offline"
    assert status["location"] is None


# --------------------------------------------------------------------------- X4


def test_x4_now_returns_start():
    p = new_platform()
    assert p.now() == START


def test_x4_advance_moves_the_virtual_clock():
    p = new_platform()
    p.advance(minutes=5, seconds=30)
    assert p.now() == START + timedelta(minutes=5, seconds=30)
    p.advance(seconds=45)
    assert p.now() == START + timedelta(minutes=6, seconds=15)
    p.advance(minutes=60 * 24)
    assert p.now() == START + timedelta(days=1, minutes=6, seconds=15)
    p.advance()
    assert p.now() == START + timedelta(days=1, minutes=6, seconds=15)


def test_x4_dates_are_judged_against_virtual_clock_in_the_past():
    # Real today is later than 2021-01-01; only the virtual clock makes this license valid.
    p = new_platform(datetime(2020, 1, 6, 9, 0))
    drv = make_driver(p, expires=date(2021, 1, 1))
    assert drv == "DRV-000001"


def test_x4_dates_are_judged_against_virtual_clock_in_the_future():
    p = new_platform(datetime(2035, 1, 1, 9, 0))
    with pytest.raises(ValueError):
        make_driver(p, expires=date(2034, 12, 31))
    drv = make_driver(p, expires=date(2036, 1, 1))
    # current year + 1 is 2036 on the virtual clock
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2036, 4, "economy").startswith("VEH-")


# --------------------------------------------------------------------------- X5


def test_x5_payment_methods_returns_fresh_list():
    p = new_platform()
    rid = make_rider(p)
    p.add_card(rid, CARD, 12, 2030, "123")
    first = p.payment_methods(rid)
    snapshot = copy.deepcopy(first)
    first[0]["id"] = "HACKED"
    first.append({"id": "PM-999999"})
    first.reverse()
    assert p.payment_methods(rid) == snapshot


def test_x5_audit_log_returns_fresh_list():
    p = new_platform()
    adm = make_admin(p)
    cur = make_courier(p)
    p.approve(adm, cur)
    first = p.audit_log(adm)
    assert first
    snapshot = copy.deepcopy(first)
    first[0]["action"] = "tampered"
    first.clear()
    assert p.audit_log(adm) == snapshot


def test_x5_returned_dicts_are_fresh():
    p, drv, _veh, ride_id = assigned_ride_world()
    account = p.get_account(drv)
    account_snapshot = copy.deepcopy(account)
    account["status"] = "suspended"
    account["name"] = "Hacked"
    assert p.get_account(drv) == account_snapshot
    status = p.worker_status(drv)
    status_snapshot = copy.deepcopy(status)
    status["status"] = "offline"
    status["vehicle_id"] = None
    assert p.worker_status(drv) == status_snapshot
    ride = p.ride(ride_id)
    ride_snapshot = copy.deepcopy(ride)
    ride["status"] = "cancelled"
    ride["driver_id"] = None
    assert p.ride(ride_id) == ride_snapshot


# --------------------------------------------------------------------------- X6


def test_x6_distance_limit_inclusive_after_rounding_to_three_places():
    # C2: a location belongs to a zone whose center is at most radius_km away; X6: the
    # distance is compared after rounding to 3 places, ends included.
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Five km", CENTER, 5, 60)
    on_edge = north(CENTER, 5.0004)  # raw 5.0004 km, rounds to 5.000
    just_out = north(CENTER, 5.0006)  # raw 5.0006 km, rounds to 5.001
    assert expected_distance(CENTER, on_edge) == Decimal("5.000")
    assert expected_distance(CENTER, just_out) == Decimal("5.001")
    target = (4.5, -74.05)
    assert p.eta_minutes(on_edge, target) == expected_eta(on_edge, target, 60) == 22
    assert p.eta_minutes(just_out, target) == expected_eta(just_out, target, 30) == 44


# --------------------------------------------------------------------------- X7


def test_x7_money_with_more_than_two_decimals_rejected():
    p = new_platform()
    rid = make_rider(p)
    card = p.add_card(rid, CARD, 12, 2030, "123")
    for amount in ["10.005", Decimal("10.001"), "5.001"]:
        with pytest.raises(ValueError):
            p.top_up_wallet(rid, card, amount)
    assert p.wallet_balance(rid) == Decimal("0.00")
    adm = make_admin(p)
    rst = make_restaurant(p)
    p.approve(adm, rst)
    with pytest.raises(ValueError):
        p.add_menu_item(rst, "Arepa", "12.345", "food", 10)
    assert p.menu(rst) == []


def test_x7_year_must_be_an_int():
    p = new_platform()
    drv = make_driver(p)
    for year in [2020.0, "2020", Decimal("2020")]:
        with pytest.raises(ValueError):
            p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", year, 4, "economy")
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy") == "VEH-000001"


def test_x7_identifiers_are_not_normalised():
    p = new_platform()
    rid = make_rider(p)
    for ident in [rid.lower(), f" {rid}", f"{rid} ", "RID-1"]:
        with pytest.raises(KeyError):
            p.get_account(ident)


def test_x7_plates_and_license_numbers_are_not_normalised():
    p = new_platform()
    for number in ["ab12345", " AB12345", "AB12345 "]:
        with pytest.raises(ValueError):
            p.register_driver("Bob", "bob@example.com", "+573001234567", PASSWORD, number, LICENSE_EXPIRES)
    drv = p.register_driver("Bob", "bob@example.com", "+573001234567", PASSWORD, LICENSE, LICENSE_EXPIRES)
    for plate in ["abc123", " ABC123", "ABC123 ", "Abc123"]:
        with pytest.raises(ValueError):
            p.add_vehicle(drv, plate, "Toyota", "Corolla", 2020, 4, "economy")
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy") == "VEH-000001"


def test_x7_referral_codes_are_not_normalised():
    p = new_platform()
    r1 = make_rider(p)
    assert p.referral_code(r1) == "REF000001"
    for code in ["ref000001", " REF000001", "REF000001 "]:
        with pytest.raises(ValueError):
            p.register_rider("Bea", "bea@example.com", "+573001234568", PASSWORD, referral_code=code)
    assert make_rider(p) == "RID-000002"


def test_x7_texts_made_only_of_spaces_are_empty():
    p = new_platform()
    drv = make_driver(p)
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "   ", "Corolla", 2020, 4, "economy")
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "Toyota", "    ", 2020, 4, "economy")
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy") == "VEH-000001"


# --------------------------------------------------------------------------- X8


def test_x8_unknown_identifier_reported_before_permission_problem():
    p = new_platform()
    make_admin(p)
    rid = make_rider(p)
    with pytest.raises(KeyError):
        p.approve(rid, "CUR-999999")
    with pytest.raises(KeyError):
        p.suspend(rid, "RID-999999", "")
    with pytest.raises(KeyError):
        p.reactivate(rid, "DRV-999999")


def test_x8_unknown_actor_raises_key_error():
    p = new_platform()
    make_admin(p)
    cur = make_courier(p)
    with pytest.raises(KeyError):
        p.approve("ADM-999999", cur)
    with pytest.raises(KeyError):
        p.add_zone("ADM-999999", "Bogota", CENTER, 10, 30)
    assert p.get_account(cur)["status"] == "pending"


def test_x8_permission_problem_reported_before_invalid_value():
    p = new_platform()
    adm = make_admin(p)
    rid = make_rider(p)
    victim = make_rider(p)
    with pytest.raises(PermissionError):
        p.suspend(rid, victim, "   ")
    with pytest.raises(PermissionError):
        p.suspend(rid, adm, "")
    with pytest.raises(PermissionError):
        p.add_zone(rid, "Bad", CENTER, 0.1, 500)
    assert p.get_account(victim)["status"] == "active"
    assert p.add_zone(adm, "Ok", CENTER, 5, 30) == "ZON-000001"


def test_x8_go_online_errors_in_precedence_order():
    p = new_platform()
    adm = make_admin(p)
    _d1, v1 = make_active_driver(p, adm, plate="ABC123")
    pending = make_driver(p)  # not active: a ValueError on its own
    with pytest.raises(KeyError):
        p.go_online("DRV-999999", CENTER, v1)
    with pytest.raises(KeyError):
        p.go_online(pending, CENTER, "VEH-999999")
    with pytest.raises(PermissionError):
        p.go_online(pending, CENTER, v1)
    assert p.worker_status(pending)["status"] == "offline"


# --------------------------------------------------------------------------- A1


@pytest.mark.parametrize("role", ["admin", "rider", "driver", "courier", "restaurant"])
def test_a1_name_and_email_are_normalised(role):
    p = new_platform()
    acc = register_role(p, role, "  Ana Maria  ", "  Ana.Maria@Example.COM ", "+573001234567", PASSWORD)
    account = p.get_account(acc)
    assert account["name"] == "Ana Maria"
    assert account["email"] == "ana.maria@example.com"


@pytest.mark.parametrize("role", ["admin", "rider", "driver", "courier", "restaurant"])
def test_a1_invalid_name_rejected(role):
    p = new_platform()
    for name in ["", "   ", "x" * 81, "  " + "y" * 81 + "  "]:
        with pytest.raises(ValueError):
            register_role(p, role, name, "ana@example.com", "+573001234567", PASSWORD)


def test_a1_name_of_80_characters_after_stripping_accepted():
    p = new_platform()
    rid = p.register_rider("  " + "n" * 80 + "  ", "ana@example.com", "+573001234567", PASSWORD)
    assert p.get_account(rid)["name"] == "n" * 80


@pytest.mark.parametrize("role", ["admin", "rider", "driver", "courier", "restaurant"])
def test_a1_invalid_email_rejected(role):
    p = new_platform()
    for email in ["", "ana", "ana@example", "@example.com", "ana@", "ana@@example.com",
                  "ana maria@example.com", "ana@exa mple.com", "ana@example.", "ana@.com"]:
        with pytest.raises(ValueError):
            register_role(p, role, "Ana", email, "+573001234567", PASSWORD)


def test_a1_minimal_email_accepted():
    p = new_platform()
    rid = p.register_rider("Ana", "a@b.c", "+573001234567", PASSWORD)
    assert p.get_account(rid)["email"] == "a@b.c"


def test_a1_email_unique_across_all_accounts_case_insensitive():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    with pytest.raises(ValueError):
        p.register_courier("Ana", " ANA@example.com", "+573001234568", PASSWORD, "bike")
    with pytest.raises(ValueError):
        p.create_admin("Ana", "ana@EXAMPLE.com", "+573001234569", PASSWORD)


@pytest.mark.parametrize("role", ["admin", "rider", "driver", "courier", "restaurant"])
def test_a1_invalid_phone_rejected(role):
    p = new_platform()
    for phone in ["", "573001234567", "+0573001234567", "+1234567", "+1234567890123456",
                  "+57 3001234567", "+57300123456a", "++573001234567"]:
        with pytest.raises(ValueError):
            register_role(p, role, "Ana", "ana@example.com", phone, PASSWORD)


def test_a1_phone_is_not_stripped():
    p = new_platform()
    for phone in [" +573001234567", "+573001234567 ", " +573001234567 "]:
        with pytest.raises(ValueError):
            p.register_rider("Ana", "ana@example.com", phone, PASSWORD)
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert rid == "RID-000001"


def test_a1_password_needs_an_ascii_letter_and_an_ascii_digit():
    p = new_platform()
    # Only non-ASCII letters, or only non-ASCII digits, do not count.
    for password in ["ñññññññññ1", "ÁÉÍÓÚáéíóú7", "abcdefghi١", "abcdefghij٣٤", "ａｂｃｄｅｆｇｈｉｊ1"]:
        with pytest.raises(ValueError):
            p.register_rider("Ana", "ana@example.com", "+573001234567", password)
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", "ñññññññññ1a")
    assert p.authenticate("ana@example.com", "ñññññññññ1a") == rid


def test_a1_phone_length_boundaries_accepted():
    p = new_platform()
    r1 = p.register_rider("Ana", "ana@example.com", "+12345678", PASSWORD)
    r2 = p.register_rider("Bea", "bea@example.com", "+123456789012345", PASSWORD)
    assert r1 == "RID-000001"
    assert r2 == "RID-000002"


def test_a1_phone_unique_across_all_accounts():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    with pytest.raises(ValueError):
        p.register_restaurant("Casa", "casa@example.com", "+573001234567", PASSWORD, CENTER)


@pytest.mark.parametrize("role", ["admin", "rider", "driver", "courier", "restaurant"])
def test_a1_weak_password_rejected(role):
    p = new_platform()
    for password in ["abcdefgh1", "abcdefghij", "1234567890", "", "          "]:
        with pytest.raises(ValueError):
            register_role(p, role, "Ana", "ana@example.com", "+573001234567", password)


def test_a1_password_of_ten_characters_with_letter_and_digit_accepted():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", "123456789a")
    assert p.authenticate("ana@example.com", "123456789a") == rid


# --------------------------------------------------------------------------- A2


def test_a2_plain_password_not_returned_by_get_account():
    p = new_platform()
    plain_text = "Zq9xUniqueSecret77"
    acc = p.register_driver("Ana", "ana@example.com", "+573001234567", plain_text, LICENSE, LICENSE_EXPIRES)
    account = p.get_account(acc)
    assert plain_text not in repr(account)
    assert all(plain_text not in str(value) for value in account.values())


def test_a2_plain_password_not_written_to_database(tmp_path: Path):
    db = tmp_path / "rides.db"
    p = new_platform(db_path=str(db))
    plain_text = "Zq9xUniqueSecret77"
    p.register_rider("Ana", "ana@example.com", "+573001234567", plain_text)
    p.save()
    files = [f for f in tmp_path.iterdir() if f.is_file()]
    assert db.exists()
    for f in files:
        data = f.read_bytes()
        assert plain_text.encode("utf-8") not in data
        assert plain_text.encode("utf-16-le") not in data


# --------------------------------------------------------------------------- A3


def test_a3_authenticate_returns_account_id():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert p.authenticate("ana@example.com", PASSWORD) == rid


def test_a3_email_matched_case_insensitively():
    p = new_platform()
    adm = p.create_admin("Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert p.authenticate("ANA@Example.Com", PASSWORD) == adm


def test_a3_email_is_stripped():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert p.authenticate("  Ana@Example.com  ", PASSWORD) == rid


def test_a3_wrong_password_or_unknown_email_raises_permission_error():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", "wrongpass99")
    with pytest.raises(PermissionError):
        p.authenticate("nobody@example.com", PASSWORD)


def test_a3_lock_after_five_failures():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    for _ in range(5):
        with pytest.raises(PermissionError):
            p.authenticate("ana@example.com", "wrongpass99")
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", PASSWORD)


def test_a3_four_failures_do_not_lock():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    for _ in range(4):
        with pytest.raises(PermissionError):
            p.authenticate("ana@example.com", "wrongpass99")
    assert p.authenticate("ana@example.com", PASSWORD) == rid


def test_a3_lock_still_active_before_fifteen_minutes():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    for _ in range(5):
        with pytest.raises(PermissionError):
            p.authenticate("ana@example.com", "wrongpass99")
    p.advance(minutes=14, seconds=59)
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", PASSWORD)


def test_a3_lock_ends_at_exactly_fifteen_minutes():
    # X6: "locked for 15 minutes" holds while less than 15 minutes have elapsed.
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    fail_login(p, 5)
    p.advance(minutes=14, seconds=59)
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", PASSWORD)
    p.advance(seconds=1)
    assert p.authenticate("ana@example.com", PASSWORD) == rid


def test_a3_attempts_during_lock_neither_count_nor_extend_it():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    fail_login(p, 5)
    p.advance(minutes=10)
    fail_login(p, 5)  # would lock again if they counted
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", PASSWORD)
    p.advance(minutes=5)  # 15 minutes after the fifth failure
    assert p.authenticate("ana@example.com", PASSWORD) == rid


def test_a3_count_starts_again_at_zero_after_lock():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    fail_login(p, 5)
    p.advance(minutes=15)
    fail_login(p, 4)
    assert p.authenticate("ana@example.com", PASSWORD) == rid


def test_a3_five_new_failures_after_lock_lock_again():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    fail_login(p, 5)
    p.advance(minutes=15)
    fail_login(p, 5)
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", PASSWORD)
    p.advance(minutes=14, seconds=59)
    with pytest.raises(PermissionError):
        p.authenticate("ana@example.com", PASSWORD)


def test_a3_success_resets_failure_count():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    for _ in range(4):
        with pytest.raises(PermissionError):
            p.authenticate("ana@example.com", "wrongpass99")
    assert p.authenticate("ana@example.com", PASSWORD) == rid
    for _ in range(4):
        with pytest.raises(PermissionError):
            p.authenticate("ana@example.com", "wrongpass99")
    assert p.authenticate("ana@example.com", PASSWORD) == rid


def test_a3_lock_is_per_account():
    p = new_platform()
    p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    bea = p.register_rider("Bea", "bea@example.com", "+573001234568", PASSWORD)
    for _ in range(5):
        with pytest.raises(PermissionError):
            p.authenticate("ana@example.com", "wrongpass99")
    assert p.authenticate("bea@example.com", PASSWORD) == bea


def test_a3_suspended_account_cannot_authenticate():
    # The exception type is not fixed by the spec (see the module docstring); only the refusal is.
    p = new_platform()
    adm = make_admin(p)
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    p.suspend(adm, rid, "fraud")
    with pytest.raises((PermissionError, ValueError)):
        p.authenticate("ana@example.com", PASSWORD)
    p.reactivate(adm, rid)
    assert p.authenticate("ana@example.com", PASSWORD) == rid


# --------------------------------------------------------------------------- A4


def test_a4_get_account_fields():
    p = new_platform()
    rid = p.register_rider("Ana Maria", "Ana@Example.com", "+573001234567", PASSWORD)
    account = p.get_account(rid)
    assert account["id"] == rid
    assert account["role"] == "rider"
    assert account["name"] == "Ana Maria"
    assert account["email"] == "ana@example.com"
    assert account["phone"] == "+57******4567"
    assert account["status"] == "active"
    assert all("+573001234567" not in str(value) for value in account.values())


@pytest.mark.parametrize(
    ("phone", "masked"),
    [
        ("+573001234567", "+57******4567"),
        ("+12345678", "+12**5678"),
        ("+123456789012345", "+12*********2345"),
        ("+4915112345678", "+49*******5678"),
    ],
)
def test_a4_phone_is_masked(phone, masked):
    p = new_platform()
    cur = p.register_courier("Ana", "ana@example.com", phone, PASSWORD, "moto")
    assert p.get_account(cur)["phone"] == masked


@pytest.mark.parametrize(
    ("role", "prefix"),
    [("admin", "ADM-"), ("rider", "RID-"), ("driver", "DRV-"), ("courier", "CUR-"), ("restaurant", "RST-")],
)
def test_a4_role_reported_for_each_kind_of_account(role, prefix):
    p = new_platform()
    acc = register_role(p, role, "Ana", "ana@example.com", "+573001234567", PASSWORD)
    account = p.get_account(acc)
    assert acc.startswith(prefix)
    assert account["role"] == role
    assert account["phone"] == "+57******4567"


# --------------------------------------------------------------------------- A5


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", "active"), ("rider", "active"), ("driver", "pending"), ("courier", "pending"),
     ("restaurant", "pending")],
)
def test_a5_initial_status(role, status):
    p = new_platform()
    acc = register_role(p, role, "Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert p.get_account(acc)["status"] == status


def test_a5_approve_activates_courier_and_restaurant():
    p = new_platform()
    adm = make_admin(p)
    cur = make_courier(p)
    rst = make_restaurant(p)
    p.approve(adm, cur)
    p.approve(adm, rst)
    assert p.get_account(cur)["status"] == "active"
    assert p.get_account(rst)["status"] == "active"


def test_a5_suspend_and_reactivate():
    p = new_platform()
    adm = make_admin(p)
    rid = make_rider(p)
    p.suspend(adm, rid, "abusive behaviour")
    assert p.get_account(rid)["status"] == "suspended"
    p.reactivate(adm, rid)
    assert p.get_account(rid)["status"] == "active"


def test_a5_suspend_requires_reason():
    p = new_platform()
    adm = make_admin(p)
    rid = make_rider(p)
    with pytest.raises(ValueError):
        p.suspend(adm, rid, "")
    assert p.get_account(rid)["status"] == "active"


def test_a5_reason_made_only_of_spaces_is_empty():
    p = new_platform()
    adm = make_admin(p)
    rid = make_rider(p)
    for reason in [" ", "     "]:
        with pytest.raises(ValueError):
            p.suspend(adm, rid, reason)
    assert p.get_account(rid)["status"] == "active"
    p.suspend(adm, rid, "  fraud  ")
    assert p.get_account(rid)["status"] == "suspended"


def test_a5_suspend_forces_worker_offline():
    p = new_platform()
    adm = make_admin(p)
    cur = make_active_courier(p, adm)
    p.go_online(cur, CENTER)
    assert p.worker_status(cur)["status"] == "available"
    p.suspend(adm, cur, "complaints")
    status = p.worker_status(cur)
    assert status["status"] == "offline"
    assert status["location"] is None


def test_a5_only_admins_approve_suspend_reactivate():
    p = new_platform()
    adm = make_admin(p)
    rid = make_rider(p)
    cur = make_courier(p)
    victim = make_rider(p)
    with pytest.raises(PermissionError):
        p.approve(rid, cur)
    assert p.get_account(cur)["status"] == "pending"
    p.approve(adm, cur)
    with pytest.raises(PermissionError):
        p.suspend(cur, victim, "spam")
    assert p.get_account(victim)["status"] == "active"
    p.suspend(adm, victim, "spam")
    with pytest.raises(PermissionError):
        p.reactivate(rid, victim)
    assert p.get_account(victim)["status"] == "suspended"


def test_a5_admin_cannot_suspend_itself():
    p = new_platform()
    adm = make_admin(p)
    with pytest.raises(ValueError):
        p.suspend(adm, adm, "testing")
    assert p.get_account(adm)["status"] == "active"
    assert p.audit_log(adm) == []


def test_a5_admin_may_suspend_another_admin():
    p = new_platform()
    adm = make_admin(p)
    other = make_admin(p, "Second Admin")
    p.suspend(adm, other, "left the company")
    assert p.get_account(other)["status"] == "suspended"


# --------------------------------------------------------------------------- A6


def test_a6_license_number_length_boundaries_accepted():
    p = new_platform()
    d1 = make_driver(p, license_number="ABC123")
    d2 = make_driver(p, license_number="A1B2C3D4E5F6")
    d3 = make_driver(p, license_number="123456")
    assert (d1, d2, d3) == ("DRV-000001", "DRV-000002", "DRV-000003")


def test_a6_invalid_license_number_rejected():
    p = new_platform()
    for number in ["AB123", "A1B2C3D4E5F6G", "AB-1234", "AB 1234", "", "ÁB12345"]:
        with pytest.raises(ValueError):
            p.register_driver("Bob", "bob@example.com", "+573001234567", PASSWORD, number, LICENSE_EXPIRES)


def test_a6_license_must_expire_after_current_date():
    p = new_platform()
    for expires in [date(2026, 3, 2), date(2026, 3, 1), date(2020, 1, 1)]:
        with pytest.raises(ValueError):
            p.register_driver("Bob", "bob@example.com", "+573001234567", PASSWORD, LICENSE, expires)
    drv = p.register_driver("Bob", "bob@example.com", "+573001234567", PASSWORD, LICENSE, date(2026, 3, 3))
    assert drv == "DRV-000001"


def test_a6_rider_birth_date_optional():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD)
    assert p.get_account(rid)["status"] == "active"


def test_a6_rider_of_exactly_sixteen_accepted():
    p = new_platform()
    rid = p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD, birth_date=date(2010, 3, 2))
    assert rid == "RID-000001"


def test_a6_rider_under_sixteen_rejected():
    p = new_platform()
    for born in [date(2010, 3, 3), date(2015, 1, 1), date(2026, 3, 1)]:
        with pytest.raises(ValueError):
            p.register_rider("Ana", "ana@example.com", "+573001234567", PASSWORD, birth_date=born)


# --------------------------------------------------------------------------- A7


def test_a7_valid_courier_vehicles():
    p = new_platform()
    ids = [make_courier(p, vehicle) for vehicle in ("bike", "moto", "car")]
    assert ids == ["CUR-000001", "CUR-000002", "CUR-000003"]


def test_a7_invalid_courier_vehicle_rejected():
    p = new_platform()
    for vehicle in ["truck", "scooter", "", "walk", "Bike", "CAR"]:
        with pytest.raises(ValueError):
            p.register_courier("Ana", "ana@example.com", "+573001234567", PASSWORD, vehicle)


# --------------------------------------------------------------------------- A8


def test_a8_referral_code_is_ref_plus_rider_digits():
    p = new_platform()
    make_admin(p)
    r1 = make_rider(p)
    r2 = make_rider(p)
    assert p.referral_code(r1) == "REF000001"
    assert p.referral_code(r2) == "REF000002"


def test_a8_register_with_existing_code_of_another_rider():
    p = new_platform()
    r1 = make_rider(p)
    code = p.referral_code(r1)
    r2 = p.register_rider("Bea", "bea@example.com", "+573001234568", PASSWORD, referral_code=code)
    assert r2 == "RID-000002"
    assert p.get_account(r2)["status"] == "active"


def test_a8_unknown_referral_code_rejected():
    p = new_platform()
    make_rider(p)
    for code in ["REF000099", "HELLO", "REF00000"]:
        with pytest.raises(ValueError):
            p.register_rider("Bea", "bea@example.com", "+573001234568", PASSWORD, referral_code=code)


def test_a8_referral_code_of_unknown_rider_raises_key_error():
    p = new_platform()
    with pytest.raises(KeyError):
        p.referral_code("RID-000042")


# --------------------------------------------------------------------------- B1


def test_b1_add_vehicle_returns_identifier():
    p = new_platform()
    drv = make_driver(p)
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy") == "VEH-000001"
    assert p.add_vehicle(drv, "XYZ987", "Yamaha", "NMax", 2012, 1, "moto") == "VEH-000002"


def test_b1_invalid_plate_rejected():
    p = new_platform()
    drv = make_driver(p)
    for plate in ["AB123", "ABCD123", "ABC12", "ABC1234", "123ABC", "ABC 123", "AB1234", ""]:
        with pytest.raises(ValueError):
            p.add_vehicle(drv, plate, "Toyota", "Corolla", 2020, 4, "economy")


def test_b1_plate_unique():
    p = new_platform()
    d1 = make_driver(p)
    d2 = make_driver(p)
    p.add_vehicle(d1, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    with pytest.raises(ValueError):
        p.add_vehicle(d2, "ABC123", "Kia", "Rio", 2021, 4, "economy")
    with pytest.raises(ValueError):
        p.add_vehicle(d1, "ABC123", "Kia", "Rio", 2021, 4, "economy")


def test_b1_make_and_model_not_empty():
    p = new_platform()
    drv = make_driver(p)
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "", "Corolla", 2020, 4, "economy")
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "Toyota", "", 2020, 4, "economy")


def test_b1_year_range():
    p = new_platform()
    drv = make_driver(p)
    for year in [2011, 2028, 1999]:
        with pytest.raises(ValueError):
            p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", year, 4, "economy")
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", "2020", 4, "economy")
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2012, 4, "economy").startswith("VEH-")
    assert p.add_vehicle(drv, "ABC124", "Toyota", "Corolla", 2027, 4, "economy").startswith("VEH-")


def test_b1_invalid_category_rejected():
    p = new_platform()
    drv = make_driver(p)
    for category in ["suv", "", "luxury", "bike"]:
        with pytest.raises(ValueError):
            p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, category)


def test_b1_seats_valid_per_category():
    p = new_platform()
    drv = make_driver(p)
    valid = [("moto", 1), ("economy", 4), ("economy", 6), ("comfort", 4), ("comfort", 6), ("xl", 6), ("xl", 8)]
    for i, (category, seats) in enumerate(valid):
        veh = p.add_vehicle(drv, f"AAA{i:03d}", "Make", "Model", 2020, seats, category)
        assert veh == f"VEH-{i + 1:06d}"


def test_b1_seats_invalid_per_category():
    p = new_platform()
    drv = make_driver(p)
    invalid = [("moto", 0), ("moto", 2), ("economy", 3), ("economy", 7), ("comfort", 3), ("comfort", 7),
               ("xl", 5), ("xl", 9)]
    for category, seats in invalid:
        with pytest.raises(ValueError):
            p.add_vehicle(drv, "ABC123", "Make", "Model", 2020, seats, category)


def test_b1_comfort_requires_2018_or_later():
    p = new_platform()
    drv = make_driver(p)
    with pytest.raises(ValueError):
        p.add_vehicle(drv, "ABC123", "Toyota", "Camry", 2017, 4, "comfort")
    assert p.add_vehicle(drv, "ABC123", "Toyota", "Camry", 2018, 4, "comfort") == "VEH-000001"
    assert p.add_vehicle(drv, "ABC124", "Toyota", "Yaris", 2017, 4, "economy") == "VEH-000002"


# --------------------------------------------------------------------------- B2


def test_b2_approve_driver_without_vehicle_rejected():
    p = new_platform()
    adm = make_admin(p)
    drv = make_driver(p)
    with pytest.raises(ValueError):
        p.approve(adm, drv)
    assert p.get_account(drv)["status"] == "pending"


def test_b2_approve_driver_with_expired_license_rejected():
    p = new_platform()
    adm = make_admin(p)
    drv = make_driver(p, expires=date(2026, 3, 5))
    p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    p.advance(minutes=60 * 24 * 7)
    with pytest.raises(ValueError):
        p.approve(adm, drv)
    assert p.get_account(drv)["status"] == "pending"


def test_b2_license_expiring_today_is_expired_for_approval():
    # A6: valid while the current date is before license_expires; it expires on that date.
    p = new_platform()
    adm = make_admin(p)
    today = make_driver(p, expires=date(2026, 3, 5))
    p.add_vehicle(today, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    tomorrow = make_driver(p, expires=date(2026, 3, 6))
    p.add_vehicle(tomorrow, "XYZ789", "Toyota", "Corolla", 2020, 4, "economy")
    p.advance(minutes=60 * 60)  # 2026-03-05 00:00
    assert p.now().date() == date(2026, 3, 5)
    with pytest.raises(ValueError):
        p.approve(adm, today)
    assert p.get_account(today)["status"] == "pending"
    p.approve(adm, tomorrow)
    assert p.get_account(tomorrow)["status"] == "active"


def test_b2_approve_driver_with_vehicle_and_valid_license():
    p = new_platform()
    adm = make_admin(p)
    drv = make_driver(p)
    p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    p.approve(adm, drv)
    assert p.get_account(drv)["status"] == "active"


# --------------------------------------------------------------------------- B3


def test_b3_workers_start_offline():
    p = new_platform()
    adm = make_admin(p)
    drv, _veh = make_active_driver(p, adm)
    cur = make_active_courier(p, adm)
    for worker in (drv, cur):
        status = p.worker_status(worker)
        assert status["status"] == "offline"
        assert status["location"] is None


def test_b3_driver_goes_online_with_own_vehicle():
    p = new_platform()
    adm = make_admin(p)
    drv, veh = make_active_driver(p, adm)
    p.go_online(drv, (4.651, -74.049), veh)
    status = p.worker_status(drv)
    assert status["status"] == "available"
    assert tuple(status["location"]) == (4.651, -74.049)
    assert status["vehicle_id"] == veh


def test_b3_driver_must_give_a_vehicle():
    p = new_platform()
    adm = make_admin(p)
    drv, _veh = make_active_driver(p, adm)
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER)
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER, None)
    assert p.worker_status(drv)["status"] == "offline"


def test_b3_driver_cannot_use_another_drivers_vehicle():
    p = new_platform()
    adm = make_admin(p)
    _d1, v1 = make_active_driver(p, adm, plate="ABC123")
    d2, _v2 = make_active_driver(p, adm, plate="XYZ789")
    with pytest.raises(PermissionError):
        p.go_online(d2, CENTER, v1)
    assert p.worker_status(d2)["status"] == "offline"


def test_b3_unknown_vehicle_raises_key_error():
    p = new_platform()
    adm = make_admin(p)
    drv, _veh = make_active_driver(p, adm)
    with pytest.raises(KeyError):
        p.go_online(drv, CENTER, "VEH-999999")
    assert p.worker_status(drv)["status"] == "offline"


def test_b3_pending_driver_cannot_go_online():
    p = new_platform()
    drv = make_driver(p)
    veh = p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER, veh)
    assert p.worker_status(drv)["status"] == "offline"


def test_b3_suspended_driver_cannot_go_online():
    p = new_platform()
    adm = make_admin(p)
    drv, veh = make_active_driver(p, adm)
    p.suspend(adm, drv, "complaints")
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER, veh)
    assert p.worker_status(drv)["status"] == "offline"


def test_b3_license_expiring_today_is_expired_for_going_online():
    p = new_platform()
    adm = make_admin(p)
    drv, veh = make_active_driver(p, adm, expires=date(2026, 3, 5))
    p.advance(minutes=60 * 60 - 1)  # 2026-03-04 23:59
    p.go_online(drv, CENTER, veh)
    assert p.worker_status(drv)["status"] == "available"
    p.go_offline(drv)
    p.advance(minutes=1)  # 2026-03-05 00:00
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER, veh)
    assert p.worker_status(drv)["status"] == "offline"


def test_b3_driver_with_license_expired_now_cannot_go_online():
    p = new_platform()
    adm = make_admin(p)
    drv, veh = make_active_driver(p, adm, expires=date(2026, 3, 5))
    p.advance(minutes=60 * 24 * 7)
    with pytest.raises(ValueError):
        p.go_online(drv, CENTER, veh)
    assert p.worker_status(drv)["status"] == "offline"


def test_b3_courier_goes_online_without_vehicle():
    p = new_platform()
    adm = make_admin(p)
    cur = make_active_courier(p, adm)
    p.go_online(cur, (4.66, -74.06))
    status = p.worker_status(cur)
    assert status["status"] == "available"
    assert tuple(status["location"]) == (4.66, -74.06)


def test_b3_courier_giving_a_vehicle_rejected():
    p = new_platform()
    adm = make_admin(p)
    drv = make_driver(p)
    veh = p.add_vehicle(drv, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
    cur = make_active_courier(p, adm)
    with pytest.raises(ValueError):
        p.go_online(cur, CENTER, veh)
    assert p.worker_status(cur)["status"] == "offline"


def test_b3_inactive_courier_cannot_go_online():
    p = new_platform()
    adm = make_admin(p)
    pending = make_courier(p)
    suspended = make_active_courier(p, adm)
    p.suspend(adm, suspended, "complaints")
    for cur in (pending, suspended):
        with pytest.raises(ValueError):
            p.go_online(cur, CENTER)
        assert p.worker_status(cur)["status"] == "offline"


# --------------------------------------------------------------------------- B4


def test_b4_go_offline_sets_offline():
    p = new_platform()
    adm = make_admin(p)
    drv, veh = make_active_driver(p, adm)
    p.go_online(drv, CENTER, veh)
    p.go_offline(drv)
    status = p.worker_status(drv)
    assert status["status"] == "offline"
    assert status["location"] is None


def test_b4_update_location_of_online_worker():
    p = new_platform()
    adm = make_admin(p)
    cur = make_active_courier(p, adm)
    p.go_online(cur, CENTER)
    p.update_location(cur, (4.7, -74.1))
    status = p.worker_status(cur)
    assert tuple(status["location"]) == (4.7, -74.1)
    assert status["status"] == "available"


def test_b4_update_location_when_offline_rejected():
    p = new_platform()
    adm = make_admin(p)
    cur = make_active_courier(p, adm)
    with pytest.raises(ValueError):
        p.update_location(cur, (4.7, -74.1))
    p.go_online(cur, CENTER)
    p.go_offline(cur)
    with pytest.raises(ValueError):
        p.update_location(cur, (4.7, -74.1))
    assert p.worker_status(cur)["location"] is None


def test_b4_go_offline_with_active_ride_rejected():
    p, drv, _veh, ride_id = assigned_ride_world()
    with pytest.raises(ValueError):
        p.go_offline(drv)
    assert p.worker_status(drv)["status"] == "busy"
    assert p.ride(ride_id)["status"] == "driver_assigned"


# --------------------------------------------------------------------------- B5


def test_b5_worker_status_fields():
    p = new_platform()
    adm = make_admin(p)
    drv, veh = make_active_driver(p, adm)
    offline = p.worker_status(drv)
    assert offline["status"] == "offline"
    assert offline["location"] is None
    assert "vehicle_id" in offline
    p.go_online(drv, (4.6, -74.0), veh)
    online = p.worker_status(drv)
    assert online["status"] == "available"
    assert tuple(online["location"]) == (4.6, -74.0)
    assert online["vehicle_id"] == veh


def test_b5_worker_with_accepted_ride_is_busy():
    p, drv, veh, _ride_id = assigned_ride_world()
    status = p.worker_status(drv)
    assert status["status"] == "busy"
    assert status["vehicle_id"] == veh
    assert status["location"] is not None


# --------------------------------------------------------------------------- C1


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ((4.6097, -74.0817), (6.2442, -75.5812)),
        ((40.7128, -74.0060), (51.5074, -0.1278)),
        ((4.65, -74.05), (4.6512, -74.0489)),
        ((-33.8688, 151.2093), (-37.8136, 144.9631)),
        ((4.65, -74.05), (4.72, -74.02)),
    ],
)
def test_c1_distance_matches_haversine(a, b):
    p = new_platform()
    assert p.distance_km(a, b) == expected_distance(a, b)


def test_c1_one_degree_of_latitude():
    p = new_platform()
    assert p.distance_km((0.0, 0.0), (1.0, 0.0)) == Decimal("111.195")


def test_c1_returns_decimal_with_three_places():
    p = new_platform()
    d = p.distance_km((4.6097, -74.0817), (6.2442, -75.5812))
    assert isinstance(d, Decimal)
    assert d == Decimal("246.136")
    assert d.as_tuple().exponent == -3
    ten = p.distance_km((0.0, 0.0), (0.0899322, 0.0))
    assert ten == Decimal("10.000")
    assert ten.as_tuple().exponent == -3


def test_c1_same_point_is_zero():
    p = new_platform()
    d = p.distance_km(CENTER, CENTER)
    assert d == Decimal("0.000")
    assert d.as_tuple().exponent == -3


def test_c1_symmetric_and_across_antimeridian():
    p = new_platform()
    a, b = (40.7128, -74.0060), (51.5074, -0.1278)
    assert p.distance_km(a, b) == p.distance_km(b, a)
    assert p.distance_km((0.0, 179.5), (0.0, -179.5)) == Decimal("111.195")


# --------------------------------------------------------------------------- C2


def test_c2_only_admins_add_zones():
    p = new_platform()
    rid = make_rider(p)
    with pytest.raises(PermissionError):
        p.add_zone(rid, "Rogue", CENTER, 10, 60)
    a, b = CENTER, (4.72, -74.02)
    assert p.eta_minutes(a, b) == expected_eta(a, b, 30)


def test_c2_radius_bounds():
    p = new_platform()
    adm = make_admin(p)
    for radius in [0.49, 50.01, 0, -1]:
        with pytest.raises(ValueError):
            p.add_zone(adm, "Bad", CENTER, radius, 30)
    assert p.add_zone(adm, "Small", CENTER, 0.5, 30) == "ZON-000001"
    assert p.add_zone(adm, "Large", CENTER, 50, 30) == "ZON-000002"


def test_c2_speed_bounds():
    p = new_platform()
    adm = make_admin(p)
    for speed in [4.9, 121, 0, -30]:
        with pytest.raises(ValueError):
            p.add_zone(adm, "Bad", CENTER, 10, speed)
    assert p.add_zone(adm, "Slow", CENTER, 10, 5) == "ZON-000001"
    assert p.add_zone(adm, "Fast", CENTER, 10, 120, airport=True) == "ZON-000002"


def test_c2_first_zone_in_creation_order_wins():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Small first", (4.60, -74.10), 10, 20)
    p.add_zone(adm, "Big second", CENTER, 20, 60)
    a, b = (4.62, -74.08), (4.70, -74.02)  # a is inside both zones
    assert p.eta_minutes(a, b) == expected_eta(a, b, 20)
    c = (4.75, -74.0)  # only inside the second zone
    assert p.eta_minutes(c, b) == expected_eta(c, b, 60)


def test_c2_membership_by_distance_to_center():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Five km", CENTER, 5, 60)
    inside = (4.694876, -74.05)  # about 4.990 km from the center
    outside = (4.695056, -74.05)  # about 5.010 km from the center
    target = (4.5, -74.05)
    assert p.eta_minutes(inside, target) == expected_eta(inside, target, 60)
    assert p.eta_minutes(outside, target) == expected_eta(outside, target, 30)


def test_c2_ride_outside_every_zone_rejected():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Bogota", CENTER, 5, 30)
    rid = make_rider(p)
    far = (5.0, -74.05)  # about 39 km away
    with pytest.raises(ValueError):
        p.quote_ride(rid, far, CENTER, "economy")
    with pytest.raises(ValueError):
        p.quote_ride(rid, CENTER, far, "economy")
    quote = p.quote_ride(rid, CENTER, (4.66, -74.05), "economy")
    assert quote["quote_id"].startswith("QTE-")


def test_c2_order_with_restaurant_outside_every_zone_rejected():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Elsewhere", (6.2442, -75.5812), 10, 30)
    rst = make_restaurant(p, location=CENTER)
    p.approve(adm, rst)
    p.set_hours(rst, 0, "08:00", "22:00")  # START is Monday 12:00
    item = p.add_menu_item(rst, "Arepa", "12.00", "food", 10)
    rid = make_rider(p)
    card = p.add_card(rid, CARD, 12, 2030, "123")
    lines = [{"item_id": item, "quantity": 1}]
    dropoff = (4.66, -74.05)
    with pytest.raises(ValueError):
        p.place_order(rid, rst, lines, dropoff, card)
    p.add_zone(adm, "Bogota", CENTER, 10, 30)
    assert p.place_order(rid, rst, lines, dropoff, card).startswith("ORD-")


# --------------------------------------------------------------------------- C3


def test_c3_eta_uses_zone_speed():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Bogota", CENTER, 20, 40)
    a, b = CENTER, (4.72, -74.02)
    eta = p.eta_minutes(a, b)
    assert isinstance(eta, int)
    assert eta == expected_eta(a, b, 40) == 13


def test_c3_outside_every_zone_speed_is_30():
    p = new_platform()
    a, b = (4.6097, -74.0817), (6.2442, -75.5812)
    assert p.eta_minutes(a, b) == expected_eta(a, b, 30) == 493


def test_c3_uses_zone_of_origin_not_destination():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Bogota", CENTER, 5, 60)
    inside, outside = CENTER, (4.72, -74.02)
    assert p.eta_minutes(inside, outside) == expected_eta(inside, outside, 60) == 9
    assert p.eta_minutes(outside, inside) == expected_eta(outside, inside, 30) == 17


def test_c3_eta_at_least_one_minute():
    p = new_platform()
    adm = make_admin(p)
    p.add_zone(adm, "Bogota", CENTER, 5, 120)
    assert p.eta_minutes(CENTER, CENTER) == 1
    assert p.eta_minutes(CENTER, (4.6501, -74.05)) == 1


def test_c3_exact_whole_minutes_not_rounded_up():
    # distance_km is exactly 10.000; 10.000 / 30 x 60 = 20 and 10.000 / 60 x 60 = 10.
    p = new_platform()
    a, b = (0.0, 0.0), (0.0899322, 0.0)
    assert p.eta_minutes(a, b) == 20
    adm = make_admin(p)
    p.add_zone(adm, "Equator", (0.0, 0.0), 20, 60)
    assert p.eta_minutes(a, b) == 10
