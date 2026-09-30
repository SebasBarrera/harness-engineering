from fixture_pricing import apply_discount


def test_below_threshold_is_unchanged() -> None:
    assert apply_discount(99, 100, 0.10) == 99
