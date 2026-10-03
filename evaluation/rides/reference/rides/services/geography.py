"""Zones, distances and travel times (C1-C3, E2 caps)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from ..core.geo import distance_km, travel_minutes
from ..core.validation import Location, require_bool, require_location, require_number, require_text
from ..domain.fleet import Zone
from .accounts import AccountService
from .context import Context

DEFAULT_SPEED_KMH = Decimal(30)


class GeographyService:
    def __init__(self, ctx: Context, accounts: AccountService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.zone_listeners: list[Callable[[], None]] = []

    def add_zone(
        self, admin_id: Any, name: Any, center: Any, radius_km: Any, speed_kmh: Any, airport: Any = False
    ) -> str:
        admin = self.accounts.admin(admin_id)
        clean_name = require_text(name, "name")
        point = require_location(center, "center")
        radius = require_number(radius_km, "radius_km", Decimal("0.5"), Decimal(50))
        speed = require_number(speed_kmh, "speed_kmh", Decimal(5), Decimal(120))
        require_bool(airport, "airport")
        zone = Zone(self.ctx.next_id("ZON"), clean_name, point, radius, speed, airport)
        self.state.zones[zone.id] = zone
        self.ctx.audit(admin.id, "add_zone", zone.id)
        for listener in self.zone_listeners:
            listener()
        return zone.id

    def set_surge_cap(self, admin_id: Any, zone_id: Any, cap: Any) -> None:
        admin = self.accounts.get(admin_id)
        zone = self.zone(zone_id)
        self.accounts.require_admin(admin)
        zone.surge_cap = require_number(cap, "cap", Decimal("1.0"), Decimal("3.0"))
        self.ctx.audit(admin.id, "set_surge_cap", zone.id)

    def zone(self, zone_id: Any) -> Zone:
        zone = self.state.zones.get(zone_id) if isinstance(zone_id, str) else None
        if zone is None:
            raise KeyError(f"unknown zone {zone_id!r}")
        return zone

    def zone_of(self, location: Location) -> Zone | None:
        """C2: the first zone, in creation order, whose center is at most ``radius_km`` away."""
        for zone in self.state.zones.values():
            if distance_km(zone.center, location) <= zone.radius_km:
                return zone
        return None

    def require_zone(self, location: Location, what: str) -> Zone:
        zone = self.zone_of(location)
        if zone is None:
            raise ValueError(f"{what} is outside every zone")
        return zone

    def distance(self, a: Any, b: Any) -> Decimal:
        return distance_km(require_location(a, "a"), require_location(b, "b"))

    def eta(self, a: Location, b: Location) -> int:
        """C3, using the speed of the zone of ``a`` (30 km/h outside every zone)."""
        zone = self.zone_of(a)
        return travel_minutes(distance_km(a, b), zone.speed_kmh if zone else DEFAULT_SPEED_KMH)

    def eta_minutes(self, a: Any, b: Any) -> int:
        return self.eta(require_location(a, "a"), require_location(b, "b"))
