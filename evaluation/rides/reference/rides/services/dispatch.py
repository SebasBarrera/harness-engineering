"""Offering rides to drivers (F2-F4) and ride status bookkeeping."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ..core.geo import distance_km
from ..domain.accounts import AccountStatus
from ..domain.records import TimerKind
from ..domain.rides import Ride, RideStatus
from .context import Context
from .fleet import FleetService
from .payments import PaymentService
from .ratings import RatingService

OFFER_DURATION = timedelta(seconds=15)
MAX_RADIUS_KM = Decimal(8)
MIN_RATING = Decimal("4.60")
UNRATED = Decimal("5.00")
MAX_FAILED_OFFERS = 5


class DispatchService:
    def __init__(self, ctx: Context, fleet: FleetService, payments: PaymentService, ratings: RatingService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.fleet = fleet
        self.payments = payments
        self.ratings = ratings
        fleet.offline_listeners.append(self.withdraw_offers)

    def set_status(self, ride: Ride, status: str) -> None:
        """Every ride status change goes through here to keep the demand index (E1) exact."""
        requested = self.ctx.idx.requested_by_zone.setdefault(ride.pickup_zone_id, set())
        if status == RideStatus.REQUESTED:
            requested.add(ride.id)
        else:
            requested.discard(ride.id)
        ride.status = status

    def demand(self, zone_id: str) -> int:
        return len(self.ctx.idx.requested_by_zone.get(zone_id, ()))

    def best_driver(self, ride: Ride) -> str | None:
        """F2: nearest eligible driver (available, active, of the category, within 8 km, rated
        4.60 or more or unrated, not offered this ride, no pending offer); ties go to the higher
        rating (unrated = 5.00), then to the lower id."""
        best: tuple[Decimal, Decimal, str] | None = None
        for driver_id in self.fleet.drivers_near(ride.category, ride.pickup, float(MAX_RADIUS_KM)):
            if driver_id in ride.offered or self.ctx.idx.offers_by_driver.get(driver_id):
                continue
            if self.state.accounts[driver_id].status != AccountStatus.ACTIVE:
                continue
            location = self.state.workers[driver_id].location
            assert location is not None
            distance = distance_km(location, ride.pickup)
            if distance > MAX_RADIUS_KM:
                continue
            rating = self.ratings.rating(driver_id)
            if rating is not None and rating < MIN_RATING:
                continue
            key = (distance, -(rating if rating is not None else UNRATED), driver_id)
            if best is None or key < best:
                best = key
        return best[2] if best else None

    def offer_next(self, ride: Ride) -> None:
        """Offer the ride to the best eligible driver, or end it as ``no_driver`` (F4)."""
        driver_id = self.best_driver(ride)
        if driver_id is None:
            self.no_driver(ride)
            return
        ride.offered_to = driver_id
        ride.offered.append(driver_id)
        ride.offer_token += 1
        ride.offer_expires_at = self.ctx.now + OFFER_DURATION
        self.ctx.idx.offers_by_driver.setdefault(driver_id, set()).add(ride.id)
        self.fleet.refresh_id(driver_id)
        self.ctx.schedule(ride.offer_expires_at, TimerKind.OFFER_EXPIRY, ride.id, ride.offer_token)
        self.ctx.notify(driver_id, "ride_offer", ride.id)

    def clear_offer(self, ride: Ride) -> None:
        driver_id = ride.offered_to
        ride.offered_to = None
        ride.offer_expires_at = None
        if driver_id is not None:
            self.ctx.idx.offers_by_driver.get(driver_id, set()).discard(ride.id)
            self.fleet.refresh_id(driver_id)

    def offer_failed(self, ride: Ride) -> None:
        """A decline, an expiry or a driver leaving: next driver, or ``no_driver`` after 5."""
        self.clear_offer(ride)
        ride.failed_offers += 1
        if ride.failed_offers >= MAX_FAILED_OFFERS:
            self.no_driver(ride)
        else:
            self.offer_next(ride)

    def no_driver(self, ride: Ride) -> None:
        self.clear_offer(ride)
        self.set_status(ride, RideStatus.NO_DRIVER)
        self.payments.release(ride.hold_id)
        self.ctx.notify(ride.rider_id, "no_driver", ride.id)

    def expire_offer(self, ride_id: str, token: int) -> None:
        """Timer: the offer with ``token`` lapsed if it is still the current one."""
        ride = self.state.rides.get(ride_id)
        if ride is not None and ride.status == RideStatus.REQUESTED and ride.offer_token == token and ride.offered_to:
            self.offer_failed(ride)

    def withdraw_offers(self, driver_id: str) -> None:
        """A driver that goes offline lets its pending offers go (counted like an expiry)."""
        for ride_id in sorted(self.ctx.idx.offers_by_driver.get(driver_id, set())):
            ride = self.state.rides[ride_id]
            if ride.status == RideStatus.REQUESTED and ride.offered_to == driver_id:
                self.offer_failed(ride)
