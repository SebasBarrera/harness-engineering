"""Hidden acceptance checks of the large-project scenario. Never shown to the agent.

Each test name starts with the letter of the part of the requirements it checks (a to j).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from shop import Shop

D = Decimal
DAY = date(2026, 3, 10)


@pytest.fixture()
def shop() -> Shop:
    s = Shop()
    s.add_product("GEN-0001", "Headphones", "59.99", "general", "0.3")
    s.add_product("FOD-0001", "Coffee", "12.50", "food", 1)
    s.add_product("BOK-0001", "Novel", 20, "books", "0.5")
    s.add_product("GEN-0002", "Desk", D("149.90"), "general", "18")
    for sku in ("GEN-0001", "FOD-0001", "BOK-0001", "GEN-0002"):
        s.receive_stock(sku, 50)
    return s


# A. Catalog --------------------------------------------------------------------------------------
@pytest.mark.parametrize("sku", ["gen-0001", "GE-0001", "GEN-001", "GEN0001", "GEN-00011", ""])
def test_a_invalid_sku(sku):
    with pytest.raises(ValueError):
        Shop().add_product(sku, "x", "1", "general", "1")


def test_a_duplicate_sku(shop):
    with pytest.raises(ValueError):
        shop.add_product("GEN-0001", "Other", "1", "general", "1")


def test_a_empty_name_and_strip():
    s = Shop()
    with pytest.raises(ValueError):
        s.add_product("ABC-0001", "   ", "1", "general", "1")
    s.add_product("ABC-0002", "  Lamp ", "1", "general", "1")
    assert s.get_product("ABC-0002")["name"] == "Lamp"


@pytest.mark.parametrize("price", ["0", "-1", 0.5, "abc"])
def test_a_invalid_price(price):
    with pytest.raises(ValueError):
        Shop().add_product("ABC-0001", "x", price, "general", "1")


def test_a_price_is_decimal_rounded_half_up():
    s = Shop()
    s.add_product("ABC-0001", "x", "10.005", "general", "1")
    s.add_product("ABC-0002", "y", 7, "general", "1")
    assert s.get_product("ABC-0001")["price"] == D("10.01")
    assert isinstance(s.get_product("ABC-0002")["price"], Decimal)
    assert s.get_product("ABC-0002")["price"] == D("7.00")


def test_a_category_and_weight():
    s = Shop()
    with pytest.raises(ValueError):
        s.add_product("ABC-0001", "x", "1", "toys", "1")
    with pytest.raises(ValueError):
        s.add_product("ABC-0002", "x", "1", "general", "0")
    s.add_product("ABC-0003", "x", "1", "food", "0.12345")
    assert s.get_product("ABC-0003")["weight_kg"] == D("0.123")


def test_a_get_list_and_unknown(shop):
    assert shop.get_product("FOD-0001")["price"] == D("12.50")
    assert [p["sku"] for p in shop.list_products()] == ["BOK-0001", "FOD-0001", "GEN-0001", "GEN-0002"]
    assert [p["sku"] for p in shop.list_products("general")] == ["GEN-0001", "GEN-0002"]
    with pytest.raises(KeyError):
        shop.get_product("XYZ-9999")


# B. Stock ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("quantity", [0, -3, 1.5, "2", True])
def test_b_invalid_receive(shop, quantity):
    with pytest.raises(ValueError):
        shop.receive_stock("GEN-0001", quantity)


def test_b_unknown_sku(shop):
    with pytest.raises(KeyError):
        shop.receive_stock("XYZ-9999", 1)
    with pytest.raises(KeyError):
        shop.stock("XYZ-9999")


def test_b_order_reserves_and_cancel_releases(shop):
    order_id = shop.create_order("a@b.co", {"GEN-0001": 3}, on=DAY)
    assert shop.stock("GEN-0001") == 47
    shop.cancel(order_id)
    assert shop.stock("GEN-0001") == 50


def test_b_oversell_is_atomic(shop):
    with pytest.raises(ValueError):
        shop.create_order("a@b.co", {"GEN-0001": 2, "FOD-0001": 51}, on=DAY)
    assert shop.stock("GEN-0001") == 50
    assert shop.create_order("a@b.co", {"GEN-0001": 1}, on=DAY) == "ORD-000001"


# C. Pricing and tax ------------------------------------------------------------------------------
def test_c_lines_and_tax_rates(shop):
    o = shop.order(shop.create_order("a@b.co", {"GEN-0001": 2, "FOD-0001": 3, "BOK-0001": 1}, on=DAY))
    lines = {line["sku"]: line for line in o["lines"]}
    assert [line["sku"] for line in o["lines"]] == ["BOK-0001", "FOD-0001", "GEN-0001"]
    assert lines["GEN-0001"]["net"] == D("119.98")
    assert lines["GEN-0001"]["tax"] == D("22.80")
    assert lines["FOD-0001"]["tax"] == D("1.88")
    assert lines["BOK-0001"]["tax"] == D("0.00")
    assert o["subtotal"] == D("177.48")
    assert o["tax"] == D("24.68")


def test_c_total_formula(shop):
    o = shop.order(shop.create_order("a@b.co", {"FOD-0001": 1}, on=DAY))
    assert o["total"] == o["subtotal"] - o["discount"] + o["tax"] + o["shipping"]
    assert o["total"] == D("12.50") + D("0.63") + D("5.00")


def test_c_unit_price_frozen_at_order_time(shop):
    order_id = shop.create_order("a@b.co", {"GEN-0001": 1}, on=DAY)
    assert shop.order(order_id)["lines"][0]["unit_price"] == D("59.99")


@pytest.mark.parametrize("items", [{}, {"GEN-0001": 0}, {"GEN-0001": 1.0}])
def test_c_invalid_items(shop, items):
    with pytest.raises(ValueError):
        shop.create_order("a@b.co", items, on=DAY)


def test_c_unknown_item(shop):
    with pytest.raises(KeyError):
        shop.create_order("a@b.co", {"XYZ-9999": 1}, on=DAY)


# D. Coupons --------------------------------------------------------------------------------------
@pytest.mark.parametrize("code", ["AB", "THIS-IS-BAD", "WAYTOOLONGCODE1"])
def test_d_invalid_code(code):
    with pytest.raises(ValueError):
        Shop().add_coupon(code, 10)


@pytest.mark.parametrize("percent", [0, 51, 12.5, "10"])
def test_d_invalid_percent(percent):
    with pytest.raises(ValueError):
        Shop().add_coupon("SAVE10", percent)


def test_d_code_normalized_and_duplicate(shop):
    shop.add_coupon("save10", 10)
    with pytest.raises(ValueError):
        shop.add_coupon("SAVE10", 20)
    o = shop.order(shop.create_order("a@b.co", {"GEN-0001": 1}, coupon="Save10", on=DAY))
    assert o["discount"] == D("6.00")


def test_d_discount_per_line_and_tax_after_discount(shop):
    shop.add_coupon("TEN10", 10)
    o = shop.order(shop.create_order("a@b.co", {"GEN-0001": 2, "FOD-0001": 3}, coupon="TEN10", on=DAY))
    lines = {line["sku"]: line for line in o["lines"]}
    assert lines["GEN-0001"]["discount"] == D("12.00")
    assert lines["FOD-0001"]["discount"] == D("3.75")
    assert lines["GEN-0001"]["tax"] == D("20.52")
    assert lines["FOD-0001"]["tax"] == D("1.69")
    assert o["discount"] == D("15.75")


def test_d_minimum_expiry_and_unknown(shop):
    shop.add_coupon("BIG20", 20, min_subtotal="100")
    shop.add_coupon("OLD10", 10, expires=date(2026, 3, 9))
    with pytest.raises(ValueError):
        shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="BIG20", on=DAY)
    with pytest.raises(ValueError):
        shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="OLD10", on=DAY)
    with pytest.raises(ValueError):
        shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="NOPE", on=DAY)
    assert shop.stock("FOD-0001") == 50
    assert shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="OLD10", on=date(2026, 3, 9))


def test_d_max_uses_counts_paid_orders(shop):
    shop.add_coupon("ONCE", 5, max_uses=1)
    first = shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="ONCE", on=DAY)
    second = shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="ONCE", on=DAY)
    shop.pay(first, shop.order(first)["total"], "k1")
    assert second
    with pytest.raises(ValueError):
        shop.create_order("a@b.co", {"FOD-0001": 1}, coupon="ONCE", on=DAY)


# E. Shipping -------------------------------------------------------------------------------------
@pytest.fixture()
def heavy(shop: Shop) -> Shop:
    shop.add_product("SND-0001", "Sand bag", "2", "general", "4")
    shop.receive_stock("SND-0001", 50)
    return shop


@pytest.mark.parametrize(("items", "expected"), [
    ({"BOK-0001": 2}, "5.00"),
    ({"FOD-0001": 2}, "9.00"),
    ({"FOD-0001": 5}, "9.00"),
    ({"FOD-0001": 6}, "15.00"),
    ({"SND-0001": 5}, "15.00"),
    ({"SND-0001": 6}, "19.00"),
])
def test_e_weight_bands(heavy, items, expected):
    assert heavy.order(heavy.create_order("a@b.co", items, on=DAY))["shipping"] == D(expected)


def test_e_started_kilo_above_twenty(heavy):
    o = heavy.order(heavy.create_order("a@b.co", {"SND-0001": 5, "BOK-0001": 1}, on=DAY))
    assert o["shipping"] == D("16.00")


def test_e_free_shipping_after_discount_threshold(shop):
    shop.add_coupon("TWENTY", 20)
    assert shop.order(shop.create_order("a@b.co", {"GEN-0002": 1}, on=DAY))["shipping"] == D("15.00")
    assert shop.order(shop.create_order("a@b.co", {"GEN-0002": 1, "BOK-0001": 1}, on=DAY))["shipping"] == D("0.00")
    o = shop.order(shop.create_order("a@b.co", {"GEN-0002": 1, "BOK-0001": 1}, coupon="TWENTY", on=DAY))
    assert o["shipping"] == D("15.00")


def test_e_shipping_not_taxed(shop):
    o = shop.order(shop.create_order("a@b.co", {"BOK-0001": 1}, on=DAY))
    assert o["tax"] == D("0.00") and o["shipping"] == D("5.00") and o["total"] == D("25.00")


# F. Lifecycle ------------------------------------------------------------------------------------
@pytest.mark.parametrize("email", ["", "plain", "a@b", "a b@c.co", "@b.co"])
def test_f_invalid_email(shop, email):
    with pytest.raises(ValueError):
        shop.create_order(email, {"GEN-0001": 1}, on=DAY)


def test_f_ids_and_email_lowercase(shop):
    first = shop.create_order("Ana@Mail.COM", {"GEN-0001": 1}, on=DAY)
    second = shop.create_order("b@c.co", {"GEN-0001": 1}, on=DAY)
    assert (first, second) == ("ORD-000001", "ORD-000002")
    assert shop.order(first)["customer"] == "ana@mail.com"
    assert shop.order(first)["status"] == "created"


def test_f_transitions(shop):
    order_id = shop.create_order("a@b.co", {"GEN-0001": 1}, on=DAY)
    with pytest.raises(ValueError):
        shop.ship(order_id)
    shop.pay(order_id, shop.order(order_id)["total"], "k1")
    shop.ship(order_id)
    assert shop.order(order_id)["status"] == "shipped"
    with pytest.raises(ValueError):
        shop.cancel(order_id)


def test_f_cancelled_is_final(shop):
    order_id = shop.create_order("a@b.co", {"GEN-0001": 1}, on=DAY)
    shop.cancel(order_id)
    with pytest.raises(ValueError):
        shop.pay(order_id, shop.order(order_id)["total"], "k1")
    with pytest.raises(ValueError):
        shop.cancel(order_id)


def test_f_unknown_order(shop):
    with pytest.raises(KeyError):
        shop.order("ORD-999999")


# G. Payments -------------------------------------------------------------------------------------
def test_g_exact_amount(shop):
    order_id = shop.create_order("a@b.co", {"FOD-0001": 1}, on=DAY)
    with pytest.raises(ValueError):
        shop.pay(order_id, "18.12", "k1")
    with pytest.raises(ValueError):
        shop.pay(order_id, 18.13, "k1")
    assert shop.pay(order_id, "18.13", "k1") == "PAY-000001"
    assert shop.order(order_id)["paid"] == D("18.13")


def test_g_idempotency(shop):
    first = shop.create_order("a@b.co", {"FOD-0001": 1}, on=DAY)
    second = shop.create_order("a@b.co", {"FOD-0001": 1}, on=DAY)
    total = shop.order(first)["total"]
    payment = shop.pay(first, total, "same-key")
    assert shop.pay(first, total, "same-key") == payment
    with pytest.raises(ValueError):
        shop.pay(first, total, "other-key")
    with pytest.raises(ValueError):
        shop.pay(second, total, "same-key")
    assert shop.pay(second, total, "second-key") == "PAY-000002"


# H. Refunds and returns --------------------------------------------------------------------------
def test_h_refund_rules(shop):
    order_id = shop.create_order("a@b.co", {"FOD-0001": 1}, on=DAY)
    with pytest.raises(ValueError):
        shop.refund(order_id, "1")
    shop.pay(order_id, "18.13", "k1")
    shop.refund(order_id, "10")
    shop.refund(order_id, D("8.13"))
    assert shop.order(order_id)["refunded"] == D("18.13")
    with pytest.raises(ValueError):
        shop.refund(order_id, "0.01")
    with pytest.raises(ValueError):
        shop.refund(order_id, "0")


def test_h_cancel_after_payment_refunds_everything(shop):
    order_id = shop.create_order("a@b.co", {"FOD-0001": 2}, on=DAY)
    shop.pay(order_id, shop.order(order_id)["total"], "k1")
    shop.refund(order_id, "5")
    shop.cancel(order_id)
    o = shop.order(order_id)
    assert o["status"] == "cancelled" and o["refunded"] == o["paid"]
    assert shop.stock("FOD-0001") == 50


def test_h_returns(shop):
    order_id = shop.create_order("a@b.co", {"GEN-0001": 3}, on=DAY)
    shop.pay(order_id, shop.order(order_id)["total"], "k1")
    with pytest.raises(ValueError):
        shop.return_items(order_id, "GEN-0001", 1)
    shop.ship(order_id)
    shop.return_items(order_id, "GEN-0001", 2)
    assert shop.stock("GEN-0001") == 49
    with pytest.raises(ValueError):
        shop.return_items(order_id, "GEN-0001", 2)
    with pytest.raises(ValueError):
        shop.return_items(order_id, "FOD-0001", 1)


# I. Reports --------------------------------------------------------------------------------------
def test_i_report(shop):
    a = shop.create_order("a@b.co", {"GEN-0001": 2}, on=date(2026, 3, 1))
    b = shop.create_order("a@b.co", {"BOK-0001": 1}, on=date(2026, 3, 5))
    c = shop.create_order("a@b.co", {"FOD-0001": 1}, on=date(2026, 3, 5))
    shop.create_order("a@b.co", {"FOD-0001": 4}, on=date(2026, 3, 6))
    for order_id, key in ((a, "ka"), (b, "kb"), (c, "kc")):
        shop.pay(order_id, shop.order(order_id)["total"], key)
    shop.ship(a)
    shop.return_items(a, "GEN-0001", 1)
    shop.refund(b, "5")
    shop.cancel(c)
    report = shop.sales_report(date(2026, 3, 1), date(2026, 3, 31))
    assert report["orders"] == 2
    assert report["revenue"] == shop.order(a)["total"] + shop.order(b)["total"] - D("5")
    assert report["tax"] == shop.order(a)["tax"] + shop.order(b)["tax"]
    assert report["units"] == 2


def test_i_inclusive_bounds_and_order(shop):
    order_id = shop.create_order("a@b.co", {"BOK-0001": 1}, on=date(2026, 3, 5))
    shop.pay(order_id, shop.order(order_id)["total"], "k1")
    assert shop.sales_report(date(2026, 3, 5), date(2026, 3, 5))["orders"] == 1
    assert shop.sales_report(date(2026, 3, 6), date(2026, 3, 9))["orders"] == 0
    with pytest.raises(ValueError):
        shop.sales_report(date(2026, 3, 9), date(2026, 3, 1))


# J. Persistence ----------------------------------------------------------------------------------
def test_j_round_trip(shop, tmp_path):
    shop.add_coupon("TEN10", 10)
    order_id = shop.create_order("a@b.co", {"GEN-0001": 2}, coupon="TEN10", on=DAY)
    shop.pay(order_id, shop.order(order_id)["total"], "k1")
    path = tmp_path / "store.json"
    shop.save(path)
    loaded = Shop.load(path)
    assert loaded.order(order_id) == shop.order(order_id)
    assert loaded.stock("GEN-0001") == 48
    assert loaded.get_product("FOD-0001")["price"] == D("12.50")
    assert loaded.create_order("a@b.co", {"BOK-0001": 1}, on=DAY) == "ORD-000002"
    assert loaded.pay(order_id, shop.order(order_id)["total"], "k1") == "PAY-000001"


def test_j_rejects_bad_paths_and_content(shop, tmp_path):
    with pytest.raises(ValueError):
        shop.save(tmp_path / "store.txt")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        Shop.load(bad)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        Shop.load(other)
