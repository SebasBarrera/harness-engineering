"""Reference implementation used only to validate the hidden tests."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

_SKU = re.compile(r"[A-Z]{3}-\d{4}")
_CENT = Decimal("0.01")


def _price(value):
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid price") from exc
    if not price.is_finite() or price < 0:
        raise ValueError("invalid price")
    return price.quantize(_CENT, rounding=ROUND_HALF_UP)


def _qty(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise ValueError("invalid quantity")
    return value


class Inventory:
    def __init__(self):
        self._items, self._history = {}, {}

    def add_item(self, sku, name, price, quantity=0):
        if not isinstance(sku, str) or not _SKU.fullmatch(sku) or sku in self._items:
            raise ValueError("invalid sku")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid name")
        self._items[sku] = {"sku": sku, "name": name, "price": _price(price), "quantity": _qty(quantity)}
        self._history[sku] = []

    def get_item(self, sku):
        return dict(self._items[sku])

    def remove_item(self, sku):
        del self._items[sku]
        self._history.pop(sku, None)

    def list_items(self):
        return [dict(self._items[k]) for k in sorted(self._items)]

    def receive(self, sku, quantity):
        item = self._items[sku]
        item["quantity"] += _qty(quantity, positive=True)
        self._history[sku].append(("receive", quantity))

    def ship(self, sku, quantity):
        item = self._items[sku]
        if _qty(quantity, positive=True) > item["quantity"]:
            raise ValueError("insufficient stock")
        item["quantity"] -= quantity
        self._history[sku].append(("ship", quantity))

    def history(self, sku):
        self._items[sku]
        return list(self._history[sku])

    def save(self, path):
        data = {"items": [{**i, "price": str(i["price"]), "history": [list(h) for h in self._history[i["sku"]]]}
                          for i in self.list_items()]}
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.suffix != ".json" or path.stat().st_size > 1024 * 1024:
            raise ValueError("invalid file")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            inv = cls()
            for item in data["items"]:
                inv.add_item(item["sku"], item["name"], item["price"], item["quantity"])
                for kind, qty in item.get("history", []):
                    if kind not in ("receive", "ship"):
                        raise ValueError("invalid history")
                    inv._history[item["sku"]].append((kind, _qty(qty, positive=True)))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid data") from exc
        return inv

    def low_stock(self, threshold):
        return sorted(k for k, v in self._items.items() if v["quantity"] < threshold)

    def total_value(self):
        total = sum((v["price"] * v["quantity"] for v in self._items.values()), Decimal(0))
        return total.quantize(_CENT, rounding=ROUND_HALF_UP)
