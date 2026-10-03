"""Hidden acceptance tests: payments and wallet (O), earnings and payouts (P), ratings (Q), support,
safety and sharing (R), notifications (S), reports and audit (T), persistence (U) and the
non-functional requirements (V) of SPEC.md, plus the general rules (X5, X7, X8) as they apply to those
sections.

Only the public interface of INTERFACE.md is used (``from rides import Platform``), plus the standard
library: ``sqlite3`` to inspect the database file (U2, V1) and ``ast``/``inspect`` on the package source
for the code requirements of V5. Each test checks one rule; its name starts with ``test_<rule id>_``.

Geometry used throughout (haversine, R = 6371.0 km, computed beforehand):
- the city zone is centred on P0 = (4.65, -74.05), radius 10 km, 30 km/h; the airport zone is centred
  on (4.70, -74.14), radius 2 km, and lies 11.419 km from P0 (outside the city zone);
- P0 -> D0 = 3.002 km, 7 min: economy fare 2.50 + 3.3022 + 1.75 = 7.5522 + 1.50 = 9.05; driver share
  0.75 x 7.55 = 5.6625 -> 5.66;
- P0 -> D_TIE = 3.100 km, 7 min: fare 9.16; share 0.75 x 7.66 = 5.745 -> 5.75 (half up; half even
  would give 5.74);
- P0 -> airport = 11.419 km, 23 min: fare 27.31; share 0.75 x 20.81 = 15.6075 -> 15.61, plus 5.00;
- restaurant R0 -> OD = 2.224 km (raw 2.2239): courier pay 2.50 + 1.3344 = 3.83 either way.
Expected driver shares are computed from the fare the platform reports, so a P test does not fail
because of a fare (D) defect.

Points the requirements still leave open, deliberately left untested:
- O1/O2: the expiry of the payment methods (``payment_methods`` lists only id, kind, brand, last4);
  extra keys in returned dicts.
- O4: the ``ref``, ``method_id`` and ``at`` of a payout or of a referral credit (none is a rider
  payment entry checked here); whether a refund of 0.00 creates a payment entry.
- P3: the bonus when the ``earnings`` window is not a whole week (only Monday-to-Monday windows are
  used).
- Q1: the error when an actor names a target it may not rate (e.g. a rider with ``target="rider"``).
- R3: whether the driver may call ``share_trip`` (only another rider is checked).
- S1: the ``ref`` of a ``payout`` notification; the exact time of ``order_ready`` (only the order
  of the kinds is checked); other kinds besides the listed ones may exist, so only the listed kinds
  are filtered and compared.
- T1: whether ride tips and refunds enter ``platform_revenue`` (no test mixes them in), whether
  ``order_revenue`` includes the capture of a cancelled order; the numeric type of
  ``cancellation_rate`` (compared through ``Decimal(str(...))``).
- V1: a 3-digit cvc cannot be searched for reliably in binary data, so the cvc check uses a 4-digit
  amex cvc matched as a whole token. V3: the setup registers 5 000 drivers through the public API
  (PBKDF2 makes it slow; the tests carry a 3600 s timeout marker); only the two calls are timed.
"""

from __future__ import annotations

import ast
import copy
import gc
import inspect
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

import rides
from rides import Platform

D = Decimal
CENT = D("0.01")
START = datetime(2026, 3, 2, 12, 0)  # a Monday
DAY = date(2026, 3, 2)
WEEK = date(2026, 3, 2)
WEEK_START = datetime(2026, 3, 2, 0, 0)
WEEK_END = datetime(2026, 3, 9, 0, 0)
PW = "Quartz-Lynx-4821"
CARD = "4242424242424242"
DECLINE = "4000000000000002"
AMEX = "378282246310005"
AMEX_CVC = "7391"
LICENSE_EXPIRES = date(2030, 1, 1)

P0 = (4.65, -74.05)
D0 = (4.677, -74.05)
D_TIE = (4.67788, -74.05)
MID = (4.665, -74.05)
AIRPORT = (4.70, -74.14)
R0 = (4.66, -74.06)
OD = (4.68, -74.06)
RB = (4.655, -74.065)
RC = (4.645, -74.055)
MID_BC = (4.65, -74.06)


def money(value: object) -> Decimal:
    return D(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def driver_share(fare: Decimal, airport: bool = False) -> Decimal:
    """P1 for an economy ride (booking fee 1.50) without wait fee or tip."""
    surcharge = D("5.00") if airport else D("0.00")
    return money((D(fare) - D("1.50") - surcharge) * D("0.75")) + surcharge


class World:
    """A platform on Monday 2026-03-02 12:00 with an admin, a city and an airport zone, a rider
    with a visa card and one approved economy driver online at P0."""

    def __init__(self, db_path: str | None = None) -> None:
        self.p = p = Platform(START, db_path=db_path)
        self._n = 0
        self.loc: dict[str, tuple[float, float]] = {}
        self.admin = p.create_admin("Ada Admin", "ada@rides.test", "+573000000001", PW)
        self.city = p.add_zone(self.admin, "City", P0, 10, 30)
        self.airport = p.add_zone(self.admin, "Airport", AIRPORT, 2, 30, airport=True)
        self.rider = p.register_rider("Rita Rider", "rita@rides.test", "+573100000001", PW)
        self.card = p.add_card(self.rider, CARD, 12, 2030, "123")
        self.cards = {self.rider: self.card}
        self.driver = p.register_driver("Dan Driver", "dan@rides.test", "+573200000001", PW,
                                        "LIC00001", LICENSE_EXPIRES)
        self.vehicle = p.add_vehicle(self.driver, "ABC123", "Toyota", "Corolla", 2020, 4, "economy")
        p.approve(self.admin, self.driver)
        p.go_online(self.driver, P0, self.vehicle)
        self.restaurant: str = ""
        self.item: str = ""
        self.courier: str = ""

    # -- accounts -------------------------------------------------------------------------------
    def _next(self) -> int:
        self._n += 1
        return self._n

    def new_rider(self, with_card: bool = True) -> str:
        n = self._next()
        rider = self.p.register_rider(f"Rider {n}", f"rider{n}@rides.test", f"+5733{n:08d}", PW)
        if with_card:
            self.cards[rider] = self.p.add_card(rider, CARD, 12, 2030, "123")
        return rider

    def new_admin(self) -> str:
        n = self._next()
        return self.p.create_admin(f"Admin {n}", f"admin{n}@rides.test", f"+5737{n:08d}", PW)

    def new_driver(self, location: tuple[float, float] | None = P0, approve: bool = True) -> str:
        n = self._next()
        driver = self.p.register_driver(f"Driver {n}", f"driver{n}@rides.test", f"+5734{n:08d}", PW,
                                        f"LIC{n:05d}", LICENSE_EXPIRES)
        vehicle = self.p.add_vehicle(driver, f"DRV{n:03d}", "Kia", "Rio", 2021, 4, "economy")
        if approve:
            self.p.approve(self.admin, driver)
            if location is not None:
                self.p.go_online(driver, location, vehicle)
        return driver

    def add_restaurant(self, location: tuple[float, float] = R0, price: str = "12.00") -> tuple[str, str]:
        n = self._next()
        rst = self.p.register_restaurant(f"Resto {n}", f"resto{n}@rides.test", f"+5735{n:08d}", PW,
                                         location)
        self.p.approve(self.admin, rst)
        for weekday in range(7):
            self.p.set_hours(rst, weekday, "00:00", "23:59")
        item = self.p.add_menu_item(rst, f"Dish {n}", price, "food", 10)
        self.loc[rst] = location
        return rst, item

    def add_courier(self, location: tuple[float, float] = R0) -> str:
        n = self._next()
        courier = self.p.register_courier(f"Courier {n}", f"courier{n}@rides.test", f"+5736{n:08d}",
                                          PW, "moto")
        self.p.approve(self.admin, courier)
        self.p.go_online(courier, location)
        return courier

    def eats(self) -> World:
        self.restaurant, self.item = self.add_restaurant()
        self.courier = self.add_courier()
        return self

    def wallet(self, rider: str | None = None) -> str:
        return "WAL-" + (rider or self.rider)[-6:]

    # -- flows ----------------------------------------------------------------------------------
    def request(self, rider: str | None = None, pickup=P0, dropoff=D0, method: str | None = None,
                category: str = "economy") -> str:
        rider = rider or self.rider
        quote = self.p.quote_ride(rider, pickup, dropoff, category)
        return self.p.request_ride(rider, quote["quote_id"], method or self.cards[rider])

    def ride(self, rider: str | None = None, pickup=P0, dropoff=D0, method: str | None = None,
             driver: str | None = None, wait: int = 0, minutes: int = 5) -> str:
        """Quote, request, accept, arrive, start and complete one ride along [pickup, dropoff]."""
        p = self.p
        driver = driver or self.driver
        p.update_location(driver, pickup)
        rid = self.request(rider, pickup, dropoff, method)
        assert p.ride(rid)["offered_to"] == driver
        p.accept_ride(driver, rid)
        p.driver_arrived(driver, rid)
        if wait:
            p.advance(minutes=wait)
        p.start_ride(driver, rid)
        p.advance(minutes=minutes)
        p.complete_ride(driver, rid, [pickup, dropoff])
        return rid

    def place(self, rider: str | None = None, restaurant: str | None = None, item: str | None = None,
              dropoff=OD, tip: str = "0", method: str | None = None) -> str:
        rider = rider or self.rider
        return self.p.place_order(rider, restaurant or self.restaurant,
                                  [{"item_id": item or self.item, "quantity": 1}], dropoff,
                                  method or self.cards[rider], tip=D(tip))

    def order(self, rider: str | None = None, restaurant: str | None = None, item: str | None = None,
              dropoff=OD, tip: str = "0", method: str | None = None) -> str:
        """Place, accept, prepare, pick up and deliver one order of one item."""
        p = self.p
        restaurant = restaurant or self.restaurant
        p.update_location(self.courier, self.loc[restaurant])
        oid = self.place(rider, restaurant, item, dropoff, tip, method)
        p.accept_order(restaurant, oid)
        assert p.order(oid)["courier_id"] == self.courier
        p.advance(minutes=10)
        p.mark_ready(restaurant, oid)
        p.pick_up(self.courier, oid)
        p.update_location(self.courier, dropoff)
        p.deliver(self.courier, oid)
        return oid

    def advance_to(self, when: datetime) -> None:
        seconds = int((when - self.p.now()).total_seconds())
        assert seconds >= 0
        self.p.advance(minutes=seconds // 60, seconds=seconds % 60)


@pytest.fixture()
def w() -> World:
    return World()


def kinds(p: Platform, account: str, wanted: set[str]) -> list[tuple[str, str]]:
    return [(n["kind"], n["ref"]) for n in p.notifications(account) if n["kind"] in wanted]


def day_earnings(w: World, worker: str) -> dict:
    return w.p.earnings(worker, WEEK_START, WEEK_START + timedelta(days=1))


# =============================================================================== O. Payments
def test_o1_luhn_rejected(w):
    with pytest.raises(ValueError):
        w.p.add_card(w.rider, "4242424242424241", 12, 2030, "123")


@pytest.mark.parametrize("number", ["424242424242", "42424242424242424242"])  # both pass Luhn
def test_o1_length_outside_13_to_19_rejected(w, number):
    with pytest.raises(ValueError):
        w.p.add_card(w.rider, number, 12, 2030, "123")


@pytest.mark.parametrize("number", ["4242 4242 4242 4242", "4242-4242-4242-4242", " 4242424242424242",
                                    "4242424242424242 ", 4242424242424242])
def test_o1_number_is_a_string_of_digits_without_spaces(w, number):
    with pytest.raises(ValueError):
        w.p.add_card(w.rider, number, 12, 2030, "123")
    assert [m["id"] for m in w.p.payment_methods(w.rider)] == ["WAL-000001", w.card]


@pytest.mark.parametrize("number", ["4222222222222", "4000000000000000006"])
def test_o1_13_and_19_digits_accepted(w, number):
    card = w.p.add_card(w.rider, number, 12, 2030, "123")
    entry = next(m for m in w.p.payment_methods(w.rider) if m["id"] == card)
    assert (entry["brand"], entry["last4"]) == ("visa", number[-4:])


@pytest.mark.parametrize("number,cvc,brand", [
    (CARD, "123", "visa"),
    ("5105105105105100", "123", "mastercard"),
    ("5555555555554444", "123", "mastercard"),
    (AMEX, "1234", "amex"),
    ("371449635398431", "1234", "amex"),
])
def test_o1_brand_detected(w, number, cvc, brand):
    card = w.p.add_card(w.rider, number, 12, 2030, cvc)
    assert card.startswith("PM-")
    entry = next(m for m in w.p.payment_methods(w.rider) if m["id"] == card)
    assert entry["brand"] == brand
    assert entry["last4"] == number[-4:]


@pytest.mark.parametrize("number", ["6011111111111117", "5612345678901230", "2230000000000008"])
def test_o1_unknown_brand_rejected(w, number):
    with pytest.raises(ValueError):
        w.p.add_card(w.rider, number, 12, 2030, "123")


@pytest.mark.parametrize("month,year", [(2, 2026), (12, 2025), (13, 2030), (0, 2030)])
def test_o1_expiry_before_current_month_rejected(w, month, year):
    with pytest.raises(ValueError):
        w.p.add_card(w.rider, CARD, month, year, "123")


def test_o1_expiry_in_current_month_accepted(w):
    card = w.p.add_card(w.rider, "5555555555554444", 3, 2026, "123")
    assert card in [m["id"] for m in w.p.payment_methods(w.rider)]


@pytest.mark.parametrize("number,cvc", [(CARD, "1234"), (CARD, "12"), (CARD, "12a"), (AMEX, "123"),
                                        (AMEX, "12345")])
def test_o1_cvc_length_by_brand(w, number, cvc):
    with pytest.raises(ValueError):
        w.p.add_card(w.rider, number, 12, 2030, cvc)


def test_o1_only_brand_and_last4_exposed(w):
    card = w.p.add_card(w.rider, "5105105105105100", 12, 2030, "123")
    methods = w.p.payment_methods(w.rider)
    assert "5105105105105100" not in repr(methods)
    assert CARD not in repr(methods)
    entry = next(m for m in methods if m["id"] == card)
    assert entry["last4"] == "5100"


def test_o2_wallet_id_listed_first(w):
    second = w.new_rider()
    third = w.new_rider(with_card=False)
    assert w.p.payment_methods(w.rider)[0]["id"] == "WAL-000001"
    assert w.p.payment_methods(second)[0]["id"] == "WAL-" + second[-6:]
    assert [m["id"] for m in w.p.payment_methods(third)] == ["WAL-" + third[-6:]]
    assert [m["id"] for m in w.p.payment_methods(w.rider)] == ["WAL-000001", w.card]


def test_o2_payment_method_kinds(w):
    amex = w.p.add_card(w.rider, AMEX, 12, 2030, AMEX_CVC)
    methods = w.p.payment_methods(w.rider)
    assert [(m["id"], m["kind"], m["brand"], m["last4"]) for m in methods] == [
        ("WAL-000001", "wallet", None, None),
        (w.card, "card", "visa", "4242"),
        (amex, "card", "amex", "0005"),
    ]


def test_o2_wallet_starts_empty(w):
    assert w.p.wallet_balance(w.rider) == D("0.00")
    assert isinstance(w.p.wallet_balance(w.rider), Decimal)


def test_o2_top_up_credits_wallet_and_charges_card(w):
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    assert w.p.wallet_balance(w.rider) == D("20.00")
    top_ups = [x for x in w.p.payments(w.rider) if x["kind"] == "top_up"]
    assert len(top_ups) == 1
    assert top_ups[0]["amount"] == D("20.00")
    assert top_ups[0]["method_id"] == w.card
    assert top_ups[0]["ref"] == w.card  # O4: the card id for a top-up
    assert top_ups[0]["at"] == START


def test_o2_wallet_balance_subtracts_active_holds(w):
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    rid = w.request(method=w.wallet())
    quoted = w.p.ride(rid)["quoted_fare"]
    assert w.p.wallet_balance(w.rider) == D("20.00") - quoted
    w.p.cancel_ride(w.rider, rid, "changed my mind")  # free in requested: the hold is released
    assert w.p.wallet_balance(w.rider) == D("20.00")


def test_o2_wallet_balance_subtracts_an_order_hold(w):
    w.eats()
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    oid = w.place(method=w.wallet())
    assert w.p.order(oid)["total"] == D("16.06")
    assert w.p.wallet_balance(w.rider) == D("3.94")
    w.p.reject_order(w.restaurant, oid, "closing early")
    assert w.p.wallet_balance(w.rider) == D("20.00")


@pytest.mark.parametrize("amount", ["4.99", "500.01", "0", "-5.00", "20.001", 20.0])
def test_o2_top_up_out_of_range_rejected(w, amount):
    with pytest.raises(ValueError):
        w.p.top_up_wallet(w.rider, w.card, amount)
    assert w.p.wallet_balance(w.rider) == D("0.00")
    assert w.p.payments(w.rider) == []


def test_o2_top_up_bounds_inclusive(w):
    w.p.top_up_wallet(w.rider, w.card, "5.00")
    w.p.top_up_wallet(w.rider, w.card, 500)
    assert w.p.wallet_balance(w.rider) == D("505.00")


def test_o2_top_up_unknown_card(w):
    with pytest.raises(KeyError):
        w.p.top_up_wallet(w.rider, "PM-999999", "20.00")
    with pytest.raises(KeyError):  # X8: before the out-of-range amount
        w.p.top_up_wallet(w.rider, "PM-999999", "1.00")
    with pytest.raises(KeyError):  # X7: identifiers are not normalized
        w.p.top_up_wallet(w.rider, w.card.lower(), "20.00")
    assert w.p.wallet_balance(w.rider) == D("0.00")


def test_o3_declining_card_hold_fails(w):
    bad = w.p.add_card(w.rider, DECLINE, 12, 2030, "123")
    quote = w.p.quote_ride(w.rider, P0, D0, "economy")
    with pytest.raises(ValueError):
        w.p.request_ride(w.rider, quote["quote_id"], bad)


def test_o3_declining_card_charge_fails_on_top_up(w):
    bad = w.p.add_card(w.rider, DECLINE, 12, 2030, "123")
    with pytest.raises(ValueError):
        w.p.top_up_wallet(w.rider, bad, "20.00")
    assert w.p.wallet_balance(w.rider) == D("0.00")


def test_o3_declining_card_order_hold_fails(w):
    w.eats()
    bad = w.p.add_card(w.rider, DECLINE, 12, 2030, "123")
    with pytest.raises(ValueError):
        w.place(method=bad)


def test_o3_other_card_hold_succeeds(w):
    master = w.p.add_card(w.rider, "5555555555554444", 12, 2030, "123")
    rid = w.request(method=master)
    assert w.p.ride(rid)["status"] == "requested"


def test_o3_wallet_hold_needs_balance(w):
    w.p.top_up_wallet(w.rider, w.card, "5.00")  # every economy fare is at least 7.50
    quote = w.p.quote_ride(w.rider, P0, D0, "economy")
    with pytest.raises(ValueError):
        w.p.request_ride(w.rider, quote["quote_id"], w.wallet())


def test_o3_wallet_hold_counts_active_holds(w):
    w.eats()
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    rid = w.request(method=w.wallet())  # hold 9.05, 10.95 left
    with pytest.raises(ValueError):
        w.place(method=w.wallet())  # total 16.06
    w.p.cancel_ride(w.rider, rid, "changed my mind")  # free in requested: hold released
    oid = w.place(method=w.wallet())
    assert w.p.order(oid)["status"] == "placed"


def test_o3_wallet_payment_captured(w):
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    rid = w.ride(method=w.wallet())
    assert w.p.wallet_balance(w.rider) == D("20.00") - w.p.ride(rid)["charged"]


def test_o3_other_riders_card_forbidden(w):
    other = w.new_rider()
    quote = w.p.quote_ride(w.rider, P0, D0, "economy")
    with pytest.raises(PermissionError):
        w.p.request_ride(w.rider, quote["quote_id"], w.cards[other])


def test_o3_other_riders_wallet_forbidden(w):
    other = w.new_rider()
    w.p.top_up_wallet(other, w.cards[other], "50.00")
    quote = w.p.quote_ride(w.rider, P0, D0, "economy")
    with pytest.raises(PermissionError):
        w.p.request_ride(w.rider, quote["quote_id"], w.wallet(other))


def test_o3_top_up_with_other_riders_card_forbidden(w):
    other = w.new_rider()
    with pytest.raises(PermissionError):
        w.p.top_up_wallet(w.rider, w.cards[other], "20.00")
    assert w.p.wallet_balance(w.rider) == D("0.00")


def test_x8_other_riders_method_reported_before_a_broken_rule(w):
    other = w.new_rider()
    with pytest.raises(PermissionError):  # amount out of range too
        w.p.top_up_wallet(w.rider, w.cards[other], "1.00")
    quote = w.p.quote_ride(w.rider, P0, D0, "economy")
    w.p.advance(minutes=6)  # the quote has expired too
    with pytest.raises(PermissionError):
        w.p.request_ride(w.rider, quote["quote_id"], w.cards[other])
    with pytest.raises(KeyError):  # unknown method before the expired quote
        w.p.request_ride(w.rider, quote["quote_id"], "PM-999999")


def test_o4_history_in_time_order(w):
    w.eats()
    p = w.p
    p.top_up_wallet(w.rider, w.card, "20.00")
    t_top_up = p.now()
    p.advance(minutes=1)
    rid = w.ride()
    t_ride = p.now()
    p.advance(minutes=1)
    p.add_tip(w.rider, rid, "2.00")
    t_tip = p.now()
    p.advance(minutes=1)
    oid = w.order()
    t_order = p.now()
    p.advance(minutes=1)
    ticket = p.report_issue(w.rider, oid, "missing_items", "The fries were missing from the bag")
    p.resolve_ticket(w.admin, ticket, "1.00")
    t_refund = p.now()
    history = p.payments(w.rider)
    assert [x["kind"] for x in history] == ["top_up", "ride", "tip", "order", "refund"]
    assert [x["at"] for x in history] == [t_top_up, t_ride, t_tip, t_order, t_refund]
    assert [x["amount"] for x in history] == [D("20.00"), p.ride(rid)["charged"], D("2.00"),
                                              p.order(oid)["charged"], D("-1.00")]
    assert [x["method_id"] for x in history] == [w.card] * 5
    assert [x["ref"] for x in history] == [w.card, rid, rid, oid, oid]
    # only ledger entries consume PAY- ids; the ride and order holds did not
    assert [x["id"] for x in history] == ["PAY-000001", "PAY-000002", "PAY-000003", "PAY-000004",
                                          "PAY-000005"]


def test_o4_holds_are_not_payments(w):
    rid = w.request()
    assert w.p.payments(w.rider) == []
    w.p.cancel_ride(w.rider, rid, "changed my mind")
    assert w.p.payments(w.rider) == []


def test_o4_holds_consume_no_pay_ids(w):
    w.eats()
    rid = w.request()  # ride hold
    w.p.cancel_ride(w.rider, rid, "changed my mind")  # released
    oid = w.place()  # order hold
    w.p.reject_order(w.restaurant, oid, "out of stock")  # released
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    assert [x["id"] for x in w.p.payments(w.rider)] == ["PAY-000001"]


def test_o4_cancellation_fee_listed(w):
    rid = w.request()
    w.p.accept_ride(w.driver, rid)
    w.p.advance(minutes=3)
    w.p.cancel_ride(w.rider, rid, "took too long")
    history = w.p.payments(w.rider)
    assert [(x["kind"], x["ref"], x["method_id"], x["amount"], x["at"]) for x in history] == [
        ("cancellation_fee", rid, w.card, D("3.00"), w.p.now())]


def test_o4_wallet_payment_method_id(w):
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    rid = w.ride(method=w.wallet())
    rides_paid = [x for x in w.p.payments(w.rider) if x["kind"] == "ride"]
    assert [(x["ref"], x["method_id"], x["amount"]) for x in rides_paid] == [
        (rid, w.wallet(), w.p.ride(rid)["charged"])]


def test_o4_refund_is_negative(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", "The route was longer than needed")
    w.p.resolve_ticket(w.admin, ticket, "2.50")
    refunds = [x for x in w.p.payments(w.rider) if x["kind"] == "refund"]
    assert [(x["amount"], x["method_id"], x["ref"]) for x in refunds] == [(D("-2.50"), w.card, rid)]


# =============================================================================== P. Earnings
def test_p1_driver_gets_75_percent_of_fare_minus_booking_fee(w):
    rid = w.ride()
    fare = w.p.ride(rid)["fare"]
    earned = day_earnings(w, w.driver)
    assert earned["fares"] == driver_share(fare)  # 9.05 -> 5.66
    assert earned["total"] == driver_share(fare)


def test_p1_share_rounded_half_up(w):
    rid = w.ride(dropoff=D_TIE)
    fare = w.p.ride(rid)["fare"]  # 9.16 -> 0.75 x 7.66 = 5.745 -> 5.75
    assert day_earnings(w, w.driver)["fares"] == driver_share(fare)


def test_p1_airport_surcharge_goes_to_driver(w):
    rid = w.ride(dropoff=AIRPORT)
    fare = w.p.ride(rid)["fare"]  # 27.31 -> 15.61 + 5.00
    earned = day_earnings(w, w.driver)
    assert earned["fares"] == driver_share(fare, airport=True)  # P2: fares include the surcharge
    assert earned["total"] == driver_share(fare, airport=True)


def test_p1_wait_fee_goes_to_driver(w):
    rid = w.ride(wait=8)  # 3 full minutes beyond 5 -> 0.90
    earned = day_earnings(w, w.driver)
    assert earned["fees"] == D("0.90")
    assert earned["total"] == driver_share(w.p.ride(rid)["fare"]) + D("0.90")


def test_p1_tip_goes_to_driver(w):
    rid = w.ride()
    w.p.add_tip(w.rider, rid, "4.00")
    earned = day_earnings(w, w.driver)
    assert earned["tips"] == D("4.00")
    assert earned["total"] == driver_share(w.p.ride(rid)["fare"]) + D("4.00")


def test_p1_rider_cancellation_fee_goes_to_driver(w):
    rid = w.request()
    w.p.accept_ride(w.driver, rid)
    w.p.advance(minutes=3)
    w.p.cancel_ride(w.rider, rid, "took too long")
    earned = day_earnings(w, w.driver)
    assert earned["fees"] == D("3.00")
    assert earned["total"] == D("3.00")


def test_p2_cancellation_fee_is_a_fee_not_a_trip(w):
    rid = w.request()
    w.p.accept_ride(w.driver, rid)
    w.p.advance(minutes=3)
    w.p.cancel_ride(w.rider, rid, "took too long")
    earned = day_earnings(w, w.driver)
    assert (earned["trips"], earned["fares"], earned["tips"], earned["fees"], earned["bonus"]) == (
        0, D("0.00"), D("0.00"), D("3.00"), D("0.00"))
    assert earned["total"] == D("3.00")


def test_p1_courier_pay_and_tip(w):
    w.eats()
    w.order(tip="2.00")
    earned = day_earnings(w, w.courier)
    assert earned["trips"] == 1
    assert earned["fares"] == D("3.83")
    assert earned["tips"] == D("2.00")
    assert earned["total"] == D("3.83") + D("2.00")


def test_p2_returns_all_fields(w):
    w.ride()
    earned = day_earnings(w, w.driver)
    assert {"trips", "fares", "tips", "fees", "bonus", "total"} <= set(earned)
    assert earned["bonus"] == D("0.00")


def test_p2_period_is_half_open(w):
    w.ride()
    done = w.p.now()
    assert w.p.earnings(w.driver, START, done)["trips"] == 0
    assert w.p.earnings(w.driver, START, done)["total"] == D("0.00")
    assert w.p.earnings(w.driver, done, done + timedelta(minutes=1))["trips"] == 1


def test_p2_sums_rides_in_period(w):
    first = w.ride()
    second = w.ride(dropoff=D_TIE, wait=7)  # wait fee 0.60
    w.p.add_tip(w.rider, first, "3.00")
    earned = day_earnings(w, w.driver)
    shares = driver_share(w.p.ride(first)["fare"]) + driver_share(w.p.ride(second)["fare"])
    assert earned["trips"] == 2
    assert earned["fares"] == shares
    assert earned["tips"] == D("3.00")
    assert earned["fees"] == D("0.60")
    assert earned["total"] == shares + D("3.60")


def test_p2_courier_trips_counted(w):
    w.eats()
    w.order()
    w.order()
    earned = day_earnings(w, w.courier)
    assert earned["trips"] == 2
    assert earned["fares"] == D("7.66")
    assert earned["total"] == D("7.66")


def test_p2_no_activity_is_zero(w):
    earned = day_earnings(w, w.driver)
    assert earned["trips"] == 0
    assert earned["total"] == D("0.00")


def _rides(w: World, count: int, **kwargs) -> list[str]:
    return [w.ride(**kwargs) for _ in range(count)]


def test_p3_bonus_with_20_rides_in_week(w):
    ids = _rides(w, 20)
    earned = w.p.earnings(w.driver, WEEK_START, WEEK_END)
    shares = sum((driver_share(w.p.ride(r)["fare"]) for r in ids), D("0"))
    assert earned["trips"] == 20
    assert earned["bonus"] == D("30.00")
    assert earned["total"] == shares + D("30.00")


def test_p3_no_bonus_with_19_rides(w):
    _rides(w, 19)
    earned = w.p.earnings(w.driver, WEEK_START, WEEK_END)
    assert earned["trips"] == 19
    assert earned["bonus"] == D("0.00")


def test_p3_rides_split_across_weeks_earn_no_bonus(w):
    _rides(w, 19)
    w.advance_to(WEEK_END + timedelta(hours=10))
    _rides(w, 1)
    assert w.p.earnings(w.driver, WEEK_START, WEEK_END)["bonus"] == D("0.00")
    following = w.p.earnings(w.driver, WEEK_END, WEEK_END + timedelta(days=7))
    assert following["trips"] == 1
    assert following["bonus"] == D("0.00")


def test_p4_pays_week_earnings_per_worker(w):
    w.eats()
    w.new_driver(location=None)  # approved, never drives: not paid
    rid = w.ride()
    w.order()
    w.advance_to(WEEK_END + timedelta(hours=1))
    paid = w.p.run_payouts(w.admin, WEEK)
    assert paid == {w.driver: driver_share(w.p.ride(rid)["fare"]), w.courier: D("3.83")}


def test_p4_payout_includes_bonus(w):
    ids = _rides(w, 20)
    w.advance_to(WEEK_END + timedelta(hours=1))
    paid = w.p.run_payouts(w.admin, WEEK)
    shares = sum((driver_share(w.p.ride(r)["fare"]) for r in ids), D("0"))
    assert paid == {w.driver: shares + D("30.00")}


def test_p4_second_run_same_week_rejected(w):
    w.ride()
    w.advance_to(WEEK_END + timedelta(hours=1))
    w.p.run_payouts(w.admin, WEEK)
    with pytest.raises(ValueError):
        w.p.run_payouts(w.admin, WEEK)


@pytest.mark.parametrize("week_start", [date(2026, 3, 3), date(2026, 3, 1), date(2026, 2, 24)])
def test_p4_week_start_must_be_monday(w, week_start):
    w.ride()
    w.advance_to(WEEK_END + timedelta(days=8))  # every candidate week has ended
    with pytest.raises(ValueError):
        w.p.run_payouts(w.admin, week_start)
    assert [n for n in w.p.notifications(w.driver) if n["kind"] == "payout"] == []


def test_p4_week_not_ended_rejected(w):
    rid = w.ride()
    with pytest.raises(ValueError):  # the current week
        w.p.run_payouts(w.admin, WEEK)
    with pytest.raises(ValueError):  # a future week
        w.p.run_payouts(w.admin, date(2026, 3, 16))
    w.advance_to(WEEK_END - timedelta(seconds=1))
    with pytest.raises(ValueError):  # one second before the week ends
        w.p.run_payouts(w.admin, WEEK)
    w.p.advance(seconds=1)  # now == week_start + 7 days: the week has ended
    assert w.p.run_payouts(w.admin, WEEK) == {w.driver: driver_share(w.p.ride(rid)["fare"])}


def test_p4_admins_only(w):
    w.ride()
    w.advance_to(WEEK_END + timedelta(hours=1))
    for actor in (w.rider, w.driver):
        with pytest.raises(PermissionError):
            w.p.run_payouts(actor, WEEK)
        with pytest.raises(PermissionError):  # X8: before the non-Monday date
            w.p.run_payouts(actor, date(2026, 3, 4))
    with pytest.raises(KeyError):
        w.p.run_payouts("ADM-999999", WEEK)
    assert w.p.run_payouts(w.admin, WEEK) == {w.driver: driver_share(w.p.ride("RDE-000001")["fare"])}


def test_p4_week_without_activity_pays_nobody(w):
    w.ride()
    assert w.p.run_payouts(w.admin, date(2026, 2, 23)) == {}


# =============================================================================== Q. Ratings
@pytest.mark.parametrize("stars", [0, 6, 4.5, 5.0, "5", True])
def test_q1_stars_an_int_from_1_to_5(w, stars):
    rid = w.ride()
    with pytest.raises(ValueError):
        w.p.rate(w.rider, rid, stars)
    assert w.p.rating(w.driver) is None


def test_q1_comment_up_to_500_characters(w):
    first = w.ride()
    with pytest.raises(ValueError):
        w.p.rate(w.rider, first, 5, "x" * 501)
    w.p.rate(w.rider, first, 5, "x" * 500)
    assert w.p.rating(w.driver) == D("5.00")


def test_x7_comment_is_stripped_before_the_length_check(w):
    rid = w.ride()
    w.p.rate(w.rider, rid, 4, "   " + "x" * 500 + "   ")
    assert w.p.rating(w.driver) == D("4.00")


def test_q1_rider_rates_driver_once(w):
    rid = w.ride()
    w.p.rate(w.rider, rid, 4, "ok")
    with pytest.raises(ValueError):
        w.p.rate(w.rider, rid, 5, "again")
    assert w.p.rating(w.driver) == D("4.00")


def test_q1_driver_rates_rider_once(w):
    rid = w.ride()
    w.p.rate(w.driver, rid, 3, "late", "rider")
    with pytest.raises(ValueError):
        w.p.rate(w.driver, rid, 5, "", "rider")
    assert w.p.rating(w.rider) == D("3.00")
    assert w.p.rating(w.driver) is None


def test_q1_ride_must_be_completed(w):
    rid = w.request()
    w.p.accept_ride(w.driver, rid)
    w.p.driver_arrived(w.driver, rid)
    w.p.start_ride(w.driver, rid)
    with pytest.raises(ValueError):
        w.p.rate(w.rider, rid, 5)


def test_q1_ride_rating_window_is_seven_days(w):
    late = w.ride()
    w.advance_to(w.p.now() + timedelta(days=7))  # X6: at exactly 7 days the window has passed
    with pytest.raises(ValueError):
        w.p.rate(w.rider, late, 5)
    with pytest.raises(ValueError):
        w.p.rate(w.driver, late, 5, "", "rider")
    assert w.p.rating(w.driver) is None


def test_q1_ride_rating_inside_seven_days(w):
    rid = w.ride()
    w.advance_to(w.p.now() + timedelta(days=7) - timedelta(seconds=1))
    w.p.rate(w.rider, rid, 5)
    w.p.rate(w.driver, rid, 4, "", "rider")
    assert w.p.rating(w.driver) == D("5.00")
    assert w.p.rating(w.rider) == D("4.00")


def test_q1_targets_driver_and_rider(w):
    rid = w.ride()
    w.p.rate(w.rider, rid, 5, "", target="driver")
    w.p.rate(w.driver, rid, 2, "", target="rider")
    assert (w.p.rating(w.driver), w.p.rating(w.rider)) == (D("5.00"), D("2.00"))
    with pytest.raises(ValueError):  # a ride has no restaurant or courier to rate
        w.p.rate(w.rider, rid, 5, "", target="restaurant")


def test_q1_outsider_cannot_rate(w):
    other = w.new_rider()
    rid = w.ride()
    with pytest.raises(PermissionError):
        w.p.rate(other, rid, 1)
    with pytest.raises(PermissionError):  # X8: before the invalid stars
        w.p.rate(other, rid, 0)
    with pytest.raises(KeyError):
        w.p.rate(w.rider, "RDE-999999", 5)
    assert w.p.rating(w.driver) is None


def test_q1_order_rates_restaurant_and_courier(w):
    w.eats()
    oid = w.order()
    w.p.rate(w.rider, oid, 4, "tasty", "restaurant")
    w.p.rate(w.rider, oid, 5, "fast", "courier")
    assert w.p.rating(w.restaurant) == D("4.00")
    assert w.p.rating(w.courier) == D("5.00")


def test_q1_order_target_rated_once(w):
    w.eats()
    oid = w.order()
    w.p.rate(w.rider, oid, 4, "", "restaurant")
    with pytest.raises(ValueError):
        w.p.rate(w.rider, oid, 2, "", "restaurant")
    assert w.p.rating(w.restaurant) == D("4.00")


def test_q1_order_must_be_delivered(w):
    w.eats()
    oid = w.place()
    w.p.accept_order(w.restaurant, oid)
    with pytest.raises(ValueError):
        w.p.rate(w.rider, oid, 5, "", "restaurant")


def test_q1_order_rating_window_is_seven_days(w):
    w.eats()
    oid = w.order()
    w.advance_to(w.p.now() + timedelta(days=7) - timedelta(seconds=1))
    w.p.rate(w.rider, oid, 4, "", "restaurant")
    w.p.advance(seconds=1)  # exactly 7 days after delivery
    with pytest.raises(ValueError):
        w.p.rate(w.rider, oid, 5, "", "courier")
    assert w.p.rating(w.courier) is None


def test_q1_order_target_must_be_restaurant_or_courier(w):
    w.eats()
    oid = w.order()
    with pytest.raises(ValueError):
        w.p.rate(w.rider, oid, 5, "", "driver")


def test_q2_none_without_ratings(w):
    w.ride()
    assert w.p.rating(w.driver) is None
    assert w.p.rating(w.rider) is None


@pytest.mark.parametrize("stars,expected", [
    ((5, 4, 4), "4.33"),
    ((5, 5, 4), "4.67"),
    ((5, 5, 5, 5, 5, 4, 4, 4), "4.63"),  # 4.625 rounded half up
    ((1, 2), "1.50"),
])
def test_q2_mean_rounded_half_up(w, stars, expected):
    for s in stars:
        rid = w.ride(minutes=1)
        w.p.rate(w.driver, rid, s, "", "rider")
    value = w.p.rating(w.rider)
    assert isinstance(value, Decimal)
    assert value == D(expected)


def test_q2_driver_rating_mean(w):
    first = w.ride()
    w.p.rate(w.rider, first, 5)
    second = w.ride()
    w.p.rate(w.rider, second, 4)
    assert w.p.rating(w.driver) == D("4.50")


def test_q2_only_last_100_ratings(w):
    rid = w.ride(minutes=1)
    w.p.rate(w.driver, rid, 1, "", "rider")
    for _ in range(99):
        rid = w.ride(minutes=1)
        w.p.rate(w.driver, rid, 5, "", "rider")
    assert w.p.rating(w.rider) == D("4.96")  # (1 + 99 x 5) / 100
    rid = w.ride(minutes=1)
    w.p.rate(w.driver, rid, 5, "", "rider")
    assert w.p.rating(w.rider) == D("5.00")  # the 1 is now the 101st most recent


def _rated_rides(w: World, stars: list[int]) -> None:
    for s in stars:
        rid = w.ride(minutes=1)
        w.p.rate(w.rider, rid, s)


def _nineteen_ratings_and_an_unrated_ride(w: World) -> str:
    # 12 fives then 7 fours keep the mean >= 4.60 (dispatch eligibility); a 3 on the returned ride
    # makes it 91/20 = 4.55.
    _rated_rides(w, [5] * 12 + [4] * 7)
    last = w.ride(minutes=1)
    assert w.p.get_account(w.driver)["status"] == "active"
    return last


def _under_review_driver(w: World) -> None:
    last = _nineteen_ratings_and_an_unrated_ride(w)
    assert w.p.worker_status(w.driver)["status"] == "available"
    w.p.rate(w.rider, last, 3)


def test_q3_below_threshold_with_20_ratings_under_review(w):
    _under_review_driver(w)
    assert w.p.rating(w.driver) == D("4.55")
    assert w.p.get_account(w.driver)["status"] == "under_review"
    with pytest.raises(ValueError):
        w.p.go_online(w.driver, P0, w.vehicle)


def test_q3_driver_without_active_ride_is_forced_offline(w):
    _under_review_driver(w)
    assert w.p.worker_status(w.driver)["status"] == "offline"


def test_q3_driver_with_active_ride_goes_offline_when_it_ends(w):
    last = _nineteen_ratings_and_an_unrated_ride(w)
    rid = _assigned(w)
    w.p.rate(w.rider, last, 3)
    assert w.p.get_account(w.driver)["status"] == "under_review"
    assert w.p.worker_status(w.driver)["status"] == "busy"
    assert w.p.ride(rid)["status"] == "driver_assigned"
    w.p.driver_arrived(w.driver, rid)
    w.p.start_ride(w.driver, rid)
    w.p.advance(minutes=5)
    w.p.complete_ride(w.driver, rid, [P0, D0])
    assert w.p.ride(rid)["status"] == "completed"
    assert w.p.worker_status(w.driver)["status"] == "offline"
    with pytest.raises(ValueError):
        w.p.go_online(w.driver, P0, w.vehicle)


def test_q3_threshold_uses_the_rounded_rating(w):
    # 26 fives, 15 fours and a 3: 193 / 42 = 4.5952..., rounded 4.60, so not below 4.60.
    # Every earlier mean is at least 190 / 41 = 4.63 (dispatch eligibility of F2).
    _rated_rides(w, [5] * 26 + [4] * 15 + [3])
    assert w.p.rating(w.driver) == D("4.60")
    assert w.p.get_account(w.driver)["status"] == "active"
    assert w.p.worker_status(w.driver)["status"] == "available"


def test_q3_reactivation_allows_going_online(w):
    _under_review_driver(w)
    w.p.reactivate(w.admin, w.driver)
    assert w.p.get_account(w.driver)["status"] == "active"
    w.p.go_online(w.driver, P0, w.vehicle)
    assert w.p.worker_status(w.driver)["status"] == "available"


def test_q3_exactly_threshold_stays_active(w):
    _rated_rides(w, [5] * 12 + [4] * 8)  # 92 / 20 = 4.60
    assert w.p.rating(w.driver) == D("4.60")
    assert w.p.get_account(w.driver)["status"] == "active"
    w.p.go_offline(w.driver)
    w.p.go_online(w.driver, P0, w.vehicle)
    assert w.p.worker_status(w.driver)["status"] == "available"


def test_q3_low_rating_with_few_ratings_stays_active(w):
    _rated_rides(w, [1])
    assert w.p.rating(w.driver) == D("1.00")
    assert w.p.get_account(w.driver)["status"] == "active"
    w.p.go_offline(w.driver)
    w.p.go_online(w.driver, P0, w.vehicle)
    assert w.p.worker_status(w.driver)["status"] == "available"


# =============================================================================== R. Support
TEXT = "Something went wrong on this trip"


def test_r1_rider_report_returns_ticket_ids(w):
    rid = w.ride()
    assert w.p.report_issue(w.rider, rid, "lost_item", "I left my umbrella in the car") == "TCK-000001"
    assert w.p.report_issue(w.rider, rid, "overcharge", TEXT) == "TCK-000002"


def test_r1_driver_of_ride_may_report(w):
    rid = w.ride()
    assert w.p.report_issue(w.driver, rid, "lost_item", "The rider left a phone behind").startswith("TCK-")


def test_r1_other_actor_forbidden(w):
    other = w.new_rider()
    rid = w.ride()
    with pytest.raises(PermissionError):
        w.p.report_issue(other, rid, "overcharge", TEXT)


def test_r1_unknown_kind_rejected(w):
    rid = w.ride()
    with pytest.raises(ValueError):
        w.p.report_issue(w.rider, rid, "rude_driver", TEXT)


def test_r1_missing_items_not_for_rides(w):
    rid = w.ride()
    with pytest.raises(ValueError):
        w.p.report_issue(w.rider, rid, "missing_items", TEXT)


def test_r1_missing_items_for_orders(w):
    w.eats()
    oid = w.order()
    assert w.p.report_issue(w.rider, oid, "missing_items", "The drink was not in the bag").startswith("TCK-")


@pytest.mark.parametrize("length", [9, 1001])
def test_r1_text_length_out_of_range_rejected(w, length):
    rid = w.ride()
    with pytest.raises(ValueError):
        w.p.report_issue(w.rider, rid, "overcharge", "x" * length)


@pytest.mark.parametrize("length", [10, 1000])
def test_r1_text_length_bounds_accepted(w, length):
    rid = w.ride()
    assert w.p.report_issue(w.rider, rid, "overcharge", "x" * length).startswith("TCK-")


def test_x7_ticket_text_is_stripped_before_the_length_check(w):
    rid = w.ride()
    with pytest.raises(ValueError):  # 9 characters once stripped
        w.p.report_issue(w.rider, rid, "overcharge", "    " + "x" * 9 + "    ")
    with pytest.raises(ValueError):
        w.p.report_issue(w.rider, rid, "overcharge", " " * 20)
    assert w.p.report_issue(w.rider, rid, "overcharge", "  " + "x" * 1000 + "  ") == "TCK-000001"


def test_r1_any_status_may_be_reported(w):
    requested = w.request()
    assert w.p.ride(requested)["status"] == "requested"
    assert w.p.report_issue(w.rider, requested, "safety", "The app showed a strange driver") == "TCK-000001"
    w.p.cancel_ride(w.rider, requested, "changed my mind")
    assert w.p.report_issue(w.rider, requested, "lost_item", "Left my scarf at the pickup") == "TCK-000002"
    assigned = _assigned(w)
    assert w.p.report_issue(w.driver, assigned, "safety", "The pickup street is unsafe") == "TCK-000003"


def test_r1_order_reported_only_by_its_rider(w):
    w.eats()
    other = w.new_rider()
    oid = w.order()
    for actor in (w.courier, w.restaurant, other, w.driver):
        with pytest.raises(PermissionError):
            w.p.report_issue(actor, oid, "missing_items", "The drink was not in the bag")
    assert w.p.report_issue(w.rider, oid, "missing_items", "The drink was not in the bag") == "TCK-000001"


def test_r1_unknown_ref(w):
    with pytest.raises(KeyError):
        w.p.report_issue(w.rider, "RDE-999999", "overcharge", TEXT)
    with pytest.raises(KeyError):
        w.p.report_issue(w.rider, "ORD-999999", "missing_items", TEXT)
    with pytest.raises(KeyError):  # X8: before the invalid kind and text
        w.p.report_issue(w.rider, "RDE-999999", "rude_driver", "short")


def test_x8_outsider_report_is_a_permission_error_before_invalid_input(w):
    other = w.new_rider()
    rid = w.ride()
    with pytest.raises(PermissionError):
        w.p.report_issue(other, rid, "rude_driver", "short")
    assert w.p.report_issue(w.rider, rid, "overcharge", TEXT) == "TCK-000001"


def test_r1_safety_ticket_notifies_every_admin(w):
    second = w.new_admin()
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "safety", "The driver was speeding all the way")
    for admin in (w.admin, second):
        notes = [n for n in w.p.notifications(admin) if n["kind"] == "safety_ticket"]
        assert [(n["ref"], n["at"]) for n in notes] == [(ticket, w.p.now())]
    assert [n for n in w.p.notifications(w.rider) if n["kind"] == "safety_ticket"] == []


def test_r1_safety_ticket_by_a_driver_notifies_admins(w):
    rid = w.ride()
    w.p.advance(minutes=2)
    ticket = w.p.report_issue(w.driver, rid, "safety", "The rider threatened me during the trip")
    assert kinds(w.p, w.admin, {"safety_ticket"}) == [("safety_ticket", ticket)]


def test_r1_other_tickets_do_not_notify_admins(w):
    rid = w.ride()
    w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    assert [n for n in w.p.notifications(w.admin) if n["kind"] == "safety_ticket"] == []


def test_r2_refund_back_to_card(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    w.p.resolve_ticket(w.admin, ticket, "2.00")
    refunds = [x for x in w.p.payments(w.rider) if x["kind"] == "refund"]
    assert [(x["amount"], x["method_id"]) for x in refunds] == [(D("-2.00"), w.card)]


def test_r2_refund_back_to_wallet(w):
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    rid = w.ride(method=w.wallet())
    before = w.p.wallet_balance(w.rider)
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    w.p.resolve_ticket(w.admin, ticket, "2.00")
    assert w.p.wallet_balance(w.rider) == before + D("2.00")


def test_r2_ticket_resolved_once(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    w.p.resolve_ticket(w.admin, ticket, "1.00")
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, ticket, "1.00")
    assert len([x for x in w.p.payments(w.rider) if x["kind"] == "refund"]) == 1


def test_r2_refund_cannot_exceed_charged(w):
    rid = w.ride()
    charged = w.p.ride(rid)["charged"]
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, ticket, charged + CENT)
    w.p.resolve_ticket(w.admin, ticket, charged)
    assert [x["amount"] for x in w.p.payments(w.rider) if x["kind"] == "refund"] == [-charged]


def test_r2_earlier_refunds_reduce_the_limit(w):
    rid = w.ride()
    charged = w.p.ride(rid)["charged"]
    first = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    second = w.p.report_issue(w.rider, rid, "lost_item", "I also lost my sunglasses there")
    w.p.resolve_ticket(w.admin, first, "5.00")
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, second, charged - D("5.00") + CENT)
    w.p.resolve_ticket(w.admin, second, charged - D("5.00"))


def test_r2_negative_refund_rejected(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, ticket, "-1.00")
    w.p.resolve_ticket(w.admin, ticket, "1.00")  # the failed call did not resolve it


def test_r2_zero_refund_resolves_the_ticket(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    w.p.resolve_ticket(w.admin, ticket, "0")
    with pytest.raises(ValueError):  # resolved once
        w.p.resolve_ticket(w.admin, ticket, "0")
    assert [x for x in w.p.payments(w.rider) if x["kind"] == "refund" and x["amount"] != 0] == []


def test_r2_refund_limit_includes_tips(w):
    rid = w.ride()
    w.p.add_tip(w.rider, rid, "4.00")
    limit = w.p.ride(rid)["charged"] + D("4.00")
    first = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    second = w.p.report_issue(w.rider, rid, "lost_item", "I also lost my sunglasses there")
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, first, limit + CENT)
    w.p.resolve_ticket(w.admin, first, limit - D("1.00"))
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, second, D("1.01"))
    w.p.resolve_ticket(w.admin, second, D("1.00"))
    refunds = [x["amount"] for x in w.p.payments(w.rider) if x["kind"] == "refund"]
    assert refunds == [-(limit - D("1.00")), D("-1.00")]


def test_r2_refund_of_a_cancellation_fee(w):
    rid = w.request()
    w.p.accept_ride(w.driver, rid)
    w.p.advance(minutes=3)
    w.p.cancel_ride(w.rider, rid, "took too long")  # 3.00 fee captured
    ticket = w.p.report_issue(w.rider, rid, "overcharge", "I was charged a fee for a late driver")
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, ticket, "3.01")
    w.p.resolve_ticket(w.admin, ticket, "3.00")
    refunds = [(x["amount"], x["ref"], x["method_id"]) for x in w.p.payments(w.rider) if x["kind"] == "refund"]
    assert refunds == [(D("-3.00"), rid, w.card)]


def test_r2_admins_only(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    for actor in (w.rider, w.driver):
        with pytest.raises(PermissionError):
            w.p.resolve_ticket(actor, ticket, "1.00")
        with pytest.raises(PermissionError):  # X8: before the negative refund
            w.p.resolve_ticket(actor, ticket, "-1.00")
    with pytest.raises(KeyError):  # X8: unknown ticket before the permission problem
        w.p.resolve_ticket(w.rider, "TCK-999999", "1.00")
    assert w.p.payments(w.rider)[-1]["kind"] == "ride"


def test_r2_order_refund_recorded(w):
    w.eats()
    oid = w.order()
    ticket = w.p.report_issue(w.rider, oid, "missing_items", "The drink was not in the bag")
    w.p.resolve_ticket(w.admin, ticket, "3.00")
    assert w.p.order(oid)["refunded"] == D("3.00")
    refunds = [x for x in w.p.payments(w.rider) if x["kind"] == "refund"]
    assert [(x["amount"], x["method_id"], x["ref"]) for x in refunds] == [(D("-3.00"), w.card, oid)]


def test_r2_order_refund_limit_is_the_charged_total(w):
    w.eats()
    oid = w.order(tip="2.00")
    charged = w.p.order(oid)["charged"]
    assert charged == D("18.06")
    ticket = w.p.report_issue(w.rider, oid, "missing_items", "The whole bag was missing items")
    with pytest.raises(ValueError):
        w.p.resolve_ticket(w.admin, ticket, charged + CENT)
    w.p.resolve_ticket(w.admin, ticket, charged)
    assert w.p.order(oid)["refunded"] == charged


def _assigned(w: World, driver_at=P0) -> str:
    w.p.update_location(w.driver, driver_at)
    rid = w.request()
    w.p.accept_ride(w.driver, rid)
    return rid


def test_r3_trip_status_fields(w):
    rid = _assigned(w)
    token = w.p.share_trip(w.rider, rid)
    status = w.p.trip_status(token)
    assert status["status"] == "driver_assigned"
    assert status["driver_name"] == "Dan Driver"
    assert status["vehicle_plate"] == "ABC123"


def test_r3_eta_to_pickup_before_start(w):
    rid = _assigned(w, driver_at=D0)
    token = w.p.share_trip(w.rider, rid)
    assert w.p.trip_status(token)["eta_minutes"] == w.p.eta_minutes(D0, P0) == 7
    w.p.update_location(w.driver, MID)
    assert w.p.trip_status(token)["eta_minutes"] == w.p.eta_minutes(MID, P0)


def test_r3_eta_to_dropoff_after_start(w):
    rid = _assigned(w)
    token = w.p.share_trip(w.rider, rid)
    w.p.driver_arrived(w.driver, rid)
    w.p.start_ride(w.driver, rid)
    assert w.p.trip_status(token)["status"] == "in_progress"
    assert w.p.trip_status(token)["eta_minutes"] == w.p.eta_minutes(P0, D0) == 7
    w.p.update_location(w.driver, MID)
    assert w.p.trip_status(token)["eta_minutes"] == w.p.eta_minutes(MID, D0)


def test_r3_no_contact_data(w):
    rid = _assigned(w)
    status = w.p.trip_status(w.p.share_trip(w.rider, rid))
    text = repr(status)
    assert "@" not in text
    for phone in ("+573200000001", "3200000001", "+573100000001", "3100000001"):
        assert phone not in text


def test_r3_token_invalid_after_completion(w):
    rid = _assigned(w)
    token = w.p.share_trip(w.rider, rid)
    w.p.driver_arrived(w.driver, rid)
    w.p.start_ride(w.driver, rid)
    w.p.advance(minutes=5)
    w.p.complete_ride(w.driver, rid, [P0, D0])
    with pytest.raises(KeyError):
        w.p.trip_status(token)


def test_r3_token_invalid_after_cancellation(w):
    rid = _assigned(w)
    token = w.p.share_trip(w.rider, rid)
    w.p.cancel_ride(w.rider, rid, "changed my mind")
    with pytest.raises(KeyError):
        w.p.trip_status(token)


def test_r3_unknown_token(w):
    with pytest.raises(KeyError):
        w.p.trip_status("no-such-token")


def test_r3_only_the_rider_shares(w):
    other = w.new_rider()
    rid = _assigned(w)
    with pytest.raises(PermissionError):
        w.p.share_trip(other, rid)


def test_r3_ended_ride_cannot_be_shared(w):
    rid = w.ride()
    with pytest.raises(ValueError):
        w.p.share_trip(w.rider, rid)


def test_r3_requested_ride_cannot_be_shared(w):
    rid = w.request()
    assert w.p.ride(rid)["status"] == "requested"
    with pytest.raises(ValueError):
        w.p.share_trip(w.rider, rid)


def test_r3_cancelled_or_no_driver_ride_cannot_be_shared(w):
    cancelled = _assigned(w)
    w.p.cancel_ride(w.rider, cancelled, "changed my mind")
    with pytest.raises(ValueError):
        w.p.share_trip(w.rider, cancelled)
    w.p.go_offline(w.driver)
    lonely = w.request()
    assert w.p.ride(lonely)["status"] == "no_driver"
    with pytest.raises(ValueError):
        w.p.share_trip(w.rider, lonely)


def test_r3_scheduled_ride_cannot_be_shared(w):
    rid = w.p.schedule_ride(w.rider, P0, D0, "economy", START + timedelta(hours=2), w.card)
    with pytest.raises(ValueError):
        w.p.share_trip(w.rider, rid)


def test_r3_arrived_and_in_progress_rides_can_be_shared(w):
    rid = _assigned(w)
    w.p.driver_arrived(w.driver, rid)
    arrived = w.p.share_trip(w.rider, rid)
    assert w.p.trip_status(arrived)["status"] == "arrived"
    w.p.start_ride(w.driver, rid)
    started = w.p.share_trip(w.rider, rid)
    assert w.p.trip_status(started)["status"] == "in_progress"


def test_x8_other_rider_sharing_an_ended_ride_is_a_permission_error(w):
    other = w.new_rider()
    rid = w.ride()
    with pytest.raises(PermissionError):
        w.p.share_trip(other, rid)
    with pytest.raises(KeyError):
        w.p.share_trip(other, "RDE-999999")


# =============================================================================== S. Notifications
def test_s1_ride_offer_notifies_driver(w):
    rid = w.request()
    offers = [(n["at"], n["ref"]) for n in w.p.notifications(w.driver) if n["kind"] == "ride_offer"]
    assert offers == [(START, rid)]


def test_s1_rider_ride_lifecycle_in_order(w):
    p = w.p
    rid = w.request()
    p.advance(minutes=1)
    p.accept_ride(w.driver, rid)
    assigned = p.now()
    p.advance(minutes=1)
    p.driver_arrived(w.driver, rid)
    arrived = p.now()
    p.start_ride(w.driver, rid)
    p.advance(minutes=6)
    p.complete_ride(w.driver, rid, [P0, D0])
    done = p.now()
    wanted = {"ride_assigned", "driver_arrived", "ride_completed"}
    got = [(n["kind"], n["ref"], n["at"]) for n in p.notifications(w.rider) if n["kind"] in wanted]
    assert got == [("ride_assigned", rid, assigned), ("driver_arrived", rid, arrived),
                   ("ride_completed", rid, done)]


def test_s1_no_driver_notifies_rider(w):
    w.p.go_offline(w.driver)
    rid = w.request()
    assert w.p.ride(rid)["status"] == "no_driver"
    assert kinds(w.p, w.rider, {"no_driver"}) == [("no_driver", rid)]


def test_s1_admin_ride_cancellation_notifies_rider(w):
    rid = _assigned(w)
    w.p.cancel_ride(w.admin, rid, "safety check")
    assert kinds(w.p, w.rider, {"ride_cancelled"}) == [("ride_cancelled", rid)]


def test_s1_new_order_notifies_restaurant(w):
    w.eats()
    oid = w.place()
    notes = [(n["at"], n["ref"]) for n in w.p.notifications(w.restaurant) if n["kind"] == "new_order"]
    assert notes == [(START, oid)]


def test_s1_order_assigned_notifies_courier(w):
    w.eats()
    oid = w.place()
    w.p.advance(minutes=2)
    w.p.accept_order(w.restaurant, oid)
    notes = [(n["at"], n["ref"]) for n in w.p.notifications(w.courier) if n["kind"] == "order_assigned"]
    assert notes == [(w.p.now(), oid)]


def test_s1_rider_order_lifecycle_in_order(w):
    w.eats()
    oid = w.order()
    wanted = {"order_accepted", "order_ready", "order_delivered"}
    assert kinds(w.p, w.rider, wanted) == [("order_accepted", oid), ("order_ready", oid),
                                           ("order_delivered", oid)]
    delivered = [n for n in w.p.notifications(w.rider) if n["kind"] == "order_delivered"]
    assert delivered[0]["at"] == w.p.now()


def test_s1_admin_order_cancellation_notifies_rider(w):
    w.eats()
    oid = w.place()
    w.p.accept_order(w.restaurant, oid)
    w.p.cancel_order(w.admin, oid, "restaurant closed early")
    assert kinds(w.p, w.rider, {"order_cancelled"}) == [("order_cancelled", oid)]


def test_s1_payout_notifies_worker(w):
    w.ride()
    w.advance_to(WEEK_END + timedelta(hours=1))
    w.p.run_payouts(w.admin, WEEK)
    notes = [n for n in w.p.notifications(w.driver) if n["kind"] == "payout"]
    assert len(notes) == 1
    assert notes[0]["at"] == w.p.now()


def test_s1_notifications_have_fields_in_time_order(w):
    w.eats()
    w.ride()
    w.order()
    for account in (w.rider, w.driver, w.restaurant, w.courier):
        notes = w.p.notifications(account)
        assert notes
        assert all({"at", "kind", "ref"} <= set(n) for n in notes)
        times = [n["at"] for n in notes]
        assert times == sorted(times)


# =============================================================================== T. Reports
def _cancelled_request(w: World) -> str:
    rid = w.request()
    w.p.cancel_ride(w.rider, rid, "changed my mind")
    return rid


def test_t1_ride_counts(w):
    w.ride()
    w.ride()
    _cancelled_request(w)
    report = w.p.daily_report(w.admin, DAY)
    assert report["rides_completed"] == 2
    assert report["rides_cancelled"] == 1


def test_t1_cancellation_rate_four_places(w):
    w.ride()
    w.ride()
    _cancelled_request(w)
    assert D(str(w.p.daily_report(w.admin, DAY)["cancellation_rate"])) == D("0.3333")


def test_t1_cancellation_rate_two_thirds(w):
    w.ride()
    _cancelled_request(w)
    _cancelled_request(w)
    assert D(str(w.p.daily_report(w.admin, DAY)["cancellation_rate"])) == D("0.6667")


def test_t1_ride_revenue_sums_charges(w):
    first = w.ride()
    second = w.ride(dropoff=D_TIE)
    report = w.p.daily_report(w.admin, DAY)
    assert report["ride_revenue"] == w.p.ride(first)["charged"] + w.p.ride(second)["charged"]


def test_t1_platform_revenue_is_charges_minus_driver_pay(w):
    ids = [w.ride(), w.ride(dropoff=D_TIE)]
    report = w.p.daily_report(w.admin, DAY)
    charges = sum((w.p.ride(r)["charged"] for r in ids), D("0"))
    shares = sum((driver_share(w.p.ride(r)["fare"]) for r in ids), D("0"))
    assert report["platform_revenue"] == charges - shares  # 18.21 - 11.41 = 6.80


def test_t1_order_revenue(w):
    w.eats()
    oid = w.order()
    report = w.p.daily_report(w.admin, DAY)
    assert report["orders_delivered"] == 1
    assert report["order_revenue"] == w.p.order(oid)["charged"]


def test_t1_ride_revenue_counts_only_completed_rides(w):
    done = w.ride()
    fee_ride = w.request()
    w.p.accept_ride(w.driver, fee_ride)
    w.p.advance(minutes=3)
    w.p.cancel_ride(w.rider, fee_ride, "took too long")  # 3.00 fee captured
    report = w.p.daily_report(w.admin, DAY)
    assert report["ride_revenue"] == w.p.ride(done)["charged"]
    assert (report["rides_completed"], report["rides_cancelled"]) == (1, 1)


def test_t1_platform_revenue_with_a_cancellation_fee(w):
    done = w.ride()
    fee_ride = w.request()
    w.p.accept_ride(w.driver, fee_ride)
    w.p.advance(minutes=3)
    w.p.cancel_ride(w.rider, fee_ride, "took too long")
    report = w.p.daily_report(w.admin, DAY)
    charged = w.p.ride(done)["charged"]
    share = driver_share(w.p.ride(done)["fare"])
    # captured: charged + 3.00 fee; earnings: share + 3.00 fee (it goes entirely to the driver)
    assert report["platform_revenue"] == charged + D("3.00") - (share + D("3.00"))


def test_t1_platform_revenue_with_an_order(w):
    w.eats()
    oid = w.order(tip="2.00")
    assert w.p.order(oid)["charged"] == D("18.06")
    report = w.p.daily_report(w.admin, DAY)
    # captured 18.06 minus the courier's earnings 3.83 + 2.00 tip; restaurants are not paid
    assert report["platform_revenue"] == D("12.23")


def test_t1_no_driver_rides_are_neither_completed_nor_cancelled(w):
    w.ride()
    w.p.go_offline(w.driver)
    lonely = w.request()
    assert w.p.ride(lonely)["status"] == "no_driver"
    report = w.p.daily_report(w.admin, DAY)
    assert (report["rides_completed"], report["rides_cancelled"]) == (1, 0)
    assert D(str(report["cancellation_rate"])) == D("0")


def test_t1_only_no_driver_rides_give_rate_zero(w):
    w.p.go_offline(w.driver)
    w.request()
    report = w.p.daily_report(w.admin, DAY)
    assert (report["rides_completed"], report["rides_cancelled"]) == (0, 0)
    assert D(str(report["cancellation_rate"])) == D("0")


def test_t1_other_day_is_empty(w):
    w.eats()
    w.ride()
    _cancelled_request(w)
    w.order()
    report = w.p.daily_report(w.admin, date(2026, 3, 3))
    assert report["rides_completed"] == 0
    assert report["rides_cancelled"] == 0
    assert report["orders_delivered"] == 0
    assert report["ride_revenue"] == D("0.00")
    assert report["order_revenue"] == D("0.00")
    assert report["cancellation_rate"] == 0


def _three_restaurants(w: World) -> tuple[str, str, list[str], str, str]:
    """Restaurant A with two orders (32.12), B (cheap, 14.71) and C (pricey, 45.20) with one each."""
    w.eats()
    cheap, cheap_item = w.add_restaurant(RB, "9.00")
    pricey, pricey_item = w.add_restaurant(RC, "40.00")
    a_orders = [w.order(), w.order()]
    b_order = w.order(restaurant=cheap, item=cheap_item, dropoff=MID_BC)
    c_order = w.order(restaurant=pricey, item=pricey_item, dropoff=MID_BC)
    return cheap, pricey, a_orders, b_order, c_order


def test_t2_ranked_by_orders_then_revenue(w):
    cheap, pricey, a_orders, b_order, c_order = _three_restaurants(w)
    top = w.p.top_restaurants(w.admin, START, w.p.now() + timedelta(minutes=1))
    revenue = lambda oid: w.p.order(oid)["charged"]  # noqa: E731
    assert [(t["restaurant_id"], t["orders"], t["revenue"]) for t in top] == [
        (w.restaurant, 2, revenue(a_orders[0]) + revenue(a_orders[1])),
        (pricey, 1, revenue(c_order)),
        (cheap, 1, revenue(b_order)),
    ]


def test_t2_limit(w):
    _cheap, pricey, *_ = _three_restaurants(w)
    top = w.p.top_restaurants(w.admin, START, w.p.now() + timedelta(minutes=1), limit=2)
    assert [t["restaurant_id"] for t in top] == [w.restaurant, pricey]


def test_t2_ties_go_to_lower_id(w):
    first, first_item = w.add_restaurant(RB, "12.00")
    second, second_item = w.add_restaurant(RC, "12.00")
    w.courier = w.add_courier()
    w.order(restaurant=second, item=second_item, dropoff=MID_BC)
    w.order(restaurant=first, item=first_item, dropoff=MID_BC)
    top = w.p.top_restaurants(w.admin, START, w.p.now() + timedelta(minutes=1))
    assert [(t["restaurant_id"], t["orders"]) for t in top] == [(first, 1), (second, 1)]
    assert top[0]["revenue"] == top[1]["revenue"]


def test_t2_only_delivered_orders_in_period(w):
    w.eats()
    other, other_item = w.add_restaurant(RB, "12.00")
    first = w.order()
    cut = w.p.now() + timedelta(seconds=1)
    rejected = w.place(restaurant=other, item=other_item, dropoff=MID_BC)
    w.p.reject_order(other, rejected, "out of stock")
    w.order()
    top = w.p.top_restaurants(w.admin, START, cut)
    assert [(t["restaurant_id"], t["orders"], t["revenue"]) for t in top] == [
        (w.restaurant, 1, w.p.order(first)["charged"])]  # `other` has no delivered order: not listed


def test_t2_restaurant_without_orders_in_the_period_is_not_listed(w):
    w.eats()
    w.add_restaurant(RB, "12.00")
    w.order()
    later = w.p.now() + timedelta(minutes=1)
    assert w.p.top_restaurants(w.admin, later, later + timedelta(days=1)) == []


def test_t3_setup_actions_logged(w):
    log = w.p.audit_log(w.admin)
    assert [(e["action"], e["target"], e["admin_id"], e["at"]) for e in log] == [
        ("add_zone", w.city, w.admin, START),
        ("add_zone", w.airport, w.admin, START),
        ("approve", w.driver, w.admin, START),
    ]


def test_t3_suspend_and_reactivate_logged(w):
    w.p.advance(minutes=3)
    w.p.suspend(w.admin, w.rider, "chargeback fraud")
    w.p.advance(minutes=2)
    w.p.reactivate(w.admin, w.rider)
    tail = [(e["action"], e["target"], e["admin_id"], e["at"]) for e in w.p.audit_log(w.admin)[-2:]]
    assert tail == [("suspend", w.rider, w.admin, START + timedelta(minutes=3)),
                    ("reactivate", w.rider, w.admin, START + timedelta(minutes=5))]


def test_t3_zone_promo_and_payout_actions_logged(w):
    w.p.set_surge_cap(w.admin, w.city, D("1.5"))
    w.p.create_promo(w.admin, "SAVE10", "rides", "percent", 10)
    w.p.run_payouts(w.admin, date(2026, 2, 23))
    tail = w.p.audit_log(w.admin)[-3:]
    assert [(e["action"], e["target"], e["admin_id"], e["at"]) for e in tail] == [
        ("set_surge_cap", w.city, w.admin, START),
        ("create_promo", "SAVE10", w.admin, START),  # the promo code
        ("run_payouts", "2026-02-23", w.admin, START),  # the week start in ISO format
    ]


def test_t3_add_zone_target_is_the_new_zone(w):
    zone = w.p.add_zone(w.admin, "South", (4.55, -74.10), 3, 25)
    assert [(e["action"], e["target"]) for e in w.p.audit_log(w.admin)][-1] == ("add_zone", zone)


def test_t3_resolve_ticket_and_admin_cancellation_logged(w):
    rid = w.ride()
    ticket = w.p.report_issue(w.rider, rid, "overcharge", TEXT)
    w.p.resolve_ticket(w.admin, ticket, "1.00")
    assert [(e["action"], e["target"]) for e in w.p.audit_log(w.admin)][-1] == ("resolve_ticket", ticket)
    count = len(w.p.audit_log(w.admin))
    cancelled = w.request()
    w.p.cancel_ride(w.admin, cancelled, "duplicate request")
    log = w.p.audit_log(w.admin)
    assert len(log) == count + 1
    assert (log[-1]["action"], log[-1]["target"], log[-1]["admin_id"]) == ("cancel_ride", cancelled, w.admin)


def test_t3_admin_order_cancellation_logged(w):
    w.eats()
    oid = w.place()
    w.p.advance(minutes=1)
    w.p.cancel_order(w.admin, oid, "restaurant closed early")
    entry = w.p.audit_log(w.admin)[-1]
    assert (entry["action"], entry["target"], entry["admin_id"], entry["at"]) == (
        "cancel_order", oid, w.admin, START + timedelta(minutes=1))


def test_t3_rider_cancellations_are_not_logged(w):
    w.eats()
    before = w.p.audit_log(w.admin)
    rid = w.request()
    w.p.cancel_ride(w.rider, rid, "changed my mind")
    oid = w.place()
    w.p.cancel_order(w.rider, oid, "changed my mind")
    assert w.p.audit_log(w.admin) == before


def test_t3_lists_actions_of_every_admin(w):
    second = w.new_admin()
    w.p.suspend(second, w.rider, "abusive language")
    entry = w.p.audit_log(w.admin)[-1]
    assert (entry["action"], entry["target"], entry["admin_id"]) == ("suspend", w.rider, second)


def test_t3_non_admin_and_failed_actions_not_logged(w):
    before = w.p.audit_log(w.admin)
    w.ride()
    bare = w.new_driver(approve=False)
    w.p.go_offline(w.driver)
    with pytest.raises(ValueError):
        w.p.suspend(w.admin, w.rider, "")  # failed admin action
    assert w.p.audit_log(w.admin) == before
    w.p.approve(w.admin, bare)
    assert len(w.p.audit_log(w.admin)) == len(before) + 1


@pytest.mark.parametrize("call", ["daily_report", "top_restaurants", "audit_log"])
def test_t4_reports_admin_only(w, call):
    args = {"daily_report": (DAY,), "top_restaurants": (START, START + timedelta(days=1)),
            "audit_log": ()}[call]
    for actor in (w.rider, w.driver):
        with pytest.raises(PermissionError):
            getattr(w.p, call)(actor, *args)
    with pytest.raises(KeyError):  # X8: an unknown actor is reported first
        getattr(w.p, call)("ADM-999999", *args)


# =============================================================================== U. Persistence
def _db(tmp_path: Path) -> str:
    return str(tmp_path / "rides.db")


def test_u1_clock_and_counters_continue(tmp_path):
    w = World(_db(tmp_path))
    w.ride()
    w.p.advance(minutes=37)
    w.p.save()
    q = Platform.open(_db(tmp_path))
    assert q.now() == w.p.now()
    assert q.register_rider("Nina New", "nina@rides.test", "+573300000099", PW) == "RID-000002"
    assert q.add_card(w.rider, "5555555555554444", 12, 2030, "123") == "PM-000002"
    assert q.add_vehicle(w.driver, "XYZ789", "Kia", "Rio", 2021, 4, "economy") == "VEH-000002"
    quote = q.quote_ride(w.rider, P0, D0, "economy")
    assert quote["quote_id"] == "QTE-000002"
    assert quote["expires_at"] == w.p.now() + timedelta(minutes=5)
    assert q.request_ride(w.rider, quote["quote_id"], w.card) == "RDE-000002"


def test_u1_password_hashes_survive(tmp_path):
    w = World(_db(tmp_path))
    w.p.save()
    q = Platform.open(_db(tmp_path))
    assert q.authenticate("RITA@rides.test", PW) == w.rider
    assert q.authenticate("dan@rides.test", PW) == w.driver
    with pytest.raises(PermissionError):
        q.authenticate("rita@rides.test", PW + "x")


def test_u1_lockout_survives(tmp_path):
    w = World(_db(tmp_path))
    for _ in range(5):
        with pytest.raises(PermissionError):
            w.p.authenticate("rita@rides.test", "wrong-password-1")
    w.p.save()
    q = Platform.open(_db(tmp_path))
    with pytest.raises(PermissionError):
        q.authenticate("rita@rides.test", PW)
    q.advance(minutes=16)
    assert q.authenticate("rita@rides.test", PW) == w.rider


def test_u1_pending_offer_expires_after_reopen(tmp_path):
    w = World(_db(tmp_path))
    second = w.new_driver(location=D0)
    rid = w.request()
    assert w.p.ride(rid)["offered_to"] == w.driver
    w.p.advance(seconds=10)
    w.p.save()
    q = Platform.open(_db(tmp_path))
    assert q.ride(rid)["offered_to"] == w.driver
    q.advance(seconds=6)
    assert q.ride(rid)["status"] == "requested"
    assert q.ride(rid)["offered_to"] == second
    assert [n["ref"] for n in q.notifications(second) if n["kind"] == "ride_offer"] == [rid]


def test_u1_pending_offer_accepted_after_reopen(tmp_path):
    w = World(_db(tmp_path))
    rid = w.request()
    w.p.save()
    q = Platform.open(_db(tmp_path))
    q.accept_ride(w.driver, rid)
    assert q.ride(rid)["status"] == "driver_assigned"
    assert q.worker_status(w.driver)["status"] == "busy"


def test_u1_last_offer_expiry_after_reopen_releases_ride(tmp_path):
    w = World(_db(tmp_path))
    rid = w.request()
    w.p.save()
    q = Platform.open(_db(tmp_path))
    q.advance(seconds=16)
    assert q.ride(rid)["status"] == "no_driver"
    assert ("no_driver", rid) in kinds(q, w.rider, {"no_driver"})


def test_u1_wallet_hold_survives(tmp_path):
    w = World(_db(tmp_path)).eats()
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    rid = w.request(method=w.wallet())  # hold 9.05
    w.p.save()
    q = Platform.open(_db(tmp_path))
    lines = [{"item_id": w.item, "quantity": 1}]
    with pytest.raises(ValueError):  # total 16.06 > 10.95 left
        q.place_order(w.rider, w.restaurant, lines, OD, w.wallet())
    q.accept_ride(w.driver, rid)
    q.driver_arrived(w.driver, rid)
    q.start_ride(w.driver, rid)
    q.advance(minutes=5)
    q.complete_ride(w.driver, rid, [P0, D0])
    assert q.wallet_balance(w.rider) == D("20.00") - q.ride(rid)["charged"]


def test_u1_order_acceptance_timer_survives(tmp_path):
    w = World(_db(tmp_path)).eats()
    oid = w.place()
    w.p.save()
    q = Platform.open(_db(tmp_path))
    q.advance(minutes=6)
    assert q.order(oid)["status"] == "expired"


def test_u1_scheduled_dispatch_survives(tmp_path):
    w = World(_db(tmp_path))
    rid = w.p.schedule_ride(w.rider, P0, D0, "economy", START + timedelta(minutes=60), w.card)
    w.p.save()
    q = Platform.open(_db(tmp_path))
    assert q.ride(rid)["status"] == "scheduled"
    q.advance(minutes=50)
    assert q.ride(rid)["status"] == "requested"
    assert q.ride(rid)["offered_to"] == w.driver


def _views(p: Platform, w: World, rid: str, oid: str) -> list:
    accounts = (w.admin, w.rider, w.driver, w.restaurant, w.courier)
    return [
        p.now(),
        [p.get_account(a) for a in accounts],
        [p.notifications(a) for a in accounts],
        p.payments(w.rider),
        p.payment_methods(w.rider),
        p.wallet_balance(w.rider),
        p.ride(rid),
        p.order(oid),
        p.audit_log(w.admin),
        p.rating(w.driver), p.rating(w.rider), p.rating(w.restaurant), p.rating(w.courier),
        p.earnings(w.driver, WEEK_START, WEEK_END),
        p.earnings(w.courier, WEEK_START, WEEK_END),
        p.worker_status(w.driver), p.worker_status(w.courier),
        p.menu(w.restaurant),
        p.daily_report(w.admin, DAY),
        p.top_restaurants(w.admin, START, START + timedelta(days=1)),
        p.referral_code(w.rider),
    ]


def test_u1_everything_restored(tmp_path):
    w = World(_db(tmp_path)).eats()
    p = w.p
    p.top_up_wallet(w.rider, w.card, "30.00")
    rid = w.ride(wait=7)
    p.add_tip(w.rider, rid, "2.00")
    p.rate(w.rider, rid, 5, "great")
    p.rate(w.driver, rid, 4, "", "rider")
    oid = w.order(tip="1.00", method=w.wallet())
    p.rate(w.rider, oid, 3, "cold", "restaurant")
    ticket = p.report_issue(w.rider, oid, "missing_items", "The drink was not in the bag")
    p.resolve_ticket(w.admin, ticket, "1.50")
    p.save()
    q = Platform.open(_db(tmp_path))
    assert _views(q, w, rid, oid) == _views(p, w, rid, oid)


def test_u1_open_restores_the_last_save(tmp_path):
    w = World(_db(tmp_path))
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    w.p.save()
    saved_at = w.p.now()
    w.p.advance(minutes=30)
    late = w.p.register_rider("Lena Late", "lena@rides.test", "+573300000077", PW)
    w.p.top_up_wallet(w.rider, w.card, "50.00")
    rider, card = w.rider, w.card
    del w  # the platform stops without saving again
    gc.collect()
    q = Platform.open(_db(tmp_path))
    assert q.now() == saved_at
    with pytest.raises(KeyError):
        q.get_account(late)
    assert q.wallet_balance(rider) == D("20.00")
    assert [(x["id"], x["ref"]) for x in q.payments(rider)] == [("PAY-000001", card)]
    assert q.register_rider("Nina New", "nina@rides.test", "+573300000099", PW) == "RID-000002"


def test_u1_a_second_save_replaces_the_first(tmp_path):
    w = World(_db(tmp_path))
    w.p.save()
    w.p.advance(minutes=10)
    w.p.top_up_wallet(w.rider, w.card, "20.00")
    w.p.save()
    q = Platform.open(_db(tmp_path))
    assert q.now() == START + timedelta(minutes=10)
    assert q.wallet_balance(w.rider) == D("20.00")


def _schema_db(tmp_path: Path) -> str:
    w = World(_db(tmp_path))
    w.p.save()
    del w
    gc.collect()
    return _db(tmp_path)


def test_u2_schema_version_has_one_row(tmp_path):
    path = _schema_db(tmp_path)
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    finally:
        con.close()
    assert Platform.open(path).now() == START


def test_u2_unknown_version_rejected(tmp_path):
    path = _schema_db(tmp_path)
    con = sqlite3.connect(path)
    try:
        columns = [row[1] for row in con.execute("PRAGMA table_info(schema_version)")]
        assert columns
        con.execute("UPDATE schema_version SET " + ", ".join(f'"{c}" = 987654' for c in columns))
        con.commit()
    finally:
        con.close()
    with pytest.raises(ValueError):
        Platform.open(path)


# =============================================================================== V. Non-functional
_CVC_TOKEN = re.compile(rb"(?<![0-9A-Za-z])" + AMEX_CVC.encode() + rb"(?![0-9A-Za-z])")


def test_v1_database_has_no_secrets(tmp_path):
    w = World(_db(tmp_path))
    w.p.add_card(w.rider, AMEX, 12, 2030, AMEX_CVC)
    w.ride()
    w.p.save()
    files = [f for f in tmp_path.iterdir() if f.name.startswith("rides.db")]
    assert files
    for f in files:
        data = f.read_bytes()
        assert PW.encode() not in data
        assert CARD.encode() not in data
        assert AMEX.encode() not in data
        assert not _CVC_TOKEN.search(data)


def test_v1_returned_data_has_no_secrets(w):
    w.eats()
    w.p.add_card(w.rider, AMEX, 12, 2030, AMEX_CVC)
    rid = w.ride()
    w.order()
    accounts = (w.admin, w.rider, w.driver, w.restaurant, w.courier)
    exposed = repr([
        [w.p.get_account(a) for a in accounts],
        [w.p.notifications(a) for a in accounts],
        w.p.payment_methods(w.rider), w.p.payments(w.rider), w.p.audit_log(w.admin), w.p.ride(rid),
    ]).encode()
    assert PW.encode() not in exposed
    assert CARD.encode() not in exposed
    assert AMEX.encode() not in exposed
    assert not _CVC_TOKEN.search(exposed)


@pytest.mark.parametrize("phone,masked", [
    ("+573159872614", "+57******2614"),
    ("+12025550123", "+12*****0123"),
    ("+573001234567890", "+57*********7890"),
])
def test_v2_phone_masked_in_account(w, phone, masked):
    rider = w.p.register_rider("Mia Mask", "mia@rides.test", phone, PW)
    assert w.p.get_account(rider)["phone"] == masked


def test_v2_phone_masked_for_every_role(w):
    w.eats()
    for account in (w.admin, w.rider, w.driver, w.restaurant, w.courier):
        phone = w.p.get_account(account)["phone"]
        assert re.fullmatch(r"\+\d\d\*{6}\d{4}", phone), phone


def test_v2_no_raw_phone_in_returned_data(w):
    w.eats()
    rid = _assigned(w)
    shared = w.p.trip_status(w.p.share_trip(w.rider, rid))
    w.p.driver_arrived(w.driver, rid)
    w.p.start_ride(w.driver, rid)
    w.p.advance(minutes=5)
    w.p.complete_ride(w.driver, rid, [P0, D0])
    oid = w.order()
    accounts = (w.admin, w.rider, w.driver, w.restaurant, w.courier)
    exposed = repr([
        shared, [w.p.get_account(a) for a in accounts], [w.p.notifications(a) for a in accounts],
        w.p.payments(w.rider), w.p.ride(rid), w.p.order(oid), w.p.audit_log(w.admin),
        w.p.worker_status(w.driver), w.p.menu(w.restaurant),
    ])
    # national digits of admin, rider, driver, restaurant (+573500000001) and courier (+573600000002)
    for raw in ("3000000001", "3100000001", "3200000001", "3500000001", "3600000002"):
        assert raw not in exposed


# =============================================================================== X. Returned objects
def _scribble(value) -> None:
    """Mutate a returned structure in place, nested lists and dicts included."""
    if isinstance(value, list):
        for entry in value:
            _scribble(entry)
        value.append("scribbled")
    elif isinstance(value, dict):
        for key in list(value):
            _scribble(value[key])
            value[key] = "scribbled"
        value["scribbled"] = True


def test_x5_returned_lists_and_dicts_are_new_objects(w):
    w.eats()
    p = w.p
    p.top_up_wallet(w.rider, w.card, "30.00")
    rid = w.ride()
    oid = w.order()
    ticket = p.report_issue(w.rider, rid, "safety", "The driver was speeding all the way")
    p.resolve_ticket(w.admin, ticket, "1.00")
    live = _assigned(w)
    token = p.share_trip(w.rider, live)
    calls = {
        "payments": lambda: p.payments(w.rider),
        "payment_methods": lambda: p.payment_methods(w.rider),
        "notifications": lambda: p.notifications(w.admin),
        "audit_log": lambda: p.audit_log(w.admin),
        "earnings": lambda: p.earnings(w.driver, WEEK_START, WEEK_END),
        "daily_report": lambda: p.daily_report(w.admin, DAY),
        "top_restaurants": lambda: p.top_restaurants(w.admin, START, START + timedelta(days=1)),
        "trip_status": lambda: p.trip_status(token),
        "ride": lambda: p.ride(rid),
        "order": lambda: p.order(oid),
        "get_account": lambda: p.get_account(w.rider),
        "worker_status": lambda: p.worker_status(w.courier),
    }
    for name, call in calls.items():
        first = call()
        snapshot = copy.deepcopy(first)
        assert call() is not first, name
        _scribble(first)
        assert call() == snapshot, name


try:  # the oracle runs with --timeout=120 when pytest-timeout is installed; V3 needs a long setup
    import pytest_timeout  # noqa: F401

    LONG_SETUP = pytest.mark.timeout(3600)
except ImportError:  # without the plugin the marker would be unknown (and fail with --strict-markers)
    def LONG_SETUP(test):  # noqa: N802
        return test


@pytest.fixture(scope="module")
def crowded():
    """5 000 approved economy drivers online within 3.5 km of P0 (setup is slow: PBKDF2)."""
    p = Platform(START)
    admin = p.create_admin("Big Admin", "big@v3.test", "+573700000001", PW)
    p.add_zone(admin, "City", P0, 10, 30)
    for i in range(5000):
        driver = p.register_driver(f"Driver {i}", f"drv{i}@v3.test", f"+5731{i:08d}", PW,
                                   f"LIC{i:06d}", LICENSE_EXPIRES)
        vehicle = p.add_vehicle(driver, f"Q{chr(65 + i // 1000)}Z{i % 1000:03d}", "Kia", "Rio", 2020, 4,
                                "economy")
        p.approve(admin, driver)
        row, col = divmod(i, 71)
        p.go_online(driver, (4.65 + (row - 35) * 0.0009, -74.05 + (col - 35) * 0.0009), vehicle)
    riders = []
    for n in range(3):
        rider = p.register_rider(f"Rider {n}", f"rider{n}@v3.test", f"+5738{n:08d}", PW)
        riders.append((rider, p.add_card(rider, CARD, 12, 2030, "123")))
    return p, riders


@LONG_SETUP
def test_v3_quote_ride_under_half_second(crowded):
    p, riders = crowded
    for n, (rider, _card) in enumerate(riders):
        started = time.perf_counter()
        quote = p.quote_ride(rider, (4.651 + n * 0.001, -74.049), D0, "economy")
        elapsed = time.perf_counter() - started
        assert quote["quote_id"].startswith("QTE-")
        assert elapsed < 0.5, elapsed


@LONG_SETUP
def test_v3_request_ride_under_half_second(crowded):
    p, riders = crowded
    for n, (rider, card) in enumerate(riders):
        quote = p.quote_ride(rider, (4.648 - n * 0.001, -74.051), D0, "economy")
        started = time.perf_counter()
        rid = p.request_ride(rider, quote["quote_id"], card)
        elapsed = time.perf_counter() - started
        assert p.ride(rid)["offered_to"] is not None
        assert elapsed < 0.5, elapsed


def _transcript() -> tuple[World, list]:
    w = World().eats()
    p = w.p
    out: list = []
    quote = p.quote_ride(w.rider, P0, D0, "economy")
    rid = p.request_ride(w.rider, quote["quote_id"], w.card)
    out += [quote, rid, p.ride(rid)]
    p.accept_ride(w.driver, rid)
    p.driver_arrived(w.driver, rid)
    p.advance(minutes=7)
    p.start_ride(w.driver, rid)
    p.advance(minutes=9)
    p.complete_ride(w.driver, rid, [P0, MID, D0])
    p.add_tip(w.rider, rid, "3.00")
    p.rate(w.rider, rid, 5, "great")
    p.rate(w.driver, rid, 4, "", "rider")
    oid = w.order(tip="1.50")
    ticket = p.report_issue(w.rider, oid, "missing_items", "The drink was not in the bag")
    p.resolve_ticket(w.admin, ticket, "1.00")
    accounts = (w.admin, w.rider, w.driver, w.restaurant, w.courier)
    out += [
        ticket, p.ride(rid), p.order(oid), p.payments(w.rider), p.payment_methods(w.rider),
        p.wallet_balance(w.rider), [p.get_account(a) for a in accounts],
        [p.notifications(a) for a in accounts], p.audit_log(w.admin),
        p.earnings(w.driver, WEEK_START, WEEK_END), p.earnings(w.courier, WEEK_START, WEEK_END),
        p.rating(w.driver), p.rating(w.rider), p.daily_report(w.admin, DAY),
        p.top_restaurants(w.admin, START, START + timedelta(days=1)), p.menu(w.restaurant),
        p.worker_status(w.driver), p.now(),
    ]
    return w, out


def test_v4_same_calls_same_results():
    _, first = _transcript()
    time.sleep(1.1)  # a platform that read the system clock would now return different times
    _, second = _transcript()
    assert first == second


def test_v4_share_token_deterministic():
    tokens = []
    for _ in range(2):
        w = World()
        rid = _assigned(w)
        tokens.append(w.p.share_trip(w.rider, rid))
    assert tokens[0] == tokens[1]


def test_v4_times_come_from_virtual_clock():
    w, _ = _transcript()
    p = w.p
    stamps = [n["at"] for a in (w.admin, w.rider, w.driver, w.restaurant, w.courier)
              for n in p.notifications(a)]
    stamps += [x["at"] for x in p.payments(w.rider)] + [e["at"] for e in p.audit_log(w.admin)]
    assert stamps
    assert all(START <= at <= p.now() for at in stamps)


PUBLIC_METHODS = [
    "open", "save", "now", "advance", "create_admin", "register_rider", "register_driver",
    "register_courier", "register_restaurant", "authenticate", "get_account", "referral_code",
    "suspend", "reactivate", "add_vehicle", "approve", "go_online", "go_offline", "update_location",
    "worker_status", "add_zone", "distance_km", "eta_minutes", "set_surge_cap", "quote_ride",
    "request_ride", "schedule_ride", "accept_ride", "decline_ride", "driver_arrived", "start_ride",
    "complete_ride", "cancel_ride", "ride", "add_tip", "share_trip", "trip_status", "set_hours",
    "is_open", "add_menu_item", "add_option_group", "add_option", "set_item_available", "menu",
    "place_order", "accept_order", "reject_order", "mark_ready", "pick_up", "deliver", "cancel_order",
    "order", "create_promo", "add_card", "payment_methods", "wallet_balance", "top_up_wallet",
    "payments", "earnings", "run_payouts", "rate", "rating", "report_issue", "resolve_ticket",
    "notifications", "daily_report", "top_restaurants", "audit_log",
]


def _package_dir() -> Path:
    return Path(rides.__file__).resolve().parent


def test_v5_standard_library_only():
    allowed = set(sys.stdlib_module_names) | {"rides", "__future__"}
    foreign = []
    for source in _package_dir().rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            foreign += [f"{source.name}: {n}" for n in names if n.split(".")[0] not in allowed]
    assert foreign == []


def test_v5_public_methods_type_hinted():
    missing = []
    for name in PUBLIC_METHODS:
        signature = inspect.signature(getattr(Platform, name))
        if signature.return_annotation is inspect.Signature.empty:
            missing.append(f"{name} -> return")
        for param in signature.parameters.values():
            if param.name not in ("self", "cls") and param.annotation is inspect.Parameter.empty:
                missing.append(f"{name}({param.name})")
    assert missing == []


def test_v5_tests_directory():
    package = _package_dir()
    if package.parent.name != "src":
        pytest.skip("the rides package is not laid out under src/")
    tests = package.parent.parent / "tests"
    assert tests.is_dir()
    assert list(tests.rglob("test_*.py"))
