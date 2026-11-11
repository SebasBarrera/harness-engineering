"""R1-R2, S1, T1-T4."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import CENTER, START, World, at


def test_tickets_and_refunds(w: World) -> None:
    p = w.p
    admin2 = p.create_admin("Second", "second@x.com", "+573009990001", "secret1234")
    driver, _ = w.driver()
    rider, stranger = w.rider(), w.rider()
    card = w.card(rider)
    ride = w.complete(rider, card, driver)
    p.add_tip(rider, ride, "2.00")
    with pytest.raises(PermissionError):
        p.report_issue(stranger, ride, "overcharge", "I was charged twice")
    with pytest.raises(ValueError):
        p.report_issue(rider, ride, "missing_items", "Missing the fries")
    with pytest.raises(ValueError):
        p.report_issue(rider, ride, "overcharge", "  short    ")
    with pytest.raises(KeyError):
        p.report_issue(rider, "RDE-999999", "overcharge", "I was charged twice")
    ticket = p.report_issue(rider, ride, "overcharge", "I was charged twice")
    assert ticket == "TCK-000001"
    safety = p.report_issue(driver, ride, "safety", "Rider was aggressive")
    for admin in (w.admin, admin2):
        assert p.notifications(admin)[-1] == {"at": p.now(), "kind": "safety_ticket", "ref": safety}
    charged = p.ride(ride)["charged"] + Decimal("2.00")
    with pytest.raises(PermissionError):
        p.resolve_ticket(rider, ticket, "1")
    with pytest.raises(ValueError):
        p.resolve_ticket(w.admin, ticket, charged + Decimal("0.01"))
    with pytest.raises(ValueError):
        p.resolve_ticket(w.admin, ticket, "-1")
    p.resolve_ticket(w.admin, ticket, charged - Decimal("1.00"))
    with pytest.raises(ValueError):
        p.resolve_ticket(w.admin, ticket, "0")
    refund = p.payments(rider)[-1]
    assert (refund["kind"], refund["ref"], refund["method_id"], refund["amount"]) == (
        "refund", ride, card, -(charged - Decimal("1.00")))
    with pytest.raises(ValueError):
        p.resolve_ticket(w.admin, safety, "1.01")  # only 1.00 left
    p.resolve_ticket(w.admin, safety, "1.00")
    assert ("resolve_ticket", ticket) in [(e["action"], e["target"]) for e in p.audit_log(w.admin)]


def test_order_refund_to_wallet(w: World) -> None:
    p = w.p
    restaurant = w.restaurant()
    item = p.add_menu_item(restaurant, "Tea", "6", "food", 1)
    courier = w.courier()
    rider = w.rider()
    card = w.card(rider)
    p.top_up_wallet(rider, card, "50")
    wallet = p.payment_methods(rider)[0]["id"]
    order = p.place_order(rider, restaurant, [{"item_id": item, "quantity": 2}], at(1), wallet)
    p.accept_order(restaurant, order)
    p.mark_ready(restaurant, order)
    p.pick_up(courier, order)
    p.update_location(courier, at(1))
    p.deliver(courier, order)
    before = p.wallet_balance(rider)
    ticket = p.report_issue(rider, order, "missing_items", "The tea was missing")
    p.resolve_ticket(w.admin, ticket, "3.00")
    assert p.wallet_balance(rider) == before + Decimal("3.00")
    assert p.order(order)["refunded"] == Decimal("3.00")


def test_rider_notifications_for_a_ride(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    ride = w.complete(rider, w.card(rider), driver)
    assert [n["kind"] for n in p.notifications(rider)] == [
        "ride_assigned", "driver_arrived", "ride_completed"]
    assert all(n["ref"] == ride for n in p.notifications(rider))


def test_daily_report_and_top_restaurants(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    completed = w.complete(rider, card, driver)
    p.add_tip(rider, completed, "1.00")
    cancelled = w.ride_to_assigned(rider, card, driver)
    p.advance(minutes=3)
    p.cancel_ride(rider, cancelled)
    no_driver_rider = w.rider()
    q = p.quote_ride(no_driver_rider, at(-1), at(19), "xl")
    p.request_ride(no_driver_rider, q["quote_id"], w.card(no_driver_rider))
    r1, r2 = w.restaurant(), w.restaurant(location=at(0.5))
    i1 = p.add_menu_item(r1, "Tea", "6", "food", 1)
    i2 = p.add_menu_item(r2, "Tea", "6", "food", 1)
    courier = w.courier()
    for restaurant, item in ((r1, i1), (r2, i2), (r2, i2)):
        order = p.place_order(rider, restaurant, [{"item_id": item, "quantity": 2}], at(1), card)
        p.accept_order(restaurant, order)
        p.mark_ready(restaurant, order)
        p.update_location(courier, p._state.restaurants[restaurant].location)
        p.pick_up(courier, order)
        p.update_location(courier, at(1))
        p.deliver(courier, order)
    report = p.daily_report(w.admin, START.date())
    ride_charged = p.ride(completed)["charged"]
    order_total = p.order("ORD-000001")["total"]
    assert report["rides_completed"] == 1 and report["rides_cancelled"] == 1
    assert report["ride_revenue"] == ride_charged
    assert report["orders_delivered"] == 3
    assert report["order_revenue"] == 3 * order_total
    earned = sum((p.earnings(x, START, START + timedelta(days=1))["total"] for x in (driver, courier)), Decimal(0))
    captured = ride_charged + Decimal("1.00") + Decimal("3.00") + 3 * order_total
    assert report["platform_revenue"] == captured - earned
    assert report["cancellation_rate"] == Decimal("0.5000")
    empty = p.daily_report(w.admin, (START + timedelta(days=3)).date())
    assert empty["cancellation_rate"] == 0 and empty["rides_completed"] == 0
    top = p.top_restaurants(w.admin, START, START + timedelta(days=1))
    assert top == [
        {"restaurant_id": r2, "orders": 2, "revenue": 2 * order_total},
        {"restaurant_id": r1, "orders": 1, "revenue": order_total},
    ]
    assert p.top_restaurants(w.admin, START, START + timedelta(days=1), limit=1) == top[:1]
    with pytest.raises(PermissionError):
        p.daily_report(rider, START.date())
    with pytest.raises(PermissionError):
        p.top_restaurants(rider, START, START)
    with pytest.raises(PermissionError):
        p.audit_log(rider)


def test_audit_log_entries(w: World) -> None:
    p = w.p
    log = p.audit_log(w.admin)
    assert log == [{"at": START, "admin_id": w.admin, "action": "add_zone", "target": w.zone}]
    p.set_surge_cap(w.admin, w.zone, "1.5")
    assert p.audit_log(w.admin)[-1]["action"] == "set_surge_cap"
    assert CENTER
