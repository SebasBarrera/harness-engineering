"""Lossless JSON encoding of the domain state.

Plain JSON cannot tell a ``Decimal`` from a float, a tuple from a list or a ``date`` from a
string, so non-JSON values are tagged: ``{"$dec": "1.20"}``, ``{"$dt": iso}``,
``{"$date": iso}``, ``{"$tup": [...]}``, ``{"$set": [...]}``, ``{"$map": [[k, v], ...]}``
for dicts with non-string keys and ``{"$obj": "ClassName", "f": {...}}`` for dataclasses.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..domain.accounts import Account, CourierProfile, DriverProfile, RestaurantProfile, RiderProfile, WorkerState
from ..domain.fleet import Vehicle, Zone
from ..domain.food import MenuItem, Option, OptionGroup, Order, OrderLine
from ..domain.money import Card, EarningEntry, Hold, Payment, Promo, Wallet
from ..domain.records import AuditEntry, Notification, Rating, Ticket, Timer
from ..domain.rides import Quote, Ride

_CLASSES: dict[str, type[Any]] = {
    cls.__name__: cls
    for cls in (
        Account, RiderProfile, DriverProfile, CourierProfile, RestaurantProfile, WorkerState, Vehicle, Zone,
        Quote, Ride, MenuItem, OptionGroup, Option, Order, OrderLine, Promo, Card, Wallet, Hold, Payment,
        EarningEntry, Rating, Ticket, Notification, AuditEntry, Timer,
    )
}  # fmt: skip


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return str(value) if isinstance(value, str) else value
    if isinstance(value, Decimal):
        return {"$dec": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, tuple):
        return {"$tup": [_encode(item) for item in value]}
    if isinstance(value, set | frozenset):
        return {"$set": [_encode(item) for item in sorted(value)]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        if all(isinstance(key, str) and not key.startswith("$") for key in value):
            return {key: _encode(item) for key, item in value.items()}
        return {"$map": [[_encode(key), _encode(item)] for key, item in value.items()]}
    if dataclasses.is_dataclass(value) and type(value).__name__ in _CLASSES:
        fields = {field.name: _encode(getattr(value, field.name)) for field in dataclasses.fields(value)}
        return {"$obj": type(value).__name__, "f": fields}
    raise TypeError(f"cannot persist {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$dec" in value:
        return Decimal(value["$dec"])
    if "$dt" in value:
        return datetime.fromisoformat(value["$dt"])
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    if "$tup" in value:
        return tuple(_decode(item) for item in value["$tup"])
    if "$set" in value:
        return {_decode(item) for item in value["$set"]}
    if "$map" in value:
        return {_decode(key): _decode(item) for key, item in value["$map"]}
    if "$obj" in value:
        cls = _CLASSES[value["$obj"]]
        return cls(**{name: _decode(item) for name, item in value["f"].items()})
    return {key: _decode(item) for key, item in value.items()}


def dumps(value: Any) -> str:
    return json.dumps(_encode(value), separators=(",", ":"), sort_keys=False)


def loads(text: str) -> Any:
    return _decode(json.loads(text))
