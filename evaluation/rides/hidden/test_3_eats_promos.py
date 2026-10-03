"""Hidden acceptance checks of the ride-hailing scenario, part 3: food delivery and promotions.

Never shown to the agent. Covers sections J (restaurants and menus), K (food order pricing),
L (food order lifecycle), M (couriers and batching) and N (promotions and referrals) of SPEC.md,
plus the general rules (X1, X2, X5, X7, X8) as they apply to those sections.
Each test name starts with the identifier of the one requirement it checks and uses only the public
interface (`from rides import Platform`).

Geometry: every point lies on the meridian -74.05, so distances are exact multiples of the arc of
0.001 degrees of latitude. The points were chosen so that rounding the distance to 3 places first, or
not, gives the same fees and courier pay (no value sits near a rounding boundary), except the
boundary points (AT_0_200, DROP_10_IN, ...) whose raw distance is slightly above the limit and rounds
to it (X6: distances are compared after rounding to 3 places).

Deliberately not checked (the requirements do not determine them): the order of the option ids in
`order()["lines"]` when they are not given in id order (tests give them in id order), extra keys in
the dicts returned, whether a non-active rider may place an order, and whether a restaurant or a
courier may cancel an order.
"""

from __future__ import annotations

import copy
import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

import pytest
from rides import Platform

D = Decimal
START = datetime(2026, 3, 2, 12, 0)  # a Monday
DAY_START = datetime(2026, 3, 2, 0, 0)
DAY_END = datetime(2026, 3, 3, 0, 0)
PASSWORD = "Secret12345"
CARD = "4242424242424242"
DECLINED_CARD = "4000000000000002"

REST = (4.65, -74.05)  # restaurant and zone center
D_NEAR = (4.665, -74.05)  # 1.668 km from REST
D_B = (4.675, -74.05)  # 2.780 km from REST, 1.112 km from D_NEAR
D_SOUTH = (4.635, -74.05)  # 1.668 km from REST, 3.336 km from D_NEAR
D_FAR3 = (4.68, -74.05)  # 3.336 km from REST
D_5K = (4.695, -74.05)  # 5.004 km from REST
D_IN = (4.738, -74.05)  # 9.785 km from REST
D_OUT = (4.75, -74.05)  # 11.119 km from REST
NEAR_HALF = (4.6545, -74.05)  # 0.500 km from REST
NEAR_ONE = (4.659, -74.05)  # 1.001 km from REST
AT_5_8 = (4.702, -74.05)  # 5.782 km from REST
AT_6_7 = (4.71, -74.05)  # 6.672 km from REST
NEAR_DROP_OK = (4.6635, -74.05)  # 0.167 km from D_NEAR
NEAR_DROP_FAR = (4.6692, -74.05)  # 0.467 km from D_NEAR
PICK = (4.66, -74.05)  # ride pickup
DROP = (4.69, -74.05)  # ride dropoff, 3.336 km from PICK
# Boundary points (raw haversine distance from REST, then rounded to 3 places).
AT_0_200 = (4.6518, -74.05)  # 0.20015 -> 0.200 km
AT_0_210 = (4.65189, -74.05)  # 0.21016 -> 0.210 km
D_4_000 = (4.685975, -74.05)  # 4.00024 -> 4.000 km
D_4_001 = (4.68598, -74.05)  # 4.00079 -> 4.001 km
AT_6_000 = (4.703961, -74.05)  # 6.00019 -> 6.000 km
AT_6_001 = (4.70397, -74.05)  # 6.00119 -> 6.001 km
DROP_10_IN = (4.739935, -74.05)  # 10.00032 -> 10.000 km
DROP_10_OUT = (4.73994, -74.05)  # 10.00087 -> 10.001 km
NEAR_DROP_200 = (4.6668, -74.05)  # 0.200 km from D_NEAR (raw 0.20015)
NEAR_DROP_210 = (4.66689, -74.05)  # 0.210 km from D_NEAR


# Expected-value helpers ---------------------------------------------------------------------------
def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def km(a: tuple[float, float], b: tuple[float, float]) -> Decimal:
    return D(repr(_haversine(a, b))).quantize(D("0.001"), ROUND_HALF_UP)


def money(value: Decimal) -> Decimal:
    return D(value).quantize(D("0.01"), ROUND_HALF_UP)


def courier_pay(dropoff: tuple[float, float]) -> Decimal:
    return money(D("2.50") + D("0.60") * km(REST, dropoff))


# World builder ------------------------------------------------------------------------------------
class World:
    """A platform at START with one admin and one zone of 30 km around REST."""

    def __init__(self) -> None:
        self.p = Platform(START)
        self._n = 0
        email, phone = self._contact()
        self.admin = self.p.create_admin("Admin One", email, phone, PASSWORD)
        self.zone = self.p.add_zone(self.admin, "City", REST, 30, 30)

    def _contact(self) -> tuple[str, str]:
        self._n += 1
        return f"user{self._n}@example.com", f"+57300{self._n:07d}"

    def restaurant(self, location=REST, hours=("08:00", "22:00"), approve=True) -> str:
        email, phone = self._contact()
        rst = self.p.register_restaurant(f"Restaurant {self._n}", email, phone, PASSWORD, location)
        if approve:
            self.p.approve(self.admin, rst)
        if hours is not None:
            self.p.set_hours(rst, START.weekday(), hours[0], hours[1])
        return rst

    def rider(self, birth_date=None, referral_code=None) -> tuple[str, str]:
        email, phone = self._contact()
        kwargs = {}
        if birth_date is not None:
            kwargs["birth_date"] = birth_date
        if referral_code is not None:
            kwargs["referral_code"] = referral_code
        rider = self.p.register_rider(f"Rider {self._n}", email, phone, PASSWORD, **kwargs)
        card = self.p.add_card(rider, CARD, 12, 2030, "123")
        return rider, card

    def wallet_rider(self, amount) -> tuple[str, str]:
        rider, card = self.rider()
        self.p.top_up_wallet(rider, card, amount)
        return rider, "WAL-" + rider[4:]

    def courier(self, vehicle="moto", location=REST, online=True) -> str:
        email, phone = self._contact()
        courier = self.p.register_courier(f"Courier {self._n}", email, phone, PASSWORD, vehicle)
        self.p.approve(self.admin, courier)
        if online:
            self.p.go_online(courier, location)
        return courier

    def driver(self, location=PICK) -> str:
        email, phone = self._contact()
        driver = self.p.register_driver(
            f"Driver {self._n}", email, phone, PASSWORD, "LIC12345", date(2030, 1, 1)
        )
        vehicle = self.p.add_vehicle(driver, f"ABC{self._n:03d}", "Toyota", "Corolla", 2022, 4, "economy")
        self.p.approve(self.admin, driver)
        self.p.go_online(driver, location, vehicle)
        return driver


def line(item: str, quantity: int = 1, options=None) -> dict:
    data: dict = {"item_id": item, "quantity": quantity}
    if options is not None:
        data["options"] = list(options)
    return data


def deliver_order(p: Platform, rst: str, order_id: str, courier: str, dropoff) -> None:
    """Accept, prepare, pick up and deliver an order, expecting `courier` to be assigned."""
    p.accept_order(rst, order_id)
    assert p.order(order_id)["courier_id"] == courier
    p.mark_ready(rst, order_id)
    p.pick_up(courier, order_id)
    p.update_location(courier, dropoff)
    p.deliver(courier, order_id)


def finish_ride(p: Platform, rider: str, card: str, driver: str, promo_code=None) -> tuple[str, dict]:
    """Quote, request, accept, arrive, start and complete an economy ride from PICK to DROP."""
    p.update_location(driver, PICK)
    quote = p.quote_ride(rider, PICK, DROP, "economy")
    if promo_code is None:
        ride_id = p.request_ride(rider, quote["quote_id"], card)
    else:
        ride_id = p.request_ride(rider, quote["quote_id"], card, promo_code=promo_code)
    p.accept_ride(driver, ride_id)
    p.driver_arrived(driver, ride_id)
    p.start_ride(driver, ride_id)
    p.advance(minutes=6)
    p.complete_ride(driver, ride_id, [PICK, DROP])
    return ride_id, quote


@pytest.fixture()
def eats() -> SimpleNamespace:
    """An open restaurant at REST with a menu, and a rider (no birth date) with a card."""
    w = World()
    p = w.p
    rst = w.restaurant()
    rider, card = w.rider()
    burger = p.add_menu_item(rst, "Burger", "8.50", "food", 15)
    size = p.add_option_group(burger, "Size", True, 1, 1)
    regular = p.add_option(size, "Regular", "0")
    large = p.add_option(size, "Large", "1.50")
    extras = p.add_option_group(burger, "Extras", False, 0, 2)
    cheese = p.add_option(extras, "Cheese", "0.75")
    bacon = p.add_option(extras, "Bacon", "1.25")
    egg = p.add_option(extras, "Egg", "1.00")
    pizza = p.add_menu_item(rst, "Pizza", "12.25", "food", 25)
    beer = p.add_menu_item(rst, "Beer", "4.50", "alcohol", 2)
    return SimpleNamespace(
        w=w, p=p, rst=rst, rider=rider, card=card, burger=burger, size=size, regular=regular,
        large=large, extras=extras, cheese=cheese, bacon=bacon, egg=egg, pizza=pizza, beer=beer,
    )


def place(e: SimpleNamespace, lines, dropoff=D_NEAR, rider=None, method=None, **kwargs) -> str:
    return e.p.place_order(rider or e.rider, e.rst, lines, dropoff, method or e.card, **kwargs)


def burger_line(e: SimpleNamespace, quantity: int = 1) -> dict:
    """Burger, size Regular: 8.50 each. One of them at D_NEAR totals 14.17."""
    return line(e.burger, quantity, [e.regular])


# J. Restaurants and menus -------------------------------------------------------------------------
def test_j1_open_inside_todays_interval():
    w = World()
    rst = w.restaurant(hours=("08:00", "22:00"))
    assert w.p.is_open(rst) is True


@pytest.mark.parametrize(
    ("opens", "closes", "expected"),
    [
        ("12:00", "13:00", True),  # opens is inclusive
        ("08:00", "12:00", False),  # closes is exclusive
        ("12:01", "20:00", False),
        ("00:00", "11:59", False),
    ],
)
def test_j1_interval_is_half_open(opens, closes, expected):
    w = World()
    rst = w.restaurant(hours=(opens, closes))
    assert w.p.is_open(rst) is expected


def test_j1_without_hours_is_closed():
    w = World()
    rst = w.restaurant(hours=None)
    assert w.p.is_open(rst) is False


def test_j1_only_the_current_weekday_counts():
    w = World()
    rst = w.restaurant(hours=None)
    w.p.set_hours(rst, 1, "08:00", "22:00")  # Tuesday only
    assert w.p.is_open(rst) is False
    w.p.advance(minutes=24 * 60)  # Tuesday 12:00
    assert w.p.is_open(rst) is True


def test_j1_set_hours_replaces_the_previous_interval():
    w = World()
    rst = w.restaurant(hours=("08:00", "11:00"))
    assert w.p.is_open(rst) is False
    w.p.set_hours(rst, 0, "11:00", "13:00")
    assert w.p.is_open(rst) is True
    w.p.set_hours(rst, 0, "13:00", "14:00")
    assert w.p.is_open(rst) is False


def test_j1_closes_when_the_clock_reaches_closing_time():
    w = World()
    rst = w.restaurant(hours=("08:00", "12:30"))
    w.p.advance(minutes=29)
    assert w.p.is_open(rst) is True
    w.p.advance(minutes=1)
    assert w.p.is_open(rst) is False


def test_j1_pending_restaurant_is_not_open():
    w = World()
    rst = w.restaurant(approve=False, hours=("08:00", "22:00"))
    assert w.p.is_open(rst) is False
    w.p.approve(w.admin, rst)
    assert w.p.is_open(rst) is True


def test_j1_suspended_restaurant_is_not_open():
    w = World()
    rst = w.restaurant()
    w.p.suspend(w.admin, rst, "health inspection")
    assert w.p.is_open(rst) is False


@pytest.mark.parametrize("weekday", [7, -1])
def test_j1_invalid_weekday(weekday):
    w = World()
    rst = w.restaurant(hours=None)
    with pytest.raises(ValueError):
        w.p.set_hours(rst, weekday, "08:00", "22:00")


@pytest.mark.parametrize(("opens", "closes"), [("12:00", "12:00"), ("14:00", "10:00")])
def test_j1_opens_must_be_before_closes(opens, closes):
    w = World()
    rst = w.restaurant(hours=None)
    with pytest.raises(ValueError):
        w.p.set_hours(rst, 0, opens, closes)


@pytest.mark.parametrize(
    ("opens", "closes"),
    [
        ("25:00", "26:00"),
        ("12:60", "13:00"),
        ("noon", "13:00"),
        ("0800", "1200"),
        ("08:00", "24:30"),
        ("08:00", "24:00"),  # X7: 00:00 to 23:59
        ("8:00", "12:00"),  # X7: two digits each
        ("08:00", "12:5"),
    ],
)
def test_j1_times_must_be_hh_mm(opens, closes):
    w = World()
    rst = w.restaurant(hours=None)
    with pytest.raises(ValueError):
        w.p.set_hours(rst, 0, opens, closes)


def test_j1_whole_day_bounds_are_accepted():
    w = World()
    rst = w.restaurant(hours=None)
    w.p.set_hours(rst, 0, "00:00", "23:59")
    assert w.p.is_open(rst) is True


def test_j1_unknown_restaurant():
    w = World()
    with pytest.raises(KeyError):
        w.p.set_hours("RST-999999", 0, "08:00", "22:00")
    with pytest.raises(KeyError):
        w.p.is_open("RST-999999")


def test_j2_new_item_starts_available():
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Soup", "6.00", "food", 10)
    [entry] = w.p.menu(rst)
    assert entry["id"] == item
    assert entry["name"] == "Soup"
    assert entry["price"] == D("6.00")
    assert entry["category"] == "food"
    assert entry["available"] is True


@pytest.mark.parametrize("price", ["0", "-1.00", 0, "-0.01"])
def test_j2_price_must_be_positive(price):
    w = World()
    rst = w.restaurant()
    with pytest.raises(ValueError):
        w.p.add_menu_item(rst, "Soup", price, "food", 10)


def test_j2_smallest_positive_price_is_accepted():
    w = World()
    rst = w.restaurant()
    w.p.add_menu_item(rst, "Mint", "0.01", "food", 1)
    assert w.p.menu(rst)[0]["price"] == D("0.01")


@pytest.mark.parametrize("name", ["", "   "])
def test_j2_name_must_not_be_empty(name):
    w = World()
    rst = w.restaurant()
    with pytest.raises(ValueError):
        w.p.add_menu_item(rst, name, "6.00", "food", 10)
    assert w.p.menu(rst) == []


@pytest.mark.parametrize("category", ["drink", "", "beverage"])
def test_j2_category_must_be_food_or_alcohol(category):
    w = World()
    rst = w.restaurant()
    with pytest.raises(ValueError):
        w.p.add_menu_item(rst, "Soup", "6.00", category, 10)


def test_j2_alcohol_category_is_accepted():
    w = World()
    rst = w.restaurant()
    w.p.add_menu_item(rst, "Wine", "9.00", "alcohol", 1)
    assert w.p.menu(rst)[0]["category"] == "alcohol"


@pytest.mark.parametrize("prep", [0, 121, -5, 10.5, "10", True, 10.0])
def test_j2_prep_minutes_int_from_1_to_120(prep):
    w = World()
    rst = w.restaurant()
    with pytest.raises(ValueError):
        w.p.add_menu_item(rst, "Soup", "6.00", "food", prep)


@pytest.mark.parametrize("prep", [1, 120])
def test_j2_prep_minutes_bounds_are_accepted(prep):
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Soup", "6.00", "food", prep)
    assert [entry["id"] for entry in w.p.menu(rst)] == [item]


def test_j2_unknown_restaurant():
    w = World()
    with pytest.raises(KeyError):
        w.p.add_menu_item("RST-999999", "Soup", "6.00", "food", 10)


@pytest.mark.parametrize(
    ("required", "min_choices", "max_choices"),
    [(False, 2, 1), (False, 0, 0), (False, -1, 1), (True, 0, 2), (True, 0, 0), (True, 3, 2)],
)
def test_j3_invalid_group_bounds(required, min_choices, max_choices):
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Bowl", "7.00", "food", 10)
    with pytest.raises(ValueError):
        w.p.add_option_group(item, "Toppings", required, min_choices, max_choices)


@pytest.mark.parametrize(
    ("required", "min_choices", "max_choices"),
    [(True, 1, 1), (False, 0, 3), (True, 2, 3), (False, 0, 1)],
)
def test_j3_valid_group_bounds(required, min_choices, max_choices):
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Bowl", "7.00", "food", 10)
    group = w.p.add_option_group(item, "Toppings", required, min_choices, max_choices)
    [entry] = w.p.menu(rst)[0]["groups"]
    assert entry["id"] == group
    assert entry["required"] is required
    assert entry["min_choices"] == min_choices
    assert entry["max_choices"] == max_choices


@pytest.mark.parametrize("delta", ["-0.01", "100.01", -1, "250"])
def test_j3_option_delta_from_0_to_100(delta):
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Bowl", "7.00", "food", 10)
    group = w.p.add_option_group(item, "Toppings", False, 0, 2)
    with pytest.raises(ValueError):
        w.p.add_option(group, "Gold leaf", delta)


@pytest.mark.parametrize("delta", ["0", "100", "100.00", 0])
def test_j3_option_delta_bounds_are_accepted(delta):
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Bowl", "7.00", "food", 10)
    group = w.p.add_option_group(item, "Toppings", False, 0, 2)
    option = w.p.add_option(group, "Topping", delta)
    [entry] = w.p.menu(rst)[0]["groups"][0]["options"]
    assert entry["id"] == option
    assert entry["price_delta"] == D(delta)


def test_j3_unknown_item_or_group():
    w = World()
    with pytest.raises(KeyError):
        w.p.add_option_group("ITM-999999", "Toppings", False, 0, 1)
    with pytest.raises(KeyError):
        w.p.add_option("GRP-999999", "Topping", "1.00")


def test_j4_menu_lists_items_in_id_order(eats):
    menu = eats.p.menu(eats.rst)
    assert [entry["id"] for entry in menu] == sorted([eats.burger, eats.pizza, eats.beer])
    by_id = {entry["id"]: entry for entry in menu}
    assert (by_id[eats.burger]["name"], by_id[eats.burger]["price"]) == ("Burger", D("8.50"))
    assert (by_id[eats.pizza]["name"], by_id[eats.pizza]["price"]) == ("Pizza", D("12.25"))
    assert (by_id[eats.beer]["name"], by_id[eats.beer]["price"]) == ("Beer", D("4.50"))
    assert by_id[eats.beer]["category"] == "alcohol"
    assert by_id[eats.pizza]["category"] == "food"
    assert all(entry["available"] is True for entry in menu)
    assert by_id[eats.pizza]["groups"] == []


def test_j4_menu_shows_groups_and_options(eats):
    burger = next(entry for entry in eats.p.menu(eats.rst) if entry["id"] == eats.burger)
    assert [group["id"] for group in burger["groups"]] == [eats.size, eats.extras]
    size, extras = burger["groups"]
    assert (size["name"], size["required"], size["min_choices"], size["max_choices"]) == ("Size", True, 1, 1)
    assert (extras["name"], extras["required"], extras["min_choices"], extras["max_choices"]) == (
        "Extras",
        False,
        0,
        2,
    )
    size_options = [(option["id"], option["name"], option["price_delta"]) for option in size["options"]]
    assert size_options == [(eats.regular, "Regular", D("0")), (eats.large, "Large", D("1.50"))]
    extra_options = [(option["id"], option["name"], option["price_delta"]) for option in extras["options"]]
    assert extra_options == [
        (eats.cheese, "Cheese", D("0.75")),
        (eats.bacon, "Bacon", D("1.25")),
        (eats.egg, "Egg", D("1.00")),
    ]


def test_j4_groups_and_options_in_id_order_not_name_order():
    w = World()
    rst = w.restaurant()
    item = w.p.add_menu_item(rst, "Bowl", "7.00", "food", 10)
    zeta = w.p.add_option_group(item, "Zeta sauces", False, 0, 2)
    alpha = w.p.add_option_group(item, "Alpha toppings", False, 0, 2)
    late = w.p.add_option(alpha, "Zucchini", "0.50")
    early_name = w.p.add_option(zeta, "Aioli", "0.25")
    first_in_alpha = w.p.add_option(alpha, "Avocado", "1.00")
    [entry] = w.p.menu(rst)
    assert [group["id"] for group in entry["groups"]] == [zeta, alpha]
    assert [option["id"] for option in entry["groups"][0]["options"]] == [early_name]
    assert [option["id"] for option in entry["groups"][1]["options"]] == [late, first_in_alpha]


def test_j4_menu_shows_availability(eats):
    eats.p.set_item_available(eats.rst, eats.beer, False)
    available = {entry["id"]: entry["available"] for entry in eats.p.menu(eats.rst)}
    assert available == {eats.burger: True, eats.pizza: True, eats.beer: False}
    eats.p.set_item_available(eats.rst, eats.beer, True)
    assert all(entry["available"] is True for entry in eats.p.menu(eats.rst))


def test_j4_menu_lists_only_the_restaurants_items(eats):
    other = eats.w.restaurant()
    salad = eats.p.add_menu_item(other, "Salad", "7.00", "food", 5)
    assert [entry["id"] for entry in eats.p.menu(other)] == [salad]
    assert salad not in [entry["id"] for entry in eats.p.menu(eats.rst)]


def test_j4_unknown_restaurant():
    w = World()
    with pytest.raises(KeyError):
        w.p.menu("RST-999999")


# K. Food order pricing ----------------------------------------------------------------------------
@pytest.mark.parametrize("quantity", [0, 21, -1, 1.5, 2.0, "2", True])
def test_k1_quantity_int_from_1_to_20(eats, quantity):
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza, quantity)])


def test_k1_quantity_twenty_is_accepted(eats):
    order = eats.p.order(place(eats, [line(eats.pizza, 20)]))
    assert order["status"] == "placed"
    assert order["subtotal"] == D("245.00")


def test_k1_restaurant_must_be_open(eats):
    eats.p.set_hours(eats.rst, 0, "08:00", "11:00")
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)])


def test_k1_unknown_restaurant(eats):
    with pytest.raises(KeyError):
        eats.p.place_order(eats.rider, "RST-999999", [line(eats.pizza)], D_NEAR, eats.card)


def test_k1_item_must_belong_to_the_restaurant(eats):
    other = eats.w.restaurant()
    salad = eats.p.add_menu_item(other, "Salad", "7.00", "food", 5)
    with pytest.raises(ValueError):
        place(eats, [line(salad)])


def test_k1_unknown_item(eats):
    with pytest.raises(KeyError):
        place(eats, [line("ITM-999999")])


def test_k1_item_must_be_available(eats):
    eats.p.set_item_available(eats.rst, eats.pizza, False)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)])
    eats.p.set_item_available(eats.rst, eats.pizza, True)
    assert eats.p.order(place(eats, [line(eats.pizza)]))["status"] == "placed"


def test_k1_options_must_belong_to_the_item(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza, 1, [eats.cheese])])
    with pytest.raises(ValueError):  # an option of another item, valid choices otherwise
        place(eats, [line(eats.burger, 1, [eats.regular]), line(eats.pizza, 1, [eats.large])])


def test_k1_unknown_option(eats):
    with pytest.raises(KeyError):
        place(eats, [line(eats.burger, 1, [eats.regular, "OPT-999999"])])
    with pytest.raises(KeyError):
        place(eats, [line(eats.pizza, 1, ["OPT-999999"])])


def test_k1_required_group_needs_min_choices(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.burger)])
    with pytest.raises(ValueError):
        place(eats, [line(eats.burger, 1, [])])
    with pytest.raises(ValueError):
        place(eats, [line(eats.burger, 1, [eats.cheese])])


def test_k1_required_group_at_most_max_choices(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.burger, 1, [eats.regular, eats.large])])


def test_k1_optional_group_at_most_max_choices(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.burger, 1, [eats.regular, eats.cheese, eats.bacon, eats.egg])])


def test_k1_option_not_repeated(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.burger, 1, [eats.regular, eats.cheese, eats.cheese])])


def test_k1_valid_choices_are_accepted(eats):
    order_id = place(eats, [line(eats.burger, 1, [eats.large, eats.cheese, eats.bacon])])
    assert eats.p.order(order_id)["status"] == "placed"


def test_k1_optional_group_may_be_left_empty(eats):
    order_id = place(eats, [line(eats.burger, 1, [eats.regular])])
    assert eats.p.order(order_id)["status"] == "placed"


def test_k1_dropoff_at_most_10_km_from_restaurant(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], dropoff=D_OUT)
    order_id = place(eats, [line(eats.pizza)], dropoff=D_IN)
    assert eats.p.order(order_id)["status"] == "placed"


def test_k1_dropoff_limit_compared_after_rounding(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], dropoff=DROP_10_OUT)  # 10.001 km
    order_id = place(eats, [line(eats.pizza)], dropoff=DROP_10_IN)  # 10.000 km (raw 10.0003)
    assert eats.p.order(order_id)["status"] == "placed"


def test_k1_at_least_one_line(eats):
    with pytest.raises(ValueError):
        place(eats, [])


def test_k1_order_lines_in_the_given_order(eats):
    lines = [line(eats.pizza, 3, []), line(eats.burger, 2, [eats.large, eats.cheese])]
    order = eats.p.order(place(eats, lines))
    keys = ("item_id", "quantity", "options", "unit_price", "amount", "tax")
    assert [{key: entry[key] for key in keys} for entry in order["lines"]] == [
        {
            "item_id": eats.pizza,
            "quantity": 3,
            "options": [],
            "unit_price": D("12.25"),
            "amount": D("36.75"),
            "tax": D("2.94"),
        },
        {
            "item_id": eats.burger,
            "quantity": 2,
            "options": [eats.large, eats.cheese],
            "unit_price": D("10.75"),  # 8.50 + 1.50 + 0.75
            "amount": D("21.50"),
            "tax": D("1.72"),
        },
    ]
    assert order["subtotal"] == D("58.25")
    assert order["tax"] == D("4.66")


def test_k1_order_line_alcohol_tax(eats):
    adult, card = eats.w.rider(birth_date=date(1990, 1, 1))
    order = eats.p.order(place(eats, [line(eats.beer, 3)], rider=adult, method=card))
    [entry] = order["lines"]
    assert (entry["unit_price"], entry["amount"], entry["tax"]) == (D("4.50"), D("13.50"), D("2.57"))


def test_k2_alcohol_requires_a_birth_date(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.beer)])


def test_k2_alcohol_requires_18_years(eats):
    minor, card = eats.w.rider(birth_date=date(2008, 3, 3))  # 18 tomorrow
    with pytest.raises(ValueError):
        place(eats, [line(eats.beer)], rider=minor, method=card)


def test_k2_alcohol_allowed_on_the_18th_birthday(eats):
    adult, card = eats.w.rider(birth_date=date(2008, 3, 2))
    order_id = place(eats, [line(eats.beer)], rider=adult, method=card)
    assert eats.p.order(order_id)["status"] == "placed"


def test_k2_food_needs_no_age_check(eats):
    minor, card = eats.w.rider(birth_date=date(2009, 6, 1))
    assert eats.p.order(place(eats, [line(eats.pizza)], rider=minor, method=card))["status"] == "placed"
    assert eats.p.order(place(eats, [line(eats.pizza)]))["status"] == "placed"


def test_k3_unit_price_includes_option_deltas(eats):
    # (8.50 + 1.50 + 0.75 + 1.25) x 3 = 36.00
    order_id = place(eats, [line(eats.burger, 3, [eats.large, eats.cheese, eats.bacon])])
    assert eats.p.order(order_id)["subtotal"] == D("36.00")


def test_k3_subtotal_sums_the_lines(eats):
    # 8.50 x 2 + (8.50 + 1.50 + 1.00) x 1 + 12.25 x 3 = 17.00 + 11.00 + 36.75
    lines = [burger_line(eats, 2), line(eats.burger, 1, [eats.large, eats.egg]), line(eats.pizza, 3)]
    assert eats.p.order(place(eats, lines))["subtotal"] == D("64.75")


@pytest.mark.parametrize(
    ("price", "fee"),
    [
        ("10.00", "1.00"),  # 0.50 raised to the minimum
        ("19.99", "1.00"),
        ("20.10", "1.01"),  # 1.005 half up
        ("22.50", "1.13"),  # 1.125 half up
        ("30.00", "1.50"),
        ("100.00", "5.00"),
        ("120.00", "5.00"),  # 6.00 capped
    ],
)
def test_k4_service_fee_bounds(eats, price, fee):
    item = eats.p.add_menu_item(eats.rst, "Platter", price, "food", 10)
    assert eats.p.order(place(eats, [line(item)]))["service_fee"] == D(fee)


@pytest.mark.parametrize(("price", "fee"), [("9.99", "2.00"), ("5.00", "2.00"), ("10.00", "0.00"), ("10.01", "0.00")])
def test_k4_small_order_fee_below_10(eats, price, fee):
    item = eats.p.add_menu_item(eats.rst, "Platter", price, "food", 10)
    assert eats.p.order(place(eats, [line(item)]))["small_order_fee"] == D(fee)


@pytest.mark.parametrize(
    ("price", "dropoff", "fee"),
    [
        ("20.00", D_NEAR, "1.99"),  # 1.668 km
        ("20.00", D_B, "2.38"),  # 1.99 + 0.50 x 0.780
        ("20.00", D_FAR3, "2.66"),  # 1.99 + 0.50 x 1.336 = 2.658
        ("20.00", D_IN, "5.88"),  # 1.99 + 0.50 x 7.785 = 5.8825
        ("34.99", D_FAR3, "2.66"),
        ("35.00", D_FAR3, "0.00"),
        ("35.00", D_IN, "0.00"),
    ],
)
def test_k4_delivery_fee(eats, price, dropoff, fee):
    item = eats.p.add_menu_item(eats.rst, "Platter", price, "food", 10)
    assert eats.p.order(place(eats, [line(item)], dropoff=dropoff))["delivery_fee"] == D(fee)


def test_k5_tax_is_rounded_per_line(eats):
    tea = eats.p.add_menu_item(eats.rst, "Tea", "1.05", "food", 1)
    cake = eats.p.add_menu_item(eats.rst, "Cake", "1.05", "food", 1)
    # 0.084 -> 0.08 per line, so 0.16 (rounding the sum would give 0.17)
    assert eats.p.order(place(eats, [line(tea), line(cake)]))["tax"] == D("0.16")


def test_k5_food_tax_is_8_percent(eats):
    assert eats.p.order(place(eats, [line(eats.pizza, 2)]))["tax"] == D("1.96")


def test_k5_alcohol_tax_is_19_percent_half_up(eats):
    adult, card = eats.w.rider(birth_date=date(1990, 1, 1))
    cider = eats.p.add_menu_item(eats.rst, "Cider", "1.50", "alcohol", 1)
    # 1.50 x 0.19 = 0.285 -> 0.29; 13.50 x 0.19 = 2.565 -> 2.57
    order_id = place(eats, [line(cider), line(eats.beer, 3)], rider=adult, method=card)
    assert eats.p.order(order_id)["tax"] == D("2.86")


@pytest.mark.parametrize("tip", ["10.01", "-0.01", "50"])
def test_k5_tip_at_most_half_the_subtotal(eats, tip):
    item = eats.p.add_menu_item(eats.rst, "Platter", "20.00", "food", 10)
    with pytest.raises(ValueError):
        place(eats, [line(item)], tip=tip)


@pytest.mark.parametrize("tip", ["0", "3.33", "10.00"])
def test_k5_tip_within_bounds_is_added(eats, tip):
    item = eats.p.add_menu_item(eats.rst, "Platter", "20.00", "food", 10)
    order = eats.p.order(place(eats, [line(item)], tip=tip))
    assert order["tip"] == D(tip)
    # 20.00 + 1.00 service + 1.99 delivery + 1.60 tax + tip
    assert order["total"] == D("24.59") + D(tip)


def test_k5_total_adds_every_component(eats):
    adult, card = eats.w.rider(birth_date=date(1990, 1, 1))
    lines = [line(eats.burger, 2, [eats.large]), line(eats.beer, 3)]
    order = eats.p.order(place(eats, lines, dropoff=D_FAR3, rider=adult, method=card, tip="5.00"))
    assert order["subtotal"] == D("33.50")
    assert order["service_fee"] == D("1.68")  # 1.675 half up
    assert order["delivery_fee"] == D("2.66")
    assert order["small_order_fee"] == D("0.00")
    assert order["tax"] == D("4.17")  # 1.60 + 2.57
    assert order["discount"] == D("0.00")
    assert order["tip"] == D("5.00")
    assert order["total"] == D("47.01")


def test_k5_total_with_small_order_fee(eats):
    order = eats.p.order(place(eats, [burger_line(eats)]))
    # 8.50 + 1.00 service + 1.99 delivery + 2.00 small order + 0.68 tax
    assert order["total"] == D("14.17")


def test_k6_hold_is_for_the_total(eats):
    short, short_wallet = eats.w.wallet_rider("14.16")
    with pytest.raises(ValueError):
        place(eats, [burger_line(eats)], rider=short, method=short_wallet)
    exact, exact_wallet = eats.w.wallet_rider("14.17")
    order_id = place(eats, [burger_line(eats)], rider=exact, method=exact_wallet)
    assert eats.p.order(order_id)["total"] == D("14.17")
    with pytest.raises(ValueError):  # the first hold leaves nothing for a second order
        place(eats, [burger_line(eats)], rider=exact, method=exact_wallet)


def test_k6_failed_card_hold_rejects_the_order(eats):
    declined = eats.p.add_card(eats.rider, DECLINED_CARD, 12, 2030, "123")
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], method=declined)


def test_k6_idempotency_key_returns_the_same_order(eats):
    first = place(eats, [line(eats.pizza)], idempotency_key="k-1")
    again = place(eats, [line(eats.pizza)], idempotency_key="k-1")
    assert again == first


def test_k6_idempotent_retry_places_no_second_hold(eats):
    rider, wallet = eats.w.wallet_rider("14.17")
    first = place(eats, [burger_line(eats)], rider=rider, method=wallet, idempotency_key="k-1")
    assert place(eats, [burger_line(eats)], rider=rider, method=wallet, idempotency_key="k-1") == first
    eats.p.reject_order(eats.rst, first, "busy")
    second = place(eats, [burger_line(eats)], rider=rider, method=wallet, idempotency_key="k-2")
    assert second != first
    assert second == "ORD-000002"


def test_k6_idempotency_key_is_per_rider(eats):
    other, card = eats.w.rider()
    first = place(eats, [line(eats.pizza)], idempotency_key="same")
    second = place(eats, [line(eats.pizza)], rider=other, method=card, idempotency_key="same")
    assert second != first
    assert eats.p.order(second)["rider_id"] == other


# L. Food order lifecycle --------------------------------------------------------------------------
def test_l1_statuses_follow_the_happy_path(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    assert eats.p.order(order_id)["status"] == "placed"
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["status"] == "accepted"
    eats.p.mark_ready(eats.rst, order_id)
    assert eats.p.order(order_id)["status"] == "ready"
    eats.p.pick_up(courier, order_id)
    assert eats.p.order(order_id)["status"] == "picked_up"
    eats.p.update_location(courier, D_NEAR)
    eats.p.deliver(courier, order_id)
    assert eats.p.order(order_id)["status"] == "delivered"


def test_l2_order_expires_without_acceptance(eats):
    order_id = place(eats, [burger_line(eats)])
    eats.p.advance(minutes=5, seconds=1)
    assert eats.p.order(order_id)["status"] == "expired"


def test_l2_order_expires_at_exactly_five_minutes(eats):
    order_id = place(eats, [burger_line(eats)])
    eats.p.advance(minutes=5)  # X6: at exactly N the limit has passed
    assert eats.p.order(order_id)["status"] == "expired"
    with pytest.raises(ValueError):
        eats.p.accept_order(eats.rst, order_id)


def test_l2_order_can_be_accepted_within_five_minutes(eats):
    order_id = place(eats, [burger_line(eats)])
    eats.p.advance(minutes=4, seconds=59)
    assert eats.p.order(order_id)["status"] == "placed"
    eats.p.accept_order(eats.rst, order_id)
    eats.p.advance(minutes=10)
    assert eats.p.order(order_id)["status"] == "accepted"


def test_l2_expired_order_cannot_be_accepted(eats):
    order_id = place(eats, [burger_line(eats)])
    eats.p.advance(minutes=6)
    with pytest.raises(ValueError):
        eats.p.accept_order(eats.rst, order_id)


def test_l2_expiry_releases_the_hold(eats):
    rider, wallet = eats.w.wallet_rider("14.17")
    place(eats, [burger_line(eats)], rider=rider, method=wallet)
    eats.p.advance(minutes=6)
    order_id = place(eats, [burger_line(eats)], rider=rider, method=wallet)
    assert eats.p.order(order_id)["status"] == "placed"


def test_l2_ready_at_uses_the_longest_prep_time(eats):
    order_id = place(eats, [burger_line(eats), line(eats.pizza)])  # 15 and 25 minutes
    eats.p.advance(minutes=2)
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["ready_at"] == datetime(2026, 3, 2, 12, 27)


def test_l2_reject_releases_the_hold(eats):
    rider, wallet = eats.w.wallet_rider("14.17")
    first = place(eats, [burger_line(eats)], rider=rider, method=wallet)
    eats.p.reject_order(eats.rst, first, "out of buns")
    assert eats.p.order(first)["status"] == "rejected"
    second = place(eats, [burger_line(eats)], rider=rider, method=wallet)
    assert eats.p.order(second)["status"] == "placed"


def test_l2_mark_ready_requires_accepted(eats):
    order_id = place(eats, [burger_line(eats)])
    with pytest.raises(ValueError):
        eats.p.mark_ready(eats.rst, order_id)
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    assert eats.p.order(order_id)["status"] == "ready"
    with pytest.raises(ValueError):  # not from ready
        eats.p.mark_ready(eats.rst, order_id)


def test_l2_mark_ready_does_not_wait_for_ready_at(eats):
    order_id = place(eats, [line(eats.pizza)])  # 25 minutes of preparation
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["ready_at"] == START + timedelta(minutes=25)
    eats.p.advance(minutes=1)
    eats.p.mark_ready(eats.rst, order_id)
    assert eats.p.order(order_id)["status"] == "ready"


def test_l2_only_the_restaurant_marks_ready(eats):
    other = eats.w.restaurant()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    with pytest.raises(PermissionError):
        eats.p.mark_ready(other, order_id)
    assert eats.p.order(order_id)["status"] == "accepted"


def test_l2_unknown_order(eats):
    with pytest.raises(KeyError):
        eats.p.accept_order(eats.rst, "ORD-999999")
    with pytest.raises(KeyError):
        eats.p.mark_ready(eats.rst, "ORD-999999")
    with pytest.raises(KeyError):
        eats.p.order("ORD-999999")


def test_l2_accept_only_once(eats):
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    with pytest.raises(ValueError):
        eats.p.accept_order(eats.rst, order_id)


def test_l2_only_the_restaurant_accepts(eats):
    other = eats.w.restaurant()
    order_id = place(eats, [burger_line(eats)])
    with pytest.raises(PermissionError):
        eats.p.accept_order(other, order_id)
    with pytest.raises(PermissionError):
        eats.p.reject_order(other, order_id, "not mine")
    assert eats.p.order(order_id)["status"] == "placed"


def test_x8_another_restaurant_gets_permission_error_before_a_broken_rule(eats):
    other = eats.w.restaurant()
    order_id = place(eats, [burger_line(eats)])
    eats.p.advance(minutes=6)  # expired: accepting would also break a rule
    with pytest.raises(PermissionError):
        eats.p.accept_order(other, order_id)
    with pytest.raises(PermissionError):
        eats.p.mark_ready(other, order_id)


def test_l3_pick_up_requires_ready(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    with pytest.raises(ValueError):
        eats.p.pick_up(courier, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    assert eats.p.order(order_id)["status"] == "picked_up"


def test_l3_only_the_assigned_courier_picks_up_and_delivers(eats):
    assigned = eats.w.courier(location=REST)
    other = eats.w.courier(location=NEAR_ONE)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == assigned
    eats.p.mark_ready(eats.rst, order_id)
    with pytest.raises(PermissionError):
        eats.p.pick_up(other, order_id)
    eats.p.pick_up(assigned, order_id)
    eats.p.update_location(other, D_NEAR)
    with pytest.raises(PermissionError):
        eats.p.deliver(other, order_id)


def test_l3_deliver_requires_picked_up(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.update_location(courier, D_NEAR)
    with pytest.raises(ValueError):
        eats.p.deliver(courier, order_id)


def test_l3_deliver_within_200_m_of_the_dropoff(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    eats.p.update_location(courier, NEAR_DROP_FAR)  # 0.467 km away
    with pytest.raises(ValueError):
        eats.p.deliver(courier, order_id)
    eats.p.update_location(courier, NEAR_DROP_OK)  # 0.167 km away
    eats.p.deliver(courier, order_id)
    assert eats.p.order(order_id)["status"] == "delivered"


def test_l3_deliver_distance_compared_after_rounding(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    eats.p.update_location(courier, NEAR_DROP_210)  # 0.210 km
    with pytest.raises(ValueError):
        eats.p.deliver(courier, order_id)
    assert eats.p.order(order_id)["status"] == "picked_up"
    eats.p.update_location(courier, NEAR_DROP_200)  # 0.200 km (raw 0.20015)
    eats.p.deliver(courier, order_id)
    assert eats.p.order(order_id)["status"] == "delivered"


def test_l3_pick_up_within_200_m_of_the_restaurant(eats):
    courier = eats.w.courier(location=REST)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == courier
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.update_location(courier, NEAR_HALF)  # 0.500 km
    with pytest.raises(ValueError):
        eats.p.pick_up(courier, order_id)
    eats.p.update_location(courier, AT_0_210)  # 0.210 km
    with pytest.raises(ValueError):
        eats.p.pick_up(courier, order_id)
    assert eats.p.order(order_id)["status"] == "ready"
    eats.p.update_location(courier, AT_0_200)  # 0.200 km (raw 0.20015)
    eats.p.pick_up(courier, order_id)
    assert eats.p.order(order_id)["status"] == "picked_up"


def test_l3_charged_is_zero_until_delivery(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    assert eats.p.order(order_id)["charged"] == D("0.00")
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["charged"] == D("0.00")
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    assert eats.p.order(order_id)["charged"] == D("0.00")
    assert [pay for pay in eats.p.payments(eats.rider) if pay["ref"] == order_id] == []
    eats.p.update_location(courier, D_NEAR)
    eats.p.deliver(courier, order_id)
    assert eats.p.order(order_id)["charged"] == D("14.17")


def test_l3_delivery_captures_the_total(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)], tip="2.00")
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    order = eats.p.order(order_id)
    assert order["total"] == D("16.17")
    assert order["charged"] == D("16.17")
    captures = [pay for pay in eats.p.payments(eats.rider) if pay["kind"] == "order" and pay["ref"] == order_id]
    assert [(pay["amount"], pay["method_id"]) for pay in captures] == [(D("16.17"), eats.card)]


def test_l3_delivery_from_wallet_moves_the_total(eats):
    courier = eats.w.courier()
    rider, wallet = eats.w.wallet_rider("50.00")
    order_id = place(eats, [burger_line(eats)], rider=rider, method=wallet)
    assert eats.p.wallet_balance(rider) == D("35.83")  # O2: the active hold is subtracted
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    assert eats.p.wallet_balance(rider) == D("35.83")  # 50.00 - 14.17 captured, hold gone


def test_l3_courier_available_after_delivery(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    assert eats.p.worker_status(courier)["status"] == "available"


def test_l3_courier_with_another_order_stays_busy(eats):
    courier = eats.w.courier()
    second_rider, second_card = eats.w.rider()
    first = place(eats, [burger_line(eats)], dropoff=D_NEAR)
    second = place(eats, [line(eats.pizza)], dropoff=D_B, rider=second_rider, method=second_card)
    for order_id in (first, second):  # batched: both accepted before either is picked up
        eats.p.accept_order(eats.rst, order_id)
        assert eats.p.order(order_id)["courier_id"] == courier
    for order_id in (first, second):
        eats.p.mark_ready(eats.rst, order_id)
        eats.p.pick_up(courier, order_id)
    eats.p.update_location(courier, D_NEAR)
    eats.p.deliver(courier, first)
    assert eats.p.worker_status(courier)["status"] == "busy"
    eats.p.update_location(courier, D_B)
    eats.p.deliver(courier, second)
    assert eats.p.worker_status(courier)["status"] == "available"


def test_l4_rider_cancels_a_placed_order_for_free(eats):
    rider, wallet = eats.w.wallet_rider("14.17")
    first = place(eats, [burger_line(eats)], rider=rider, method=wallet)
    eats.p.cancel_order(rider, first, "changed my mind")
    assert eats.p.order(first)["status"] == "cancelled"
    assert eats.p.order(first)["charged"] == D("0")
    assert eats.p.wallet_balance(rider) == D("14.17")
    second = place(eats, [burger_line(eats)], rider=rider, method=wallet)  # the hold was released
    assert eats.p.order(second)["status"] == "placed"


def test_l4_cancel_when_accepted_charges_subtotal_and_tax(eats):
    rider, wallet = eats.w.wallet_rider("50.00")
    order_id = place(eats, [burger_line(eats)], rider=rider, method=wallet, tip="2.00")
    eats.p.accept_order(eats.rst, order_id)
    eats.p.cancel_order(rider, order_id, "too slow")
    order = eats.p.order(order_id)
    assert order["status"] == "cancelled"
    assert order["charged"] == D("9.18")  # 8.50 + 0.68
    assert eats.p.wallet_balance(rider) == D("40.82")
    captures = [(pay["kind"], pay["amount"], pay["method_id"]) for pay in eats.p.payments(rider)
                if pay["ref"] == order_id]
    assert captures == [("order", D("9.18"), wallet)]


def test_l4_cancel_when_ready_charges_subtotal_and_tax(eats):
    order_id = place(eats, [line(eats.pizza, 2)], tip="3.00")
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.cancel_order(eats.rider, order_id)
    order = eats.p.order(order_id)
    assert order["status"] == "cancelled"
    assert order["charged"] == D("26.46")  # 24.50 + 1.96


def test_l4_rider_cannot_cancel_after_pick_up(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    with pytest.raises(ValueError):
        eats.p.cancel_order(eats.rider, order_id)
    assert eats.p.order(order_id)["status"] == "picked_up"


def test_l4_admin_cancels_a_picked_up_order_without_charge(eats):
    courier = eats.w.courier()
    rider, wallet = eats.w.wallet_rider("50.00")
    order_id = place(eats, [burger_line(eats)], rider=rider, method=wallet)
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    eats.p.cancel_order(eats.w.admin, order_id, "fraud check")
    order = eats.p.order(order_id)
    assert order["status"] == "cancelled"
    assert order["charged"] == D("0")
    assert eats.p.wallet_balance(rider) == D("50.00")


def test_l4_delivered_order_cannot_be_cancelled(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    with pytest.raises(ValueError):
        eats.p.cancel_order(eats.w.admin, order_id)
    with pytest.raises(ValueError):
        eats.p.cancel_order(eats.rider, order_id)


def test_l4_another_rider_cannot_cancel(eats):
    other, _ = eats.w.rider()
    order_id = place(eats, [burger_line(eats)])
    with pytest.raises(PermissionError):
        eats.p.cancel_order(other, order_id)
    assert eats.p.order(order_id)["status"] == "placed"


# M. Couriers and batching -------------------------------------------------------------------------
def test_m1_assigned_on_acceptance_not_before(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    assert eats.p.order(order_id)["courier_id"] is None
    assert eats.p.worker_status(courier)["status"] == "available"
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == courier


def test_m1_nearest_courier_is_assigned(eats):
    eats.w.courier(location=NEAR_ONE)
    nearest = eats.w.courier(location=NEAR_HALF)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == nearest


def test_m1_tie_goes_to_the_lower_id(eats):
    first = eats.w.courier(location=NEAR_ONE)
    eats.w.courier(location=NEAR_ONE)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == first


def test_m1_courier_beyond_6_km_is_not_assigned(eats):
    courier = eats.w.courier(location=AT_6_7)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    order = eats.p.order(order_id)
    assert order["courier_id"] is None
    assert order["status"] == "accepted"
    assert eats.p.worker_status(courier)["status"] == "available"


def test_m1_courier_within_6_km_is_assigned(eats):
    courier = eats.w.courier(location=AT_5_8)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == courier


def test_m1_courier_at_exactly_6_km_after_rounding_is_assigned(eats):
    courier = eats.w.courier(location=AT_6_000)  # 6.000 km (raw 6.0002)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == courier


def test_m1_courier_at_6_001_km_is_not_assigned(eats):
    eats.w.courier(location=AT_6_001)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] is None


def test_m1_offline_courier_is_not_assigned(eats):
    eats.w.courier(online=False)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] is None


def test_m1_assignment_retried_when_a_courier_goes_online(eats):
    courier = eats.w.courier(online=False)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] is None
    eats.p.go_online(courier, NEAR_ONE)
    assert eats.p.order(order_id)["courier_id"] == courier
    assert eats.p.worker_status(courier)["status"] == "busy"


def test_m1_assignment_retried_on_advance(eats):
    courier = eats.w.courier(location=AT_6_7)
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] is None
    eats.p.update_location(courier, NEAR_ONE)
    eats.p.advance(seconds=30)
    assert eats.p.order(order_id)["courier_id"] == courier


def test_m1_assignment_retried_when_a_courier_becomes_available(eats):
    courier = eats.w.courier()
    other_rider, other_card = eats.w.rider()
    first = place(eats, [burger_line(eats)], dropoff=D_NEAR)
    eats.p.accept_order(eats.rst, first)
    eats.p.mark_ready(eats.rst, first)
    eats.p.pick_up(courier, first)
    second = place(eats, [line(eats.pizza)], dropoff=D_B, rider=other_rider, method=other_card)
    eats.p.accept_order(eats.rst, second)
    assert eats.p.order(second)["courier_id"] is None  # the only courier carries a picked-up order
    eats.p.update_location(courier, D_NEAR)
    eats.p.deliver(courier, first)
    assert eats.p.order(second)["courier_id"] == courier
    assert eats.p.worker_status(courier)["status"] == "busy"


def test_m1_bike_skips_dropoffs_beyond_4_km(eats):
    eats.w.courier(vehicle="bike", location=REST)
    moto = eats.w.courier(vehicle="moto", location=NEAR_ONE)
    order_id = place(eats, [burger_line(eats)], dropoff=D_5K)
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == moto


def test_m1_bike_takes_dropoffs_within_4_km(eats):
    bike = eats.w.courier(vehicle="bike", location=REST)
    eats.w.courier(vehicle="moto", location=NEAR_ONE)
    order_id = place(eats, [burger_line(eats)], dropoff=D_FAR3)
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == bike


def test_m1_bike_limit_compared_after_rounding(eats):
    bike = eats.w.courier(vehicle="bike", location=REST)
    order_in = place(eats, [burger_line(eats)], dropoff=D_4_000)  # 4.000 km (raw 4.0002)
    eats.p.accept_order(eats.rst, order_in)
    assert eats.p.order(order_in)["courier_id"] == bike
    other_bike = eats.w.courier(vehicle="bike", location=REST)
    rider_b, card_b = eats.w.rider()
    eats.p.advance(minutes=6)  # not batched with the first order
    order_out = place(eats, [burger_line(eats)], dropoff=D_4_001, rider=rider_b, method=card_b)  # 4.001 km
    eats.p.accept_order(eats.rst, order_out)
    assert eats.p.order(order_out)["courier_id"] is None
    assert eats.p.worker_status(other_bike)["status"] == "available"


def test_m1_order_waits_when_only_a_bike_is_near(eats):
    bike = eats.w.courier(vehicle="bike", location=REST)
    order_id = place(eats, [burger_line(eats)], dropoff=D_5K)
    eats.p.accept_order(eats.rst, order_id)
    eats.p.advance(minutes=1)
    assert eats.p.order(order_id)["courier_id"] is None
    assert eats.p.worker_status(bike)["status"] == "available"


def _two_couriers_one_order(eats, dropoff=D_NEAR):
    """Couriers C1 and C2 at the restaurant; order A accepted and assigned to C1 (tie, lower id)."""
    first_courier = eats.w.courier(location=REST)
    second_courier = eats.w.courier(location=REST)
    order_a = place(eats, [burger_line(eats)], dropoff=dropoff)
    eats.p.accept_order(eats.rst, order_a)
    assert eats.p.order(order_a)["courier_id"] == first_courier
    return first_courier, second_courier, order_a


def test_m2_second_order_is_batched_with_the_carrying_courier(eats):
    first_courier, second_courier, _ = _two_couriers_one_order(eats)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == first_courier
    assert eats.p.worker_status(second_courier)["status"] == "available"


def test_m2_batch_when_accepted_within_5_minutes(eats):
    first_courier, _, _ = _two_couriers_one_order(eats)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.advance(minutes=4)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == first_courier


def test_m2_no_batch_when_accepted_more_than_5_minutes_apart(eats):
    _, second_courier, _ = _two_couriers_one_order(eats)
    eats.p.advance(minutes=6)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == second_courier


def test_m2_no_batch_when_accepted_exactly_5_minutes_apart(eats):
    _, second_courier, _ = _two_couriers_one_order(eats)
    eats.p.advance(minutes=5)  # X6: "within 5 minutes" excludes exactly 5 minutes
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == second_courier


def test_m2_batch_when_accepted_just_under_5_minutes_apart(eats):
    first_courier, _, _ = _two_couriers_one_order(eats)
    eats.p.advance(minutes=4, seconds=59)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == first_courier


def test_m2_no_batch_when_dropoffs_are_more_than_2_km_apart(eats):
    _, second_courier, _ = _two_couriers_one_order(eats)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_SOUTH, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == second_courier


def test_m2_no_batch_after_the_first_order_is_picked_up(eats):
    first_courier, second_courier, order_a = _two_couriers_one_order(eats)
    eats.p.mark_ready(eats.rst, order_a)
    eats.p.pick_up(first_courier, order_a)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == second_courier


def test_m2_no_batch_across_restaurants(eats):
    _, second_courier, _ = _two_couriers_one_order(eats)
    other = eats.w.restaurant(location=REST)
    soup = eats.p.add_menu_item(other, "Soup", "9.00", "food", 5)
    rider_b, card_b = eats.w.rider()
    order_b = eats.p.place_order(rider_b, other, [line(soup)], D_B, card_b)
    eats.p.accept_order(other, order_b)
    assert eats.p.order(order_b)["courier_id"] == second_courier


def test_m2_never_more_than_two_orders(eats):
    first_courier, second_courier, _ = _two_couriers_one_order(eats)
    rider_b, card_b = eats.w.rider()
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    eats.p.accept_order(eats.rst, order_b)
    assert eats.p.order(order_b)["courier_id"] == first_courier
    rider_c, card_c = eats.w.rider()
    order_c = place(eats, [line(eats.pizza)], dropoff=D_NEAR, rider=rider_c, method=card_c)
    eats.p.accept_order(eats.rst, order_c)
    assert eats.p.order(order_c)["courier_id"] == second_courier


def test_m3_assigned_courier_is_busy_and_shown(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    assert eats.p.order(order_id)["courier_id"] == courier
    assert eats.p.worker_status(courier)["status"] == "busy"


def test_m3_courier_stays_busy_until_its_orders_are_delivered(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    assert eats.p.worker_status(courier)["status"] == "busy"
    eats.p.pick_up(courier, order_id)
    assert eats.p.worker_status(courier)["status"] == "busy"
    assert eats.p.order(order_id)["courier_id"] == courier


def test_m4_courier_pay_and_tip_for_one_order(eats):
    courier = eats.w.courier()
    order_id = place(eats, [line(eats.pizza)], dropoff=D_FAR3, tip="2.00")
    deliver_order(eats.p, eats.rst, order_id, courier, D_FAR3)
    pay = courier_pay(D_FAR3)  # 2.50 + 0.60 x 3.336 = 4.5016 -> 4.50
    assert pay == D("4.50")
    earned = eats.p.earnings(courier, DAY_START, DAY_END)
    assert earned["trips"] == 1
    assert earned["fares"] == pay  # P2: fares is the courier pay of M4
    assert earned["tips"] == D("2.00")
    assert earned["fees"] == D("0.00")
    assert earned["total"] == pay + D("2.00")


def test_m4_second_order_of_a_batch_earns_70_percent(eats):
    courier = eats.w.courier()
    rider_b, card_b = eats.w.rider()
    order_a = place(eats, [burger_line(eats)], dropoff=D_NEAR)
    order_b = place(eats, [line(eats.pizza)], dropoff=D_B, rider=rider_b, method=card_b)
    for order_id in (order_a, order_b):
        eats.p.accept_order(eats.rst, order_id)
        assert eats.p.order(order_id)["courier_id"] == courier
    for order_id in (order_a, order_b):
        eats.p.mark_ready(eats.rst, order_id)
        eats.p.pick_up(courier, order_id)
    for order_id, dropoff in ((order_a, D_NEAR), (order_b, D_B)):
        eats.p.update_location(courier, dropoff)
        eats.p.deliver(courier, order_id)
    pay_a = courier_pay(D_NEAR)  # 2.50 + 0.60 x 1.668 = 3.5008 -> 3.50
    pay_b = money(courier_pay(D_B) * D("0.70"))  # 4.168 -> 4.17; x 0.70 = 2.919 -> 2.92
    assert (pay_a, pay_b) == (D("3.50"), D("2.92"))
    earned = eats.p.earnings(courier, DAY_START, DAY_END)
    assert earned["trips"] == 2
    assert earned["fares"] == D("6.42")
    assert earned["total"] == D("6.42")


def test_m4_no_pay_for_an_order_not_delivered(eats):
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)], tip="2.00")
    eats.p.accept_order(eats.rst, order_id)
    eats.p.mark_ready(eats.rst, order_id)
    eats.p.pick_up(courier, order_id)
    eats.p.cancel_order(eats.w.admin, order_id, "fraud check")
    earned = eats.p.earnings(courier, DAY_START, DAY_END)
    assert earned["trips"] == 0
    assert earned["total"] == D("0")


# N. Promotions and referrals ----------------------------------------------------------------------
def test_n1_only_admins_create_promos(eats):
    with pytest.raises(PermissionError):
        eats.p.create_promo(eats.rider, "SAVE10", "eats", "percent", 10)
    with pytest.raises(PermissionError):
        eats.p.create_promo(eats.rst, "SAVE10", "eats", "percent", 10)
    with pytest.raises(ValueError):  # the code was not created by the refused calls
        place(eats, [line(eats.pizza)], promo_code="SAVE10")


def test_x8_non_admin_promo_with_invalid_code_is_a_permission_error(eats):
    with pytest.raises(PermissionError):
        eats.p.create_promo(eats.rider, "X", "food", "percent", 0)


def test_x8_unknown_admin_creating_a_promo_is_a_key_error():
    w = World()
    with pytest.raises(KeyError):
        w.p.create_promo("ADM-999999", "X", "food", "percent", 0)


@pytest.mark.parametrize("code", ["AB1", "ABCDEFGHIJKLMNOP", "SAVE-10", "SAVE 10", "", "   ", "ÑANDU10"])
def test_n1_code_format(code):
    w = World()
    with pytest.raises(ValueError):
        w.p.create_promo(w.admin, code, "eats", "percent", 10)


@pytest.mark.parametrize("code", ["ABCD", "ABCDEFGHIJKLMNO", "2026"])
def test_n1_code_length_bounds_are_accepted(code):
    w = World()
    assert w.p.create_promo(w.admin, code, "eats", "percent", 10) is None


def test_n1_code_is_stripped_and_uppercased(eats):
    eats.p.create_promo(eats.w.admin, "  save10 ", "eats", "percent", 10)
    order_id = place(eats, [line(eats.pizza)], promo_code="SAVE10")
    assert eats.p.order(order_id)["discount"] == D("1.23")


def test_n1_code_is_unique(eats):
    eats.p.create_promo(eats.w.admin, "SAVE10", "eats", "percent", 10)
    with pytest.raises(ValueError):
        eats.p.create_promo(eats.w.admin, "save10", "rides", "fixed", "5.00")


@pytest.mark.parametrize("service", ["food", "", "all"])
def test_n1_service_is_rides_eats_or_both(service):
    w = World()
    with pytest.raises(ValueError):
        w.p.create_promo(w.admin, "SAVE10", service, "percent", 10)


@pytest.mark.parametrize("kind", ["amount", "", "free"])
def test_n1_kind_is_percent_or_fixed(kind):
    w = World()
    with pytest.raises(ValueError):
        w.p.create_promo(w.admin, "SAVE10", "eats", kind, 10)


@pytest.mark.parametrize("value", [0, 51, -5, 100, "10", 10.0, True, D("10")])
def test_n1_percent_value_an_int_from_1_to_50(value):
    w = World()
    with pytest.raises(ValueError):
        w.p.create_promo(w.admin, "SAVE10", "eats", "percent", value)


@pytest.mark.parametrize("value", ["0.99", "100.01", "0", -1, "5.005", 5.0])
def test_n1_fixed_value_from_1_to_100(value):
    w = World()
    with pytest.raises(ValueError):
        w.p.create_promo(w.admin, "SAVE10", "eats", "fixed", value)


@pytest.mark.parametrize(("kind", "value"), [("percent", 1), ("percent", 50), ("fixed", "1.00"), ("fixed", "100.00")])
def test_n1_value_bounds_are_accepted(kind, value):
    w = World()
    assert w.p.create_promo(w.admin, "SAVE10", "both", kind, value) is None


def test_n2_unknown_code(eats):
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="NOPE2026")


def test_n2_code_matches_case_insensitively(eats):
    eats.p.create_promo(eats.w.admin, "SAVE10", "eats", "percent", 10)
    order_id = place(eats, [line(eats.pizza)], promo_code="save10")
    assert eats.p.order(order_id)["discount"] == D("1.23")


def test_n2_rides_promo_not_valid_for_orders(eats):
    eats.p.create_promo(eats.w.admin, "RIDE10", "rides", "percent", 10)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="RIDE10")


def test_n2_both_promo_valid_for_orders(eats):
    eats.p.create_promo(eats.w.admin, "ALL10", "both", "percent", 10)
    order_id = place(eats, [line(eats.pizza)], promo_code="ALL10")
    assert eats.p.order(order_id)["discount"] == D("1.23")


def test_n2_eats_promo_not_valid_for_rides():
    w = World()
    rider, card = w.rider()
    w.p.create_promo(w.admin, "EATS10", "eats", "percent", 10)
    quote = w.p.quote_ride(rider, PICK, DROP, "economy")
    with pytest.raises(ValueError):
        w.p.request_ride(rider, quote["quote_id"], card, promo_code="EATS10")


def test_n2_promo_not_started_yet(eats):
    eats.p.create_promo(
        eats.w.admin, "LATER10", "eats", "percent", 10, starts=START + timedelta(hours=1),
        ends=START + timedelta(hours=3),
    )
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="LATER10")
    eats.p.advance(minutes=61)
    order_id = place(eats, [line(eats.pizza)], promo_code="LATER10")
    assert eats.p.order(order_id)["discount"] == D("1.23")


def test_n2_promo_already_ended(eats):
    eats.p.create_promo(
        eats.w.admin, "NOON10", "eats", "percent", 10, starts=START - timedelta(hours=1),
        ends=START + timedelta(minutes=30),
    )
    eats.p.advance(minutes=31)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="NOON10")


def test_n2_window_start_is_included(eats):
    eats.p.create_promo(
        eats.w.admin, "LATER10", "eats", "percent", 10, starts=START + timedelta(hours=1),
        ends=START + timedelta(hours=3),
    )
    eats.p.advance(minutes=59, seconds=59)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="LATER10")
    eats.p.advance(seconds=1)  # exactly at starts
    order_id = place(eats, [line(eats.pizza)], promo_code="LATER10")
    assert eats.p.order(order_id)["discount"] == D("1.23")


def test_n2_window_end_is_excluded(eats):
    eats.p.create_promo(
        eats.w.admin, "NOON10", "eats", "percent", 10, starts=START - timedelta(hours=1),
        ends=START + timedelta(minutes=30),
    )
    eats.p.advance(minutes=29, seconds=59)
    order_id = place(eats, [line(eats.pizza)], promo_code="NOON10")
    assert eats.p.order(order_id)["discount"] == D("1.23")
    eats.p.advance(seconds=1)  # exactly at ends
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="NOON10")


def test_n2_window_start_included_for_rides():
    w = World()
    rider, card = w.rider()
    w.driver()
    w.p.create_promo(w.admin, "RIDEAT1", "rides", "fixed", "1.00", starts=START + timedelta(minutes=10))
    w.p.advance(minutes=10)
    quote = w.p.quote_ride(rider, PICK, DROP, "economy")
    ride_id = w.p.request_ride(rider, quote["quote_id"], card, promo_code="RIDEAT1")
    assert w.p.ride(ride_id)["status"] == "requested"


def test_n2_minimum_spend_not_reached(eats):
    eats.p.create_promo(eats.w.admin, "MIN1225", "eats", "fixed", "3.00", min_spend="12.25")
    with pytest.raises(ValueError):
        place(eats, [burger_line(eats)], promo_code="MIN1225")  # subtotal 8.50


def test_n2_minimum_spend_reached_exactly(eats):
    eats.p.create_promo(eats.w.admin, "MIN1225", "eats", "fixed", "3.00", min_spend="12.25")
    order_id = place(eats, [line(eats.pizza)], promo_code="MIN1225")  # subtotal 12.25
    assert eats.p.order(order_id)["discount"] == D("3.00")


def test_n2_total_use_limit(eats):
    courier = eats.w.courier()
    other, other_card = eats.w.rider()
    eats.p.create_promo(eats.w.admin, "ONCE", "eats", "fixed", "2.00", max_total_uses=1)
    order_id = place(eats, [line(eats.pizza)], promo_code="ONCE")
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], rider=other, method=other_card, promo_code="ONCE")


def test_n2_per_user_limit(eats):
    courier = eats.w.courier()
    other, other_card = eats.w.rider()
    eats.p.create_promo(eats.w.admin, "EACH1", "eats", "fixed", "2.00", max_uses_per_user=1)
    order_id = place(eats, [line(eats.pizza)], promo_code="EACH1")
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="EACH1")
    order_id = place(eats, [line(eats.pizza)], rider=other, method=other_card, promo_code="EACH1")
    assert eats.p.order(order_id)["discount"] == D("2.00")


def test_n2_first_order_only_for_eats(eats):
    courier = eats.w.courier()
    eats.p.create_promo(eats.w.admin, "WELCOME", "eats", "fixed", "4.00", first_order_only=True)
    order_id = place(eats, [line(eats.pizza)], promo_code="WELCOME")
    assert eats.p.order(order_id)["discount"] == D("4.00")
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="WELCOME")


def test_n2_first_order_only_ignores_undelivered_orders(eats):
    eats.p.create_promo(eats.w.admin, "WELCOME", "eats", "fixed", "4.00", first_order_only=True)
    first = place(eats, [line(eats.pizza)])
    eats.p.accept_order(eats.rst, first)
    eats.p.cancel_order(eats.rider, first)
    order_id = place(eats, [line(eats.pizza)], promo_code="WELCOME")
    assert eats.p.order(order_id)["discount"] == D("4.00")


def test_n2_first_order_only_for_eats_ignores_completed_rides(eats):
    driver = eats.w.driver()
    finish_ride(eats.p, eats.rider, eats.card, driver)
    eats.p.create_promo(eats.w.admin, "WELCOME", "eats", "fixed", "4.00", first_order_only=True)
    order_id = place(eats, [line(eats.pizza)], promo_code="WELCOME")
    assert eats.p.order(order_id)["discount"] == D("4.00")


def test_n2_first_order_only_for_rides():
    w = World()
    rider, card = w.rider()
    driver = w.driver()
    w.p.create_promo(w.admin, "FIRSTRIDE", "rides", "fixed", "2.00", first_order_only=True)
    ride_id, _ = finish_ride(w.p, rider, card, driver, promo_code="FIRSTRIDE")
    assert w.p.ride(ride_id)["discount"] == D("2.00")
    w.p.update_location(driver, PICK)
    quote = w.p.quote_ride(rider, PICK, DROP, "economy")
    with pytest.raises(ValueError):
        w.p.request_ride(rider, quote["quote_id"], card, promo_code="FIRSTRIDE")


def test_n3_percent_discount_rounded_half_up(eats):
    eats.p.create_promo(eats.w.admin, "SAVE10", "eats", "percent", 10)
    order = eats.p.order(place(eats, [line(eats.pizza)], promo_code="SAVE10"))
    assert order["discount"] == D("1.23")  # 1.225 half up
    # 12.25 + 1.00 service + 1.99 delivery + 0.98 tax - 1.23
    assert order["total"] == D("14.99")


def test_n3_percent_discount_capped_by_max_discount(eats):
    feast = eats.p.add_menu_item(eats.rst, "Feast", "40.00", "food", 30)
    eats.p.create_promo(eats.w.admin, "HALF", "eats", "percent", 50, max_discount="7.50")
    order = eats.p.order(place(eats, [line(feast)], promo_code="HALF"))
    assert order["discount"] == D("7.50")
    # 40.00 + 2.00 service + 0.00 delivery + 3.20 tax - 7.50
    assert order["total"] == D("37.70")


def test_n3_fixed_discount(eats):
    eats.p.create_promo(eats.w.admin, "FIVE", "eats", "fixed", "5.00")
    order = eats.p.order(place(eats, [line(eats.pizza)], promo_code="FIVE"))
    assert order["discount"] == D("5.00")
    assert order["total"] == D("11.22")


def test_n3_fixed_discount_at_most_the_subtotal(eats):
    eats.p.create_promo(eats.w.admin, "HUNDRED", "eats", "fixed", "100.00")
    order = eats.p.order(place(eats, [burger_line(eats)], promo_code="HUNDRED"))
    assert order["discount"] == D("8.50")
    assert order["total"] == D("5.67")  # 14.17 - 8.50


def test_n3_uses_count_only_on_delivery(eats):
    other, other_card = eats.w.rider()
    eats.p.create_promo(eats.w.admin, "ONCE", "eats", "fixed", "2.00", max_total_uses=1)
    first = place(eats, [line(eats.pizza)], promo_code="ONCE")
    eats.p.accept_order(eats.rst, first)
    second = place(eats, [line(eats.pizza)], rider=other, method=other_card, promo_code="ONCE")
    assert eats.p.order(second)["discount"] == D("2.00")


def test_n3_cancelled_order_does_not_use_the_promo(eats):
    courier = eats.w.courier()
    eats.p.create_promo(eats.w.admin, "ONCE", "eats", "fixed", "2.00", max_total_uses=1)
    cancelled = place(eats, [line(eats.pizza)], promo_code="ONCE")
    eats.p.cancel_order(eats.rider, cancelled)
    delivered = place(eats, [line(eats.pizza)], promo_code="ONCE")
    deliver_order(eats.p, eats.rst, delivered, courier, D_NEAR)
    with pytest.raises(ValueError):
        place(eats, [line(eats.pizza)], promo_code="ONCE")


def test_n3_ride_discount_is_a_percent_of_the_quoted_fare():
    w = World()
    rider, card = w.rider()
    driver = w.driver()
    w.p.create_promo(w.admin, "RIDE15", "rides", "percent", 15)
    ride_id, quote = finish_ride(w.p, rider, card, driver, promo_code="ride15")
    discount = money(quote["fare"] * D("0.15"))
    ride = w.p.ride(ride_id)
    assert ride["fare"] == quote["fare"]
    assert ride["discount"] == discount
    assert ride["charged"] == quote["fare"] - discount


def test_n4_referred_riders_first_ride_credits_both_wallets():
    w = World()
    referrer, _ = w.rider()
    referred, card = w.rider(referral_code=w.p.referral_code(referrer))
    driver = w.driver()
    finish_ride(w.p, referred, card, driver)
    assert w.p.wallet_balance(referrer) == D("5.00")
    assert w.p.wallet_balance(referred) == D("5.00")


def test_n4_referral_bonus_paid_once():
    w = World()
    referrer, _ = w.rider()
    referred, card = w.rider(referral_code=w.p.referral_code(referrer))
    driver = w.driver()
    finish_ride(w.p, referred, card, driver)
    finish_ride(w.p, referred, card, driver)
    assert w.p.wallet_balance(referrer) == D("5.00")
    assert w.p.wallet_balance(referred) == D("5.00")


def test_n4_no_bonus_for_riders_without_a_referral_code():
    w = World()
    referrer, referrer_card = w.rider()
    w.rider(referral_code=w.p.referral_code(referrer))
    driver = w.driver()
    finish_ride(w.p, referrer, referrer_card, driver)  # the referrer was not referred
    assert w.p.wallet_balance(referrer) == D("0")


def test_n4_delivered_order_does_not_trigger_the_referral(eats):
    referred, card = eats.w.rider(referral_code=eats.p.referral_code(eats.rider))
    courier = eats.w.courier()
    order_id = place(eats, [line(eats.pizza)], rider=referred, method=card)
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    assert eats.p.wallet_balance(eats.rider) == D("0")
    assert eats.p.wallet_balance(referred) == D("0")


# X. General rules applied to food orders and promotions ------------------------------------------
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


def test_x5_menu_is_a_new_object(eats):
    first = eats.p.menu(eats.rst)
    snapshot = copy.deepcopy(first)
    assert eats.p.menu(eats.rst) is not first
    _scribble(first)
    assert eats.p.menu(eats.rst) == snapshot


def test_x5_order_is_a_new_object(eats):
    order_id = place(eats, [line(eats.burger, 1, [eats.large, eats.cheese]), line(eats.pizza)])
    first = eats.p.order(order_id)
    snapshot = copy.deepcopy(first)
    assert eats.p.order(order_id) is not first
    _scribble(first)
    assert eats.p.order(order_id) == snapshot


@pytest.mark.parametrize("call", ["menu_price", "option_delta", "order_tip", "promo_fixed", "promo_min_spend"])
def test_x2_float_money_is_rejected(eats, call):
    p = eats.p
    with pytest.raises(ValueError):
        if call == "menu_price":
            p.add_menu_item(eats.rst, "Soup", 6.5, "food", 10)
        elif call == "option_delta":
            p.add_option(eats.extras, "Onion", 0.5)
        elif call == "order_tip":
            place(eats, [line(eats.pizza)], tip=1.0)
        elif call == "promo_fixed":
            p.create_promo(eats.w.admin, "FLOAT5", "eats", "fixed", 5.0)
        else:
            p.create_promo(eats.w.admin, "FLOAT5", "eats", "fixed", "5.00", min_spend=10.0)


@pytest.mark.parametrize(
    "call", ["menu_price", "option_delta", "order_tip", "promo_max_discount", "promo_min_spend"]
)
def test_x7_money_with_more_than_two_decimals_is_rejected(eats, call):
    p = eats.p
    with pytest.raises(ValueError):
        if call == "menu_price":
            p.add_menu_item(eats.rst, "Soup", "6.505", "food", 10)
        elif call == "option_delta":
            p.add_option(eats.extras, "Onion", "0.125")
        elif call == "order_tip":
            place(eats, [line(eats.pizza)], tip="1.005")
        elif call == "promo_max_discount":
            p.create_promo(eats.w.admin, "SAVE10", "eats", "percent", 10, max_discount="7.505")
        else:
            p.create_promo(eats.w.admin, "SAVE10", "eats", "percent", 10, min_spend="10.001")


def test_x7_identifiers_are_not_normalized(eats):
    with pytest.raises(KeyError):
        place(eats, [line(eats.pizza.lower())])
    with pytest.raises(KeyError):
        eats.p.menu(eats.rst.lower())


def test_x1_failed_calls_consume_no_identifier(eats):
    with pytest.raises(ValueError):
        place(eats, [])
    with pytest.raises(ValueError):
        eats.p.add_menu_item(eats.rst, "Soup", "0", "food", 10)
    assert place(eats, [line(eats.pizza)]) == "ORD-000001"
    assert eats.p.add_menu_item(eats.rst, "Soup", "6.00", "food", 10) == "ITM-000004"


def test_x8_unknown_item_is_reported_before_a_closed_restaurant(eats):
    eats.p.set_hours(eats.rst, 0, "08:00", "11:00")  # closed at 12:00
    with pytest.raises(KeyError):
        place(eats, [line("ITM-999999")])


def test_x8_another_riders_card_is_reported_before_a_broken_rule(eats):
    _, other_card = eats.w.rider()
    eats.p.set_hours(eats.rst, 0, "08:00", "11:00")  # closed at 12:00
    with pytest.raises(PermissionError):
        place(eats, [line(eats.pizza)], method=other_card)
    with pytest.raises(PermissionError):
        place(eats, [line(eats.pizza, 0)], dropoff=D_OUT, method=other_card)


def test_x8_another_rider_cancelling_a_delivered_order_is_a_permission_error(eats):
    other, _ = eats.w.rider()
    courier = eats.w.courier()
    order_id = place(eats, [burger_line(eats)])
    deliver_order(eats.p, eats.rst, order_id, courier, D_NEAR)
    with pytest.raises(PermissionError):
        eats.p.cancel_order(other, order_id)
    with pytest.raises(KeyError):
        eats.p.cancel_order(other, "ORD-999999")
