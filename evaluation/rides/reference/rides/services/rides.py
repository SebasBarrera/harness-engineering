"""Quotes, ride requests, the ride lifecycle, cancellations, scheduled rides, tips and trip
sharing (D, E, F, G, H, I, R3)."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..core.geo import distance_km, route_km
from ..core.money import ZERO, parse_money
from ..core.validation import Location, require_choice, require_datetime, require_location
from ..domain.accounts import Role
from ..domain.fleet import CATEGORIES
from ..domain.money import PaymentKind
from ..domain.pricing import NO_SURGE, driver_fare_share, is_night, ride_fare, surge_multiplier
from ..domain.pricing import wait_fee as compute_wait_fee
from ..domain.records import TimerKind
from ..domain.rides import ASSIGNED_RIDE, Quote, Ride, RideStatus
from ..domain.state import idempotency_key
from .accounts import AccountService
from .context import Context
from .dispatch import DispatchService
from .earnings import EarningsService
from .fleet import FleetService
from .geography import GeographyService
from .payments import PaymentService
from .promos import PromoService

BLOCKING_RIDE = (RideStatus.REQUESTED, *ASSIGNED_RIDE)
QUOTE_TTL = timedelta(minutes=5)
MIN_DISTANCE = Decimal("0.2")
MAX_DISTANCE = Decimal(150)
ARRIVAL_RADIUS = Decimal("0.2")
FREE_CANCEL_WINDOW = timedelta(minutes=2)
CANCEL_FEES = {"economy": Decimal("3.00"), "moto": Decimal("3.00"), "comfort": Decimal("5.00"), "xl": Decimal("5.00")}
RIDER_FEE_CANCEL_LIMIT = 3
RIDER_BAN = timedelta(hours=24)
SCHEDULE_MIN = timedelta(minutes=30)
SCHEDULE_MAX = timedelta(days=30)
DISPATCH_LEAD = timedelta(minutes=10)
FREE_SCHEDULED_CANCEL = timedelta(minutes=60)
SCHEDULED_CANCEL_FEE = Decimal("5.00")
RECOMPUTE_FACTOR = Decimal("1.2")
TIP_WINDOW = timedelta(hours=24)
MIN_TIP, MAX_TIP = Decimal("1.00"), Decimal("100.00")


class RideService:
    def __init__(
        self,
        ctx: Context,
        accounts: AccountService,
        geography: GeographyService,
        fleet: FleetService,
        payments: PaymentService,
        promos: PromoService,
        dispatch: DispatchService,
        earnings: EarningsService,
    ) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.geography = geography
        self.fleet = fleet
        self.payments = payments
        self.promos = promos
        self.dispatch = dispatch
        self.earnings = earnings

    def get(self, ride_id: Any) -> Ride:
        ride = self.state.rides.get(ride_id) if isinstance(ride_id, str) else None
        if ride is None:
            raise KeyError(f"unknown ride {ride_id!r}")
        return ride

    # --- quotes (D1-D3, E1-E3) -----------------------------------------------------------

    def _trip(self, pickup: Any, dropoff: Any, category: Any) -> tuple[Location, Location, Any, Decimal, int, bool]:
        """Validate a trip; return points, pickup zone, distance, minutes and airport flag."""
        start = require_location(pickup, "pickup")
        end = require_location(dropoff, "dropoff")
        require_choice(category, CATEGORIES, "category")
        pickup_zone = self.geography.require_zone(start, "pickup")
        dropoff_zone = self.geography.require_zone(end, "dropoff")
        distance = distance_km(start, end)
        if not MIN_DISTANCE <= distance <= MAX_DISTANCE:
            raise ValueError("the trip must be from 0.2 to 150 km")
        minutes = self.geography.eta(start, end)
        return start, end, pickup_zone, distance, minutes, pickup_zone.airport or dropoff_zone.airport

    def quote(self, rider_id: Any, pickup: Any, dropoff: Any, category: Any) -> dict[str, Any]:
        rider = self.accounts.actor(rider_id, Role.RIDER)
        start, end, zone, distance, minutes, airport = self._trip(pickup, dropoff, category)
        surge = surge_multiplier(self.dispatch.demand(zone.id), self.fleet.supply(zone.id, category), zone.surge_cap)
        night = is_night(self.ctx.now)
        fare = ride_fare(category, distance, minutes, night=night, surge=surge, airport=airport)
        quote = Quote(
            self.ctx.next_id("QTE"), rider.id, category, start, end, zone.id, distance, minutes, surge, night,
            airport, fare, self.ctx.now, self.ctx.now + QUOTE_TTL,
        )  # fmt: skip
        self.state.quotes[quote.id] = quote
        return {
            "quote_id": quote.id,
            "category": quote.category,
            "distance_km": quote.distance_km,
            "duration_min": quote.duration_min,
            "surge": quote.surge,
            "fare": quote.fare,
            "expires_at": quote.expires_at,
        }

    # --- requests (F1, F5, G4, N2, O3) ---------------------------------------------------

    def _check_rider_ban(self, rider_id: str) -> None:
        """G4: three fee cancellations within 24 hours block requests for 24 hours."""
        times = self.state.riders[rider_id].fee_cancellations
        now = self.ctx.now
        for index in range(RIDER_FEE_CANCEL_LIMIT - 1, len(times)):
            third = times[index]
            if third - times[index - RIDER_FEE_CANCEL_LIMIT + 1] < RIDER_BAN and now - third < RIDER_BAN:
                raise ValueError("the rider cannot request rides for 24 hours after 3 paid cancellations")

    def request(
        self, rider_id: Any, quote_id: Any, payment_method_id: Any, promo_code: Any = None, idempotency: Any = None
    ) -> str:
        rider = self.accounts.get(rider_id)
        if isinstance(idempotency, str):
            existing = self.state.ride_keys.get(idempotency_key(rider.id, idempotency))
            if existing is not None:
                return existing
        quote = self.state.quotes.get(quote_id) if isinstance(quote_id, str) else None
        if quote is None:
            raise KeyError(f"unknown quote {quote_id!r}")
        method = self.payments.lookup(payment_method_id)
        self.accounts.require_role(rider, Role.RIDER)
        self.payments.require_owner(rider.id, method)
        self.accounts.require_active(rider)
        if idempotency is not None and not isinstance(idempotency, str):
            raise ValueError("idempotency_key must be a string")
        self._check_rider_ban(rider.id)
        for ride_id in self.ctx.idx.rides_by_rider.get(rider.id, []):
            if self.state.rides[ride_id].status in BLOCKING_RIDE:
                raise ValueError("the rider has another ride in course")
        if quote.rider_id != rider.id:
            raise ValueError("the quote belongs to another rider")
        if quote.used:
            raise ValueError("the quote was already used")
        if self.ctx.now >= quote.expires_at:
            raise ValueError("the quote has expired")
        code, discount = None, ZERO
        if promo_code is not None:
            code, discount = self.promos.evaluate(promo_code, "rides", rider.id, quote.fare)
        self.payments.ensure_chargeable(method, quote.fare)
        # Every check passed: from here on nothing raises.
        ride = Ride(
            self.ctx.next_id("RDE"), rider.id, quote.category, quote.pickup, quote.dropoff, quote.pickup_zone_id,
            method.id, quote.id, quote.distance_km, quote.duration_min, quote.surge, quote.night, quote.airport,
            quote.fare, RideStatus.REQUESTED, self.ctx.now, promo_code=code, discount=discount,
        )  # fmt: skip
        quote.used = True
        ride.hold_id = self.payments.place_hold(rider.id, method.id, quote.fare, ride.id)
        self._store(ride)
        if idempotency is not None:
            self.state.ride_keys[idempotency_key(rider.id, idempotency)] = ride.id
        self.dispatch.set_status(ride, RideStatus.REQUESTED)
        self.dispatch.offer_next(ride)
        return ride.id

    def _store(self, ride: Ride) -> None:
        self.state.rides[ride.id] = ride
        self.ctx.idx.rides_by_rider.setdefault(ride.rider_id, []).append(ride.id)

    # --- scheduled rides (I1-I3) ---------------------------------------------------------

    def schedule(
        self, rider_id: Any, pickup: Any, dropoff: Any, category: Any, pickup_at: Any, payment_method_id: Any
    ) -> str:
        rider = self.accounts.get(rider_id)
        method = self.payments.lookup(payment_method_id)
        self.accounts.require_role(rider, Role.RIDER)
        self.payments.require_owner(rider.id, method)
        self.accounts.require_active(rider)
        when = require_datetime(pickup_at, "pickup_at")
        if not self.ctx.now + SCHEDULE_MIN <= when <= self.ctx.now + SCHEDULE_MAX:
            raise ValueError("pickup_at must be from 30 minutes to 30 days ahead")
        self._check_rider_ban(rider.id)
        start, end, zone, distance, minutes, airport = self._trip(pickup, dropoff, category)
        night = is_night(when)
        fare = ride_fare(category, distance, minutes, night=night, surge=NO_SURGE, airport=airport)
        ride = Ride(
            self.ctx.next_id("RDE"), rider.id, category, start, end, zone.id, method.id, None, distance, minutes,
            NO_SURGE, night, airport, fare, RideStatus.SCHEDULED, self.ctx.now, pickup_at=when,
        )  # fmt: skip
        self._store(ride)
        self.dispatch.set_status(ride, RideStatus.SCHEDULED)
        self.ctx.schedule(when - DISPATCH_LEAD, TimerKind.SCHEDULED_DISPATCH, ride.id)
        return ride.id

    def dispatch_scheduled(self, ride_id: str) -> None:
        """Timer (I2): place the hold and offer the ride, or cancel it if the hold fails."""
        ride = self.state.rides.get(ride_id)
        if ride is None or ride.status != RideStatus.SCHEDULED:
            return
        try:
            ride.hold_id = self.payments.place_hold(ride.rider_id, ride.payment_method_id, ride.quoted_fare, ride.id)
        except ValueError:
            ride.cancelled_at = self.ctx.now
            ride.cancel_reason = "payment hold failed"
            self.dispatch.set_status(ride, RideStatus.CANCELLED)
            self.ctx.notify(ride.rider_id, "ride_cancelled", ride.id)
            return
        self.dispatch.set_status(ride, RideStatus.REQUESTED)
        self.dispatch.offer_next(ride)

    # --- offers (F3, F4) -----------------------------------------------------------------

    def _offered_ride(self, driver_id: Any, ride_id: Any) -> tuple[str, Ride]:
        """F3: the current offer of the driver; a driver never offered the ride is not allowed,
        a driver whose offer lapsed or was answered gets ``ValueError``."""
        driver = self.accounts.get(driver_id)
        ride = self.get(ride_id)
        self.accounts.require_role(driver, Role.DRIVER)
        if ride.status == RideStatus.REQUESTED and ride.offered_to == driver.id:
            if self.ctx.now >= (ride.offer_expires_at or self.ctx.now):
                raise ValueError("the offer has expired")
            return driver.id, ride
        if driver.id in ride.offered:
            raise ValueError("the offer is no longer valid")
        raise PermissionError("the ride was not offered to this driver")

    def accept(self, driver_id: Any, ride_id: Any) -> None:
        driver, ride = self._offered_ride(driver_id, ride_id)
        self.accounts.require_active(self.state.accounts[driver])
        worker = self.state.workers[driver]
        if not worker.online or worker.busy:
            raise ValueError("the driver is not available")
        self.dispatch.clear_offer(ride)
        ride.driver_id = driver
        ride.vehicle_id = worker.vehicle_id
        ride.assigned_at = self.ctx.now
        self.dispatch.set_status(ride, RideStatus.DRIVER_ASSIGNED)
        self.fleet.set_ride(driver, ride.id)
        self.ctx.notify(ride.rider_id, "ride_assigned", ride.id)

    def decline(self, driver_id: Any, ride_id: Any) -> None:
        _, ride = self._offered_ride(driver_id, ride_id)
        self.dispatch.offer_failed(ride)

    # --- lifecycle (G1, G2, H1-H3) -------------------------------------------------------

    def _assigned_ride(self, driver_id: Any, ride_id: Any, status: str) -> tuple[str, Ride]:
        driver = self.accounts.get(driver_id)
        ride = self.get(ride_id)
        self.accounts.require_role(driver, Role.DRIVER)
        if ride.driver_id is not None and ride.driver_id != driver.id:
            raise PermissionError("the ride is assigned to another driver")
        if ride.driver_id is None and driver.id not in ride.offered:
            raise PermissionError("the ride is not assigned to this driver")
        if ride.status != status:
            raise ValueError(f"the ride is {ride.status}, not {status}")
        return driver.id, ride

    def arrived(self, driver_id: Any, ride_id: Any) -> None:
        driver, ride = self._assigned_ride(driver_id, ride_id, RideStatus.DRIVER_ASSIGNED)
        location = self.state.workers[driver].location
        if location is None or distance_km(location, ride.pickup) > ARRIVAL_RADIUS:
            raise ValueError("the driver is not within 0.2 km of the pickup")
        ride.arrived_at = self.ctx.now
        self.dispatch.set_status(ride, RideStatus.ARRIVED)
        self.ctx.notify(ride.rider_id, "driver_arrived", ride.id)

    def start(self, driver_id: Any, ride_id: Any) -> None:
        _, ride = self._assigned_ride(driver_id, ride_id, RideStatus.ARRIVED)
        ride.started_at = self.ctx.now
        assert ride.arrived_at is not None
        ride.wait_fee = compute_wait_fee((ride.started_at - ride.arrived_at).total_seconds())
        self.dispatch.set_status(ride, RideStatus.IN_PROGRESS)

    def complete(self, driver_id: Any, ride_id: Any, route: Any) -> None:
        driver, ride = self._assigned_ride(driver_id, ride_id, RideStatus.IN_PROGRESS)
        if not isinstance(route, list | tuple) or len(route) < 2:
            raise ValueError("route must have at least 2 locations")
        points = [require_location(point, "route point") for point in route]
        assert ride.started_at is not None
        now = self.ctx.now
        distance = route_km(points)
        seconds = (now - ride.started_at).total_seconds()
        minutes = max(1, int(-(-seconds // 60)))
        fare = ride.quoted_fare
        if distance > ride.quoted_distance_km * RECOMPUTE_FACTOR:
            fare = ride_fare(ride.category, distance, minutes, night=ride.night, surge=ride.surge, airport=ride.airport)
        ride.fare, ride.distance_km, ride.duration_min = fare, distance, minutes
        ride.charged = max(ZERO, fare + ride.wait_fee - ride.discount)
        ride.completed_at = now
        assert ride.hold_id is not None
        self.payments.capture(ride.hold_id, ride.charged, PaymentKind.RIDE, ride.id)
        self.dispatch.set_status(ride, RideStatus.COMPLETED)
        self.fleet.set_ride(driver, None)
        self.earnings.record_trip(
            driver, ride.id, driver_fare_share(ride.category, fare, ride.airport), ZERO, ride.wait_fee
        )
        self.promos.count_use(ride.promo_code, ride.rider_id)
        self.promos.reward_referral(ride.rider_id)
        self.ctx.notify(ride.rider_id, "ride_completed", ride.id)

    # --- cancellations (G3, G4, I3) ------------------------------------------------------

    def cancel(self, actor_id: Any, ride_id: Any, reason: Any = "") -> None:
        actor = self.accounts.get(actor_id)
        ride = self.get(ride_id)
        if actor.role == Role.ADMIN:
            kind = "admin"
        elif actor.id == ride.rider_id:
            kind = "rider"
        elif ride.driver_id is not None and actor.id == ride.driver_id:
            kind = "driver"
        else:
            raise PermissionError("only the rider, the assigned driver or an admin can cancel")
        if kind == "admin":
            self.accounts.require_active(actor)
        if reason is not None and not isinstance(reason, str):
            raise ValueError("reason must be a string")
        cancellable = (RideStatus.SCHEDULED, RideStatus.REQUESTED, *ASSIGNED_RIDE[:2])
        if ride.status not in cancellable:
            raise ValueError(f"a ride in status {ride.status} cannot be cancelled")
        text = (reason or "").strip()
        if kind == "driver":
            self._driver_cancels(ride, text)
        elif kind == "admin":
            self._close(ride, actor.id, text)
            self.ctx.audit(actor.id, "cancel_ride", ride.id)
        else:
            self._rider_cancels(ride, text)

    def _rider_cancels(self, ride: Ride, reason: str) -> None:
        now = self.ctx.now
        fee, to_driver = ZERO, False
        if ride.status == RideStatus.SCHEDULED:  # I3 until dispatched, then G3
            assert ride.pickup_at is not None
            if now >= ride.pickup_at - FREE_SCHEDULED_CANCEL:  # X6: free strictly before
                fee = SCHEDULED_CANCEL_FEE
        elif ride.status in ASSIGNED_RIDE:
            assert ride.assigned_at is not None
            if now - ride.assigned_at >= FREE_CANCEL_WINDOW:  # X6: free while elapsed < 2 min
                fee, to_driver = CANCEL_FEES[ride.category], True
        if fee > ZERO:
            if ride.hold_id is None:
                # A scheduled ride before dispatch has no hold: the fee is charged directly (I3).
                self.payments.charge(ride.rider_id, ride.payment_method_id, fee, PaymentKind.CANCELLATION_FEE, ride.id)
            else:
                self.payments.capture(ride.hold_id, fee, PaymentKind.CANCELLATION_FEE, ride.id)
        driver_id = ride.driver_id
        self._close(ride, ride.rider_id, reason)
        if fee > ZERO:
            ride.cancellation_fee = fee
            ride.charged = fee
            self.state.riders[ride.rider_id].fee_cancellations.append(now)
            if to_driver and driver_id is not None:
                self.earnings.record_fee(driver_id, ride.id, fee)

    def _close(self, ride: Ride, actor_id: str, reason: str) -> None:
        """End a ride as ``cancelled``: release the hold, free the driver, notify the rider and
        the assigned driver (G3)."""
        self.payments.release(ride.hold_id)
        self.dispatch.clear_offer(ride)
        ride.cancelled_at = self.ctx.now
        ride.cancelled_by = actor_id
        ride.cancel_reason = reason
        self.dispatch.set_status(ride, RideStatus.CANCELLED)
        self.ctx.notify(ride.rider_id, "ride_cancelled", ride.id)
        if ride.driver_id is not None:
            self.ctx.notify(ride.driver_id, "ride_cancelled", ride.id)
            self.fleet.set_ride(ride.driver_id, None)

    def _driver_cancels(self, ride: Ride, reason: str) -> None:
        """G3: back to ``requested`` and offered again, never to that driver (it stays in
        ``offered``); G4 counts the cancellation."""
        driver_id = ride.driver_id
        assert driver_id is not None
        ride.driver_id = None
        ride.vehicle_id = None
        ride.assigned_at = None
        ride.arrived_at = None
        ride.cancel_reason = reason
        self.ctx.notify(ride.rider_id, "ride_cancelled", ride.id)
        self.ctx.notify(driver_id, "ride_cancelled", ride.id)
        self.fleet.set_ride(driver_id, None)
        self.dispatch.set_status(ride, RideStatus.REQUESTED)
        self.fleet.record_driver_cancellation(driver_id)
        self.dispatch.offer_next(ride)

    # --- views, tips and sharing (H3, H4, R3) --------------------------------------------

    def view(self, ride_id: Any) -> dict[str, Any]:
        ride = self.get(ride_id)
        completed = ride.status == RideStatus.COMPLETED
        return {
            "id": ride.id,
            "rider_id": ride.rider_id,
            "driver_id": ride.driver_id,
            "status": str(ride.status),
            "category": ride.category,
            "quoted_fare": ride.quoted_fare,
            "fare": ride.fare if completed and ride.fare is not None else ride.quoted_fare,
            "distance_km": ride.distance_km if completed else ride.quoted_distance_km,
            "duration_min": ride.duration_min if completed else ride.quoted_duration_min,
            "surge": ride.surge,
            "cancellation_fee": ride.cancellation_fee,
            "wait_fee": ride.wait_fee,
            "discount": ride.discount,
            "tip": ride.tip,
            "charged": ride.charged,
            "offered_to": ride.offered_to,
            "pickup_at": ride.pickup_at,
        }

    def add_tip(self, rider_id: Any, ride_id: Any, amount: Any) -> None:
        rider = self.accounts.get(rider_id)
        ride = self.get(ride_id)
        self.accounts.require_role(rider, Role.RIDER)
        if ride.rider_id != rider.id:
            raise PermissionError("only the rider of the ride can tip")
        if ride.status != RideStatus.COMPLETED:
            raise ValueError("only completed rides can be tipped")
        assert ride.completed_at is not None and ride.driver_id is not None
        if self.ctx.now - ride.completed_at >= TIP_WINDOW:
            raise ValueError("tips are accepted within 24 hours of completion")
        if ride.tipped_at is not None:
            raise ValueError("the ride was already tipped")
        tip = parse_money(amount)
        if not MIN_TIP <= tip <= MAX_TIP:
            raise ValueError("a tip is from 1.00 to 100.00")
        self.payments.charge(rider.id, ride.payment_method_id, tip, PaymentKind.TIP, ride.id)
        ride.tip = tip
        ride.tipped_at = self.ctx.now
        self.earnings.add_tip(ride.driver_id, ride.id, tip)

    def share(self, rider_id: Any, ride_id: Any) -> str:
        rider = self.accounts.get(rider_id)
        ride = self.get(ride_id)
        self.accounts.require_role(rider, Role.RIDER)
        if ride.rider_id != rider.id:
            raise PermissionError("only the rider of the ride can share it")
        if ride.status not in ASSIGNED_RIDE:
            raise ValueError("only an active ride can be shared")
        existing = self.ctx.idx.share_by_ride.get(ride.id)
        if existing is not None:
            return existing
        number = self.state.counters.get("SHR", 0) + 1
        self.state.counters["SHR"] = number
        token = hashlib.sha256(f"share:{ride.id}:{number}".encode()).hexdigest()[:24]
        self.state.share_tokens[token] = ride.id
        self.ctx.idx.share_by_ride[ride.id] = token
        return token

    def trip_status(self, token: Any) -> dict[str, Any]:
        ride_id = self.state.share_tokens.get(token) if isinstance(token, str) else None
        if ride_id is None:
            raise KeyError("unknown trip token")
        ride = self.state.rides[ride_id]
        if ride.finished:
            raise KeyError("the trip has ended")
        driver_name = plate = eta = None
        if ride.driver_id is not None:
            driver_name = self.state.accounts[ride.driver_id].name
            if ride.vehicle_id is not None:
                plate = self.state.vehicles[ride.vehicle_id].plate
            location = self.state.workers[ride.driver_id].location
            if location is not None:
                target = ride.dropoff if ride.status == RideStatus.IN_PROGRESS else ride.pickup
                eta = self.geography.eta(location, target)
        return {"status": str(ride.status), "driver_name": driver_name, "vehicle_plate": plate, "eta_minutes": eta}
