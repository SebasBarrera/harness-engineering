from decimal import Decimal

import pytest

from inventory import Inventory


def make():
    inv = Inventory()
    inv.add_item("ABC-0001", "Bolt", "1.50", 10)
    return inv


def test_add_and_get_item():
    assert make().get_item("ABC-0001") == {
        "sku": "ABC-0001",
        "name": "Bolt",
        "price": Decimal("1.50"),
        "quantity": 10,
    }


def test_invalid_sku_rejected():
    with pytest.raises(ValueError):
        Inventory().add_item("abc-1", "Bolt", 1)


def test_receive_and_ship_update_history():
    inv = make()
    inv.receive("ABC-0001", 2)
    inv.ship("ABC-0001", 5)
    assert inv.get_item("ABC-0001")["quantity"] == 7
    assert inv.history("ABC-0001") == [("receive", 2), ("ship", 5)]


def test_over_shipping_rejected():
    with pytest.raises(ValueError):
        make().ship("ABC-0001", 11)
