"""Vehicles and the online state of workers (B1, B3-B5), with the indexes used for dispatch
and surge (E1, F2, M1, V3)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

from ..core.geo import GridIndex
from ..core.validation import Location, require_choice, require_int, require_location, require_text
from ..domain.accounts import WORKER_ROLES, Role, WorkerState, WorkerStatus
from ..domain.fleet import CATEGORIES, COMFORT_MIN_YEAR, MIN_YEAR, SEATS, Category, Vehicle
from .accounts import AccountService
from .context import Context
from .geography import GeographyService

PLATE_RE = re.compile(r"[A-Z]{3}\d{3}")
DRIVER_CANCELLATION_LIMIT = 3
DRIVER_CANCELLATION_WINDOW = timedelta(hours=24)
DRIVER_BAN = timedelta(hours=12)


class FleetService:
    def __init__(self, ctx: Context, accounts: AccountService, geography: GeographyService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.geography = geography
        # Observers wired by the platform: a driver leaving with pending offers, a courier
        # becoming available.
        self.offline_listeners: list[Callable[[str], None]] = []
        self.courier_listeners: list[Callable[[], None]] = []
        geography.zone_listeners.append(self.reindex_all)

    # --- vehicles (B1) -------------------------------------------------------------------

    def add_vehicle(
        self, driver_id: Any, plate: Any, make: Any, model: Any, year: Any, seats: Any, category: Any
    ) -> str:
        driver = self.accounts.actor(driver_id, Role.DRIVER, active=False)
        if not isinstance(plate, str) or PLATE_RE.fullmatch(plate) is None:
            raise ValueError("plate must be three letters and three digits")
        if plate in self.ctx.idx.plates:
            raise ValueError("plate already registered")
        clean_make = require_text(make, "make")
        clean_model = require_text(model, "model")
        require_int(year, "year", MIN_YEAR, self.ctx.now.year + 1)
        require_choice(category, CATEGORIES, "category")
        low, high = SEATS[Category(category)]
        require_int(seats, "seats", low, high)
        if category == Category.COMFORT and year < COMFORT_MIN_YEAR:
            raise ValueError("comfort vehicles must be from 2018 or later")
        vehicle = Vehicle(self.ctx.next_id("VEH"), driver.id, plate, clean_make, clean_model, year, seats, category)
        self.state.vehicles[vehicle.id] = vehicle
        self.state.drivers[driver.id].vehicle_ids.append(vehicle.id)
        self.ctx.idx.plates[plate] = vehicle.id
        return vehicle.id

    def vehicle(self, vehicle_id: Any) -> Vehicle:
        vehicle = self.state.vehicles.get(vehicle_id) if isinstance(vehicle_id, str) else None
        if vehicle is None:
            raise KeyError(f"unknown vehicle {vehicle_id!r}")
        return vehicle

    # --- online state (B3-B5) ------------------------------------------------------------

    def worker(self, account_id: Any) -> WorkerState:
        account = self.accounts.get(account_id)
        if account.role not in WORKER_ROLES:
            raise ValueError(f"{account.id} is not a driver or courier")
        return self.state.workers[account.id]

    def go_online(self, account_id: Any, location: Any, vehicle_id: Any = None) -> None:
        account = self.accounts.get(account_id)
        is_driver = account.role == Role.DRIVER
        vehicle = self.vehicle(vehicle_id) if vehicle_id is not None else None
        self.accounts.require_role(account, Role.DRIVER, Role.COURIER)
        if is_driver and vehicle is not None and vehicle.driver_id != account.id:
            raise PermissionError("the vehicle belongs to another driver")
        self.accounts.require_active(account)
        point = require_location(location)
        worker = self.state.workers[account.id]
        if worker.busy:
            raise ValueError("a busy worker cannot go online again")
        if is_driver:
            if vehicle is None:
                raise ValueError("drivers must give a vehicle")
            profile = self.state.drivers[account.id]
            if not profile.license_valid(self.ctx.now.date()):
                raise ValueError("the driver license has expired")
            if profile.banned_until is not None and self.ctx.now < profile.banned_until:
                raise ValueError("the driver cannot go online yet after 3 cancellations")
        elif vehicle_id is not None:
            raise ValueError("couriers do not give a vehicle")
        worker.online = True
        worker.location = point
        worker.vehicle_id = vehicle_id
        self.refresh(worker)
        if account.role == Role.COURIER:
            self._courier_available()

    def go_offline(self, account_id: Any) -> None:
        account = self.accounts.actor(account_id, Role.DRIVER, Role.COURIER, active=False)
        worker = self.state.workers[account.id]
        if worker.busy:
            raise ValueError("a worker with an active ride or order cannot go offline")
        self.force_offline(account.id)

    def force_offline(self, worker_id: str) -> None:
        """Take a worker offline (B4, A5 suspension, G4); pending offers move on."""
        worker = self.state.workers[worker_id]
        worker.online = False
        worker.location = None
        worker.vehicle_id = None
        self.refresh(worker)
        for listener in self.offline_listeners:
            listener(worker_id)

    def update_location(self, account_id: Any, location: Any) -> None:
        account = self.accounts.actor(account_id, Role.DRIVER, Role.COURIER, active=False)
        point = require_location(location)
        worker = self.state.workers[account.id]
        if not worker.online:
            raise ValueError("the worker is offline")
        worker.location = point
        self.refresh(worker)

    def status(self, account_id: Any) -> dict[str, Any]:
        worker = self.worker(account_id)
        return {"status": str(worker.status), "location": worker.location, "vehicle_id": worker.vehicle_id}

    def record_driver_cancellation(self, driver_id: str) -> None:
        """G4: a third cancellation within 24 hours forces the driver offline for 12 hours."""
        profile = self.state.drivers[driver_id]
        now = self.ctx.now
        profile.cancellations.append(now)
        recent = [moment for moment in profile.cancellations if now - moment < DRIVER_CANCELLATION_WINDOW]
        if len(recent) >= DRIVER_CANCELLATION_LIMIT:
            profile.banned_until = now + DRIVER_BAN
            self.force_offline(driver_id)

    # --- assignment hooks ----------------------------------------------------------------

    def set_ride(self, driver_id: str, ride_id: str | None) -> None:
        """Assign or free a driver; a driver freed while not active (Q3) goes offline."""
        worker = self.state.workers[driver_id]
        worker.ride_id = ride_id
        self.refresh(worker)
        if ride_id is None and worker.online and not self.state.accounts[driver_id].is_active:
            self.force_offline(driver_id)

    def take_offline_when_idle(self, worker_id: str) -> None:
        """Q3: a driver under review goes offline now, or when its ride ends."""
        worker = self.state.workers[worker_id]
        if worker.online and not worker.busy:
            self.force_offline(worker_id)

    def add_order(self, courier_id: str, order_id: str) -> None:
        worker = self.state.workers[courier_id]
        worker.order_ids.append(order_id)
        self.refresh(worker)

    def remove_order(self, courier_id: str, order_id: str) -> None:
        worker = self.state.workers[courier_id]
        if order_id in worker.order_ids:
            worker.order_ids.remove(order_id)
        self.refresh(worker)
        if worker.status == WorkerStatus.AVAILABLE:
            self._courier_available()

    def _courier_available(self) -> None:
        for listener in self.courier_listeners:
            listener()

    # --- indexes -------------------------------------------------------------------------

    def refresh_id(self, worker_id: str) -> None:
        self.refresh(self.state.workers[worker_id])

    def refresh(self, worker: WorkerState) -> None:
        """Keep the grid and supply indexes equal to the set of ``available`` workers that
        hold no pending ride offer (E1, F2)."""
        idx = self.ctx.idx
        previous = idx.indexed_worker.pop(worker.worker_id, None)
        if previous is not None:
            category, zone_id = previous
            if category is None:
                idx.courier_grid.discard(worker.worker_id)
            else:
                idx.driver_grid[category].discard(worker.worker_id)
                if zone_id is not None:
                    idx.supply[(zone_id, category)].discard(worker.worker_id)
        if worker.status != WorkerStatus.AVAILABLE or worker.location is None:
            return
        if idx.offers_by_driver.get(worker.worker_id):
            return
        if worker.role == Role.COURIER:
            idx.courier_grid.put(worker.worker_id, worker.location)
            idx.indexed_worker[worker.worker_id] = (None, None)
            return
        category = self.state.vehicles[worker.vehicle_id].category if worker.vehicle_id else None
        if category is None:
            return
        zone = self.geography.zone_of(worker.location)
        idx.driver_grid.setdefault(category, GridIndex()).put(worker.worker_id, worker.location)
        if zone is not None:
            idx.supply.setdefault((zone.id, category), set()).add(worker.worker_id)
        idx.indexed_worker[worker.worker_id] = (category, zone.id if zone else None)

    def reindex_all(self) -> None:
        for worker in self.state.workers.values():
            self.refresh(worker)

    def supply(self, zone_id: str, category: str) -> int:
        """E1: available drivers in the zone whose vehicle has the category."""
        return len(self.ctx.idx.supply.get((zone_id, category), ()))

    def drivers_near(self, category: str, center: Location, radius_km: float) -> Iterator[str]:
        grid = self.ctx.idx.driver_grid.get(category)
        return grid.near(center, radius_km) if grid else iter(())

    def couriers_near(self, center: Location, radius_km: float) -> Iterator[str]:
        return self.ctx.idx.courier_grid.near(center, radius_km)
