"""Ratings (Q1-Q3)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..core.money import round_half_up
from ..core.validation import require_int
from ..domain.accounts import AccountStatus, Role
from ..domain.food import OrderStatus
from ..domain.records import Rating
from ..domain.rides import RideStatus
from .accounts import AccountService
from .context import Context

RATING_WINDOW = timedelta(days=7)
RATINGS_CONSIDERED = 100
REVIEW_THRESHOLD = Decimal("4.60")
REVIEW_MIN_RATINGS = 20


class RatingService:
    def __init__(self, ctx: Context, accounts: AccountService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.review_listeners: list[Callable[[str], None]] = []

    def rate(self, actor_id: Any, ref_id: Any, stars: Any, comment: Any = "", target: Any = "driver") -> None:
        actor = self.accounts.get(actor_id)
        target_kind, target_id, finished_at = self._target(actor.id, ref_id, target)
        require_int(stars, "stars", 1, 5)
        if comment is None:
            comment = ""
        if not isinstance(comment, str) or len(comment.strip()) > 500:
            raise ValueError("comment must be a string of at most 500 characters")
        comment = comment.strip()
        if finished_at is None or self.ctx.now - finished_at >= RATING_WINDOW:
            raise ValueError("ratings are accepted within 7 days")
        received = self.state.ratings.get(target_id, [])
        if any((r.ref, r.rater_id, r.target) == (ref_id, actor.id, target_kind) for r in received):
            raise ValueError("already rated")
        self.state.ratings.setdefault(target_id, []).append(
            Rating(actor.id, target_id, ref_id, target_kind, stars, comment, self.ctx.now)
        )
        self.ctx.idx.rating_cache.pop(target_id, None)
        self._review(target_id)

    def _target(self, actor_id: str, ref_id: Any, target: Any) -> tuple[str, str, Any]:
        """Who is rated for ``ref_id`` by ``actor_id`` and when the ride or order finished."""
        ride = self.state.rides.get(ref_id) if isinstance(ref_id, str) else None
        if ride is not None:
            if actor_id == ride.rider_id:
                kind, target_id = "driver", ride.driver_id
            elif ride.driver_id is not None and actor_id == ride.driver_id:
                kind, target_id = "rider", ride.rider_id
            else:
                raise PermissionError("only the rider and the driver of the ride can rate it")
            if ride.status != RideStatus.COMPLETED or target_id is None:
                raise ValueError("only completed rides can be rated")
            if target != kind:
                raise ValueError(f"this rating must target the {kind}")
            return kind, target_id, ride.completed_at
        order = self.state.orders.get(ref_id) if isinstance(ref_id, str) else None
        if order is None:
            raise KeyError(f"unknown ride or order {ref_id!r}")
        if actor_id != order.rider_id:
            raise PermissionError("only the rider of the order can rate it")
        if target not in ("restaurant", "courier"):
            raise ValueError("an order rating targets the restaurant or the courier")
        if order.status != OrderStatus.DELIVERED or order.courier_id is None:
            raise ValueError("only delivered orders can be rated")
        target_id = order.restaurant_id if target == "restaurant" else order.courier_id
        return str(target), target_id, order.delivered_at

    def rating(self, account_id: Any) -> Decimal | None:
        """Q2: mean of the last 100 ratings received, rounded half up to 2 places."""
        account = self.accounts.get(account_id)
        cache = self.ctx.idx.rating_cache
        if account.id not in cache:
            last = [rating.stars for rating in self.state.ratings.get(account.id, [])[-RATINGS_CONSIDERED:]]
            cache[account.id] = round_half_up(Decimal(sum(last)) / len(last), 2) if last else None
        return cache[account.id]

    def _review(self, account_id: str) -> None:
        """Q3: a driver below 4.60 with at least 20 ratings goes under review."""
        account = self.state.accounts[account_id]
        if account.role != Role.DRIVER or account.status != AccountStatus.ACTIVE:
            return
        if len(self.state.ratings.get(account_id, [])) < REVIEW_MIN_RATINGS:
            return
        current = self.rating(account_id)
        if current is not None and current < REVIEW_THRESHOLD:
            account.status = AccountStatus.UNDER_REVIEW
            for listener in self.review_listeners:
                listener(account_id)
