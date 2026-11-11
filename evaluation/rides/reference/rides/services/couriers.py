"""Courier assignment and batching (M1-M3)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ..core.geo import distance_km
from ..domain.accounts import AccountStatus, Role, WorkerStatus
from ..domain.food import WAITING_FOR_PICKUP, Order
from .context import Context
from .fleet import FleetService

COURIER_RADIUS_KM = Decimal(6)
BIKE_MAX_KM = Decimal(4)
BATCH_WINDOW = timedelta(minutes=5)
BATCH_DROPOFF_KM = Decimal(2)


class CourierService:
    def __init__(self, ctx: Context, fleet: FleetService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.fleet = fleet
        fleet.courier_listeners.append(self.retry_waiting)

    def _can_carry(self, courier_id: str, order: Order) -> bool:
        """An active, online courier; a bike only takes dropoffs within 4 km (M1)."""
        if self.state.accounts[courier_id].status != AccountStatus.ACTIVE:
            return False
        if not self.state.workers[courier_id].online:
            return False
        return self.state.couriers[courier_id].vehicle != "bike" or order.distance_km <= BIKE_MAX_KM

    def _batch_partner(self, order: Order) -> str | None:
        """M2: a courier with exactly one assigned, not picked up order from the same
        restaurant, accepted within 5 minutes and with a dropoff within 2 km."""
        restaurant = self.state.restaurants[order.restaurant_id].location
        best: tuple[Decimal, str] | None = None
        for worker in self.state.workers.values():
            if worker.role != Role.COURIER or len(worker.order_ids) != 1:
                continue
            first = self.state.orders[worker.order_ids[0]]
            if first.restaurant_id != order.restaurant_id or first.status not in WAITING_FOR_PICKUP:
                continue
            assert first.accepted_at is not None and order.accepted_at is not None
            if abs(first.accepted_at - order.accepted_at) >= BATCH_WINDOW:  # X6: strictly within
                continue
            if distance_km(first.dropoff, order.dropoff) > BATCH_DROPOFF_KM:
                continue
            if not self._can_carry(worker.worker_id, order) or worker.location is None:
                continue
            key = (distance_km(worker.location, restaurant), worker.worker_id)
            if best is None or key < best:
                best = key
        return best[1] if best else None

    def _nearest_available(self, order: Order) -> str | None:
        """M1: nearest available active courier within 6 km of the restaurant, then lower id."""
        restaurant = self.state.restaurants[order.restaurant_id].location
        best: tuple[Decimal, str] | None = None
        for courier_id in self.fleet.couriers_near(restaurant, float(COURIER_RADIUS_KM)):
            worker = self.state.workers[courier_id]
            if worker.status != WorkerStatus.AVAILABLE or worker.location is None:
                continue
            distance = distance_km(worker.location, restaurant)
            if distance > COURIER_RADIUS_KM or not self._can_carry(courier_id, order):
                continue
            key = (distance, courier_id)
            if best is None or key < best:
                best = key
        return best[1] if best else None

    def try_assign(self, order: Order) -> bool:
        partner = self._batch_partner(order)
        courier_id = partner or self._nearest_available(order)
        if courier_id is None:
            self.ctx.idx.waiting_orders.add(order.id)
            return False
        self.ctx.idx.waiting_orders.discard(order.id)
        order.courier_id = courier_id
        order.batched = partner is not None
        self.fleet.add_order(courier_id, order.id)
        self.ctx.notify(courier_id, "order_assigned", order.id)
        return True

    def retry_waiting(self) -> None:
        """M1: retried on every advance and whenever a courier goes online or becomes free."""
        for order_id in sorted(self.ctx.idx.waiting_orders):
            order = self.state.orders[order_id]
            if order.status in WAITING_FOR_PICKUP and order.courier_id is None:
                self.try_assign(order)
            else:
                self.ctx.idx.waiting_orders.discard(order_id)

    def release(self, order: Order) -> None:
        """Take a finished or cancelled order off its courier (the courier may become free)."""
        self.ctx.idx.waiting_orders.discard(order.id)
        if order.courier_id is not None:
            self.fleet.remove_order(order.courier_id, order.id)
