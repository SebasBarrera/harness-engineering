"""Hidden acceptance checks of the ride-hailing scenario, sections D to I of SPEC.md. Never shown to the agent.

Each test is named ``test_<rule>_<description>`` and checks one rule through the public API only
(``from rides import Platform``). Expected distances and fares are derived here with an independent
haversine (radius 6371.0 km) and the D2/D3 formula; most points lie on one meridian so that the
distances are exact multiples of the kilometre offsets, away from any rounding or ``ceil`` boundary.

Boundaries follow X6: time limits ("within", "lasts", "for") have passed at exactly N, "until T" and
"before T" are strict, distance limits include their ends after rounding to 3 places. Error precedence
follows X8 (KeyError, then PermissionError, then ValueError); those checks are named ``test_x8_``.

Points the spec still leaves open, deliberately left untested:
- G1: whether the arrival distance "within 0.2 km" includes 0.200 (X6 defines "within" only for time
  limits); tests stay 50 m away from it.
- G3: whether a driver's own cancellation (the ride goes back to ``requested``) sends ``ride_cancelled``.
- G3: the value of ``cancellation_fee`` when there is no fee (``None`` or 0.00 are both accepted).
- I1: whether the ends of "from 30 minutes to 30 days" are included (X6 defines "from a to b" only
  for distance, amount and count limits); tests stay one minute away from them.
- F1/X8: whether a rider suspended after quoting may still request with that quote (D1 requires an
  active rider only for quoting).
- H3: "never below 0" cannot be reached through the public API without relying on N3.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import pytest
from rides import Platform

D = Decimal
START = datetime(2026, 3, 2, 12, 0)  # a Monday
DAY_START = datetime(2026, 3, 2)
GOOD_CARD = "4242424242424242"
FAILING_CARD = "4000000000000002"
PASSWORD = "Secret12345"

R_EARTH = 6371.0
KM_PER_DEG = R_EARTH * math.pi / 180
CITY_SPEED = 30
AIRPORT_SPEED = 40

RATES = {  # base, per km, per minute, minimum, booking fee (D2)
    "economy": (D("2.50"), D("1.10"), D("0.25"), D("6.00"), D("1.50")),
    "comfort": (D("3.50"), D("1.50"), D("0.35"), D("9.00"), D("1.50")),
    "xl": (D("4.00"), D("1.80"), D("0.40"), D("11.00"), D("2.00")),
    "moto": (D("1.50"), D("0.70"), D("0.15"), D("4.00"), D("1.00")),
}
SEATS = {"economy": 4, "comfort": 4, "xl": 6, "moto": 1}
NO_SURGE = Decimal("1")


def north(point: tuple[float, float], km: float) -> tuple[float, float]:
    """The point ``km`` kilometres north of ``point`` on the same meridian."""
    return (point[0] + km / KM_PER_DEG, point[1])


CENTER = (4.65, -74.05)  # Bogota
AIRPORT = (4.7016, -74.1469)  # El Dorado
DROP = north(CENTER, 4.1)  # 4.100 km, 9 min at 30 km/h
DROP_21 = north(CENTER, 2.1)  # 2.100 km, 5 min
SHORT = north(CENTER, 0.6)  # 0.600 km, 2 min
LONG = north(CENTER, 10.3)  # 10.300 km, 21 min
AIR_DROP = north(AIRPORT, 6.3)  # in the city zone, 6.300 km from the airport
NORTH_CENTER = north(CENTER, 30)  # center of a second, non-airport zone
NORTH_DROP = north(NORTH_CENTER, 2.1)


def raw_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def q3(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(D("0.001"), ROUND_HALF_UP)


def hav(a: tuple[float, float], b: tuple[float, float]) -> Decimal:
    return q3(raw_km(a, b))


def route_km(points: list[tuple[float, float]]) -> Decimal:
    return q3(sum(raw_km(a, b) for a, b in zip(points, points[1:], strict=False)))


def eta(distance: Decimal, speed: int = CITY_SPEED) -> int:
    return max(1, math.ceil(Fraction(str(distance)) * 60 / speed))


def fare(category: str, distance: Decimal, minutes: int, *, night: bool = False,
         surge: Decimal = NO_SURGE, airport: bool = False) -> Decimal:
    base, per_km, per_min, minimum, booking = RATES[category]
    amount = base + per_km * distance + per_min * minutes
    if night:
        amount *= D("1.2")
    amount *= surge
    amount = max(amount, minimum)
    amount += booking
    if airport:
        amount += D("5.00")
    return amount.quantize(D("0.01"), ROUND_HALF_UP)


def dec(value: object) -> Decimal:
    return Decimal(str(value))


def zero_or_none(value: object) -> bool:
    return value is None or dec(value) == 0


class World:
    """A platform with an admin, an airport zone (first) and a city zone; accounts on demand."""

    def __init__(self) -> None:
        self.p = Platform(START)
        self.n = 0
        self.vehicle: dict[str, str] = {}
        self.admin = self.p.create_admin(*self._identity())
        self.airport_zone = self.p.add_zone(self.admin, "El Dorado", AIRPORT, 2, AIRPORT_SPEED, airport=True)
        self.city_zone = self.p.add_zone(self.admin, "Bogota", CENTER, 20, CITY_SPEED)

    def _identity(self) -> tuple[str, str, str, str]:
        self.n += 1
        return f"User {self.n}", f"user{self.n}@example.com", f"+57300{self.n:07d}", PASSWORD

    def rider(self, card: str = GOOD_CARD) -> tuple[str, str]:
        rider_id = self.p.register_rider(*self._identity())
        return rider_id, self.p.add_card(rider_id, card, 12, 2030, "123")

    def driver(self, location: tuple[float, float] | None = CENTER, category: str = "economy") -> str:
        name, email, phone, password = self._identity()
        driver_id = self.p.register_driver(name, email, phone, password, f"LIC{self.n:06d}", date(2030, 1, 1))
        vehicle_id = self.p.add_vehicle(driver_id, f"ABC{self.n:03d}", "Toyota", "Corolla", 2022,
                                        SEATS[category], category)
        self.vehicle[driver_id] = vehicle_id
        self.p.approve(self.admin, driver_id)
        if location is not None:
            self.p.go_online(driver_id, location, vehicle_id)
        return driver_id

    def north_zone(self) -> str:
        return self.p.add_zone(self.admin, "North", NORTH_CENTER, 5, CITY_SPEED)

    # Ride flows ------------------------------------------------------------------------------
    def request(self, rider: str, card: str, pickup=CENTER, dropoff=DROP, category="economy", **kw) -> str:
        quote = self.p.quote_ride(rider, pickup, dropoff, category)
        return self.p.request_ride(rider, quote["quote_id"], card, **kw)

    def assigned(self, rider: str, card: str, driver: str, pickup=CENTER, dropoff=DROP,
                 category="economy", **kw) -> str:
        ride_id = self.request(rider, card, pickup, dropoff, category, **kw)
        assert self.p.ride(ride_id)["offered_to"] == driver
        self.p.accept_ride(driver, ride_id)
        return ride_id

    def started(self, rider: str, card: str, driver: str, pickup=CENTER, dropoff=DROP,
                category="economy", **kw) -> str:
        ride_id = self.assigned(rider, card, driver, pickup, dropoff, category, **kw)
        self.p.update_location(driver, pickup)
        self.p.driver_arrived(driver, ride_id)
        self.p.start_ride(driver, ride_id)
        return ride_id

    def completed(self, rider: str, card: str, driver: str, pickup=CENTER, dropoff=DROP,
                  category="economy", minutes: int = 10, **kw) -> str:
        ride_id = self.started(rider, card, driver, pickup, dropoff, category, **kw)
        self.p.advance(minutes=minutes)
        self.p.complete_ride(driver, ride_id, [pickup, dropoff])
        return ride_id

    def surge_demand(self, rides: int, pickup=CENTER, dropoff=DROP) -> None:
        """``rides`` economy rides left in status requested at ``pickup``, one economy driver each."""
        for i in range(rides):
            self.driver(north(pickup, 0.1 * i))
        for _ in range(rides):
            rider, card = self.rider()
            ride_id = self.request(rider, card, pickup, dropoff, "economy")
            assert self.p.ride(ride_id)["status"] == "requested"


@pytest.fixture()
def w() -> World:
    return World()


# D. Ride quotes and fares ------------------------------------------------------------------------
def test_d1_quote_returns_every_field(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    assert q["quote_id"] == "QTE-000001"
    assert q["category"] == "economy"
    assert q["distance_km"] == D("4.100") == hav(CENTER, DROP)
    assert q["duration_min"] == 9 == eta(D("4.100"))
    assert dec(q["surge"]) == 1
    assert q["fare"] == D("10.76") == fare("economy", D("4.100"), 9)
    assert q["expires_at"] == START + timedelta(minutes=5)


def test_d1_duration_uses_the_zone_of_the_pickup(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, AIRPORT, AIR_DROP, "economy")
    assert q["distance_km"] == D("6.300")
    assert q["duration_min"] == eta(D("6.300"), AIRPORT_SPEED) == 10
    assert q["duration_min"] == w.p.eta_minutes(AIRPORT, AIR_DROP)


def test_d1_minimum_distance_accepted(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, north(CENTER, 0.2004), "economy")
    assert q["distance_km"] == D("0.200")


def test_d1_distance_rounding_to_minimum_accepted(w):
    # X6: compared after rounding; 0.1996 km rounds to 0.200, which is inside the range.
    rider, _ = w.rider()
    end = north(CENTER, 0.1996)
    assert hav(CENTER, end) == D("0.200")
    q = w.p.quote_ride(rider, CENTER, end, "economy")
    assert q["distance_km"] == D("0.200")


def test_d1_distance_below_minimum_rejected(w):
    rider, _ = w.rider()
    with pytest.raises(ValueError):
        w.p.quote_ride(rider, CENTER, north(CENTER, 0.19), "economy")


def test_d1_distance_rounding_below_minimum_rejected(w):
    rider, _ = w.rider()
    end = north(CENTER, 0.1994)
    assert hav(CENTER, end) == D("0.199")
    with pytest.raises(ValueError):
        w.p.quote_ride(rider, CENTER, end, "economy")


def test_d1_distance_below_150_km_accepted(w):
    rider, _ = w.rider()
    w.p.add_zone(w.admin, "Far", north(CENTER, 149.5), 5, 60)
    q = w.p.quote_ride(rider, CENTER, north(CENTER, 149.1), "economy")
    assert q["distance_km"] == D("149.100")


def test_d1_distance_rounding_to_150_km_accepted(w):
    rider, _ = w.rider()
    w.p.add_zone(w.admin, "Far", north(CENTER, 149.5), 5, 60)
    end = north(CENTER, 150.0004)
    assert hav(CENTER, end) == D("150.000")
    q = w.p.quote_ride(rider, CENTER, end, "economy")
    assert q["distance_km"] == D("150.000")


def test_d1_distance_rounding_above_150_km_rejected(w):
    rider, _ = w.rider()
    w.p.add_zone(w.admin, "Far", north(CENTER, 149.5), 5, 60)
    end = north(CENTER, 150.0006)
    assert hav(CENTER, end) == D("150.001")
    with pytest.raises(ValueError):
        w.p.quote_ride(rider, CENTER, end, "economy")


def test_d1_distance_above_150_km_rejected(w):
    rider, _ = w.rider()
    w.p.add_zone(w.admin, "Far", north(CENTER, 149.5), 5, 60)
    with pytest.raises(ValueError):
        w.p.quote_ride(rider, CENTER, north(CENTER, 150.4), "economy")


def test_d1_suspended_rider_cannot_quote(w):
    rider, _ = w.rider()
    w.p.suspend(w.admin, rider, "fraud")
    with pytest.raises(ValueError):
        w.p.quote_ride(rider, CENTER, DROP, "economy")
    w.p.reactivate(w.admin, rider)
    assert w.p.quote_ride(rider, CENTER, DROP, "economy")["quote_id"] == "QTE-000001"


def test_d1_unknown_rider_raises_key_error(w):
    with pytest.raises(KeyError):
        w.p.quote_ride("RID-999999", CENTER, DROP, "economy")


@pytest.mark.parametrize("category", ["luxury", "", "bike"])
def test_d1_unknown_category_rejected(w, category):
    rider, _ = w.rider()
    with pytest.raises(ValueError):
        w.p.quote_ride(rider, CENTER, DROP, category)


@pytest.mark.parametrize(("category", "expected"), [
    ("economy", D("20.58")), ("comfort", D("27.80")), ("xl", D("32.94")), ("moto", D("12.86")),
])
def test_d2_rates_by_category(w, category, expected):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, LONG, category)
    assert q["duration_min"] == 21
    assert q["fare"] == expected == fare(category, D("10.300"), 21)


@pytest.mark.parametrize(("category", "expected"), [
    ("economy", D("7.50")), ("comfort", D("10.50")), ("xl", D("13.00")), ("moto", D("5.00")),
])
def test_d2_minimum_fare_plus_booking_fee(w, category, expected):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, SHORT, category)
    assert q["fare"] == expected == fare(category, D("0.600"), 2)


@pytest.mark.parametrize(("minutes", "night"), [
    (599, False),  # 21:59
    (600, True),  # 22:00
    (840, True),  # 02:00
    (1079, True),  # 05:59
    (1080, False),  # 06:00
])
def test_d3_night_factor_window(w, minutes, night):
    rider, _ = w.rider()
    w.p.advance(minutes=minutes)
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    assert q["fare"] == fare("economy", D("4.100"), 9, night=night)
    assert q["fare"] == (D("12.61") if night else D("10.76"))


def test_d3_minimum_applies_after_night_factor(w):
    rider, _ = w.rider()
    w.p.advance(minutes=600)
    q = w.p.quote_ride(rider, CENTER, north(CENTER, 1.6), "economy")
    # metered 5.26 x 1.2 = 6.312 is above the minimum 6.00
    assert q["fare"] == D("7.81") == fare("economy", D("1.600"), 4, night=True)


def test_d3_minimum_applies_after_surge(w):
    w.surge_demand(1)
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, SHORT, "comfort")
    assert dec(q["surge"]) == D("1.5")
    # metered 5.10 x 1.5 = 7.65 is below the minimum 9.00, which is not multiplied
    assert q["fare"] == D("10.50") == fare("comfort", D("0.600"), 2, surge=D("1.5"))


def test_d3_surge_multiplies_the_metered_fare(w):
    w.surge_demand(1)
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "comfort")
    assert q["fare"] == fare("comfort", D("4.100"), 9, surge=D("1.5")) == D("20.70")


def test_d3_airport_surcharge_for_pickup(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, AIRPORT, AIR_DROP, "economy")
    assert q["fare"] == D("18.43") == fare("economy", D("6.300"), 10, airport=True)


def test_d3_airport_surcharge_for_dropoff(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, AIR_DROP, AIRPORT, "economy")
    assert q["fare"] == D("19.18") == fare("economy", D("6.300"), 13, airport=True)


def test_d3_airport_surcharge_added_once_after_minimum(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, AIRPORT, north(AIRPORT, 1.1), "economy")
    assert q["fare"] == D("12.50") == fare("economy", D("1.100"), 2, airport=True)


def test_d3_full_order_night_surge_booking_airport(w):
    w.p.advance(minutes=600)
    w.surge_demand(1, pickup=AIRPORT, dropoff=AIR_DROP)
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, AIRPORT, AIR_DROP, "comfort")
    assert dec(q["surge"]) == D("1.5")
    # ((3.50 + 9.45 + 3.50) x 1.2 x 1.5) + 1.50 + 5.00
    assert q["fare"] == D("36.11") == fare("comfort", D("6.300"), 10, night=True, surge=D("1.5"), airport=True)


def test_d3_rounds_half_up_to_cents(w):
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, north(CENTER, 4.01), "comfort")
    # 3.50 + 6.015 + 3.15 + 1.50 = 14.165
    assert q["fare"] == D("14.17") == fare("comfort", D("4.010"), 9)


def test_d4_expired_quote_rejected(w):
    rider, card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    w.p.advance(minutes=5, seconds=1)
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)


def test_d4_quote_expired_at_exactly_expires_at(w):
    rider, card = w.rider()
    w.driver()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    w.p.advance(minutes=5)
    assert w.p.now() == q["expires_at"]
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)


def test_d4_quote_usable_before_expiry(w):
    rider, card = w.rider()
    w.driver()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    w.p.advance(minutes=4, seconds=59)
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    assert w.p.ride(ride_id)["status"] == "requested"


def test_d4_quote_of_another_rider_rejected(w):
    rider, _ = w.rider()
    other, other_card = w.rider()
    w.driver()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    with pytest.raises(ValueError):
        w.p.request_ride(other, q["quote_id"], other_card)


def test_d4_used_quote_rejected_after_no_driver(w):
    rider, card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    assert w.p.ride(ride_id)["status"] == "no_driver"
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)


def test_d4_used_quote_rejected_after_cancellation(w):
    w.driver()
    rider, card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    w.p.cancel_ride(rider, ride_id, "")
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)


# E. Surge pricing --------------------------------------------------------------------------------
def test_e1_requested_ride_counts_as_demand(w):
    rider, _ = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == 1
    w.surge_demand(1)
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")


def test_e1_assigned_ride_is_not_demand(w):
    driver = w.driver()
    r1, c1 = w.rider()
    w.assigned(r1, c1, driver)
    rider, _ = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == 1


def test_e1_demand_counts_only_the_pickup_zone(w):
    w.north_zone()
    w.surge_demand(1, pickup=NORTH_CENTER, dropoff=NORTH_DROP)
    rider, _ = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == 1
    assert dec(w.p.quote_ride(rider, NORTH_CENTER, NORTH_DROP, "comfort")["surge"]) == D("1.5")


def test_e1_supply_counts_available_drivers_of_the_category(w):
    w.surge_demand(1)  # demand 2
    rider, _ = w.rider()
    w.driver(CENTER, "comfort")
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")  # 2 / 1
    w.driver(north(CENTER, 1), "comfort")
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == 1  # 2 / 2
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "xl")["surge"]) == D("1.5")  # 2 / max(0, 1)


def test_e1_supply_ignores_drivers_outside_the_zone_or_offline(w):
    w.north_zone()
    w.surge_demand(1)
    w.driver(NORTH_CENTER, "comfort")
    offline = w.driver(CENTER, "comfort")
    w.p.go_offline(offline)
    rider, _ = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")


def test_e1_driver_with_pending_offer_is_not_supply(w):
    c1 = w.driver(CENTER, "comfort")
    c2 = w.driver(north(CENTER, 1), "comfort")
    r1, card1 = w.rider()
    ride_id = w.request(r1, card1, category="comfort")
    assert w.p.ride(ride_id)["offered_to"] == c1  # c1 now has a pending offer, c2 has none
    rider, _ = w.rider()
    # demand 1 + 1 = 2; supply 1 (only c2) -> ratio 2 -> 1.5 (counting c1 would give 1.0)
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")
    w.p.decline_ride(c1, ride_id)
    assert w.p.ride(ride_id)["offered_to"] == c2
    # c1 is free again and c2 now holds the offer: supply is still 1
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")


def test_e2_ratio_one_and_a_half_gives_1_2(w):
    w.surge_demand(2)  # demand 3
    w.driver(CENTER, "comfort")
    w.driver(north(CENTER, 1), "comfort")
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "comfort")
    assert dec(q["surge"]) == D("1.2")
    assert q["fare"] == fare("comfort", D("4.100"), 9, surge=D("1.2"))


def test_e2_ratio_two_gives_1_5(w):
    w.surge_demand(1)
    rider, _ = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")


def test_e2_ratio_three_gives_1_8(w):
    w.surge_demand(2)
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "comfort")
    assert dec(q["surge"]) == D("1.8")
    assert q["fare"] == fare("comfort", D("4.100"), 9, surge=D("1.8"))


def test_e2_ratio_above_three_gives_2_0(w):
    w.surge_demand(3)
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "comfort")
    assert dec(q["surge"]) == D("2.0")
    assert q["fare"] == fare("comfort", D("4.100"), 9, surge=D("2.0"))


def test_e2_zone_cap_limits_the_multiplier(w):
    w.p.set_surge_cap(w.admin, w.city_zone, D("1.2"))
    w.surge_demand(1)
    rider, _ = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "comfort")
    assert dec(q["surge"]) == D("1.2")
    assert q["fare"] == fare("comfort", D("4.100"), 9, surge=D("1.2"))


def test_e2_cap_applies_only_to_its_zone(w):
    w.p.set_surge_cap(w.admin, w.airport_zone, D("1.0"))
    w.surge_demand(1)
    rider, _ = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")


@pytest.mark.parametrize("cap", [D("1.0"), D("2.5"), D("3.0")])
def test_e2_cap_within_bounds_accepted(w, cap):
    w.p.set_surge_cap(w.admin, w.city_zone, cap)


@pytest.mark.parametrize("cap", [D("0.9"), D("3.1"), D("0")])
def test_e2_cap_out_of_bounds_rejected(w, cap):
    with pytest.raises(ValueError):
        w.p.set_surge_cap(w.admin, w.city_zone, cap)


def test_e2_cap_is_admin_only(w):
    rider, _ = w.rider()
    with pytest.raises(PermissionError):
        w.p.set_surge_cap(rider, w.city_zone, D("1.5"))


def test_x8_surge_cap_errors_in_precedence_order(w):
    rider, _ = w.rider()
    with pytest.raises(KeyError):
        w.p.set_surge_cap(rider, "ZON-999999", D("5.0"))
    with pytest.raises(KeyError):
        w.p.set_surge_cap(w.admin, "ZON-999999", D("5.0"))
    with pytest.raises(PermissionError):
        w.p.set_surge_cap(rider, w.city_zone, D("5.0"))


def test_e3_ride_keeps_the_quoted_surge(w):
    w.surge_demand(1)
    rider, card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "comfort")
    assert dec(q["surge"]) == D("1.5")
    comfort = w.driver(CENTER, "comfort")  # supply changes after the quote
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    ride = w.p.ride(ride_id)
    assert dec(ride["surge"]) == D("1.5")
    assert ride["quoted_fare"] == q["fare"]
    w.p.accept_ride(comfort, ride_id)
    w.p.driver_arrived(comfort, ride_id)
    w.p.start_ride(comfort, ride_id)
    w.p.advance(minutes=10)
    w.p.complete_ride(comfort, ride_id, [CENTER, DROP])
    assert w.p.ride(ride_id)["fare"] == fare("comfort", D("4.100"), 9, surge=D("1.5"))


# F. Dispatch -------------------------------------------------------------------------------------
def test_f1_request_creates_requested_ride(w):
    driver = w.driver()
    rider, card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    assert ride_id == "RDE-000001"
    ride = w.p.ride(ride_id)
    assert ride["id"] == ride_id
    assert ride["rider_id"] == rider
    assert ride["status"] == "requested"
    assert ride["category"] == "economy"
    assert ride["quoted_fare"] == q["fare"] == D("10.76")
    assert ride["offered_to"] == driver


def test_f1_rider_with_active_ride_cannot_request(w):
    w.driver()
    w.driver(north(CENTER, 1))
    rider, card = w.rider()
    w.request(rider, card)
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")  # quoting is always allowed
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)


@pytest.mark.parametrize("stage", ["driver_assigned", "arrived", "in_progress"])
def test_f1_assigned_arrived_or_in_progress_ride_blocks_a_request(w, stage):
    driver = w.driver()
    w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    if stage in ("arrived", "in_progress"):
        w.p.driver_arrived(driver, ride_id)
    if stage == "in_progress":
        w.p.start_ride(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == stage
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    assert q["quote_id"].startswith("QTE-")
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)
    assert w.p.ride(ride_id)["status"] == stage


def test_f1_scheduled_ride_does_not_block_a_request(w):
    driver = w.driver()
    rider, card = w.rider()
    scheduled = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=2), card)
    ride_id = w.request(rider, card)
    assert w.p.ride(ride_id)["status"] == "requested"
    assert w.p.ride(ride_id)["offered_to"] == driver
    assert w.p.ride(scheduled)["status"] == "scheduled"


def test_f1_rider_can_request_after_cancellation(w):
    w.driver()
    rider, card = w.rider()
    first = w.request(rider, card)
    w.p.cancel_ride(rider, first, "")
    second = w.request(rider, card)
    assert second == "RDE-000002"
    assert w.p.ride(second)["status"] == "requested"


def test_f1_rider_can_request_after_no_driver(w):
    rider, card = w.rider()
    first = w.request(rider, card)
    assert w.p.ride(first)["status"] == "no_driver"
    driver = w.driver()
    second = w.request(rider, card)
    assert w.p.ride(second)["offered_to"] == driver


def test_f1_failed_hold_rejects_the_request(w):
    w.driver()
    rider, bad_card = w.rider(FAILING_CARD)
    with pytest.raises(ValueError):
        w.request(rider, bad_card)
    good_card = w.p.add_card(rider, GOOD_CARD, 12, 2030, "123")
    assert w.request(rider, good_card) == "RDE-000001"


def test_x8_payment_method_of_another_rider_raises_permission_error(w):
    w.driver()
    rider, _ = w.rider()
    _other, other_card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    with pytest.raises(PermissionError):
        w.p.request_ride(rider, q["quote_id"], other_card)
    w.p.advance(minutes=5)  # the quote has expired too: the permission problem is reported first
    with pytest.raises(PermissionError):
        w.p.request_ride(rider, q["quote_id"], other_card)


def test_x8_unknown_quote_reported_before_permission_problem(w):
    rider, _ = w.rider()
    _other, other_card = w.rider()
    with pytest.raises(KeyError):
        w.p.request_ride(rider, "QTE-999999", other_card)
    with pytest.raises(KeyError):
        w.p.request_ride("RID-999999", "QTE-999999", other_card)


def test_f2_nearest_driver_gets_the_offer(w):
    w.driver(north(CENTER, 3))
    near = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["offered_to"] == near


def test_f2_other_category_is_ignored(w):
    w.driver(CENTER, "comfort")
    economy = w.driver(north(CENTER, 2))
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["offered_to"] == economy


def test_f2_driver_within_8_km_is_eligible(w):
    driver = w.driver(north(CENTER, 7.9))
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["offered_to"] == driver


def test_f2_driver_beyond_8_km_is_ignored(w):
    w.driver(north(CENTER, 8.1))
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["status"] == "no_driver"


def test_f2_driver_at_8_km_after_rounding_is_eligible(w):
    spot = north(CENTER, 8.0004)
    assert hav(spot, CENTER) == D("8.000")
    driver = w.driver(spot)
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["offered_to"] == driver


def test_f2_driver_at_8_001_km_after_rounding_is_ignored(w):
    spot = north(CENTER, 8.0006)
    assert hav(spot, CENTER) == D("8.001")
    w.driver(spot)
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["status"] == "no_driver"


def test_f2_driver_with_pending_offer_for_another_ride_is_ignored(w):
    near = w.driver()
    far = w.driver(north(CENTER, 1))
    r1, c1 = w.rider()
    first = w.request(r1, c1)
    assert w.p.ride(first)["offered_to"] == near
    r2, c2 = w.rider()
    second = w.request(r2, c2)
    assert w.p.ride(second)["offered_to"] == far
    assert w.p.ride(first)["offered_to"] == near


def test_f2_driver_rated_exactly_4_60_is_eligible(w):
    rated = w.driver()
    rider, card = w.rider()
    for stars in (5, 5, 5, 4, 4):
        w.p.update_location(rated, CENTER)
        w.p.rate(rider, w.completed(rider, card, rated), stars)
    assert w.p.rating(rated) == D("4.60")
    w.p.update_location(rated, CENTER)
    w.driver(north(CENTER, 2))
    assert w.p.ride(w.request(rider, card))["offered_to"] == rated


def test_f2_busy_driver_is_ignored(w):
    busy = w.driver()
    free = w.driver(north(CENTER, 2))
    r1, c1 = w.rider()
    w.assigned(r1, c1, busy)
    r2, c2 = w.rider()
    assert w.p.ride(w.request(r2, c2))["offered_to"] == free


def test_f2_offline_driver_is_ignored(w):
    offline = w.driver()
    free = w.driver(north(CENTER, 2))
    w.p.go_offline(offline)
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["offered_to"] == free


def test_f2_driver_rated_below_4_60_is_ignored(w):
    low = w.driver()
    rider, card = w.rider()
    ride_id = w.completed(rider, card, low)
    w.p.rate(rider, ride_id, 4)
    assert w.p.rating(low) == D("4.00")
    w.p.update_location(low, CENTER)
    other = w.driver(north(CENTER, 2))
    assert w.p.ride(w.request(rider, card))["offered_to"] == other


def test_f2_tie_on_distance_goes_to_higher_rating(w):
    rated = w.driver()
    rider, card = w.rider()
    for stars in (5, 5, 4):
        w.p.update_location(rated, CENTER)
        w.p.rate(rider, w.completed(rider, card, rated), stars)
    assert w.p.rating(rated) == D("4.67")
    w.p.update_location(rated, CENTER)
    unrated = w.driver(CENTER)  # higher id, no rating counts as 5.00
    assert w.p.ride(w.request(rider, card))["offered_to"] == unrated


def test_f2_tie_on_distance_and_rating_goes_to_lower_id(w):
    first = w.driver(None)
    second = w.driver(CENTER)
    w.p.go_online(first, CENTER, w.vehicle[first])
    rider, card = w.rider()
    assert w.p.ride(w.request(rider, card))["offered_to"] == first
    assert first < second


def test_f2_ride_never_offered_twice_to_a_driver(w):
    d1 = w.driver()
    d2 = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.decline_ride(d1, ride_id)
    assert w.p.ride(ride_id)["offered_to"] == d2
    w.p.decline_ride(d2, ride_id)
    assert w.p.ride(ride_id)["status"] == "no_driver"


def test_f3_accept_within_15_seconds(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.advance(seconds=14)
    w.p.accept_ride(driver, ride_id)
    ride = w.p.ride(ride_id)
    assert ride["status"] == "driver_assigned"
    assert ride["driver_id"] == driver


def test_f3_accept_makes_driver_busy(w):
    driver = w.driver()
    rider, card = w.rider()
    w.assigned(rider, card, driver)
    assert w.p.worker_status(driver)["status"] == "busy"


def test_f3_other_driver_cannot_accept(w):
    w.driver()
    other = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    with pytest.raises(PermissionError):
        w.p.accept_ride(other, ride_id)
    assert w.p.ride(ride_id)["status"] == "requested"


def test_f3_offer_expired_at_exactly_15_seconds(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.advance(seconds=15)
    with pytest.raises(ValueError):
        w.p.accept_ride(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "no_driver"


def test_f3_expired_offer_cannot_be_accepted_after_reoffer(w):
    d1 = w.driver()
    d2 = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.advance(seconds=15)
    assert w.p.ride(ride_id)["offered_to"] == d2
    with pytest.raises(ValueError):
        w.p.accept_ride(d1, ride_id)
    w.p.accept_ride(d2, ride_id)
    ride = w.p.ride(ride_id)
    assert ride["status"] == "driver_assigned"
    assert ride["driver_id"] == d2


def test_f3_declined_offer_cannot_be_accepted(w):
    d1 = w.driver()
    d2 = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.decline_ride(d1, ride_id)
    with pytest.raises(ValueError):
        w.p.accept_ride(d1, ride_id)
    ride = w.p.ride(ride_id)
    assert ride["status"] == "requested"
    assert ride["offered_to"] == d2


def test_f3_never_offered_driver_gets_permission_error_after_reoffer(w):
    d1 = w.driver()
    d2 = w.driver(north(CENTER, 1))
    d3 = w.driver(north(CENTER, 2))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.decline_ride(d1, ride_id)
    assert w.p.ride(ride_id)["offered_to"] == d2
    with pytest.raises(PermissionError):
        w.p.accept_ride(d3, ride_id)
    assert w.p.ride(ride_id)["offered_to"] == d2


def test_x8_never_offered_driver_on_finished_ride_gets_permission_error(w):
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    assert w.p.ride(ride_id)["status"] == "no_driver"
    stranger = w.driver()
    with pytest.raises(PermissionError):
        w.p.accept_ride(stranger, ride_id)


def test_x8_unknown_ride_or_driver_raises_key_error(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    with pytest.raises(KeyError):
        w.p.accept_ride(driver, "RDE-999999")
    with pytest.raises(KeyError):
        w.p.accept_ride("DRV-999999", ride_id)
    with pytest.raises(KeyError):
        w.p.cancel_ride(rider, "RDE-999999", "")
    with pytest.raises(KeyError):
        w.p.ride("RDE-999999")
    assert w.p.ride(ride_id)["offered_to"] == driver


def test_f4_decline_offers_the_next_best(w):
    d1 = w.driver()
    w.driver(north(CENTER, 3))
    d2 = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.decline_ride(d1, ride_id)
    ride = w.p.ride(ride_id)
    assert ride["status"] == "requested"
    assert ride["offered_to"] == d2


def test_f4_expired_offer_goes_to_the_next_best(w):
    d1 = w.driver()
    d2 = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.advance(seconds=14)
    assert w.p.ride(ride_id)["offered_to"] == d1
    w.p.advance(seconds=1)  # exactly 15 s: the offer has expired
    assert w.p.ride(ride_id)["offered_to"] == d2
    w.p.advance(seconds=15)
    assert w.p.ride(ride_id)["status"] == "no_driver"


def test_f4_five_refusals_give_no_driver(w):
    drivers = [w.driver(north(CENTER, 0.5 * (i + 1))) for i in range(6)]
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    assert w.p.ride(ride_id)["offered_to"] == drivers[0]
    w.p.decline_ride(drivers[0], ride_id)
    assert w.p.ride(ride_id)["offered_to"] == drivers[1]
    w.p.advance(seconds=16)
    assert w.p.ride(ride_id)["offered_to"] == drivers[2]
    w.p.decline_ride(drivers[2], ride_id)
    assert w.p.ride(ride_id)["offered_to"] == drivers[3]
    w.p.advance(seconds=16)
    assert w.p.ride(ride_id)["offered_to"] == drivers[4]
    w.p.decline_ride(drivers[4], ride_id)
    assert w.p.ride(ride_id)["status"] == "no_driver"


def test_f4_no_eligible_driver_notifies_the_rider(w):
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    assert w.p.ride(ride_id)["status"] == "no_driver"
    assert any(n["kind"] == "no_driver" and n["ref"] == ride_id for n in w.p.notifications(rider))


def test_f4_no_driver_releases_the_hold(w):
    rider, card = w.rider()
    wallet = "WAL-" + rider.split("-")[1]
    w.p.top_up_wallet(rider, card, D("10.00"))
    first = w.request(rider, wallet, CENTER, DROP_21)  # fare 7.56
    assert w.p.ride(first)["status"] == "no_driver"
    driver = w.driver()
    second = w.request(rider, wallet, CENTER, DROP_21)
    assert w.p.ride(second)["offered_to"] == driver


def test_f5_same_key_returns_the_same_ride(w):
    driver = w.driver()
    rider, card = w.rider()
    q1 = w.p.quote_ride(rider, CENTER, DROP, "economy")
    first = w.p.request_ride(rider, q1["quote_id"], card, idempotency_key="key-1")
    second = w.p.request_ride(rider, q1["quote_id"], card, idempotency_key="key-1")
    assert first == second
    ride = w.p.ride(first)
    assert ride["status"] == "requested"
    assert ride["offered_to"] == driver


def test_f5_repeated_key_changes_nothing(w):
    w.driver()
    rider, card = w.rider()
    q1 = w.p.quote_ride(rider, CENTER, DROP, "economy")
    first = w.p.request_ride(rider, q1["quote_id"], card, idempotency_key="key-1")
    q2 = w.p.quote_ride(rider, CENTER, DROP_21, "economy")
    assert w.p.request_ride(rider, q2["quote_id"], card, idempotency_key="key-1") == first
    w.p.cancel_ride(rider, first, "")
    second = w.p.request_ride(rider, q2["quote_id"], card)  # q2 was not used
    assert second == "RDE-000002"


def test_f5_same_key_of_another_rider_is_independent(w):
    w.driver()
    w.driver(north(CENTER, 1))
    r1, c1 = w.rider()
    r2, c2 = w.rider()
    first = w.request(r1, c1, idempotency_key="shared")
    second = w.request(r2, c2, idempotency_key="shared")
    assert first != second
    assert w.p.ride(second)["rider_id"] == r2


# G. Ride lifecycle and cancellations -------------------------------------------------------------
def test_g1_lifecycle_statuses(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.driver_arrived(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "arrived"
    w.p.start_ride(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "in_progress"
    w.p.advance(minutes=10)
    w.p.complete_ride(driver, ride_id, [CENTER, DROP])
    assert w.p.ride(ride_id)["status"] == "completed"
    assert w.p.worker_status(driver)["status"] == "available"


def test_g1_arrival_requires_driver_near_pickup(w):
    driver = w.driver(north(CENTER, 0.5))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    with pytest.raises(ValueError):
        w.p.driver_arrived(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "driver_assigned"
    w.p.update_location(driver, north(CENTER, 0.15))
    w.p.driver_arrived(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "arrived"


def test_g1_start_requires_arrival(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    with pytest.raises(ValueError):
        w.p.start_ride(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "driver_assigned"


def test_g1_complete_requires_in_progress(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.driver_arrived(driver, ride_id)
    with pytest.raises(ValueError):
        w.p.complete_ride(driver, ride_id, [CENTER, DROP])
    assert w.p.ride(ride_id)["status"] == "arrived"


def test_g1_other_driver_cannot_drive_the_ride(w):
    driver = w.driver()
    other = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.update_location(other, CENTER)
    with pytest.raises(PermissionError):
        w.p.driver_arrived(other, ride_id)
    w.p.driver_arrived(driver, ride_id)
    with pytest.raises(PermissionError):
        w.p.start_ride(other, ride_id)
    w.p.start_ride(driver, ride_id)
    with pytest.raises(PermissionError):
        w.p.complete_ride(other, ride_id, [CENTER, DROP])
    assert w.p.ride(ride_id)["status"] == "in_progress"


def test_x8_other_driver_out_of_order_gets_permission_error(w):
    driver = w.driver()
    other = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    # wrong driver and wrong status (start and complete need arrived / in_progress)
    with pytest.raises(PermissionError):
        w.p.start_ride(other, ride_id)
    with pytest.raises(PermissionError):
        w.p.complete_ride(other, ride_id, [CENTER])
    assert w.p.ride(ride_id)["status"] == "driver_assigned"


@pytest.mark.parametrize(("minutes", "seconds", "expected"), [
    (5, 0, D("0.00")), (5, 59, D("0.00")), (6, 0, D("0.30")), (7, 30, D("0.60")), (12, 0, D("2.10")),
])
def test_g2_wait_fee_per_full_minute_beyond_five(w, minutes, seconds, expected):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.driver_arrived(driver, ride_id)
    w.p.advance(minutes=minutes, seconds=seconds)
    w.p.start_ride(driver, ride_id)
    w.p.advance(minutes=10)
    w.p.complete_ride(driver, ride_id, [CENTER, DROP])
    ride = w.p.ride(ride_id)
    assert ride["wait_fee"] == expected
    assert ride["charged"] == D("10.76") + expected


def test_g3_rider_cancel_while_requested_is_free(w):
    w.driver()
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.advance(seconds=10)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["charged"] == D("0.00")
    assert zero_or_none(ride["cancellation_fee"])
    assert not [p for p in w.p.payments(rider) if p["kind"] == "cancellation_fee"]


def test_g3_rider_cancel_within_two_minutes_of_assignment_is_free(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.advance(minutes=1, seconds=50)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["charged"] == D("0.00")
    assert zero_or_none(ride["cancellation_fee"])


@pytest.mark.parametrize(("seconds", "fee"), [(119, None), (120, D("3.00"))])
def test_g3_free_window_ends_at_exactly_two_minutes(w, seconds, fee):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.advance(seconds=seconds)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    if fee is None:
        assert zero_or_none(ride["cancellation_fee"])
        assert ride["charged"] == D("0.00")
    else:
        assert ride["cancellation_fee"] == ride["charged"] == fee


@pytest.mark.parametrize(("category", "fee"), [
    ("economy", D("3.00")), ("moto", D("3.00")), ("comfort", D("5.00")), ("xl", D("5.00")),
])
def test_g3_rider_cancel_after_two_minutes_pays_the_category_fee(w, category, fee):
    driver = w.driver(CENTER, category)
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver, category=category)
    w.p.advance(minutes=2, seconds=10)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["cancellation_fee"] == fee


def test_g3_rider_cancel_after_arrival_pays_the_fee(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.driver_arrived(driver, ride_id)
    w.p.advance(minutes=3)
    w.p.cancel_ride(rider, ride_id, "")
    assert w.p.ride(ride_id)["cancellation_fee"] == D("3.00")


def test_g3_cancellation_fee_goes_to_the_driver(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.advance(minutes=3)
    w.p.cancel_ride(rider, ride_id, "")
    earned = w.p.earnings(driver, DAY_START, DAY_START + timedelta(days=1))
    assert earned["fees"] == D("3.00")


def test_g3_driver_cancel_reoffers_to_another_driver(w):
    d1 = w.driver()
    d2 = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, d1)
    w.p.cancel_ride(d1, ride_id, "flat tyre")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "requested"
    assert ride["offered_to"] == d2
    w.p.decline_ride(d2, ride_id)
    assert w.p.ride(ride_id)["status"] == "no_driver"  # never back to d1


def test_g3_driver_cancel_costs_the_rider_nothing(w):
    d1 = w.driver()
    w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, d1)
    w.p.advance(minutes=3)
    w.p.cancel_ride(d1, ride_id, "")
    assert w.p.ride(ride_id)["charged"] == D("0.00")
    assert not [p for p in w.p.payments(rider) if p["kind"] == "cancellation_fee"]


def test_g3_admin_can_cancel(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.cancel_ride(w.admin, ride_id, "safety")
    assert w.p.ride(ride_id)["status"] == "cancelled"


def test_g3_admin_cancellation_is_free_for_everyone(w):
    driver = w.driver(CENTER, "comfort")
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver, category="comfort")
    w.p.driver_arrived(driver, ride_id)
    w.p.advance(minutes=3)  # a rider cancelling now would pay 5.00
    w.p.cancel_ride(w.admin, ride_id, "safety")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["charged"] == D("0.00")
    assert zero_or_none(ride["cancellation_fee"])
    assert w.p.payments(rider) == []
    earned = w.p.earnings(driver, DAY_START, DAY_START + timedelta(days=1))
    assert earned["fees"] == 0
    assert earned["total"] == 0


@pytest.mark.parametrize("actor", ["rider", "admin"])
def test_g3_driver_of_a_cancelled_ride_becomes_available(w, actor):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.advance(minutes=3)
    assert w.p.worker_status(driver)["status"] == "busy"
    w.p.cancel_ride(rider if actor == "rider" else w.admin, ride_id, "")
    assert w.p.worker_status(driver)["status"] == "available"
    other, other_card = w.rider()
    assert w.p.ride(w.request(other, other_card))["offered_to"] == driver


@pytest.mark.parametrize("actor", ["rider", "admin"])
def test_g3_cancellation_notifies_rider_and_assigned_driver(w, actor):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    w.p.cancel_ride(rider if actor == "rider" else w.admin, ride_id, "")
    for account in (rider, driver):
        assert any(n["kind"] == "ride_cancelled" and n["ref"] == ride_id for n in w.p.notifications(account))


def test_g3_cancellation_of_requested_ride_notifies_the_rider(w):
    w.driver()
    rider, card = w.rider()
    ride_id = w.request(rider, card)
    w.p.cancel_ride(rider, ride_id, "")
    assert any(n["kind"] == "ride_cancelled" and n["ref"] == ride_id for n in w.p.notifications(rider))


def test_g3_dispatched_scheduled_ride_cancelled_while_requested_is_free(w):
    w.driver()
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=1), card)
    w.p.advance(minutes=50, seconds=5)  # dispatched; I3 alone would charge 5.00 now
    assert w.p.ride(ride_id)["status"] == "requested"
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["charged"] == D("0.00")
    assert zero_or_none(ride["cancellation_fee"])
    assert not [p for p in w.p.payments(rider) if p["kind"] == "cancellation_fee"]


def test_g3_dispatched_scheduled_ride_pays_the_g3_fee_to_the_driver(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=1), card)
    w.p.advance(minutes=50)
    assert w.p.ride(ride_id)["offered_to"] == driver
    w.p.accept_ride(driver, ride_id)
    w.p.advance(minutes=3)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["cancellation_fee"] == ride["charged"] == D("3.00")  # economy fee of G3, not 5.00 of I3
    earned = w.p.earnings(driver, DAY_START, DAY_START + timedelta(days=1))
    assert earned["fees"] == D("3.00")


def test_g3_another_rider_cannot_cancel(w):
    w.driver()
    rider, card = w.rider()
    other, _ = w.rider()
    ride_id = w.request(rider, card)
    with pytest.raises(PermissionError):
        w.p.cancel_ride(other, ride_id, "")
    assert w.p.ride(ride_id)["status"] == "requested"


def test_x8_another_rider_cancelling_a_finished_ride_gets_permission_error(w):
    driver = w.driver()
    rider, card = w.rider()
    other, _ = w.rider()
    ride_id = w.completed(rider, card, driver)
    with pytest.raises(PermissionError):
        w.p.cancel_ride(other, ride_id, "")
    assert w.p.ride(ride_id)["status"] == "completed"


def test_g3_unassigned_driver_cannot_cancel(w):
    driver = w.driver()
    other = w.driver(north(CENTER, 1))
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver)
    with pytest.raises(PermissionError):
        w.p.cancel_ride(other, ride_id, "")
    assert w.p.ride(ride_id)["status"] == "driver_assigned"


def test_g3_ride_in_progress_cannot_be_cancelled(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver)
    with pytest.raises(ValueError):
        w.p.cancel_ride(rider, ride_id, "")
    assert w.p.ride(ride_id)["status"] == "in_progress"


def test_g3_finished_rides_cannot_be_cancelled(w):
    rider, card = w.rider()
    no_driver = w.request(rider, card)
    assert w.p.ride(no_driver)["status"] == "no_driver"
    with pytest.raises(ValueError):
        w.p.cancel_ride(rider, no_driver, "")
    driver = w.driver()
    cancelled = w.request(rider, card)
    w.p.cancel_ride(rider, cancelled, "")
    with pytest.raises(ValueError):
        w.p.cancel_ride(rider, cancelled, "")
    completed = w.completed(rider, card, driver)
    with pytest.raises(ValueError):
        w.p.cancel_ride(rider, completed, "")
    assert w.p.ride(completed)["status"] == "completed"


def _fee_cancellations(w, rider, card, driver, count):
    for _ in range(count):
        ride_id = w.assigned(rider, card, driver)
        w.p.advance(minutes=3)
        w.p.cancel_ride(rider, ride_id, "")
        assert w.p.ride(ride_id)["cancellation_fee"] == D("3.00")


def test_g4_three_fee_cancellations_block_the_rider(w):
    driver = w.driver()
    rider, card = w.rider()
    _fee_cancellations(w, rider, card, driver, 3)
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")  # quotes are still allowed
    assert q["quote_id"].startswith("QTE-")
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)


def test_g4_rider_block_ends_at_exactly_24_hours_after_the_third(w):
    driver = w.driver()
    rider, card = w.rider()
    _fee_cancellations(w, rider, card, driver, 3)
    third = w.p.now()
    w.p.advance(minutes=24 * 60 - 1, seconds=59)
    q = w.p.quote_ride(rider, CENTER, DROP, "economy")
    with pytest.raises(ValueError):
        w.p.request_ride(rider, q["quote_id"], card)
    w.p.advance(seconds=1)
    assert w.p.now() == third + timedelta(hours=24)
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    assert w.p.ride(ride_id)["status"] == "requested"


def test_g4_fee_cancellations_older_than_24_hours_do_not_count(w):
    driver = w.driver()
    rider, card = w.rider()
    _fee_cancellations(w, rider, card, driver, 2)
    w.p.advance(minutes=25 * 60)
    _fee_cancellations(w, rider, card, driver, 1)
    assert w.p.ride(w.request(rider, card))["status"] == "requested"


def test_g4_rider_block_lasts_24_hours_after_the_third(w):
    driver = w.driver()
    rider, card = w.rider()
    _fee_cancellations(w, rider, card, driver, 3)
    w.p.advance(minutes=23 * 60 + 55)
    with pytest.raises(ValueError):
        w.request(rider, card)
    w.p.advance(minutes=6)
    w.p.update_location(driver, CENTER)
    ride_id = w.request(rider, card)
    assert w.p.ride(ride_id)["status"] == "requested"


def test_g4_two_fee_cancellations_do_not_block(w):
    driver = w.driver()
    rider, card = w.rider()
    _fee_cancellations(w, rider, card, driver, 2)
    assert w.p.ride(w.request(rider, card))["status"] == "requested"


def test_g4_free_cancellations_do_not_count(w):
    w.driver()
    rider, card = w.rider()
    for _ in range(3):
        w.p.cancel_ride(rider, w.request(rider, card), "")
    assert w.p.ride(w.request(rider, card))["status"] == "requested"


def _driver_cancellations(w, rider, card, driver, count):
    for _ in range(count):
        ride_id = w.assigned(rider, card, driver)
        w.p.cancel_ride(driver, ride_id, "")
        assert w.p.ride(ride_id)["status"] == "no_driver"


def test_g4_driver_with_three_cancellations_is_forced_offline(w):
    driver = w.driver()
    rider, card = w.rider()
    _driver_cancellations(w, rider, card, driver, 3)
    assert w.p.worker_status(driver)["status"] == "offline"
    w.p.advance(minutes=11 * 60 + 55)
    with pytest.raises(ValueError):
        w.p.go_online(driver, CENTER, w.vehicle[driver])
    assert w.p.worker_status(driver)["status"] == "offline"


def test_g4_driver_can_go_online_at_exactly_12_hours(w):
    driver = w.driver()
    rider, card = w.rider()
    _driver_cancellations(w, rider, card, driver, 3)
    w.p.advance(minutes=12 * 60 - 1, seconds=59)
    with pytest.raises(ValueError):
        w.p.go_online(driver, CENTER, w.vehicle[driver])
    w.p.advance(seconds=1)
    w.p.go_online(driver, CENTER, w.vehicle[driver])
    assert w.p.worker_status(driver)["status"] == "available"


def test_g4_driver_with_two_cancellations_stays_online(w):
    driver = w.driver()
    rider, card = w.rider()
    _driver_cancellations(w, rider, card, driver, 2)
    assert w.p.worker_status(driver)["status"] == "available"


# H. Final fare and payment of a ride -------------------------------------------------------------
@pytest.mark.parametrize("route", [[], [DROP]])
def test_h1_route_needs_two_points(w, route):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver)
    w.p.advance(minutes=10)
    with pytest.raises(ValueError):
        w.p.complete_ride(driver, ride_id, route)
    assert w.p.ride(ride_id)["status"] == "in_progress"


def test_h1_actual_distance_is_not_rounded_per_segment(w):
    end = north(CENTER, 2.0008)
    route = [CENTER, north(CENTER, 1.0004), end]
    assert route_km(route) == D("2.001")
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver, CENTER, end)
    w.p.advance(minutes=6)
    w.p.complete_ride(driver, ride_id, route)
    assert w.p.ride(ride_id)["distance_km"] == D("2.001")


def test_h1_actual_minutes_are_rounded_up(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver)
    w.p.advance(minutes=10, seconds=1)
    w.p.complete_ride(driver, ride_id, [CENTER, DROP])
    assert w.p.ride(ride_id)["duration_min"] == 11


def test_h1_actual_minutes_are_at_least_one(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver)
    w.p.complete_ride(driver, ride_id, [CENTER, DROP])
    assert w.p.ride(ride_id)["duration_min"] == 1


def _detour_ride(w, rider, card, driver, detour_km, minutes, pickup=CENTER, dropoff=DROP_21,
                 category="economy"):
    ride_id = w.started(rider, card, driver, pickup, dropoff, category)
    w.p.advance(minutes=minutes)
    w.p.complete_ride(driver, ride_id, [pickup, north(pickup, detour_km), dropoff])
    return w.p.ride(ride_id)


def test_h2_upfront_price_kept_up_to_20_percent_more(w):
    driver = w.driver()
    rider, card = w.rider()
    ride = _detour_ride(w, rider, card, driver, 2.31, 40)  # 2.520 km = 1.2 x 2.100
    assert ride["distance_km"] == D("2.520")
    assert ride["duration_min"] == 40
    assert ride["fare"] == ride["quoted_fare"] == D("7.56")


def test_h2_fare_recomputed_above_20_percent(w):
    driver = w.driver()
    rider, card = w.rider()
    ride = _detour_ride(w, rider, card, driver, 2.3105, 12)  # 2.521 km
    assert ride["quoted_fare"] == D("7.56")
    assert ride["distance_km"] == D("2.521")
    assert ride["fare"] == D("9.77") == fare("economy", D("2.521"), 12)


def test_h2_recompute_uses_the_quote_night_factor(w):
    w.p.advance(minutes=590)  # 21:50
    driver = w.driver()
    rider, card = w.rider()
    ride = _detour_ride(w, rider, card, driver, 2.8, 15)  # completes at 22:05
    assert ride["distance_km"] == D("3.500")
    assert ride["fare"] == D("11.60") == fare("economy", D("3.500"), 15)


def test_h2_recompute_uses_the_quote_night_factor_at_night(w):
    w.p.advance(minutes=1070)  # 05:50 of the next day
    driver = w.driver()
    rider, card = w.rider()
    ride = _detour_ride(w, rider, card, driver, 2.8, 15)  # completes at 06:05
    assert ride["fare"] == fare("economy", D("3.500"), 15, night=True) == D("13.62")


def test_h2_recompute_uses_the_quote_surge(w):
    w.surge_demand(1)
    rider, card = w.rider()
    q = w.p.quote_ride(rider, CENTER, DROP_21, "comfort")
    assert dec(q["surge"]) == D("1.5")
    comfort = w.driver(CENTER, "comfort")
    ride_id = w.p.request_ride(rider, q["quote_id"], card)
    w.p.accept_ride(comfort, ride_id)
    w.p.driver_arrived(comfort, ride_id)
    w.p.start_ride(comfort, ride_id)
    w.p.advance(minutes=12)
    w.p.complete_ride(comfort, ride_id, [CENTER, north(CENTER, 2.8), DROP_21])
    # (3.50 + 5.25 + 4.20) x 1.5 + 1.50 = 20.925
    assert w.p.ride(ride_id)["fare"] == D("20.93") == fare("comfort", D("3.500"), 12, surge=D("1.5"))


def test_h2_recompute_keeps_the_airport_surcharge(w):
    driver = w.driver(AIRPORT)
    rider, card = w.rider()
    ride = _detour_ride(w, rider, card, driver, 2.8, 12, pickup=AIRPORT, dropoff=north(AIRPORT, 2.1))
    assert ride["quoted_fare"] == fare("economy", D("2.100"), eta(D("2.100"), AIRPORT_SPEED), airport=True)
    assert ride["fare"] == D("15.85") == fare("economy", D("3.500"), 12, airport=True)


def test_h3_before_completion_fare_is_quoted_and_nothing_charged(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver)
    ride = w.p.ride(ride_id)
    assert ride["fare"] == ride["quoted_fare"] == D("10.76")
    assert ride["charged"] == D("0.00")


def test_h3_charged_is_fare_plus_wait_fee_minus_discount(w):
    w.p.create_promo(w.admin, "SAVE2", "rides", "fixed", D("2.00"))
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver, promo_code="SAVE2")
    w.p.driver_arrived(driver, ride_id)
    w.p.advance(minutes=7, seconds=30)
    w.p.start_ride(driver, ride_id)
    w.p.advance(minutes=10)
    w.p.complete_ride(driver, ride_id, [CENTER, DROP])
    ride = w.p.ride(ride_id)
    assert ride["fare"] == D("10.76")
    assert ride["wait_fee"] == D("0.60")
    assert ride["discount"] == D("2.00")
    assert ride["charged"] == D("9.36")


def test_h3_payment_captured_for_charged(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.completed(rider, card, driver)
    charges = [p for p in w.p.payments(rider) if p["kind"] == "ride"]
    assert len(charges) == 1
    assert charges[0]["ref"] == ride_id
    assert charges[0]["method_id"] == card
    assert charges[0]["amount"] == w.p.ride(ride_id)["charged"] == D("10.76")


def test_h3_wallet_keeps_only_the_charged_amount(w):
    driver = w.driver()
    rider, card = w.rider()
    wallet = "WAL-" + rider.split("-")[1]
    w.p.top_up_wallet(rider, card, D("20.00"))
    w.completed(rider, wallet, driver, CENTER, DROP_21)
    assert w.p.wallet_balance(rider) == D("12.44")


def test_h3_cancellation_fee_is_charged(w):
    driver = w.driver(CENTER, "comfort")
    rider, card = w.rider()
    ride_id = w.assigned(rider, card, driver, category="comfort")
    w.p.advance(minutes=3)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["cancellation_fee"] == ride["charged"] == D("5.00")
    fees = [p for p in w.p.payments(rider) if p["kind"] == "cancellation_fee"]
    assert [(p["ref"], p["amount"]) for p in fees] == [(ride_id, D("5.00"))]


@pytest.fixture()
def done(w):
    """A completed economy ride: (world, rider, card, driver, ride id)."""
    driver = w.driver()
    rider, card = w.rider()
    return w, rider, card, driver, w.completed(rider, card, driver)


def test_h4_tip_is_recorded_and_charged(done):
    w, rider, card, _, ride_id = done
    w.p.add_tip(rider, ride_id, D("5.00"))
    assert w.p.ride(ride_id)["tip"] == D("5.00")
    tips = [p for p in w.p.payments(rider) if p["kind"] == "tip"]
    assert [(p["ref"], p["method_id"], p["amount"]) for p in tips] == [(ride_id, card, D("5.00"))]


def test_h4_tip_goes_to_the_driver(done):
    w, rider, _, driver, ride_id = done
    w.p.add_tip(rider, ride_id, "7.50")
    assert w.p.earnings(driver, DAY_START, DAY_START + timedelta(days=1))["tips"] == D("7.50")


def test_h4_tip_only_for_completed_rides(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.started(rider, card, driver)
    with pytest.raises(ValueError):
        w.p.add_tip(rider, ride_id, D("5.00"))


def test_h4_tip_within_24_hours(done):
    w, rider, _, _, ride_id = done
    w.p.advance(minutes=23 * 60 + 59)
    w.p.add_tip(rider, ride_id, D("2.00"))
    assert w.p.ride(ride_id)["tip"] == D("2.00")


def test_h4_tip_after_24_hours_rejected(done):
    w, rider, _, _, ride_id = done
    w.p.advance(minutes=24 * 60 + 1)
    with pytest.raises(ValueError):
        w.p.add_tip(rider, ride_id, D("2.00"))


def test_h4_tip_one_second_before_24_hours_accepted(done):
    w, rider, _, _, ride_id = done
    w.p.advance(minutes=24 * 60 - 1, seconds=59)
    w.p.add_tip(rider, ride_id, D("2.00"))
    assert w.p.ride(ride_id)["tip"] == D("2.00")


def test_h4_tip_window_has_passed_at_exactly_24_hours(done):
    w, rider, _, _, ride_id = done
    w.p.advance(minutes=24 * 60)
    with pytest.raises(ValueError):
        w.p.add_tip(rider, ride_id, D("2.00"))
    assert not [p for p in w.p.payments(rider) if p["kind"] == "tip"]


def test_x8_tip_by_another_rider_raises_permission_error(done):
    w, _, _, _, ride_id = done
    other, _ = w.rider()
    with pytest.raises(PermissionError):
        w.p.add_tip(other, ride_id, D("5.00"))
    with pytest.raises(PermissionError):
        w.p.add_tip(other, ride_id, D("0.50"))  # invalid amount too: permission first
    with pytest.raises(KeyError):
        w.p.add_tip(other, "RDE-999999", D("5.00"))
    assert w.p.payments(other) == []


def test_h4_tip_only_once(done):
    w, rider, _, _, ride_id = done
    w.p.add_tip(rider, ride_id, D("2.00"))
    with pytest.raises(ValueError):
        w.p.add_tip(rider, ride_id, D("3.00"))
    assert w.p.ride(ride_id)["tip"] == D("2.00")


@pytest.mark.parametrize("amount", ["1.00", "100.00", 1, D("100")])
def test_h4_tip_amount_bounds_accepted(done, amount):
    w, rider, _, _, ride_id = done
    w.p.add_tip(rider, ride_id, amount)
    assert w.p.ride(ride_id)["tip"] == D(str(amount))


@pytest.mark.parametrize("amount", ["0.99", "100.01", "0", "-5", "5.001", D("1.005"), 5.0])
def test_h4_tip_amount_out_of_bounds_rejected(done, amount):
    w, rider, _, _, ride_id = done
    with pytest.raises(ValueError):
        w.p.add_tip(rider, ride_id, amount)


# I. Scheduled rides ------------------------------------------------------------------------------
@pytest.mark.parametrize(("delta", "valid"), [
    (timedelta(minutes=29), False),
    (timedelta(minutes=31), True),
    (timedelta(days=29, hours=23, minutes=59), True),
    (timedelta(days=30, minutes=1), False),
])
def test_i1_pickup_at_window(w, delta, valid):
    rider, card = w.rider()
    if valid:
        ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + delta, card)
        assert w.p.ride(ride_id)["status"] == "scheduled"
    else:
        with pytest.raises(ValueError):
            w.p.schedule_ride(rider, CENTER, DROP, "economy", START + delta, card)


def test_i1_scheduled_ride_fields(w):
    rider, card = w.rider()
    pickup_at = START + timedelta(hours=3)
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", pickup_at, card)
    ride = w.p.ride(ride_id)
    assert ride_id == "RDE-000001"
    assert ride["status"] == "scheduled"
    assert ride["rider_id"] == rider
    assert ride["pickup_at"] == pickup_at
    assert ride["quoted_fare"] == D("10.76")


def test_i1_scheduled_ride_has_no_surge(w):
    w.surge_demand(1)
    rider, card = w.rider()
    assert dec(w.p.quote_ride(rider, CENTER, DROP, "comfort")["surge"]) == D("1.5")
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "comfort", START + timedelta(hours=2), card)
    ride = w.p.ride(ride_id)
    assert dec(ride["surge"]) == 1
    assert ride["quoted_fare"] == D("14.30") == fare("comfort", D("4.100"), 9)


def test_i1_night_factor_of_pickup_time(w):
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", datetime(2026, 3, 2, 23, 0), card)
    assert w.p.ride(ride_id)["quoted_fare"] == D("12.61")


def test_i1_no_night_factor_for_day_pickup_scheduled_at_night(w):
    rider, card = w.rider()
    w.p.advance(minutes=630)  # 22:30
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", datetime(2026, 3, 3, 10, 0), card)
    assert w.p.ride(ride_id)["quoted_fare"] == D("10.76")


def test_i1_no_hold_when_scheduling(w):
    rider, bad_card = w.rider(FAILING_CARD)
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=2), bad_card)
    assert w.p.ride(ride_id)["status"] == "scheduled"


def test_i2_dispatched_ten_minutes_before_pickup(w):
    driver = w.driver()
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=1), card)
    w.p.advance(minutes=49, seconds=59)
    assert w.p.ride(ride_id)["status"] == "scheduled"
    w.p.advance(seconds=1)  # exactly 10 minutes before pickup_at
    ride = w.p.ride(ride_id)
    assert ride["status"] == "requested"
    assert ride["offered_to"] == driver
    w.p.accept_ride(driver, ride_id)
    assert w.p.ride(ride_id)["status"] == "driver_assigned"


def test_i2_dispatch_without_driver_gives_no_driver(w):
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=1), card)
    w.p.advance(minutes=51)
    assert w.p.ride(ride_id)["status"] == "no_driver"


def test_i2_failed_hold_cancels_and_notifies(w):
    w.driver()
    rider, bad_card = w.rider(FAILING_CARD)
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=1), bad_card)
    w.p.advance(minutes=50, seconds=5)
    assert w.p.ride(ride_id)["status"] == "cancelled"
    assert any(n["kind"] == "ride_cancelled" and n["ref"] == ride_id for n in w.p.notifications(rider))


def test_i3_cancel_free_until_60_minutes_before(w):
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=3), card)
    w.p.advance(minutes=119)  # 61 minutes before pickup
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["charged"] == D("0.00")
    assert not [p for p in w.p.payments(rider) if p["kind"] == "cancellation_fee"]


def test_i3_late_cancel_pays_five(w):
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=3), card)
    w.p.advance(minutes=121)  # 59 minutes before pickup
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    assert ride["cancellation_fee"] == ride["charged"] == D("5.00")
    fees = [p for p in w.p.payments(rider) if p["kind"] == "cancellation_fee"]
    assert [(p["ref"], p["method_id"], p["amount"]) for p in fees] == [(ride_id, card, D("5.00"))]


@pytest.mark.parametrize(("elapsed", "fee"), [
    (timedelta(minutes=119, seconds=59), None),  # 60 min 1 s before pickup: free
    (timedelta(minutes=120), D("5.00")),  # exactly 60 min before: "until" is strict
])
def test_i3_free_cancellation_ends_at_exactly_60_minutes_before(w, elapsed, fee):
    rider, card = w.rider()
    ride_id = w.p.schedule_ride(rider, CENTER, DROP, "economy", START + timedelta(hours=3), card)
    w.p.advance(minutes=elapsed.seconds // 60, seconds=elapsed.seconds % 60)
    w.p.cancel_ride(rider, ride_id, "")
    ride = w.p.ride(ride_id)
    assert ride["status"] == "cancelled"
    if fee is None:
        assert ride["charged"] == D("0.00")
        assert zero_or_none(ride["cancellation_fee"])
    else:
        assert ride["cancellation_fee"] == ride["charged"] == fee
