"""``Platform``: the public facade. It validates nothing itself; it wires the services,
delegates every call and owns the lifecycle (clock, persistence)."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .core.validation import Location
from .domain.records import TimerKind
from .domain.state import State
from .persistence.sqlite_store import SqliteStore
from .services.accounts import AccountService
from .services.admin import AdminService
from .services.clock import ClockService
from .services.context import Context
from .services.couriers import CourierService
from .services.dispatch import DispatchService
from .services.earnings import EarningsService
from .services.fleet import FleetService
from .services.geography import GeographyService
from .services.menus import MenuService
from .services.orders import OrderService
from .services.payments import PaymentService
from .services.promos import PromoService
from .services.ratings import RatingService
from .services.reports import ReportService
from .services.rides import RideService
from .services.support import SupportService

PathLike = str | os.PathLike[str]


class Platform:
    """Ride-hailing and food delivery backend. See SPEC.md for the rules."""

    # --- lifecycle, clock and storage (X4, U1, U2) ---------------------------------------

    def __init__(self, start: datetime, db_path: PathLike | None = None) -> None:
        if not isinstance(start, datetime):
            raise ValueError("start must be a datetime")
        self._wire(State(now=start))
        self._store = SqliteStore(db_path) if db_path is not None else None
        if self._store is not None:
            self._store.create(self._state)

    @classmethod
    def open(cls, db_path: PathLike) -> Platform:
        store = SqliteStore(db_path)
        platform = cls.__new__(cls)
        platform._wire(store.load())
        platform._store = store
        return platform

    def save(self) -> None:
        if self._store is not None:
            self._store.save(self._state)

    def now(self) -> datetime:
        return self._state.now

    def advance(self, minutes: float = 0, seconds: float = 0) -> None:
        self._clock.advance(minutes, seconds)

    def _wire(self, state: State) -> None:
        self._state = state
        ctx = self._ctx = Context(state)
        self._accounts = AccountService(ctx)
        self._admin_geo = GeographyService(ctx, self._accounts)
        self._fleet = FleetService(ctx, self._accounts, self._admin_geo)
        self._admin = AdminService(ctx, self._accounts, self._fleet)
        self._payments = PaymentService(ctx, self._accounts)
        self._promos = PromoService(ctx, self._accounts, self._payments)
        self._ratings = RatingService(ctx, self._accounts)
        self._earnings = EarningsService(ctx, self._accounts)
        self._ratings.review_listeners.append(self._fleet.take_offline_when_idle)
        self._dispatch = DispatchService(ctx, self._fleet, self._payments, self._ratings)
        self._rides = RideService(
            ctx, self._accounts, self._admin_geo, self._fleet, self._payments, self._promos, self._dispatch,
            self._earnings,
        )  # fmt: skip
        self._menus = MenuService(ctx, self._accounts)
        self._couriers = CourierService(ctx, self._fleet)
        self._orders = OrderService(
            ctx, self._accounts, self._admin_geo, self._menus, self._payments, self._promos, self._couriers,
            self._earnings,
        )  # fmt: skip
        self._support = SupportService(ctx, self._accounts, self._payments)
        self._reports = ReportService(ctx, self._accounts, self._earnings)
        self._clock = ClockService(ctx)
        self._clock.on(TimerKind.OFFER_EXPIRY, lambda t: self._dispatch.expire_offer(t.ref, t.token))
        self._clock.on(TimerKind.SCHEDULED_DISPATCH, lambda t: self._rides.dispatch_scheduled(t.ref))
        self._clock.on(TimerKind.ORDER_EXPIRY, lambda t: self._orders.expire(t.ref))
        self._clock.after_advance.append(self._couriers.retry_waiting)
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        """Derive every lookup structure from the state (after ``open``)."""
        state, idx = self._state, self._ctx.idx
        for account in state.accounts.values():
            idx.emails[account.email] = account.id
            idx.phones[account.phone] = account.id
        for vehicle in state.vehicles.values():
            idx.plates[vehicle.plate] = vehicle.id
        for ride in state.rides.values():
            idx.rides_by_rider.setdefault(ride.rider_id, []).append(ride.id)
            if ride.status == "requested":
                idx.requested_by_zone.setdefault(ride.pickup_zone_id, set()).add(ride.id)
                if ride.offered_to is not None:
                    idx.offers_by_driver.setdefault(ride.offered_to, set()).add(ride.id)
        for order in state.orders.values():
            if order.status in ("accepted", "ready") and order.courier_id is None:
                idx.waiting_orders.add(order.id)
        for token, ride_id in state.share_tokens.items():
            idx.share_by_ride[ride_id] = token
        self._fleet.reindex_all()

    # --- accounts (A) --------------------------------------------------------------------

    def create_admin(self, name: str, email: str, phone: str, password: str) -> str:
        return self._accounts.create_admin(name, email, phone, password)

    def register_rider(
        self,
        name: str,
        email: str,
        phone: str,
        password: str,
        birth_date: date | None = None,
        referral_code: str | None = None,
    ) -> str:
        return self._accounts.register_rider(name, email, phone, password, birth_date, referral_code)

    def register_driver(
        self, name: str, email: str, phone: str, password: str, license_number: str, license_expires: date
    ) -> str:
        return self._accounts.register_driver(name, email, phone, password, license_number, license_expires)

    def register_courier(self, name: str, email: str, phone: str, password: str, vehicle: str) -> str:
        return self._accounts.register_courier(name, email, phone, password, vehicle)

    def register_restaurant(self, name: str, email: str, phone: str, password: str, location: Location) -> str:
        return self._accounts.register_restaurant(name, email, phone, password, location)

    def authenticate(self, email: str, password: str) -> str:
        return self._accounts.authenticate(email, password)

    def get_account(self, account_id: str) -> dict[str, Any]:
        return self._accounts.view(account_id)

    def referral_code(self, rider_id: str) -> str:
        return self._accounts.referral_code(rider_id)

    def suspend(self, admin_id: str, account_id: str, reason: str) -> None:
        self._admin.suspend(admin_id, account_id, reason)

    def reactivate(self, admin_id: str, account_id: str) -> None:
        self._admin.reactivate(admin_id, account_id)

    # --- drivers, couriers and vehicles (B) ----------------------------------------------

    def add_vehicle(
        self, driver_id: str, plate: str, make: str, model: str, year: int, seats: int, category: str
    ) -> str:
        return self._fleet.add_vehicle(driver_id, plate, make, model, year, seats, category)

    def approve(self, admin_id: str, account_id: str) -> None:
        self._admin.approve(admin_id, account_id)

    def go_online(self, account_id: str, location: Location, vehicle_id: str | None = None) -> None:
        self._fleet.go_online(account_id, location, vehicle_id)

    def go_offline(self, account_id: str) -> None:
        self._fleet.go_offline(account_id)

    def update_location(self, account_id: str, location: Location) -> None:
        self._fleet.update_location(account_id, location)

    def worker_status(self, account_id: str) -> dict[str, Any]:
        return self._fleet.status(account_id)

    # --- geography (C) -------------------------------------------------------------------

    def add_zone(
        self,
        admin_id: str,
        name: str,
        center: Location,
        radius_km: float | Decimal,
        speed_kmh: float | Decimal,
        airport: bool = False,
    ) -> str:
        return self._admin_geo.add_zone(admin_id, name, center, radius_km, speed_kmh, airport)

    def distance_km(self, a: Location, b: Location) -> Decimal:
        return self._admin_geo.distance(a, b)

    def eta_minutes(self, a: Location, b: Location) -> int:
        return self._admin_geo.eta_minutes(a, b)

    def set_surge_cap(self, admin_id: str, zone_id: str, cap: float | Decimal) -> None:
        self._admin_geo.set_surge_cap(admin_id, zone_id, cap)

    # --- rides (D-I, R3) -----------------------------------------------------------------

    def quote_ride(self, rider_id: str, pickup: Location, dropoff: Location, category: str) -> dict[str, Any]:
        return self._rides.quote(rider_id, pickup, dropoff, category)

    def request_ride(
        self,
        rider_id: str,
        quote_id: str,
        payment_method_id: str,
        promo_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        return self._rides.request(rider_id, quote_id, payment_method_id, promo_code, idempotency_key)

    def schedule_ride(
        self,
        rider_id: str,
        pickup: Location,
        dropoff: Location,
        category: str,
        pickup_at: datetime,
        payment_method_id: str,
    ) -> str:
        return self._rides.schedule(rider_id, pickup, dropoff, category, pickup_at, payment_method_id)

    def accept_ride(self, driver_id: str, ride_id: str) -> None:
        self._rides.accept(driver_id, ride_id)

    def decline_ride(self, driver_id: str, ride_id: str) -> None:
        self._rides.decline(driver_id, ride_id)

    def driver_arrived(self, driver_id: str, ride_id: str) -> None:
        self._rides.arrived(driver_id, ride_id)

    def start_ride(self, driver_id: str, ride_id: str) -> None:
        self._rides.start(driver_id, ride_id)

    def complete_ride(self, driver_id: str, ride_id: str, route: list[Location]) -> None:
        self._rides.complete(driver_id, ride_id, route)

    def cancel_ride(self, actor_id: str, ride_id: str, reason: str = "") -> None:
        self._rides.cancel(actor_id, ride_id, reason)

    def ride(self, ride_id: str) -> dict[str, Any]:
        return self._rides.view(ride_id)

    def add_tip(self, rider_id: str, ride_id: str, amount: int | str | Decimal) -> None:
        self._rides.add_tip(rider_id, ride_id, amount)

    def share_trip(self, rider_id: str, ride_id: str) -> str:
        return self._rides.share(rider_id, ride_id)

    def trip_status(self, token: str) -> dict[str, Any]:
        return self._rides.trip_status(token)

    # --- restaurants and menus (J) -------------------------------------------------------

    def set_hours(self, restaurant_id: str, weekday: int, opens: str, closes: str) -> None:
        self._menus.set_hours(restaurant_id, weekday, opens, closes)

    def is_open(self, restaurant_id: str) -> bool:
        return self._menus.is_open(restaurant_id)

    def add_menu_item(
        self, restaurant_id: str, name: str, price: int | str | Decimal, category: str, prep_minutes: int
    ) -> str:
        return self._menus.add_item(restaurant_id, name, price, category, prep_minutes)

    def add_option_group(self, item_id: str, name: str, required: bool, min_choices: int, max_choices: int) -> str:
        return self._menus.add_group(item_id, name, required, min_choices, max_choices)

    def add_option(self, group_id: str, name: str, price_delta: int | str | Decimal) -> str:
        return self._menus.add_option(group_id, name, price_delta)

    def set_item_available(self, restaurant_id: str, item_id: str, available: bool) -> None:
        self._menus.set_available(restaurant_id, item_id, available)

    def menu(self, restaurant_id: str) -> list[dict[str, Any]]:
        return self._menus.menu(restaurant_id)

    # --- food orders (K-M) ---------------------------------------------------------------

    def place_order(
        self,
        rider_id: str,
        restaurant_id: str,
        lines: list[dict[str, Any]],
        dropoff: Location,
        payment_method_id: str,
        promo_code: str | None = None,
        tip: int | str | Decimal = Decimal("0"),
        idempotency_key: str | None = None,
    ) -> str:
        return self._orders.place(
            rider_id, restaurant_id, lines, dropoff, payment_method_id, promo_code, tip, idempotency_key
        )

    def accept_order(self, restaurant_id: str, order_id: str) -> None:
        self._orders.accept(restaurant_id, order_id)

    def reject_order(self, restaurant_id: str, order_id: str, reason: str = "") -> None:
        self._orders.reject(restaurant_id, order_id, reason)

    def mark_ready(self, restaurant_id: str, order_id: str) -> None:
        self._orders.mark_ready(restaurant_id, order_id)

    def pick_up(self, courier_id: str, order_id: str) -> None:
        self._orders.pick_up(courier_id, order_id)

    def deliver(self, courier_id: str, order_id: str) -> None:
        self._orders.deliver(courier_id, order_id)

    def cancel_order(self, actor_id: str, order_id: str, reason: str = "") -> None:
        self._orders.cancel(actor_id, order_id, reason)

    def order(self, order_id: str) -> dict[str, Any]:
        return self._orders.view(order_id)

    # --- promotions (N) ------------------------------------------------------------------

    def create_promo(
        self,
        admin_id: str,
        code: str,
        service: str,
        kind: str,
        value: int | str | Decimal,
        max_discount: int | str | Decimal | None = None,
        min_spend: int | str | Decimal = Decimal("0"),
        starts: datetime | None = None,
        ends: datetime | None = None,
        max_total_uses: int | None = None,
        max_uses_per_user: int | None = None,
        first_order_only: bool = False,
    ) -> None:
        self._promos.create(
            admin_id, code, service, kind, value, max_discount, min_spend, starts, ends, max_total_uses,
            max_uses_per_user, first_order_only,
        )  # fmt: skip

    # --- payments and wallet (O) ---------------------------------------------------------

    def add_card(self, rider_id: str, number: str, exp_month: int, exp_year: int, cvc: str) -> str:
        return self._payments.add_card(rider_id, number, exp_month, exp_year, cvc)

    def payment_methods(self, rider_id: str) -> list[dict[str, Any]]:
        return self._payments.methods(rider_id)

    def wallet_balance(self, rider_id: str) -> Decimal:
        return self._payments.balance(rider_id)

    def top_up_wallet(self, rider_id: str, card_id: str, amount: int | str | Decimal) -> None:
        self._payments.top_up(rider_id, card_id, amount)

    def payments(self, account_id: str) -> list[dict[str, Any]]:
        return self._payments.history(account_id)

    # --- earnings, ratings, support, notifications, reports, audit (P-T) -----------------

    def earnings(self, worker_id: str, start: datetime, end: datetime) -> dict[str, Any]:
        return self._earnings.earnings(worker_id, start, end)

    def run_payouts(self, admin_id: str, week_start: date) -> dict[str, Decimal]:
        return self._earnings.run_payouts(admin_id, week_start)

    def rate(self, actor_id: str, ref_id: str, stars: int, comment: str = "", target: str = "driver") -> None:
        self._ratings.rate(actor_id, ref_id, stars, comment, target)

    def rating(self, account_id: str) -> Decimal | None:
        return self._ratings.rating(account_id)

    def report_issue(self, actor_id: str, ref_id: str, kind: str, text: str) -> str:
        return self._support.report(actor_id, ref_id, kind, text)

    def resolve_ticket(self, admin_id: str, ticket_id: str, refund_amount: int | str | Decimal = Decimal("0")) -> None:
        self._support.resolve(admin_id, ticket_id, refund_amount)

    def notifications(self, account_id: str) -> list[dict[str, Any]]:
        account = self._accounts.get(account_id)
        return [
            {"at": item.at, "kind": item.kind, "ref": item.ref}
            for item in self._state.notifications.get(account.id, [])
        ]

    def daily_report(self, admin_id: str, day: date) -> dict[str, Any]:
        return self._reports.daily(admin_id, day)

    def top_restaurants(self, admin_id: str, start: datetime, end: datetime, limit: int = 5) -> list[dict[str, Any]]:
        return self._reports.top_restaurants(admin_id, start, end, limit)

    def audit_log(self, admin_id: str) -> list[dict[str, Any]]:
        return self._reports.audit_log(admin_id)


__all__ = ["Platform"]
