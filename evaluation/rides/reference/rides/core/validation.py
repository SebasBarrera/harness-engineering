"""Argument validators shared by the services. Every failure raises ``ValueError`` (X3)."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

Location = tuple[float, float]


def require_int(value: Any, name: str, low: int | None = None, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int")
    if low is not None and value < low:
        raise ValueError(f"{name} must be at least {low}")
    if high is not None and value > high:
        raise ValueError(f"{name} must be at most {high}")
    return int(value)


def require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool")
    return value


def require_text(value: Any, name: str, max_length: int | None = None) -> str:
    """A stripped, non-empty string of at most ``max_length`` characters."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if max_length is not None and len(text) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters")
    return text


def require_match(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{name} has an invalid format")
    return value


def require_choice(value: Any, choices: tuple[str, ...], name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(choices)}")
    return value


def require_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    return value


def require_date(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise ValueError(f"{name} must be a date")
    return value


def require_number(value: Any, name: str, low: Decimal, high: Decimal) -> Decimal:
    """A real number (int, float, str or Decimal) in ``[low, high]``, returned as Decimal."""
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise ValueError(f"{name} must be a number")
    try:
        number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not number.is_finite() or number < low or number > high:
        raise ValueError(f"{name} must be from {low} to {high}")
    return number


def require_location(value: Any, name: str = "location") -> Location:
    """A ``(latitude, longitude)`` pair of finite numbers within the valid ranges."""
    if not isinstance(value, tuple | list) or len(value) != 2:
        raise ValueError(f"{name} must be a (latitude, longitude) pair")
    lat, lon = value
    for part in (lat, lon):
        if isinstance(part, bool) or not isinstance(part, int | float):
            raise ValueError(f"{name} coordinates must be numbers")
        if not math.isfinite(part):
            raise ValueError(f"{name} coordinates must be finite")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"{name} is out of range")
    return (float(lat), float(lon))


def age_on(birth_date: date, today: date) -> int:
    """Completed years between ``birth_date`` and ``today``."""
    before_birthday = (today.month, today.day) < (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (1 if before_birthday else 0)
