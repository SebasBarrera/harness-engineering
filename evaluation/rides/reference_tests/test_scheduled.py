"""I1-I3 and the timers of X4."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import CENTER, DECLINED, World, at


def test_schedule_validation_and_quote(w: World) -> None:
    p = w.p
    w.driver()
    rider = w.rider()
    card = w.card(rider)
    with pytest.raises(ValueError):
        p.schedule_ride(rider, CENTER, at(3), "economy", p.now() + timedelta(minutes=29), card)
    with pytest.raises(ValueError):
        p.schedule_ride(rider, CENTER, at(3), "economy", p.now() + timedelta(days=30, seconds=1), card)
    night = p.now().replace(hour=23)  # same day 23:00, night factor of pickup_at
    ride = p.schedule_ride(rider, CENTER, at(3), "economy", night, card)
    view = p.ride(ride)
    assert view["status"] == "scheduled" and view["surge"] == Decimal("1.0")
    km = p.distance_km(CENTER, at(3))
    expected = ((Decimal("2.50") + Decimal("1.10") * km + Decimal("0.25") * 6) * Decimal("1.2") + Decimal("1.50"))
    assert view["quoted_fare"] == expected.quantize(Decimal("0.01"))
    assert p.wallet_balance(rider) == Decimal("0.00")
    # a scheduled ride does not block an immediate request (F1)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    assert p.request_ride(rider, quote["quote_id"], card)


def test_dispatch_ten_minutes_before(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    pickup_at = p.now() + timedelta(hours=2)
    ride = p.schedule_ride(rider, CENTER, at(3), "economy", pickup_at, card)
    p.advance(minutes=109, seconds=59)
    assert p.ride(ride)["status"] == "scheduled"
    p.advance(seconds=1)
    view = p.ride(ride)
    assert view["status"] == "requested" and view["offered_to"] == driver
    assert p.notifications(driver)[-1] == {"at": pickup_at - timedelta(minutes=10), "kind": "ride_offer", "ref": ride}


def test_timers_run_in_time_order_inside_one_advance(w: World) -> None:
    p = w.p
    first, _ = w.driver(location=at(0.5))
    second, _ = w.driver(location=at(1))
    rider = w.rider()
    card = w.card(rider)
    pickup_at = p.now() + timedelta(hours=1)
    ride = p.schedule_ride(rider, CENTER, at(3), "economy", pickup_at, card)
    p.advance(minutes=60)  # dispatch at +50 min, first offer expires at +50:15, second at +50:30
    assert p.notifications(first)[-1]["at"] == pickup_at - timedelta(minutes=10)
    assert p.notifications(second)[-1]["at"] == pickup_at - timedelta(minutes=10) + timedelta(seconds=15)
    assert p.ride(ride)["status"] == "no_driver"


def test_failed_hold_cancels(w: World) -> None:
    p = w.p
    w.driver()
    rider = w.rider()
    card = w.card(rider, DECLINED)
    ride = p.schedule_ride(rider, CENTER, at(3), "economy", p.now() + timedelta(hours=1), card)
    p.advance(minutes=50)
    assert p.ride(ride)["status"] == "cancelled"
    assert p.notifications(rider)[-1]["kind"] == "ride_cancelled"


def test_scheduled_cancellation_fees(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    free = p.schedule_ride(rider, CENTER, at(3), "economy", p.now() + timedelta(hours=2), card)
    p.advance(minutes=59)
    p.cancel_ride(rider, free)
    assert p.ride(free)["charged"] == Decimal("0.00")
    late = p.schedule_ride(rider, CENTER, at(3), "economy", p.now() + timedelta(hours=1), card)
    p.cancel_ride(rider, late)  # exactly 60 minutes before: no longer free
    view = p.ride(late)
    assert view["status"] == "cancelled" and view["cancellation_fee"] == view["charged"] == Decimal("5.00")
    payment = p.payments(rider)[-1]
    assert (payment["kind"], payment["ref"], payment["amount"]) == ("cancellation_fee", late, Decimal("5.00"))
    # the fee is kept by the platform
    assert p.earnings(driver, p.now() - timedelta(days=1), p.now() + timedelta(days=1))["fees"] == Decimal("0.00")


def test_dispatched_scheduled_ride_follows_g3(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    ride = p.schedule_ride(rider, CENTER, at(3), "economy", p.now() + timedelta(hours=1), card)
    p.advance(minutes=50)
    p.accept_ride(driver, ride)
    p.advance(minutes=3)
    p.cancel_ride(rider, ride)
    assert p.ride(ride)["cancellation_fee"] == Decimal("3.00")
    assert p.earnings(driver, p.now() - timedelta(days=1), p.now() + timedelta(days=1))["fees"] == Decimal("3.00")
