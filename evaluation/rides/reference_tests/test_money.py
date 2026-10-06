"""N1-N4, O1-O4, X2."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import CARD, CENTER, World, at


def test_add_card_validation(w: World) -> None:
    p = w.p
    rider = w.rider()
    assert p.add_card(rider, CARD, 3, 2026, "123") == "PM-000001"  # current month is fine
    for number, month, year, cvc in (
        ("4242424242424241", 12, 2030, "123"),  # Luhn
        ("4242 4242 4242 4242", 12, 2030, "123"),
        ("424242424242", 12, 2030, "123"),
        ("6011111111111117", 12, 2030, "123"),  # unsupported brand
        (CARD, 2, 2026, "123"),  # expired
        (CARD, 13, 2030, "123"),
        (CARD, 12, 2030, "12"),
        ("378282246310005", 12, 2030, "123"),  # amex needs 4 digits
    ):
        with pytest.raises(ValueError):
            p.add_card(rider, number, month, year, cvc)
    amex = p.add_card(rider, "378282246310005", 12, 2030, "1234")
    master = p.add_card(rider, "5555555555554444", 12, 2030, "123")
    methods = p.payment_methods(rider)
    assert methods[0] == {"id": "WAL-" + rider[-6:], "kind": "wallet", "brand": None, "last4": None}
    assert methods[1:] == [
        {"id": "PM-000001", "kind": "card", "brand": "visa", "last4": "4242"},
        {"id": amex, "kind": "card", "brand": "amex", "last4": "0005"},
        {"id": master, "kind": "card", "brand": "mastercard", "last4": "4444"},
    ]
    assert "4242424242424242" not in repr(p._state)


def test_top_up_and_payments_history(w: World) -> None:
    p = w.p
    rider, other = w.rider(), w.rider()
    card = w.card(rider)
    with pytest.raises(ValueError):
        p.top_up_wallet(rider, card, "4.99")
    with pytest.raises(ValueError):
        p.top_up_wallet(rider, card, 10.0)
    with pytest.raises(ValueError):
        p.top_up_wallet(rider, card, "500.01")
    with pytest.raises(PermissionError):
        p.top_up_wallet(other, card, "10")
    with pytest.raises(KeyError):
        p.top_up_wallet(rider, "PM-999999", "10")
    p.top_up_wallet(rider, card, 10)
    assert p.wallet_balance(rider) == Decimal("10.00")
    assert p.payments(rider) == [
        {"id": "PAY-000001", "kind": "top_up", "ref": card, "method_id": card, "amount": Decimal("10.00"),
         "at": p.now()},
    ]  # fmt: skip
    assert p.payments(other) == []


def test_promo_creation(w: World) -> None:
    p = w.p
    rider = w.rider()
    with pytest.raises(PermissionError):
        p.create_promo(rider, "SAVE10", "rides", "percent", 10)
    for args in (("AB", "rides", "percent", 10), ("SAVE10", "food", "percent", 10), ("SAVE10", "rides", "pct", 10),
                 ("SAVE10", "rides", "percent", 51), ("SAVE10", "rides", "percent", "10"),
                 ("SAVE10", "rides", "fixed", "0.99"), ("SAVE10", "rides", "fixed", 1.5)):  # fmt: skip
        with pytest.raises(ValueError):
            p.create_promo(w.admin, *args)
    p.create_promo(w.admin, "  save10 ", "rides", "percent", 10)
    with pytest.raises(ValueError):
        p.create_promo(w.admin, "SAVE10", "eats", "fixed", 5)
    assert ("create_promo", "SAVE10") in [(e["action"], e["target"]) for e in p.audit_log(w.admin)]


def test_promo_on_ride(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    p.create_promo(w.admin, "HALF", "both", "percent", 50, max_discount="3.00", max_uses_per_user=1)
    p.create_promo(w.admin, "EATS5", "eats", "fixed", 5)
    p.create_promo(w.admin, "BIG100", "rides", "fixed", 5, min_spend="100")
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card, promo_code="EATS5")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card, promo_code="BIG100")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card, promo_code="NOPE")
    ride = p.request_ride(rider, quote["quote_id"], card, promo_code="half")
    assert p.ride(ride)["discount"] == Decimal("3.00")
    p.accept_ride(driver, ride)
    p.driver_arrived(driver, ride)
    p.start_ride(driver, ride)
    p.advance(minutes=6)
    p.complete_ride(driver, ride, [CENTER, at(3)])
    assert p.ride(ride)["charged"] == quote["fare"] - Decimal("3.00")
    quote2 = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote2["quote_id"], card, promo_code="HALF")  # per-user limit reached


def test_promo_window_and_first_order(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    rider = w.rider()
    card = w.card(rider)
    p.create_promo(w.admin, "SOON", "rides", "fixed", 2, starts=p.now() + timedelta(minutes=1),
                   ends=p.now() + timedelta(minutes=2))  # fmt: skip
    p.create_promo(w.admin, "FIRST", "rides", "fixed", 2, first_order_only=True)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card, promo_code="SOON")
    p.advance(minutes=1)
    ride = p.request_ride(rider, quote["quote_id"], card, promo_code="SOON")
    assert p.ride(ride)["discount"] == Decimal("2.00")
    p.cancel_ride(rider, ride)
    p.advance(minutes=1)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card, promo_code="SOON")  # ends is excluded
    w.complete(rider, card, driver)
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(rider, quote["quote_id"], card, promo_code="FIRST")


def test_total_use_limit_counts_on_completion(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    r1, r2 = w.rider(), w.rider()
    c1, c2 = w.card(r1), w.card(r2)
    p.create_promo(w.admin, "ONCE", "rides", "fixed", 1, max_total_uses=1)
    q1 = p.quote_ride(r1, CENTER, at(3), "economy")
    ride = p.request_ride(r1, q1["quote_id"], c1, promo_code="ONCE")
    p.cancel_ride(r1, ride)
    q1 = p.quote_ride(r1, CENTER, at(3), "economy")
    ride = p.request_ride(r1, q1["quote_id"], c1, promo_code="ONCE")  # cancelled use did not count
    p.accept_ride(driver, ride)
    p.driver_arrived(driver, ride)
    p.start_ride(driver, ride)
    p.complete_ride(driver, ride, [CENTER, at(3)])
    q2 = p.quote_ride(r2, CENTER, at(3), "economy")
    with pytest.raises(ValueError):
        p.request_ride(r2, q2["quote_id"], c2, promo_code="ONCE")


def test_referral_reward_once(w: World) -> None:
    p = w.p
    driver, _ = w.driver()
    referrer = w.rider()
    referred = w.rider(referral=p.referral_code(referrer))
    card = w.card(referred)
    w.complete(referred, card, driver)
    assert p.wallet_balance(referrer) == Decimal("5.00")
    assert p.wallet_balance(referred) == Decimal("5.00")
    w.complete(referred, card, driver)
    assert p.wallet_balance(referrer) == Decimal("5.00")
