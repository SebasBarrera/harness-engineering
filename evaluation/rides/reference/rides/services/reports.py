"""Admin reports and the audit log (T1-T4)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from ..core.money import ZERO, round_half_up
from ..core.validation import require_date, require_datetime, require_int
from ..domain.food import OrderStatus
from ..domain.money import PaymentKind
from ..domain.rides import RideStatus
from .accounts import AccountService
from .context import Context
from .earnings import EarningsService

CAPTURED_KINDS = (PaymentKind.RIDE, PaymentKind.ORDER, PaymentKind.CANCELLATION_FEE, PaymentKind.TIP)


class ReportService:
    def __init__(self, ctx: Context, accounts: AccountService, earnings: EarningsService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.earnings = earnings

    def daily(self, admin_id: Any, day: Any) -> dict[str, Any]:
        """T1 for ``[day 00:00, next day 00:00)``.

        ``ride_revenue``: ``charged`` of the rides completed that day. ``order_revenue``: the
        payments of kind ``order`` captured that day (deliveries and L4 cancellation charges).
        ``platform_revenue``: every amount captured that day for rides, orders and
        cancellations (kinds ride, order, cancellation_fee and tip) minus the earnings workers
        generated that day (fares, tips, fees and bonuses of the ledger).
        """
        self.accounts.admin(admin_id)
        start = datetime.combine(require_date(day, "day"), time())
        end = start + timedelta(days=1)

        def inside(moment: datetime | None) -> bool:
            return moment is not None and start <= moment < end

        rides = self.state.rides.values()
        completed = [ride for ride in rides if ride.status == RideStatus.COMPLETED and inside(ride.completed_at)]
        cancelled = [ride for ride in rides if ride.status == RideStatus.CANCELLED and inside(ride.cancelled_at)]
        delivered = [
            order for order in self.state.orders.values()
            if order.status == OrderStatus.DELIVERED and inside(order.delivered_at)
        ]  # fmt: skip
        ride_revenue = sum((ride.charged for ride in completed), ZERO)
        payments = [payment for payment in self.state.payments.values() if inside(payment.at)]
        order_revenue = sum((payment.amount for payment in payments if payment.kind == PaymentKind.ORDER), ZERO)
        captured = sum((payment.amount for payment in payments if payment.kind in CAPTURED_KINDS), ZERO)
        generated = sum((entry.total for entry in self.earnings.entries(start, end)), ZERO)
        finished = len(completed) + len(cancelled)
        rate = round_half_up(Decimal(len(cancelled)) / finished, 4) if finished else Decimal("0.0000")
        return {
            "rides_completed": len(completed),
            "rides_cancelled": len(cancelled),
            "ride_revenue": ride_revenue,
            "orders_delivered": len(delivered),
            "order_revenue": order_revenue,
            "platform_revenue": captured - generated,
            "cancellation_rate": rate,
        }

    def top_restaurants(self, admin_id: Any, start: Any, end: Any, limit: Any = 5) -> list[dict[str, Any]]:
        self.accounts.admin(admin_id)
        begin = require_datetime(start, "start")
        finish = require_datetime(end, "end")
        count = require_int(limit, "limit", 1)
        totals: dict[str, list[Any]] = {}
        for order in self.state.orders.values():
            if order.status == OrderStatus.DELIVERED and order.delivered_at and begin <= order.delivered_at < finish:
                entry = totals.setdefault(order.restaurant_id, [0, ZERO])
                entry[0] += 1
                entry[1] += order.charged
        ranked = sorted(totals.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))
        return [
            {"restaurant_id": restaurant_id, "orders": orders, "revenue": revenue}
            for restaurant_id, (orders, revenue) in ranked[:count]
        ]

    def audit_log(self, admin_id: Any) -> list[dict[str, Any]]:
        self.accounts.admin(admin_id)
        return [
            {"at": entry.at, "admin_id": entry.admin_id, "action": entry.action, "target": entry.target}
            for entry in self.state.audit
        ]
