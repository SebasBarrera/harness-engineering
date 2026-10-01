"""Hidden acceptance tests for the greenfield task (never shown to the implementer)."""

from decimal import Decimal

import pytest
from shipping import shipping_cost


def d(value: str) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize(
    ("weight", "zone", "expected"),
    [
        (1, "local", "5.00"),
        (0.5, "local", "5.00"),
        (1.01, "local", "8.00"),
        (5, "local", "8.00"),
        (5.01, "local", "9.50"),
        (7.2, "national", "21.50"),
        (1, "international", "25.00"),
        (30, "international", "190.00"),
    ],
)
def test_base_cost_by_weight(weight, zone, expected):
    assert shipping_cost(weight, zone, 20) == d(expected)


@pytest.mark.parametrize("weight", [0, -1, 30.01, "31"])
def test_weight_out_of_range_is_rejected(weight):
    with pytest.raises(ValueError):
        shipping_cost(weight, "local", 20)


def test_negative_subtotal_is_rejected():
    with pytest.raises(ValueError):
        shipping_cost(1, "local", "-0.01")


def test_zero_subtotal_is_accepted():
    assert shipping_cost(1, "local", 0) == d("5.00")


@pytest.mark.parametrize("zone", ["Local", "mars", "", "LOCAL"])
def test_unknown_zone_is_rejected(zone):
    with pytest.raises(ValueError):
        shipping_cost(1, zone, 20)


def test_express_adds_half_of_base():
    assert shipping_cost(5, "local", 10, express=True) == d("12.00")
    assert shipping_cost(7.2, "national", 10, express=True) == d("32.25")


def test_express_international_is_rejected():
    with pytest.raises(ValueError):
        shipping_cost(1, "international", 10, express=True)


def test_free_shipping_threshold_is_inclusive():
    assert shipping_cost(3, "local", 100) == d("0.00")
    assert shipping_cost(3, "local", "99.99") == d("8.00")
    assert shipping_cost(3, "national", "100.00") == d("0.00")


def test_free_shipping_excludes_express_and_international():
    assert shipping_cost(3, "local", 150, express=True) == d("12.00")
    assert shipping_cost(1, "international", 1000) == d("25.00")


def test_percentage_coupon_rounds_half_up_once():
    assert shipping_cost(7.2, "national", 10, express=True, coupon="ENVIO10") == d("29.03")


def test_coupon_codes_ignore_case_and_whitespace():
    assert shipping_cost(7.2, "national", 10, express=True, coupon="  envio10 ") == d("29.03")


def test_flat_coupon_never_goes_below_zero():
    assert shipping_cost(1, "local", 10, coupon="FLAT5") == d("0.00")
    assert shipping_cost(1, "national", 10, coupon="flat5") == d("4.00")


def test_coupon_on_free_shipping_stays_zero():
    assert shipping_cost(3, "local", 200, coupon="FLAT5") == d("0.00")
    assert shipping_cost(3, "local", 200, coupon="ENVIO10") == d("0.00")


def test_unknown_coupon_is_rejected():
    with pytest.raises(ValueError):
        shipping_cost(1, "local", 10, coupon="BOGUS")


@pytest.mark.parametrize("coupon", [None, ""])
def test_absent_coupon_means_no_discount(coupon):
    assert shipping_cost(1, "local", 10, coupon=coupon) == d("5.00")


def test_numeric_inputs_of_every_type():
    assert shipping_cost(0.1 + 0.2, "local", 10) == d("5.00")
    assert shipping_cost("5.000", "local", 10) == d("8.00")
    assert shipping_cost(Decimal("5.0000001"), "local", 10) == d("9.50")
    assert shipping_cost(3, "local", 99.999) == d("8.00")


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [((1, "local", 10), {}), ((3, "local", 100), {}), ((7.2, "national", 10), {"coupon": "ENVIO10"})],
)
def test_result_is_decimal_with_two_places(args, kwargs):
    result = shipping_cost(*args, **kwargs)
    assert isinstance(result, Decimal)
    assert result.as_tuple().exponent == -2
