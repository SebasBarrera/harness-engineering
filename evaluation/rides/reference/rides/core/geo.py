"""Geography primitives (C1, C3) and a grid index for nearby searches (V3)."""

from __future__ import annotations

import math
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from .validation import Location

EARTH_RADIUS_KM = 6371.0
_KM = Decimal("0.001")


def haversine_km(a: Location, b: Location) -> float:
    """Great-circle distance in kilometres, not rounded."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def round_km(kilometres: float) -> Decimal:
    """A float distance as Decimal rounded half up to 3 places."""
    return Decimal(repr(kilometres)).quantize(_KM, rounding=ROUND_HALF_UP)


def distance_km(a: Location, b: Location) -> Decimal:
    """C1: haversine distance as Decimal rounded half up to 3 places."""
    return round_km(haversine_km(a, b))


def route_km(points: list[Location]) -> Decimal:
    """H1: sum of the unrounded segment distances, rounded half up to 3 places."""
    total = 0.0
    for start, end in zip(points, points[1:], strict=False):
        total += haversine_km(start, end)
    return round_km(total)


def travel_minutes(distance: Decimal, speed_kmh: Decimal) -> int:
    """C3: ``ceil(distance / speed x 60)``, at least 1, computed exactly."""
    minutes = Fraction(distance) / Fraction(speed_kmh) * 60
    return max(1, math.ceil(minutes))


class GridIndex:
    """Buckets keys by latitude/longitude cell so radius queries only visit nearby cells.

    ``near`` returns a superset of the keys within the radius; callers apply the exact
    distance rule. Cells are ``cell_deg`` degrees wide; longitude wraps at 180.
    """

    def __init__(self, cell_deg: float = 0.05) -> None:
        self._cell = cell_deg
        self._columns = round(360 / cell_deg)
        self._cells: dict[tuple[int, int], set[str]] = {}
        self._where: dict[str, tuple[int, int]] = {}

    def __len__(self) -> int:
        return len(self._where)

    def __contains__(self, key: object) -> bool:
        return key in self._where

    def _wrap(self, column: int) -> int:
        half = self._columns // 2
        return (column + half) % self._columns - half

    def _cell_of(self, location: Location) -> tuple[int, int]:
        return (math.floor(location[0] / self._cell), self._wrap(math.floor(location[1] / self._cell)))

    def put(self, key: str, location: Location) -> None:
        self.discard(key)
        cell = self._cell_of(location)
        self._cells.setdefault(cell, set()).add(key)
        self._where[key] = cell

    def discard(self, key: str) -> None:
        cell = self._where.pop(key, None)
        if cell is None:
            return
        bucket = self._cells[cell]
        bucket.discard(key)
        if not bucket:
            del self._cells[cell]

    def near(self, center: Location, radius_km: float) -> Iterator[str]:
        lat, lon = center
        dlat = radius_km / 110.0 + self._cell
        lat_low, lat_high = max(-90.0, lat - dlat), min(90.0, lat + dlat)
        rows = range(math.floor(lat_low / self._cell), math.floor(lat_high / self._cell) + 1)
        cos_lat = math.cos(math.radians(max(abs(lat_low), abs(lat_high))))
        dlon = 180.0 if cos_lat < 1e-9 else radius_km / (110.0 * cos_lat) + self._cell
        if dlon >= 180.0:
            columns: set[int] | None = None
        else:
            first, last = math.floor((lon - dlon) / self._cell), math.floor((lon + dlon) / self._cell)
            columns = {self._wrap(column) for column in range(first, last + 1)}
        if columns is not None and len(rows) * len(columns) <= len(self._cells):
            for row in rows:
                for column in columns:
                    yield from self._cells.get((row, column), ())
            return
        for (row, column), bucket in self._cells.items():
            if row in rows and (columns is None or column in columns):
                yield from bucket
