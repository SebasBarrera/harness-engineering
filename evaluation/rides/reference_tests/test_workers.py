"""P1-P4, Q1-Q3."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from conftest import CENTER, START, World, at

WEEK_START = date(2026, 3, 2)


def share(fare: Decimal, booking: str = "1.50", airport: bool = False) -> Decimal:
    surcharge = Decimal("5.00") if airport else Decimal(0)
    base = Decimal("0.75") * (fare - Decimal(booking) - surcharge)
    return base.quantize(Decimal("0.01"), ROUND_HALF_UP) + surcharge


def test_driver_earnings_breakdown(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    ride = w.ride_to_assigned(rider, card, driver)
    p.driver_arrived(driver, ride)
    p.advance(minutes=8)
    p.start_ride(driver, ride)  # wait fee 0.90
    p.advance(minutes=6)
    p.complete_ride(driver, ride, [CENTER, at(3)])
    p.add_tip(rider, ride, "4.00")
    fare = p.ride(ride)["fare"]
    earned = p.earnings(driver, START, START + timedelta(days=1))
    assert earned == {
        "trips": 1, "fares": share(fare), "tips": Decimal("4.00"), "fees": Decimal("0.90"),
        "bonus": Decimal("0.00"), "total": share(fare) + Decimal("4.90"),
    }  # fmt: skip
    assert p.earnings(driver, START + timedelta(days=1), START + timedelta(days=2))["trips"] == 0
    with pytest.raises(ValueError):
        p.earnings(rider, START, START + timedelta(days=1))


def test_airport_share(w: World) -> None:
    p = w.p
    p.add_zone(w.admin, "Airport", at(30), 3, 40, airport=True)
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    quote = p.quote_ride(rider, CENTER, at(5), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card)
    p.accept_ride(driver, ride)
    p.driver_arrived(driver, ride)
    p.start_ride(driver, ride)
    p.complete_ride(driver, ride, [CENTER, at(5)])
    assert not quote["fare"] > 100
    earned = p.earnings(driver, START, START + timedelta(days=1))
    assert earned["fares"] == share(p.ride(ride)["fare"])


def test_weekly_bonus_and_payouts(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    courier = w.courier()
    rider = w.rider()
    card = w.card(rider)
    fares = Decimal(0)
    for _ in range(20):
        ride = w.complete(rider, card, driver)
        fares += share(p.ride(ride)["fare"])
    earned = p.earnings(driver, START, START + timedelta(days=7))
    assert earned["trips"] == 20 and earned["bonus"] == Decimal("30.00")
    assert earned["total"] == fares + Decimal("30.00")
    with pytest.raises(ValueError):
        p.run_payouts(w.admin, WEEK_START)  # the week has not ended
    p.advance(minutes=7 * 24 * 60)
    with pytest.raises(ValueError):
        p.run_payouts(w.admin, date(2026, 3, 3))  # not a Monday
    with pytest.raises(ValueError):
        p.run_payouts(w.admin, datetime(2026, 3, 2))  # a datetime, not a date
    with pytest.raises(PermissionError):
        p.run_payouts(rider, WEEK_START)
    paid = p.run_payouts(w.admin, WEEK_START)
    assert paid == {driver: fares + Decimal("30.00")}
    assert courier not in paid
    assert p.notifications(driver)[-1] == {"at": p.now(), "kind": "payout", "ref": "2026-03-02"}
    with pytest.raises(ValueError):
        p.run_payouts(w.admin, WEEK_START)
    assert ("run_payouts", "2026-03-02") in [(e["action"], e["target"]) for e in p.audit_log(w.admin)]


def test_rating_rules(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider, stranger = w.rider(), w.rider()
    card = w.card(rider)
    ride = w.complete(rider, card, driver)
    with pytest.raises(PermissionError):
        p.rate(stranger, ride, 5)
    with pytest.raises(ValueError):
        p.rate(rider, ride, 6)
    with pytest.raises(ValueError):
        p.rate(rider, ride, 5, "x" * 501)
    with pytest.raises(ValueError):
        p.rate(rider, ride, 5, target="rider")
    with pytest.raises(ValueError):
        p.rate(driver, ride, 5)  # the driver must target "rider"
    p.rate(rider, ride, 5, "great")
    p.rate(driver, ride, 3, target="rider")
    with pytest.raises(ValueError):
        p.rate(rider, ride, 4)
    assert p.rating(driver) == Decimal("5.00")
    assert p.rating(rider) == Decimal("3.00")
    assert p.rating(stranger) is None
    late = w.complete(rider, card, driver)
    p.advance(minutes=7 * 24 * 60)
    with pytest.raises(ValueError):
        p.rate(rider, late, 5)


def test_rating_mean_of_last_hundred(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    rides = [w.complete(rider, card, driver, km=1, minutes=1) for _ in range(3)]
    for ride, stars in zip(rides, (5, 4, 4), strict=True):
        p.rate(rider, ride, stars)
    assert p.rating(driver) == Decimal("4.33")


def test_under_review_after_twenty_low_ratings(w: World) -> None:
    p = w.p
    driver, vehicle = w.driver()
    rider = w.rider()
    card = w.card(rider)
    stars = [5] * 12 + [4] * 7 + [1]  # stays >= 4.60 (eligible) for 19 ratings, then 4.45
    for index, value in enumerate(stars):
        ride = w.complete(rider, card, driver, km=1, minutes=1)
        p.rate(rider, ride, value)
        if index < 19:
            assert p.get_account(driver)["status"] == "active"
    assert p.rating(driver) == Decimal("4.45")
    assert p.get_account(driver)["status"] == "under_review"
    assert p.worker_status(driver)["status"] == "offline"
    with pytest.raises(ValueError):
        p.go_online(driver, CENTER, vehicle)
    p.reactivate(w.admin, driver)
    p.go_online(driver, CENTER, vehicle)


def test_order_ratings(w: World) -> None:
    p = w.p
    restaurant = w.restaurant()
    item = p.add_menu_item(restaurant, "Tea", "6", "food", 1)
    courier = w.courier()
    rider = w.rider()
    order = p.place_order(rider, restaurant, [{"item_id": item, "quantity": 2}], at(1), w.card(rider))
    with pytest.raises(ValueError):
        p.rate(rider, order, 5, target="restaurant")  # not delivered
    p.accept_order(restaurant, order)
    p.mark_ready(restaurant, order)
    p.pick_up(courier, order)
    p.update_location(courier, at(1))
    p.deliver(courier, order)
    with pytest.raises(ValueError):
        p.rate(rider, order, 5)  # target must be restaurant or courier
    p.rate(rider, order, 5, target="restaurant")
    p.rate(rider, order, 4, target="courier")
    with pytest.raises(ValueError):
        p.rate(rider, order, 4, target="courier")
    assert p.rating(restaurant) == Decimal("5.00")
    assert p.rating(courier) == Decimal("4.00")
    with pytest.raises(PermissionError):
        p.rate(courier, order, 5, target="restaurant")
    with pytest.raises(KeyError):
        p.rate(rider, "ORD-999999", 5, target="restaurant")


def test_under_review_while_busy_goes_offline_when_the_ride_ends(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider, other = w.rider(), w.rider()
    card, other_card = w.card(rider), w.card(other)
    stars = [5] * 12 + [4] * 7
    for value in stars:
        p.rate(rider, w.complete(rider, card, driver, km=1, minutes=1), value)
    last = w.complete(rider, card, driver, km=1, minutes=1)
    busy_ride = w.ride_to_assigned(other, other_card, driver, km=1)
    p.rate(rider, last, 1)
    assert p.get_account(driver)["status"] == "under_review"
    assert p.worker_status(driver)["status"] == "busy"
    p.update_location(driver, CENTER)
    p.driver_arrived(driver, busy_ride)
    p.start_ride(driver, busy_ride)
    p.complete_ride(driver, busy_ride, [CENTER, at(1)])
    assert p.worker_status(driver)["status"] == "offline"
