"""Pure pricing rules: ride fares (D2, D3, H2), surge (E2), food orders (K3-K5), worker pay
(M4, P1) and promotion discounts (N3). No state, no clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from ..core.money import ZERO, money, round_half_up


@dataclass(frozen=True, slots=True)
class Rate:
    base: Decimal
    per_km: Decimal
    per_min: Decimal
    minimum: Decimal
    booking_fee: Decimal


RATES: dict[str, Rate] = {
    "economy": Rate(Decimal("2.50"), Decimal("1.10"), Decimal("0.25"), Decimal("6.00"), Decimal("1.50")),
    "comfort": Rate(Decimal("3.50"), Decimal("1.50"), Decimal("0.35"), Decimal("9.00"), Decimal("1.50")),
    "xl": Rate(Decimal("4.00"), Decimal("1.80"), Decimal("0.40"), Decimal("11.00"), Decimal("2.00")),
    "moto": Rate(Decimal("1.50"), Decimal("0.70"), Decimal("0.15"), Decimal("4.00"), Decimal("1.00")),
}

NIGHT_FACTOR = Decimal("1.2")
AIRPORT_SURCHARGE = Decimal("5.00")
NO_SURGE = Decimal("1.0")
SURGE_STEPS: tuple[tuple[Fraction, Decimal], ...] = (
    (Fraction(1), Decimal("1.0")),
    (Fraction(3, 2), Decimal("1.2")),
    (Fraction(2), Decimal("1.5")),
    (Fraction(3), Decimal("1.8")),
)
SURGE_TOP = Decimal("2.0")


def is_night(moment: datetime) -> bool:
    """D3: from 22:00 to 05:59."""
    return moment.hour >= 22 or moment.hour < 6


def ride_fare(
    category: str, distance_km: Decimal, minutes: int, *, night: bool, surge: Decimal, airport: bool
) -> Decimal:
    """D3: metered, night factor, surge, minimum, booking fee, airport surcharge, rounded."""
    rate = RATES[category]
    amount = rate.base + rate.per_km * distance_km + rate.per_min * minutes
    if night:
        amount *= NIGHT_FACTOR
    amount *= surge
    amount = max(amount, rate.minimum) + rate.booking_fee
    if airport:
        amount += AIRPORT_SURCHARGE
    return money(amount)


def surge_multiplier(requested_rides: int, available_drivers: int, cap: Decimal) -> Decimal:
    """E1, E2: ``demand = requested + 1``, ``ratio = demand / max(supply, 1)``, capped."""
    ratio = Fraction(requested_rides + 1, max(available_drivers, 1))
    multiplier = SURGE_TOP
    for limit, value in SURGE_STEPS:
        if ratio <= limit:
            multiplier = value
            break
    return min(multiplier, cap)


def driver_fare_share(category: str, fare: Decimal, airport: bool) -> Decimal:
    """P1: 75 % of (fare - booking fee - airport surcharge), rounded, plus the surcharge."""
    surcharge = AIRPORT_SURCHARGE if airport else ZERO
    return money(Decimal("0.75") * (fare - RATES[category].booking_fee - surcharge)) + surcharge


def wait_fee(waited_seconds: float) -> Decimal:
    """G2: 0.30 per full minute beyond 5 minutes."""
    extra_minutes = int(waited_seconds // 60) - 5
    return money(Decimal("0.30") * extra_minutes) if extra_minutes > 0 else ZERO


# --- Food orders -------------------------------------------------------------------------

TAX_RATES = {"food": Decimal("0.08"), "alcohol": Decimal("0.19")}


def service_fee(subtotal: Decimal) -> Decimal:
    """K4: 5 % rounded, from 1.00 to 5.00."""
    return min(max(money(subtotal * Decimal("0.05")), Decimal("1.00")), Decimal("5.00"))


def small_order_fee(subtotal: Decimal) -> Decimal:
    return Decimal("2.00") if subtotal < Decimal("10.00") else ZERO


def delivery_fee(subtotal: Decimal, distance_km: Decimal) -> Decimal:
    """K4: 1.99 + 0.50 per km beyond 2, rounded; free from 35.00."""
    if subtotal >= Decimal("35.00"):
        return ZERO
    return money(Decimal("1.99") + Decimal("0.50") * max(distance_km - 2, Decimal(0)))


def line_tax(amount: Decimal, category: str) -> Decimal:
    return money(amount * TAX_RATES[category])


def courier_pay(distance_km: Decimal, batched: bool) -> Decimal:
    """M4: 2.50 + 0.60 x km, rounded; the second order of a batch earns 70 % of it."""
    pay = money(Decimal("2.50") + Decimal("0.60") * distance_km)
    return money(pay * Decimal("0.70")) if batched else pay


def promo_discount(kind: str, value: Decimal, max_discount: Decimal | None, amount: Decimal) -> Decimal:
    """N3: percent rounded and capped, or fixed limited to the amount."""
    if kind == "percent":
        discount = round_half_up(amount * value / 100, 2)
        return min(discount, max_discount) if max_discount is not None else discount
    return min(value, amount)
