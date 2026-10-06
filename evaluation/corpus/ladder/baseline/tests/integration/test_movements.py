from inventory import Inventory


def test_receive_then_ship_through_the_public_api():
    inv = Inventory()
    inv.add_item("ABC-0001", "Bolt", "1.50", 1)
    inv.receive("ABC-0001", 4)
    inv.ship("ABC-0001", 2)
    assert inv.get_item("ABC-0001")["quantity"] == 3
