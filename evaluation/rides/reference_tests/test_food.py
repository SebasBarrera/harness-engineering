"""J1-J4, K1-K6, L1-L4, M1-M4."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from conftest import CENTER, World, at
from rides import Platform


class Menu:
    def __init__(self, w: World, restaurant: str) -> None:
        p = w.p
        self.burger = p.add_menu_item(restaurant, "Burger", "12.00", "food", 15)
        self.beer = p.add_menu_item(restaurant, "Beer", Decimal("5"), "alcohol", 2)
        self.size = p.add_option_group(self.burger, "Size", True, 1, 1)
        self.large = p.add_option(self.size, "Large", "2.00")
        self.small = p.add_option(self.size, "Small", 0)
        self.extras = p.add_option_group(self.burger, "Extras", False, 0, 2)
        self.cheese = p.add_option(self.extras, "Cheese", "1.00")
        self.bacon = p.add_option(self.extras, "Bacon", "1.50")


@pytest.fixture
def shop(w: World) -> tuple[str, Menu]:
    restaurant = w.restaurant()
    return restaurant, Menu(w, restaurant)


def test_hours_and_is_open(w: World) -> None:
    p = w.p
    restaurant = p.register_restaurant("R", "r@x.com", "+573009998877", "secret1234", CENTER)
    p.set_hours(restaurant, 0, "11:00", "12:00")  # Monday; now is Monday 12:00
    p.approve(w.admin, restaurant)
    assert p.is_open(restaurant) is False  # closes is excluded
    p.set_hours(restaurant, 0, "12:00", "13:00")
    assert p.is_open(restaurant) is True
    for bad in (("9:00", "10:00"), ("10:00", "24:00"), ("12:00", "12:00"), ("13:00", "12:00")):
        with pytest.raises(ValueError):
            p.set_hours(restaurant, 0, *bad)
    with pytest.raises(ValueError):
        p.set_hours(restaurant, 7, "10:00", "11:00")
    p.advance(minutes=24 * 60)  # Tuesday without an interval
    assert p.is_open(restaurant) is False
    p.set_hours(restaurant, 1, "00:00", "23:59")
    assert p.is_open(restaurant) is True
    p.suspend(w.admin, restaurant, "health")
    assert p.is_open(restaurant) is False


def test_menu_validation_and_listing(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    for args in (("  ", "1", "food", 1), ("X", "0", "food", 1), ("X", "1", "drink", 1), ("X", "1", "food", 121),
                 ("X", 1.5, "food", 1), ("X", "1.234", "food", 1)):  # fmt: skip
        with pytest.raises(ValueError):
            p.add_menu_item(restaurant, *args)
    with pytest.raises(ValueError):
        p.add_option_group(menu.burger, "G", True, 0, 1)
    with pytest.raises(ValueError):
        p.add_option_group(menu.burger, "G", False, 2, 1)
    with pytest.raises(ValueError):
        p.add_option_group(menu.burger, "G", False, 0, 0)
    with pytest.raises(ValueError):
        p.add_option(menu.size, "Huge", "100.01")
    with pytest.raises(KeyError):
        p.add_option("GRP-999999", "Huge", "1")
    p.set_item_available(restaurant, menu.beer, False)
    listing = p.menu(restaurant)
    assert [item["id"] for item in listing] == [menu.burger, menu.beer]
    assert listing[1]["available"] is False
    assert listing[0]["groups"][0] == {
        "id": menu.size, "name": "Size", "required": True, "min_choices": 1, "max_choices": 1,
        "options": [{"id": menu.large, "name": "Large", "price_delta": Decimal("2.00")},
                    {"id": menu.small, "name": "Small", "price_delta": Decimal("0.00")}],
    }  # fmt: skip
    other = w.restaurant()
    with pytest.raises(PermissionError):
        p.set_item_available(other, menu.burger, False)


def test_order_pricing(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    lines = [
        {"item_id": menu.burger, "quantity": 2, "options": [menu.large, menu.cheese]},
        {"item_id": menu.beer, "quantity": 1},
    ]
    order_id = p.place_order(rider, restaurant, lines, at(3), card, tip=Decimal("2.00"))
    assert order_id == "ORD-000001"
    order = p.order(order_id)
    assert order["lines"] == [
        {"item_id": menu.burger, "quantity": 2, "options": [menu.large, menu.cheese], "unit_price": Decimal("15.00"),
         "amount": Decimal("30.00"), "tax": Decimal("2.40")},
        {"item_id": menu.beer, "quantity": 1, "options": [], "unit_price": Decimal("5.00"), "amount": Decimal("5.00"),
         "tax": Decimal("0.95")},
    ]  # fmt: skip
    assert order["subtotal"] == Decimal("35.00")
    assert order["service_fee"] == Decimal("1.75")
    assert order["delivery_fee"] == Decimal("0.00")
    assert order["small_order_fee"] == Decimal("0.00")
    assert order["tax"] == Decimal("3.35")
    assert order["total"] == Decimal("42.10")
    assert order["charged"] == Decimal("0.00") and order["status"] == "placed"
    assert p.notifications(restaurant)[-1]["kind"] == "new_order"


def test_small_order_and_delivery_fee(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    order = p.order(p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 1}], at(5), card))
    km = p.distance_km(CENTER, at(5))
    delivery = (Decimal("1.99") + Decimal("0.50") * (km - 2)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    assert order["small_order_fee"] == Decimal("2.00")
    assert order["service_fee"] == Decimal("1.00")
    assert order["delivery_fee"] == delivery
    assert order["total"] == Decimal("5.00") + Decimal("1.00") + delivery + Decimal("2.00") + Decimal("0.95")


def test_order_validation(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    other_shop = w.restaurant()
    foreign = p.add_menu_item(other_shop, "Soup", "4", "food", 5)
    burger = {"item_id": menu.burger, "quantity": 1, "options": [menu.small]}
    with pytest.raises(KeyError):
        p.place_order(rider, restaurant, [{"item_id": "ITM-999999", "quantity": 1}], at(1), card)
    with pytest.raises(KeyError):
        p.place_order(rider, restaurant, [{"item_id": menu.burger, "quantity": 1, "options": ["OPT-999"]}], at(1), card)
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [{"item_id": foreign, "quantity": 1}], at(1), card)
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [], at(1), card)
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [{"item_id": menu.burger, "quantity": 1}], at(1), card)  # required group
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [{"item_id": menu.burger, "quantity": 1, "options": [menu.small, menu.large]}],
                      at(1), card)  # fmt: skip
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [{**burger, "options": [menu.small, menu.cheese, menu.cheese]}], at(1), card)
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 1, "options": [menu.small]}], at(1), card)
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [{**burger, "quantity": 21}], at(1), card)
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [burger], at(11), card)  # more than 10 km
    with pytest.raises(ValueError):
        p.place_order(rider, restaurant, [burger], at(1), card, tip="6.01")  # more than 50 %
    teen = w.rider(birth=date(2009, 1, 1))
    with pytest.raises(ValueError):
        p.place_order(teen, restaurant, [{"item_id": menu.beer, "quantity": 1}], at(1), w.card(teen))
    no_birth = w.rider(birth=None)
    with pytest.raises(ValueError):
        p.place_order(no_birth, restaurant, [{"item_id": menu.beer, "quantity": 1}], at(1), w.card(no_birth))
    other_rider = w.rider()
    with pytest.raises(PermissionError):
        p.place_order(other_rider, restaurant, [burger], at(1), card)
    assert p.place_order(rider, restaurant, [burger], at(1), card) == "ORD-000001"


def test_closed_restaurant(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    _, menu = shop
    closed = w.restaurant(hours=("08:00", "09:00"))
    item = p.add_menu_item(closed, "Tea", "3", "food", 2)
    rider = w.rider()
    with pytest.raises(ValueError):
        p.place_order(rider, closed, [{"item_id": item, "quantity": 1}], at(1), w.card(rider))
    assert menu


def test_order_expiry_and_rejection(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    p.top_up_wallet(rider, card, "50")
    wallet = p.payment_methods(rider)[0]["id"]
    order = p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 2}], at(1), wallet)
    assert p.wallet_balance(rider) < Decimal("50.00")
    p.advance(minutes=4, seconds=59)
    assert p.order(order)["status"] == "placed"
    p.advance(seconds=1)
    assert p.order(order)["status"] == "expired"
    assert p.wallet_balance(rider) == Decimal("50.00")
    with pytest.raises(ValueError):
        p.accept_order(restaurant, order)
    second = p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 2}], at(1), wallet)
    other = w.restaurant()
    with pytest.raises(PermissionError):
        p.reject_order(other, second)
    p.reject_order(restaurant, second, "busy")
    assert p.order(second)["status"] == "rejected"
    assert p.wallet_balance(rider) == Decimal("50.00")


def test_full_order_lifecycle_with_courier(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    courier = w.courier(location=at(1))
    rider = w.rider()
    card = w.card(rider)
    order = p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 2}], at(3), card, tip="1")
    p.advance(minutes=1)
    p.accept_order(restaurant, order)
    view = p.order(order)
    assert view["status"] == "accepted" and view["ready_at"] == p.now() + timedelta(minutes=2)
    assert view["courier_id"] == courier
    assert p.worker_status(courier)["status"] == "busy"
    assert p.notifications(courier)[-1]["kind"] == "order_assigned"
    with pytest.raises(ValueError):
        p.pick_up(courier, order)  # not ready yet
    p.mark_ready(restaurant, order)
    with pytest.raises(ValueError):
        p.pick_up(courier, order)  # courier 1 km away from the restaurant
    p.update_location(courier, CENTER)
    p.pick_up(courier, order)
    with pytest.raises(ValueError):
        p.cancel_order(rider, order)
    with pytest.raises(ValueError):
        p.deliver(courier, order)
    p.update_location(courier, at(3))
    p.deliver(courier, order)
    view = p.order(order)
    assert view["status"] == "delivered" and view["charged"] == view["total"]
    assert p.worker_status(courier)["status"] == "available"
    kinds = [n["kind"] for n in p.notifications(rider)]
    assert kinds == ["order_accepted", "order_ready", "order_delivered"]
    km = p.distance_km(CENTER, at(3))
    pay = (Decimal("2.50") + Decimal("0.60") * km).quantize(Decimal("0.01"), ROUND_HALF_UP)
    earned = p.earnings(courier, p.now() - timedelta(hours=1), p.now() + timedelta(hours=1))
    assert earned == {"trips": 1, "fares": pay, "tips": Decimal("1.00"), "fees": Decimal("0.00"),
                      "bonus": Decimal("0.00"), "total": pay + 1}  # fmt: skip


def test_rider_and_admin_cancellation(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    lines = [{"item_id": menu.beer, "quantity": 2}]
    free = p.place_order(rider, restaurant, lines, at(1), card)
    p.cancel_order(rider, free)
    assert p.order(free)["status"] == "cancelled" and p.order(free)["charged"] == Decimal("0.00")
    paid = p.place_order(rider, restaurant, lines, at(1), card)
    p.accept_order(restaurant, paid)
    p.cancel_order(rider, paid)
    view = p.order(paid)
    assert view["charged"] == Decimal("10.00") + Decimal("1.90")
    assert p.payments(rider)[-1]["kind"] == "order"
    admin_cancel = p.place_order(rider, restaurant, lines, at(1), card)
    p.accept_order(restaurant, admin_cancel)
    with pytest.raises(PermissionError):
        p.cancel_order(restaurant, admin_cancel)
    p.cancel_order(w.admin, admin_cancel)
    assert p.order(admin_cancel)["charged"] == Decimal("0.00")
    assert p.notifications(rider)[-1]["kind"] == "order_cancelled"
    assert ("cancel_order", admin_cancel) in [(e["action"], e["target"]) for e in p.audit_log(w.admin)]


def test_courier_waiting_and_retry(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    far = w.courier(location=at(7))  # beyond 6 km
    order = p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 2}], at(3), card)
    p.accept_order(restaurant, order)
    assert p.order(order)["courier_id"] is None
    p.update_location(far, at(5))
    assert p.order(order)["courier_id"] is None  # retried on advance, not on location updates
    p.advance(seconds=1)
    assert p.order(order)["courier_id"] == far


def test_bike_limit_and_ties(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    bike = w.courier(location=at(0.5), vehicle="bike")
    car_b = w.courier(location=at(1), vehicle="car")
    car_a = w.courier(location=at(-1), vehicle="car")
    far_order = p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 2}], at(5), card)
    p.accept_order(restaurant, far_order)
    assert p.order(far_order)["courier_id"] == car_b  # bike skipped (> 4 km); tie to lower id
    near_order = p.place_order(rider, restaurant, [{"item_id": menu.beer, "quantity": 2}], at(-3), card)
    p.accept_order(restaurant, near_order)  # dropoffs 8 km apart: no batch
    assert p.order(near_order)["courier_id"] == bike
    assert car_a


def test_batching(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider1, rider2, rider3 = w.rider(), w.rider(), w.rider()
    first_courier = w.courier(location=at(0.1))
    lines = [{"item_id": menu.beer, "quantity": 2}]
    o1 = p.place_order(rider1, restaurant, lines, at(3), w.card(rider1))
    o2 = p.place_order(rider2, restaurant, lines, at(3.5), w.card(rider2))
    o3 = p.place_order(rider3, restaurant, lines, at(3.2), w.card(rider3))
    p.accept_order(restaurant, o1)
    other_courier = w.courier(location=at(0.05))
    p.advance(minutes=4, seconds=59)
    p.accept_order(restaurant, o2)
    assert p.order(o2)["courier_id"] == first_courier  # batched before the free courier
    p.accept_order(restaurant, o3)
    assert p.order(o3)["courier_id"] == other_courier  # never more than 2
    for order in (o1, o2):
        p.mark_ready(restaurant, order)
    p.update_location(first_courier, CENTER)
    p.pick_up(first_courier, o1)
    p.pick_up(first_courier, o2)
    p.update_location(first_courier, at(3))
    p.deliver(first_courier, o1)
    assert p.worker_status(first_courier)["status"] == "busy"
    p.update_location(first_courier, at(3.5))
    p.deliver(first_courier, o2)
    assert p.worker_status(first_courier)["status"] == "available"
    pay2 = (Decimal("2.50") + Decimal("0.60") * p.distance_km(CENTER, at(3.5))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    pay1 = (Decimal("2.50") + Decimal("0.60") * p.distance_km(CENTER, at(3))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    batched = (pay2 * Decimal("0.70")).quantize(Decimal("0.01"), ROUND_HALF_UP)
    earned = p.earnings(first_courier, p.now() - timedelta(hours=1), p.now() + timedelta(hours=1))
    assert earned["fares"] == pay1 + batched


def test_no_batch_at_exactly_five_minutes(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    r1, r2 = w.rider(), w.rider()
    first = w.courier(location=at(0.1))
    lines = [{"item_id": menu.beer, "quantity": 2}]
    o1 = p.place_order(r1, restaurant, lines, at(3), w.card(r1))
    p.accept_order(restaurant, o1)
    p.advance(minutes=4)
    o2 = p.place_order(r2, restaurant, lines, at(3), w.card(r2))
    p.advance(minutes=1)
    p.accept_order(restaurant, o2)
    assert p.order(o2)["courier_id"] is None
    assert first


def test_idempotent_order(w: World, shop: tuple[str, Menu]) -> None:
    p = w.p
    restaurant, menu = shop
    rider = w.rider()
    card = w.card(rider)
    lines = [{"item_id": menu.beer, "quantity": 2}]
    order = p.place_order(rider, restaurant, lines, at(1), card, idempotency_key="abc")
    assert p.place_order(rider, restaurant, lines, at(1), card, idempotency_key="abc") == order
    assert p.place_order(rider, restaurant, lines, at(1), card, idempotency_key="abd") != order


def test_unknown_order(p: Platform) -> None:
    with pytest.raises(KeyError):
        p.order("ORD-000001")
