"""Hidden acceptance tests of the longitudinal task, grouped by part (A to D)."""

import json
from decimal import Decimal

import pytest
from inventory import Inventory


def make():
    inv = Inventory()
    inv.add_item("ABC-0001", "Bolt", "1.50", 10)
    inv.add_item("XYZ-0002", "Nut", Decimal("0.25"), 4)
    return inv


# Part A: items
def test_a_add_and_get_item():
    inv = make()
    assert inv.get_item("ABC-0001") == {"sku": "ABC-0001", "name": "Bolt", "price": Decimal("1.50"), "quantity": 10}


@pytest.mark.parametrize("sku", ["abc-0001", "AB-0001", "ABC-001", "ABC0001", ""])
def test_a_invalid_sku_rejected(sku):
    with pytest.raises(ValueError):
        Inventory().add_item(sku, "Bolt", 1)


def test_a_duplicate_and_bad_values_rejected():
    inv = make()
    for args in [("ABC-0001", "Again", 1), ("DEF-0003", "", 1), ("DEF-0003", "X", -1), ("DEF-0003", "X", 1, -2),
                 ("DEF-0003", "X", 1, 1.5)]:
        with pytest.raises(ValueError):
            inv.add_item(*args)


def test_a_unknown_sku_and_remove():
    inv = make()
    inv.remove_item("XYZ-0002")
    with pytest.raises(KeyError):
        inv.get_item("XYZ-0002")
    with pytest.raises(KeyError):
        inv.remove_item("XYZ-0002")


def test_a_list_sorted_and_price_two_places():
    inv = Inventory()
    inv.add_item("ZZZ-0009", "Z", 2)
    inv.add_item("AAA-0001", "A", "3.456")
    items = inv.list_items()
    assert [i["sku"] for i in items] == ["AAA-0001", "ZZZ-0009"]
    assert items[0]["price"] == Decimal("3.46") and items[1]["price"] == Decimal("2.00")


# Part B: stock movements
def test_b_receive_ship_and_history():
    inv = make()
    inv.receive("ABC-0001", 5)
    inv.ship("ABC-0001", 12)
    assert inv.get_item("ABC-0001")["quantity"] == 3
    assert inv.history("ABC-0001") == [("receive", 5), ("ship", 12)]


def test_b_overship_rejected_without_change():
    inv = make()
    with pytest.raises(ValueError):
        inv.ship("XYZ-0002", 5)
    assert inv.get_item("XYZ-0002")["quantity"] == 4
    assert inv.history("XYZ-0002") == []


@pytest.mark.parametrize("qty", [0, -1, 1.5, "2"])
def test_b_invalid_quantities(qty):
    inv = make()
    with pytest.raises(ValueError):
        inv.receive("ABC-0001", qty)
    with pytest.raises(ValueError):
        inv.ship("ABC-0001", qty)


def test_b_unknown_sku():
    with pytest.raises(KeyError):
        make().receive("NOP-0000", 1)


# Part C: persistence
def test_c_round_trip(tmp_path):
    inv = make()
    inv.receive("ABC-0001", 2)
    path = tmp_path / "inv.json"
    inv.save(path)
    loaded = Inventory.load(path)
    assert loaded.list_items() == inv.list_items()
    assert loaded.history("ABC-0001") == [("receive", 2)]
    assert json.loads(path.read_text())  # plain JSON


def test_c_rejects_non_json_extension(tmp_path):
    path = tmp_path / "inv.txt"
    path.write_text("{}")
    with pytest.raises(ValueError):
        Inventory.load(path)


@pytest.mark.parametrize("content", ["{not json", '{"items": [{"sku": "bad", "name": "x", "price": "1", "quantity": 1}]}',
                                     '{"items": [{"sku": "ABC-0001", "name": "x", "price": "1", "quantity": -3}]}',
                                     '{"items": [{"sku": "ABC-0001", "name": "x", "price": "-1", "quantity": 3}]}'])
def test_c_rejects_invalid_content(tmp_path, content):
    path = tmp_path / "inv.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        Inventory.load(path)


def test_c_rejects_large_files(tmp_path):
    path = tmp_path / "big.json"
    path.write_text(" " * (1024 * 1024 + 1) + "{}")
    with pytest.raises(ValueError):
        Inventory.load(path)


# Part D: reports
def test_d_low_stock():
    inv = make()
    inv.add_item("MMM-0005", "M", 1, 0)
    assert inv.low_stock(5) == ["MMM-0005", "XYZ-0002"]
    assert inv.low_stock(4) == ["MMM-0005"]


def test_d_total_value_rounding():
    inv = Inventory()
    inv.add_item("AAA-0001", "A", "0.335", 3)
    inv.add_item("BBB-0002", "B", "2.50", 2)
    assert inv.total_value() == Decimal("6.02")
    assert Inventory().total_value() == Decimal("0.00")
