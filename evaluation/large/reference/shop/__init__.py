"""Reference solution of the large-project scenario: the backend of a small online store.

Used only to validate the hidden tests; it is never shown to the agent.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

__all__ = ["Shop"]

SKU = re.compile(r"^[A-Z]{3}-\d{4}$")
CODE = re.compile(r"^[A-Z0-9]{4,12}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TAX = {"general": Decimal("0.19"), "food": Decimal("0.05"), "books": Decimal("0")}
CENT = Decimal("0.01")
GRAM = Decimal("0.001")
FREE_SHIPPING = Decimal("150.00")
TRANSITIONS = {"created": {"paid", "cancelled"}, "paid": {"shipped", "cancelled"}, "shipped": set(), "cancelled": set()}


def _decimal(value: Any, places: Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ValueError(f"expected int, str or Decimal, got {type(value).__name__}")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return number.quantize(places, rounding=ROUND_HALF_UP)


def _money(value: Any) -> Decimal:
    return _decimal(value, CENT)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"quantity must be a positive int, got {value!r}")
    return value


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Shop:
    def __init__(self) -> None:
        self._products: dict[str, dict[str, Any]] = {}
        self._received: dict[str, int] = {}
        self._held: dict[str, int] = {}
        self._coupons: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._payments: dict[str, dict[str, str]] = {}
        self._order_seq = 0
        self._payment_seq = 0

    # A. Catalog ---------------------------------------------------------------------------------
    def add_product(self, sku: str, name: str, price: Any, category: str, weight_kg: Any) -> None:
        if not isinstance(sku, str) or not SKU.match(sku):
            raise ValueError(f"invalid SKU {sku!r}")
        if sku in self._products:
            raise ValueError(f"duplicate SKU {sku}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must not be empty")
        price_value = _money(price)
        if price_value <= 0:
            raise ValueError("price must be positive")
        if category not in TAX:
            raise ValueError(f"unknown category {category!r}")
        weight = _decimal(weight_kg, GRAM)
        if weight <= 0:
            raise ValueError("weight must be positive")
        self._products[sku] = {"sku": sku, "name": name.strip(), "price": price_value,
                               "category": category, "weight_kg": weight}
        self._received[sku] = 0
        self._held[sku] = 0

    def get_product(self, sku: str) -> dict[str, Any]:
        if sku not in self._products:
            raise KeyError(sku)
        return dict(self._products[sku])

    def list_products(self, category: str | None = None) -> list[dict[str, Any]]:
        return [dict(p) for s, p in sorted(self._products.items()) if category is None or p["category"] == category]

    # B. Stock -----------------------------------------------------------------------------------
    def receive_stock(self, sku: str, quantity: Any) -> None:
        if sku not in self._products:
            raise KeyError(sku)
        self._received[sku] += _positive_int(quantity)

    def stock(self, sku: str) -> int:
        if sku not in self._products:
            raise KeyError(sku)
        return self._received[sku] - self._held[sku]

    # D. Coupons ---------------------------------------------------------------------------------
    def add_coupon(self, code: str, percent: Any, min_subtotal: Any = "0", expires: date | None = None,
                   max_uses: int | None = None) -> None:
        if not isinstance(code, str):
            raise ValueError("code must be a string")
        normalized = code.strip().upper()
        if not CODE.match(normalized):
            raise ValueError(f"invalid coupon code {code!r}")
        if normalized in self._coupons:
            raise ValueError(f"duplicate coupon {normalized}")
        if isinstance(percent, bool) or not isinstance(percent, int) or not 1 <= percent <= 50:
            raise ValueError("percent must be an int from 1 to 50")
        minimum = _money(min_subtotal)
        if minimum < 0:
            raise ValueError("min_subtotal must not be negative")
        if expires is not None and not isinstance(expires, date):
            raise ValueError("expires must be a date")
        if max_uses is not None:
            _positive_int(max_uses)
        self._coupons[normalized] = {"code": normalized, "percent": percent, "min_subtotal": minimum,
                                     "expires": expires, "max_uses": max_uses, "uses": 0}

    # C, E, F. Orders ----------------------------------------------------------------------------
    def create_order(self, customer_email: str, items: dict[str, Any], coupon: str | None = None,
                     on: date | None = None) -> str:
        if not isinstance(customer_email, str) or not EMAIL.match(customer_email.strip()):
            raise ValueError(f"invalid email {customer_email!r}")
        if not isinstance(items, dict) or not items:
            raise ValueError("an order needs at least one item")
        day = on or date.today()
        lines = []
        for sku in sorted(items):
            if sku not in self._products:
                raise KeyError(sku)
            quantity = _positive_int(items[sku])
            if quantity > self.stock(sku):
                raise ValueError(f"not enough stock for {sku}")
            product = self._products[sku]
            lines.append({"sku": sku, "quantity": quantity, "unit_price": product["price"],
                          "net": product["price"] * quantity})
        subtotal = sum((line["net"] for line in lines), Decimal("0.00"))
        percent = 0
        code = None
        if coupon is not None:
            code = str(coupon).strip().upper()
            entry = self._coupons.get(code)
            if entry is None:
                raise ValueError(f"unknown coupon {coupon!r}")
            if subtotal < entry["min_subtotal"]:
                raise ValueError("subtotal below the coupon minimum")
            if entry["expires"] is not None and day > entry["expires"]:
                raise ValueError("coupon expired")
            if entry["max_uses"] is not None and entry["uses"] >= entry["max_uses"]:
                raise ValueError("coupon exhausted")
            percent = entry["percent"]
        weight = Decimal("0")
        for line in lines:
            line["discount"] = _round(line["net"] * percent / 100)
            rate = TAX[self._products[line["sku"]]["category"]]
            line["tax"] = _round((line["net"] - line["discount"]) * rate)
            weight += self._products[line["sku"]]["weight_kg"] * line["quantity"]
        discount = sum((line["discount"] for line in lines), Decimal("0.00"))
        tax = sum((line["tax"] for line in lines), Decimal("0.00"))
        shipping = self._shipping(weight, subtotal - discount)
        for line in lines:
            self._held[line["sku"]] += line["quantity"]
        self._order_seq += 1
        order_id = f"ORD-{self._order_seq:06d}"
        self._orders[order_id] = {
            "id": order_id, "customer": customer_email.strip().lower(), "status": "created", "date": day,
            "coupon": code, "lines": lines, "subtotal": subtotal, "discount": discount, "tax": tax,
            "shipping": shipping, "total": subtotal - discount + tax + shipping, "paid": Decimal("0.00"),
            "refunded": Decimal("0.00"), "returned": {},
        }
        return order_id

    @staticmethod
    def _shipping(weight: Decimal, discounted: Decimal) -> Decimal:
        if discounted >= FREE_SHIPPING:
            return Decimal("0.00")
        if weight <= 1:
            return Decimal("5.00")
        if weight <= 5:
            return Decimal("9.00")
        if weight <= 20:
            return Decimal("15.00")
        return Decimal("15.00") + Decimal(math.ceil(weight - 20))

    def _get(self, order_id: str) -> dict[str, Any]:
        if order_id not in self._orders:
            raise KeyError(order_id)
        return self._orders[order_id]

    def order(self, order_id: str) -> dict[str, Any]:
        o = self._get(order_id)
        return {
            "id": o["id"], "customer": o["customer"], "status": o["status"],
            "lines": [{k: line[k] for k in ("sku", "quantity", "unit_price", "net", "discount", "tax")}
                      for line in o["lines"]],
            "subtotal": o["subtotal"], "discount": o["discount"], "tax": o["tax"], "shipping": o["shipping"],
            "total": o["total"], "paid": o["paid"], "refunded": o["refunded"],
        }

    def _move(self, o: dict[str, Any], status: str) -> None:
        if status not in TRANSITIONS[o["status"]]:
            raise ValueError(f"cannot go from {o['status']} to {status}")
        o["status"] = status

    # G. Payments --------------------------------------------------------------------------------
    def pay(self, order_id: str, amount: Any, idempotency_key: str) -> str:
        o = self._get(order_id)
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("idempotency_key is required")
        seen = self._payments.get(idempotency_key)
        if seen is not None:
            if seen["order"] != order_id:
                raise ValueError("idempotency key already used for another order")
            return seen["payment"]
        if _money(amount) != o["total"]:
            raise ValueError("amount must equal the order total")
        self._move(o, "paid")
        o["paid"] = o["total"]
        if o["coupon"] is not None:
            self._coupons[o["coupon"]]["uses"] += 1
        self._payment_seq += 1
        payment_id = f"PAY-{self._payment_seq:06d}"
        self._payments[idempotency_key] = {"order": order_id, "payment": payment_id}
        return payment_id

    def ship(self, order_id: str) -> None:
        self._move(self._get(order_id), "shipped")

    def cancel(self, order_id: str) -> None:
        o = self._get(order_id)
        self._move(o, "cancelled")
        for line in o["lines"]:
            self._held[line["sku"]] -= line["quantity"]
        o["refunded"] = o["paid"]

    # H. Refunds and returns ---------------------------------------------------------------------
    def refund(self, order_id: str, amount: Any) -> None:
        o = self._get(order_id)
        if o["status"] not in {"paid", "shipped"}:
            raise ValueError("only paid or shipped orders can be refunded")
        value = _money(amount)
        if value <= 0 or o["refunded"] + value > o["paid"]:
            raise ValueError("invalid refund amount")
        o["refunded"] += value

    def return_items(self, order_id: str, sku: str, quantity: Any) -> None:
        o = self._get(order_id)
        if o["status"] != "shipped":
            raise ValueError("only shipped orders accept returns")
        shipped = sum(line["quantity"] for line in o["lines"] if line["sku"] == sku)
        if shipped == 0:
            raise ValueError(f"{sku} is not in the order")
        count = _positive_int(quantity)
        if o["returned"].get(sku, 0) + count > shipped:
            raise ValueError("cannot return more than was shipped")
        o["returned"][sku] = o["returned"].get(sku, 0) + count
        self._held[sku] -= count

    # I. Reports ---------------------------------------------------------------------------------
    def sales_report(self, start: date, end: date) -> dict[str, Any]:
        if start > end:
            raise ValueError("start must not be after end")
        rows = [o for o in self._orders.values() if o["status"] in {"paid", "shipped"} and start <= o["date"] <= end]
        return {
            "orders": len(rows),
            "revenue": sum((o["total"] - o["refunded"] for o in rows), Decimal("0.00")),
            "tax": sum((o["tax"] for o in rows), Decimal("0.00")),
            "units": sum(sum(line["quantity"] for line in o["lines"]) - sum(o["returned"].values()) for o in rows),
        }

    # J. Persistence -----------------------------------------------------------------------------
    def save(self, path: str | Path) -> None:
        target = Path(path)
        if target.suffix != ".json":
            raise ValueError("the file must end in .json")
        target.write_text(json.dumps(self.__dict__, default=_encode, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Shop:
        source = Path(path)
        if source.suffix != ".json":
            raise ValueError("the file must end in .json")
        try:
            data = json.loads(source.read_text(encoding="utf-8"), object_hook=_decode)
            shop = cls()
            for key in shop.__dict__:
                setattr(shop, key, data[key])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("invalid store file") from exc
        return shop


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    raise TypeError(type(value).__name__)


def _decode(obj: dict[str, Any]) -> Any:
    if set(obj) == {"$decimal"}:
        return Decimal(obj["$decimal"])
    if set(obj) == {"$date"}:
        return date.fromisoformat(obj["$date"])
    return obj
