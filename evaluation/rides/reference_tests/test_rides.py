"""D1-D4, E1-E3, F1-F5, G1-G4, H1-H4, R3."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from conftest import CENTER, DECLINED, World, at
from rides import Platform


def fare(base: str, km: Decimal, per_km: str, minutes: int, per_min: str, minimum: str, booking: str,
         night: bool = False, surge: str = "1.0", airport: bool = False) -> Decimal:  # fmt: skip
    metered = Decimal(base) + Decimal(per_km) * km + Decimal(per_min) * minutes
    if night:
        metered *= Decimal("1.2")
    metered *= Decimal(surge)
    total = max(metered, Decimal(minimum)) + Decimal(booking) + (Decimal("5.00") if airport else 0)
    return total.quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_quote_contents_and_fare(w: World) -> None:
    p = w.p
    w.driver()
    rider = w.rider()
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    km = p.distance_km(CENTER, at(3))
    assert quote["quote_id"] == "QTE-000001"
    assert quote["category"] == "economy"
    assert quote["distance_km"] == km
    assert quote["duration_min"] == p.eta_minutes(CENTER, at(3)) == 6
    assert quote["surge"] == Decimal("1.0")
    assert quote["expires_at"] == p.now() + timedelta(minutes=5)
    assert quote["fare"] == fare("2.50", km, "1.10", 6, "0.25", "6.00", "1.50")


def test_minimum_fare_night_and_airport(w: World) -> None:
    p = w.p
    rider = w.rider()
    short = p.quote_ride(rider, CENTER, at(0.5), "comfort")
    assert short["fare"] == Decimal("10.50")  # minimum 9.00 + booking 1.50
    airport = p.add_zone(w.admin, "Airport", at(30), 3, 40, airport=True)
    assert airport
    far = p.quote_ride(rider, CENTER, at(30), "xl")
    km = p.distance_km(CENTER, at(30))
    assert far["fare"] == fare("4.00", km, "1.80", far["duration_min"], "0.40", "11.00", "2.00", airport=True)
    p.advance(minutes=10 * 60 + 30)  # 22:30
    night = p.quote_ride(rider, CENTER, at(3), "moto")
    km3 = p.distance_km(CENTER, at(3))
    assert night["fare"] == fare("1.50", km3, "0.70", 6, "0.15", "4.00", "1.00", night=True)


def test_quote_validation(w: World) -> None:
    p = w.p
    rider = w.rider()
    with pytest.raises(ValueError):
        p.quote_ride(rider, CENTER, at(0.1), "economy")
    with pytest.raises(ValueError):
        p.quote_ride(rider, CENTER, at(25), "economy")  # dropoff outside every zone
    with pytest.raises(ValueError):
        p.quote_ride(rider, CENTER, at(3), "bus")
    p.suspend(w.admin, rider, "x")
    with pytest.raises(ValueError):
        p.quote_ride(rider, CENTER, at(3), "economy")
    driver, _ = w.driver()
    with pytest.raises(PermissionError):
        p.quote_ride(driver, CENTER, at(3), "economy")


def test_surge_by_demand_and_supply(w: World) -> None:
    p = w.p
    riders = [w.rider() for _ in range(4)]
    cards = [w.card(r) for r in riders]
    # no supply: ratio = 1 / 1 = 1.0
    assert p.quote_ride(riders[0], CENTER, at(3), "economy")["surge"] == Decimal("1.0")
    # three requested rides without drivers become no_driver at once, so demand stays at 1
    w.driver(location=at(15))  # 15 km away: in the zone, not eligible (> 8 km)
    for rider, card in zip(riders[:3], cards[:3], strict=True):
        q = p.quote_ride(rider, at(-1), at(3), "economy")
        ride = p.request_ride(rider, q["quote_id"], card)
        assert p.ride(ride)["status"] == "no_driver"
    assert p.quote_ride(riders[3], CENTER, at(3), "economy")["surge"] == Decimal("1.0")


def test_surge_steps_and_cap(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    riders = [w.rider() for _ in range(4)]
    cards = [w.card(r) for r in riders]
    # one requested ride holding the only driver's offer: demand 2, supply 0 -> ratio 2 -> 1.5
    q = p.quote_ride(riders[0], CENTER, at(3), "economy")
    p.request_ride(riders[0], q["quote_id"], cards[0])
    assert p.quote_ride(riders[1], CENTER, at(3), "economy")["surge"] == Decimal("1.5")
    p.set_surge_cap(w.admin, w.zone, Decimal("1.2"))
    assert p.quote_ride(riders[1], CENTER, at(3), "economy")["surge"] == Decimal("1.2")
    assert driver


def test_surge_fixed_at_quote_and_used_in_fare(w: World) -> None:
    p = w.p
    w.driver()
    rider1, rider2 = w.rider(), w.rider()
    card1 = w.card(rider1)
    q1 = p.quote_ride(rider1, CENTER, at(3), "economy")
    p.request_ride(rider1, q1["quote_id"], card1)  # the only driver now holds an offer
    q2 = p.quote_ride(rider2, CENTER, at(3), "economy")
    km = p.distance_km(CENTER, at(3))
    assert q2["surge"] == Decimal("1.5")
    assert q2["fare"] == fare("2.50", km, "1.10", 6, "0.25", "6.00", "1.50", surge="1.5")


def test_request_rules(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider, other = w.rider(), w.rider()
    card, other_card = w.card(rider), w.card(other)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(KeyError):
        p.request_ride(rider, "QTE-999999", card)
    with pytest.raises(KeyError):
        p.request_ride(rider, quote["quote_id"], "PM-999999")
    with pytest.raises(PermissionError):
        p.request_ride(rider, quote["quote_id"], other_card)
    with pytest.raises(ValueError):
        p.request_ride(other, quote["quote_id"], other_card)  # quote of another rider
    ride = p.request_ride(rider, quote["quote_id"], card)
    assert ride == "RDE-000001"
    view = p.ride(ride)
    assert view["status"] == "requested" and view["offered_to"] == driver
    assert view["fare"] == view["quoted_fare"] == quote["fare"]
    assert view["charged"] == Decimal("0.00")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card)  # used, and the rider has a ride in course
    q2 = p.quote_ride(rider, CENTER, at(2), "economy")  # quoting is always allowed
    with pytest.raises(ValueError):
        p.request_ride(rider, q2["quote_id"], card)
    q3 = p.quote_ride(other, CENTER, at(3), "economy")
    p.advance(minutes=5)
    with pytest.raises(ValueError):
        p.request_ride(other, q3["quote_id"], other_card)  # expired at exactly 5 minutes
    assert p.wallet_balance(rider) == Decimal("0.00")


def test_wallet_hold_and_declined_card(w: World) -> None:
    p = w.p
    w.driver()
    rider = w.rider()
    wallet = p.payment_methods(rider)[0]["id"]
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], wallet)  # empty wallet
    bad = w.card(rider, DECLINED)
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], bad)
    card = w.card(rider)
    p.top_up_wallet(rider, card, "20")
    ride = p.request_ride(rider, quote["quote_id"], wallet)
    assert p.wallet_balance(rider) == Decimal("20.00") - quote["fare"]  # balance minus active holds
    assert p.ride(ride)["status"] == "requested"
    assert [x["id"] for x in p.payments(rider)] == ["PAY-000001"]


def test_idempotency(w: World) -> None:
    p = w.p
    w.driver()
    rider = w.rider()
    card = w.card(rider)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card, idempotency_key="k1")
    notifications = p.notifications(p.ride(ride)["offered_to"])
    assert p.request_ride(rider, quote["quote_id"], card, idempotency_key="k1") == ride
    assert p.notifications(p.ride(ride)["offered_to"]) == notifications


def test_dispatch_order_and_ties(w: World) -> None:
    p = w.p
    far, _ = w.driver(location=at(2))
    near_b, _ = w.driver(location=at(1))
    near_a, _ = w.driver(location=at(-1))  # same distance as near_b, higher id
    w.driver(location=at(1), category="comfort")
    rider = w.rider()
    card = w.card(rider)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card)
    assert p.ride(ride)["offered_to"] == near_b
    assert p.notifications(near_b) == [{"at": p.now(), "kind": "ride_offer", "ref": ride}]
    p.decline_ride(near_b, ride)
    assert p.ride(ride)["offered_to"] == near_a
    p.advance(seconds=14)
    assert p.ride(ride)["offered_to"] == near_a
    p.advance(seconds=1)  # the offer lasts 15 seconds
    assert p.ride(ride)["offered_to"] == far
    with pytest.raises(ValueError):
        p.accept_ride(near_a, ride)  # expired offer
    with pytest.raises(ValueError):
        p.accept_ride(near_b, ride)  # declined offer
    stranger, _ = w.driver(location=at(15))
    with pytest.raises(PermissionError):
        p.accept_ride(stranger, ride)
    p.accept_ride(far, ride)
    assert p.ride(ride)["status"] == "driver_assigned"
    assert p.worker_status(far)["status"] == "busy"
    assert p.notifications(rider)[-1]["kind"] == "ride_assigned"


def test_rating_tie_break_and_low_rating_excluded(w: World) -> None:
    p = w.p
    rated, _ = w.driver(location=at(1))
    unrated, _ = w.driver(location=at(-1))
    rider = w.rider()
    card = w.card(rider)
    # give the first driver a 4.00 rating through a completed ride
    ride = w.complete(rider, card, rated)
    p.rate(rider, ride, 4)
    p.update_location(rated, at(1))
    q = p.quote_ride(rider, CENTER, at(3), "economy")
    ride2 = p.request_ride(rider, q["quote_id"], card)
    assert p.ride(ride2)["offered_to"] == unrated  # 4.00 < 4.60 is not eligible
    p.decline_ride(unrated, ride2)
    assert p.ride(ride2)["status"] == "no_driver"


def test_no_driver_after_five_failures(w: World) -> None:
    p = w.p
    drivers = [w.driver(location=at(0.1 * (i + 1)))[0] for i in range(6)]
    rider = w.rider()
    card = w.card(rider)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card)
    for driver in drivers[:4]:
        assert p.ride(ride)["offered_to"] == driver
        p.decline_ride(driver, ride)
    assert p.ride(ride)["offered_to"] == drivers[4]
    p.advance(seconds=15)
    view = p.ride(ride)
    assert view["status"] == "no_driver" and view["offered_to"] is None
    assert p.notifications(rider)[-1] == {"at": p.now(), "kind": "no_driver", "ref": ride}


def test_pending_offer_driver_not_offered_twice(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    r1, r2 = w.rider(), w.rider()
    c1, c2 = w.card(r1), w.card(r2)
    ride1 = p.request_ride(r1, p.quote_ride(r1, CENTER, at(3), "economy")["quote_id"], c1)
    ride2 = p.request_ride(r2, p.quote_ride(r2, CENTER, at(3), "economy")["quote_id"], c2)
    assert p.ride(ride1)["offered_to"] == driver
    assert p.ride(ride2)["status"] == "no_driver"


def test_lifecycle_wait_fee_and_completion(w: World) -> None:
    p = w.p
    driver, _ = w.driver(location=at(0.5))
    rider = w.rider()
    card = w.card(rider)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card)
    p.accept_ride(driver, ride)
    other, _ = w.driver()
    with pytest.raises(PermissionError):
        p.driver_arrived(other, ride)
    with pytest.raises(ValueError):
        p.start_ride(driver, ride)
    with pytest.raises(ValueError):
        p.driver_arrived(driver, ride)  # 0.5 km away
    p.update_location(driver, at(0.15))
    p.driver_arrived(driver, ride)
    assert p.ride(ride)["status"] == "arrived"
    p.advance(minutes=7, seconds=30)
    p.start_ride(driver, ride)
    assert p.ride(ride)["wait_fee"] == Decimal("0.60")
    p.advance(minutes=6, seconds=10)
    with pytest.raises(ValueError):
        p.complete_ride(driver, ride, [CENTER])
    p.complete_ride(driver, ride, [CENTER, at(1.5), at(3)])
    view = p.ride(ride)
    assert view["status"] == "completed"
    assert view["duration_min"] == 7
    assert view["distance_km"] == p.distance_km(CENTER, at(3))
    assert view["fare"] == quote["fare"]
    assert view["charged"] == quote["fare"] + Decimal("0.60")
    assert p.worker_status(driver)["status"] == "available"
    assert p.payments(rider)[-1]["amount"] == view["charged"]
    assert p.notifications(rider)[-1]["kind"] == "ride_completed"


def test_upfront_price_recomputed_when_route_is_longer(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    ride = w.ride_to_assigned(rider, card, driver, km=3)
    p.driver_arrived(driver, ride)
    p.start_ride(driver, ride)
    p.advance(minutes=9)
    route = [CENTER, at(0, 2), at(3, 2), at(3)]  # about 7 km
    p.complete_ride(driver, ride, route)
    actual = sum((p.distance_km(a, b) for a, b in zip(route, route[1:], strict=False)), Decimal(0))
    view = p.ride(ride)
    assert abs(view["distance_km"] - actual) <= Decimal("0.003")
    assert view["fare"] == fare("2.50", view["distance_km"], "1.10", 9, "0.25", "6.00", "1.50")
    assert view["charged"] == view["fare"]


def test_rider_cancellation_fees(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    ride = w.ride_to_assigned(rider, card, driver)
    p.advance(minutes=1, seconds=59)
    p.cancel_ride(rider, ride)
    assert p.ride(ride)["charged"] == Decimal("0.00")
    assert p.worker_status(driver)["status"] == "available"
    assert p.notifications(driver)[-1]["kind"] == "ride_cancelled"
    ride2 = w.ride_to_assigned(rider, card, driver)
    p.advance(minutes=2)  # at exactly 2 minutes the free window has passed
    p.cancel_ride(rider, ride2, "changed my mind")
    view = p.ride(ride2)
    assert view["status"] == "cancelled"
    assert view["cancellation_fee"] == view["charged"] == Decimal("3.00")
    assert p.payments(rider)[-1]["kind"] == "cancellation_fee"
    assert p.earnings(driver, p.now() - timedelta(hours=1), p.now() + timedelta(hours=1))["fees"] == Decimal("3.00")
    with pytest.raises(ValueError):
        p.cancel_ride(rider, ride2)


def test_cancel_permissions_and_admin(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider, stranger = w.rider(), w.rider()
    card = w.card(rider)
    ride = w.ride_to_assigned(rider, card, driver)
    with pytest.raises(PermissionError):
        p.cancel_ride(stranger, ride)
    p.advance(minutes=5)
    p.cancel_ride(w.admin, ride, "ops")
    assert p.ride(ride)["charged"] == Decimal("0.00")
    assert ("cancel_ride", ride) in [(e["action"], e["target"]) for e in p.audit_log(w.admin)]


def test_driver_cancellation_reoffers_and_bans(w: World) -> None:
    p = w.p
    driver, vehicle = w.driver()
    backup, _ = w.driver(location=at(1))
    rider = w.rider()
    card = w.card(rider)
    ride = w.ride_to_assigned(rider, card, driver)
    p.cancel_ride(driver, ride)
    view = p.ride(ride)
    assert view["status"] == "requested" and view["driver_id"] is None and view["offered_to"] == backup
    assert p.notifications(rider)[-1]["kind"] == "ride_cancelled"
    p.decline_ride(backup, ride)
    assert p.ride(ride)["status"] == "no_driver"  # never offered again to the cancelling driver
    for _ in range(2):
        r = w.rider()
        ride = w.ride_to_assigned(r, w.card(r), driver)
        p.cancel_ride(driver, ride)
        p.decline_ride(backup, ride)
    assert p.worker_status(driver)["status"] == "offline"
    p.advance(minutes=11 * 60 + 59)
    with pytest.raises(ValueError):
        p.go_online(driver, CENTER, vehicle)
    p.advance(minutes=1)
    p.go_online(driver, CENTER, vehicle)


def test_rider_banned_after_three_paid_cancellations(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    for _ in range(3):
        ride = w.ride_to_assigned(rider, card, driver)
        p.advance(minutes=3)
        p.cancel_ride(rider, ride)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")  # quotes still allowed
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card)
    p.advance(minutes=24 * 60)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    assert p.request_ride(rider, quote["quote_id"], card)


def test_tips(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    ride = w.complete(rider, card, driver)
    with pytest.raises(ValueError):
        p.add_tip(rider, ride, Decimal("0.99"))
    with pytest.raises(ValueError):
        p.add_tip(rider, ride, 2.5)
    with pytest.raises(ValueError):
        p.add_tip(rider, ride, "1.005")
    p.add_tip(rider, ride, "2.50")
    assert p.ride(ride)["tip"] == Decimal("2.50")
    assert p.payments(rider)[-1]["kind"] == "tip"
    with pytest.raises(ValueError):
        p.add_tip(rider, ride, 3)
    ride2 = w.complete(rider, card, driver)
    p.advance(minutes=24 * 60)
    with pytest.raises(ValueError):
        p.add_tip(rider, ride2, 3)


def test_share_trip(w: World) -> None:
    p = w.p
    driver, vehicle = w.driver(location=at(1))
    rider = w.rider()
    card = w.card(rider)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card)
    with pytest.raises(ValueError):
        p.share_trip(rider, ride)  # still requested
    p.accept_ride(driver, ride)
    token = p.share_trip(rider, ride)
    status = p.trip_status(token)
    plate = p._state.vehicles[vehicle].plate
    assert status == {"status": "driver_assigned", "driver_name": "Driver", "vehicle_plate": plate, "eta_minutes": 2}
    assert "phone" not in str(status) and "@" not in str(status)
    p.update_location(driver, CENTER)
    p.driver_arrived(driver, ride)
    p.start_ride(driver, ride)
    assert p.trip_status(token)["eta_minutes"] == 6
    p.advance(minutes=6)
    p.update_location(driver, at(3))
    p.complete_ride(driver, ride, [CENTER, at(3)])
    with pytest.raises(KeyError):
        p.trip_status(token)
    with pytest.raises(KeyError):
        p.trip_status("nope")


def test_driver_never_offered_cannot_decline(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    other, _ = w.driver(location=at(15))
    rider = w.rider()
    q = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, q["quote_id"], w.card(rider))
    with pytest.raises(PermissionError):
        p.decline_ride(other, ride)
    with pytest.raises(KeyError):
        p.decline_ride(other, "RDE-999999")
    assert driver


def test_ride_view_fields(p: Platform) -> None:
    w = World(p)
    rider = w.rider()
    when = datetime(2026, 3, 3, 9, 0)
    ride = p.schedule_ride(rider, CENTER, at(3), "economy", when, w.card(rider))
    assert set(p.ride(ride)) == {
        "id", "rider_id", "driver_id", "status", "category", "quoted_fare", "fare", "distance_km", "duration_min",
        "surge", "cancellation_fee", "wait_fee", "discount", "tip", "charged", "offered_to", "pickup_at",
    }  # fmt: skip
    assert p.ride(ride)["pickup_at"] == when


def test_error_precedence(w: World) -> None:
    p = w.p
    rider, other = w.rider(), w.rider()
    card, other_card = w.card(rider), w.card(other)
    driver, _ = w.driver()
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    p.suspend(w.admin, rider, "check")
    with pytest.raises(KeyError):
        p.request_ride(rider, "QTE-404404", other_card)  # unknown quote first
    with pytest.raises(PermissionError):
        p.request_ride(rider, quote["quote_id"], other_card)  # then someone else's card
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card)  # then the inactive rider
    with pytest.raises(PermissionError):
        p.request_ride(driver, quote["quote_id"], card)
    second_admin = p.create_admin("B", "b@x.com", "+573007776655", "secret1234")
    p.suspend(w.admin, second_admin, "left")
    with pytest.raises(ValueError):
        p.approve(second_admin, driver)  # an admin that is not active
    with pytest.raises(KeyError):
        p.approve(second_admin, "DRV-404404")


def test_cancellation_notifies_rider_and_driver(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    ride = w.ride_to_assigned(rider, w.card(rider), driver)
    p.cancel_ride(w.admin, ride)
    assert p.notifications(rider)[-1] == {"at": p.now(), "kind": "ride_cancelled", "ref": ride}
    assert p.notifications(driver)[-1] == {"at": p.now(), "kind": "ride_cancelled", "ref": ride}
    assert p.worker_status(driver)["status"] == "available"


def test_offer_moves_on_when_the_driver_leaves(w: World) -> None:
    p = w.p
    first, _ = w.driver()
    second, _ = w.driver(location=at(1))
    third, _ = w.driver(location=at(2))
    rider = w.rider()
    q = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, q["quote_id"], w.card(rider))
    p.go_offline(first)
    assert p.ride(ride)["offered_to"] == second
    p.suspend(w.admin, second, "check")
    assert p.ride(ride)["offered_to"] == third
