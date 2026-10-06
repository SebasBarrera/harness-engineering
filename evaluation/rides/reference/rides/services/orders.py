"""Food order pricing and lifecycle (K1-K6, L1-L4, M4)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..core.geo import distance_km
from ..core.money import ZERO, parse_money
from ..core.validation import age_on, require_int, require_location
from ..domain.accounts import Role
from ..domain.food import WAITING_FOR_PICKUP, Order, OrderLine, OrderStatus
from ..domain.money import PaymentKind
from ..domain.pricing import courier_pay, delivery_fee, line_tax, service_fee, small_order_fee
from ..domain.records import TimerKind
from ..domain.state import idempotency_key
from .accounts import AccountService
from .context import Context
from .couriers import CourierService
from .earnings import EarningsService
from .geography import GeographyService
from .menus import MenuService
from .payments import PaymentService
from .promos import PromoService

ACCEPT_WINDOW = timedelta(minutes=5)
MAX_DELIVERY_KM = Decimal(10)
DROPOFF_RADIUS = Decimal("0.2")
ALCOHOL_AGE = 18


class OrderService:
    def __init__(
        self,
        ctx: Context,
        accounts: AccountService,
        geography: GeographyService,
        menus: MenuService,
        payments: PaymentService,
        promos: PromoService,
        couriers: CourierService,
        earnings: EarningsService,
    ) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.geography = geography
        self.menus = menus
        self.payments = payments
        self.promos = promos
        self.couriers = couriers
        self.earnings = earnings

    def get(self, order_id: Any) -> Order:
        order = self.state.orders.get(order_id) if isinstance(order_id, str) else None
        if order is None:
            raise KeyError(f"unknown order {order_id!r}")
        return order

    # --- placing (K1-K6) -----------------------------------------------------------------

    def _lookup_lines(self, lines: Any) -> None:
        """X8: unknown items or options (``KeyError``) are reported before anything else."""
        if not isinstance(lines, list | tuple):
            return
        for line in lines:
            if not isinstance(line, dict):
                continue
            item_id = line.get("item_id")
            if isinstance(item_id, str):
                self.menus.item(item_id)
            chosen = line.get("options")
            for option_id in chosen if isinstance(chosen, list | tuple) else ():
                if isinstance(option_id, str) and option_id not in self.state.options:
                    raise KeyError(f"unknown option {option_id!r}")

    def _lines(self, restaurant_id: str, lines: Any) -> list[OrderLine]:
        if not isinstance(lines, list | tuple) or not lines:
            raise ValueError("an order needs at least one line")
        built = []
        for line in lines:
            if not isinstance(line, dict) or "item_id" not in line or "quantity" not in line:
                raise ValueError("each line needs item_id and quantity")
            item = self.menus.item(line["item_id"])
            if item.restaurant_id != restaurant_id:
                raise ValueError(f"{item.id} is not on this restaurant's menu")
            if not item.available:
                raise ValueError(f"{item.id} is not available")
            quantity = require_int(line["quantity"], "quantity", 1, 20)
            chosen = line.get("options") or []
            if not isinstance(chosen, list | tuple):
                raise ValueError("options must be a list of option ids")
            if len(set(chosen)) != len(chosen):
                raise ValueError("an option is repeated")
            per_group: dict[str, int] = {group_id: 0 for group_id in item.group_ids}
            unit = item.price
            for option_id in chosen:
                option = self.state.options.get(option_id) if isinstance(option_id, str) else None
                if option is None:
                    raise KeyError(f"unknown option {option_id!r}")
                if option.group_id not in per_group:
                    raise ValueError(f"{option.id} does not belong to {item.id}")
                per_group[option.group_id] += 1
                unit += option.price_delta
            for group_id, count in per_group.items():
                group = self.state.groups[group_id]
                low = group.min_choices if group.required else 0
                if not low <= count <= group.max_choices:
                    raise ValueError(f"group {group.id} needs {low} to {group.max_choices} choices")
            amount = unit * quantity
            built.append(
                OrderLine(item.id, quantity, list(chosen), item.category, unit, amount,
                          line_tax(amount, item.category), item.prep_minutes)
            )  # fmt: skip
        return built

    def place(
        self,
        rider_id: Any,
        restaurant_id: Any,
        lines: Any,
        dropoff: Any,
        payment_method_id: Any,
        promo_code: Any = None,
        tip: Any = Decimal("0"),
        idempotency: Any = None,
    ) -> str:
        rider = self.accounts.get(rider_id)
        if isinstance(idempotency, str):
            existing = self.state.order_keys.get(idempotency_key(rider.id, idempotency))
            if existing is not None:
                return existing
        restaurant = self.accounts.get(restaurant_id)
        method = self.payments.lookup(payment_method_id)
        self._lookup_lines(lines)
        self.accounts.require_role(rider, Role.RIDER)
        self.payments.require_owner(rider.id, method)
        self.accounts.require_active(rider)
        if idempotency is not None and not isinstance(idempotency, str):
            raise ValueError("idempotency_key must be a string")
        if restaurant.role != Role.RESTAURANT:
            raise ValueError(f"{restaurant.id} is not a restaurant")
        if not self.menus.is_open(restaurant.id):
            raise ValueError("the restaurant is closed")
        built = self._lines(restaurant.id, lines)
        point = require_location(dropoff, "dropoff")
        origin = self.state.restaurants[restaurant.id].location
        self.geography.require_zone(origin, "restaurant")
        self.geography.require_zone(point, "dropoff")
        distance = distance_km(origin, point)
        if distance > MAX_DELIVERY_KM:
            raise ValueError("the dropoff is more than 10 km from the restaurant")
        if any(line.category == "alcohol" for line in built):
            birth = self.state.riders[rider.id].birth_date
            if birth is None or age_on(birth, self.ctx.now.date()) < ALCOHOL_AGE:
                raise ValueError("alcohol requires a rider of at least 18 with a birth date")
        subtotal = sum((line.amount for line in built), ZERO)
        tip_amount = parse_money(tip, "tip")
        if not ZERO <= tip_amount <= subtotal / 2:
            raise ValueError("the tip is from 0 to 50 % of the subtotal")
        code, discount = None, ZERO
        if promo_code is not None:
            code, discount = self.promos.evaluate(promo_code, "eats", rider.id, subtotal)
        fees = (service_fee(subtotal), delivery_fee(subtotal, distance), small_order_fee(subtotal))
        tax = sum((line.tax for line in built), ZERO)
        total = subtotal + sum(fees, ZERO) + tax - discount + tip_amount
        self.payments.ensure_chargeable(method, total)
        # Every check passed: from here on nothing raises.
        order_id = self.ctx.next_id("ORD")
        hold_id = self.payments.place_hold(rider.id, method.id, total, order_id)
        order = Order(
            order_id, rider.id, restaurant.id, built, point, distance, method.id, subtotal, fees[0], fees[1], fees[2],
            tax, discount, tip_amount, total, OrderStatus.PLACED, self.ctx.now, hold_id, promo_code=code,
        )  # fmt: skip
        self.state.orders[order.id] = order
        if idempotency is not None:
            self.state.order_keys[idempotency_key(rider.id, idempotency)] = order.id
        self.ctx.schedule(self.ctx.now + ACCEPT_WINDOW, TimerKind.ORDER_EXPIRY, order.id)
        self.ctx.notify(restaurant.id, "new_order", order.id)
        return order.id

    # --- restaurant side (L2) ------------------------------------------------------------

    def _own_order(self, restaurant_id: Any, order_id: Any, status: str) -> Order:
        restaurant = self.accounts.get(restaurant_id)
        order = self.get(order_id)
        self.accounts.require_role(restaurant, Role.RESTAURANT)
        if order.restaurant_id != restaurant.id:
            raise PermissionError("the order belongs to another restaurant")
        if order.status != status:
            raise ValueError(f"the order is {order.status}, not {status}")
        return order

    def accept(self, restaurant_id: Any, order_id: Any) -> None:
        order = self._own_order(restaurant_id, order_id, OrderStatus.PLACED)
        order.status = OrderStatus.ACCEPTED
        order.accepted_at = self.ctx.now
        order.ready_at = self.ctx.now + timedelta(minutes=max(line.prep_minutes for line in order.lines))
        self.ctx.notify(order.rider_id, "order_accepted", order.id)
        self.couriers.try_assign(order)

    def reject(self, restaurant_id: Any, order_id: Any, reason: Any = "") -> None:
        order = self._own_order(restaurant_id, order_id, OrderStatus.PLACED)
        self._end(order, OrderStatus.REJECTED, "order_rejected")

    def mark_ready(self, restaurant_id: Any, order_id: Any) -> None:
        order = self._own_order(restaurant_id, order_id, OrderStatus.ACCEPTED)
        order.status = OrderStatus.READY
        self.ctx.notify(order.rider_id, "order_ready", order.id)

    def expire(self, order_id: str) -> None:
        """Timer (L2): an order not accepted within 5 minutes expires."""
        order = self.state.orders.get(order_id)
        if order is not None and order.status == OrderStatus.PLACED:
            self._end(order, OrderStatus.EXPIRED, "order_expired")

    def _end(self, order: Order, status: str, notification: str) -> None:
        self.payments.release(order.hold_id)
        order.status = status
        order.cancelled_at = self.ctx.now
        self.couriers.release(order)
        self.ctx.notify(order.rider_id, notification, order.id)

    # --- courier side (L3, M4) -----------------------------------------------------------

    def _carried(self, courier_id: Any, order_id: Any, status: str) -> tuple[str, Order]:
        courier = self.accounts.get(courier_id)
        order = self.get(order_id)
        self.accounts.require_role(courier, Role.COURIER)
        if order.courier_id != courier.id:
            raise PermissionError("the order is assigned to another courier")
        if order.status != status:
            raise ValueError(f"the order is {order.status}, not {status}")
        return courier.id, order

    def pick_up(self, courier_id: Any, order_id: Any) -> None:
        courier, order = self._carried(courier_id, order_id, OrderStatus.READY)
        location = self.state.workers[courier].location
        restaurant = self.state.restaurants[order.restaurant_id].location
        if location is None or distance_km(location, restaurant) > DROPOFF_RADIUS:
            raise ValueError("the courier is not within 0.2 km of the restaurant")
        order.status = OrderStatus.PICKED_UP
        order.picked_up_at = self.ctx.now

    def deliver(self, courier_id: Any, order_id: Any) -> None:
        courier, order = self._carried(courier_id, order_id, OrderStatus.PICKED_UP)
        location = self.state.workers[courier].location
        if location is None or distance_km(location, order.dropoff) > DROPOFF_RADIUS:
            raise ValueError("the courier is not within 0.2 km of the dropoff")
        self.payments.capture(order.hold_id, order.total, PaymentKind.ORDER, order.id)
        order.charged = order.total
        order.status = OrderStatus.DELIVERED
        order.delivered_at = self.ctx.now
        self.earnings.record_trip(courier, order.id, courier_pay(order.distance_km, order.batched), order.tip, ZERO)
        self.promos.count_use(order.promo_code, order.rider_id)
        self.ctx.notify(order.rider_id, "order_delivered", order.id)
        self.couriers.release(order)

    # --- cancellation (L4) ---------------------------------------------------------------

    def cancel(self, actor_id: Any, order_id: Any, reason: Any = "") -> None:
        actor = self.accounts.get(actor_id)
        order = self.get(order_id)
        if actor.role == Role.ADMIN:
            self.accounts.require_active(actor)
            if order.finished:
                raise ValueError(f"an order in status {order.status} cannot be cancelled")
            self._end(order, OrderStatus.CANCELLED, "order_cancelled")
            self.ctx.audit(actor.id, "cancel_order", order.id)
            return
        if actor.id != order.rider_id:
            raise PermissionError("only the rider or an admin can cancel the order")
        if order.status == OrderStatus.PLACED:
            self._end(order, OrderStatus.CANCELLED, "order_cancelled")
        elif order.status in WAITING_FOR_PICKUP:
            charge = order.subtotal + order.tax
            self.payments.capture(order.hold_id, charge, PaymentKind.ORDER, order.id)
            order.charged = charge
            self._end(order, OrderStatus.CANCELLED, "order_cancelled")
        else:
            raise ValueError(f"an order in status {order.status} cannot be cancelled")

    # --- views ---------------------------------------------------------------------------

    def view(self, order_id: Any) -> dict[str, Any]:
        order = self.get(order_id)
        return {
            "id": order.id,
            "rider_id": order.rider_id,
            "restaurant_id": order.restaurant_id,
            "courier_id": order.courier_id,
            "status": str(order.status),
            "lines": [
                {"item_id": line.item_id, "quantity": line.quantity, "options": list(line.options),
                 "unit_price": line.unit_price, "amount": line.amount, "tax": line.tax}
                for line in order.lines
            ],  # fmt: skip
            "subtotal": order.subtotal,
            "service_fee": order.service_fee,
            "delivery_fee": order.delivery_fee,
            "small_order_fee": order.small_order_fee,
            "tax": order.tax,
            "discount": order.discount,
            "tip": order.tip,
            "total": order.total,
            "charged": order.charged,
            "refunded": order.refunded,
            "ready_at": order.ready_at,
        }
