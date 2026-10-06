"""Promotions (N1-N3) and referral rewards (N4)."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..core.money import ZERO, parse_money
from ..core.validation import require_bool, require_choice, require_datetime, require_int
from ..domain.food import OrderStatus
from ..domain.money import Promo
from ..domain.pricing import promo_discount
from ..domain.rides import RideStatus
from .accounts import AccountService
from .context import Context
from .payments import PaymentService

CODE_RE = re.compile(r"[A-Z0-9]{4,15}")
SERVICES = ("rides", "eats", "both")
REFERRAL_REWARD = Decimal("5.00")


class PromoService:
    def __init__(self, ctx: Context, accounts: AccountService, payments: PaymentService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.payments = payments

    def create(
        self,
        admin_id: Any,
        code: Any,
        service: Any,
        kind: Any,
        value: Any,
        max_discount: Any = None,
        min_spend: Any = Decimal("0"),
        starts: Any = None,
        ends: Any = None,
        max_total_uses: Any = None,
        max_uses_per_user: Any = None,
        first_order_only: Any = False,
    ) -> None:
        admin = self.accounts.admin(admin_id)
        if not isinstance(code, str):
            raise ValueError("code must be a string")
        clean_code = code.strip().upper()
        if CODE_RE.fullmatch(clean_code) is None:
            raise ValueError("code must be 4 to 15 letters or digits")
        if clean_code in self.state.promos:
            raise ValueError("promo code already exists")
        require_choice(service, SERVICES, "service")
        require_choice(kind, ("percent", "fixed"), "kind")
        if kind == "percent":
            amount = Decimal(require_int(value, "value", 1, 50))
        else:
            amount = parse_money(value, "value")
            if not Decimal("1.00") <= amount <= Decimal("100.00"):
                raise ValueError("a fixed value is from 1.00 to 100.00")
        cap = None
        if max_discount is not None:
            cap = parse_money(max_discount, "max_discount")
            if cap <= ZERO:
                raise ValueError("max_discount must be positive")
        spend = parse_money(min_spend, "min_spend")
        if spend < ZERO:
            raise ValueError("min_spend cannot be negative")
        start = None if starts is None else require_datetime(starts, "starts")
        end = None if ends is None else require_datetime(ends, "ends")
        if start is not None and end is not None and start >= end:
            raise ValueError("starts must be before ends")
        total_limit = None if max_total_uses is None else require_int(max_total_uses, "max_total_uses", 1)
        user_limit = None if max_uses_per_user is None else require_int(max_uses_per_user, "max_uses_per_user", 1)
        require_bool(first_order_only, "first_order_only")
        self.state.promos[clean_code] = Promo(
            clean_code, service, kind, amount, cap, spend, start, end, total_limit, user_limit,
            first_order_only, admin.id,
        )  # fmt: skip
        self.ctx.audit(admin.id, "create_promo", clean_code)

    def evaluate(self, code: Any, service: str, rider_id: str, amount: Decimal) -> tuple[str, Decimal]:
        """N2: the normalized code and the discount on ``amount``, or ``ValueError``."""
        promo = self.state.promos.get(code.upper()) if isinstance(code, str) else None
        if promo is None:
            raise ValueError("unknown promo code")
        if not promo.applies_to(service):
            raise ValueError("the promo does not apply to this service")
        if not promo.in_window(self.ctx.now):
            raise ValueError("the promo is not active")
        if amount < promo.min_spend:
            raise ValueError("the amount does not reach the promo minimum spend")
        if promo.max_total_uses is not None and promo.uses >= promo.max_total_uses:
            raise ValueError("the promo has no uses left")
        if promo.max_uses_per_user is not None and promo.uses_by_rider.get(rider_id, 0) >= promo.max_uses_per_user:
            raise ValueError("the rider has used this promo too many times")
        if promo.first_order_only and self._has_history(rider_id, service):
            raise ValueError("the promo is for a first order only")
        return promo.code, promo_discount(promo.kind, promo.value, promo.max_discount, amount)

    def _has_history(self, rider_id: str, service: str) -> bool:
        if service == "rides":
            rides = self.ctx.idx.rides_by_rider.get(rider_id, [])
            return any(self.state.rides[ride_id].status == RideStatus.COMPLETED for ride_id in rides)
        return any(
            order.rider_id == rider_id and order.status == OrderStatus.DELIVERED for order in self.state.orders.values()
        )

    def count_use(self, code: str | None, rider_id: str) -> None:
        """N3: a use counts when the ride completes or the order is delivered."""
        if code is None:
            return
        promo = self.state.promos[code]
        promo.uses += 1
        promo.uses_by_rider[rider_id] = promo.uses_by_rider.get(rider_id, 0) + 1

    def reward_referral(self, rider_id: str) -> None:
        """N4: on the first completed ride of a referred rider, 5.00 to both wallets, once."""
        profile = self.state.riders[rider_id]
        if profile.referred_by is None or profile.referral_rewarded:
            return
        profile.referral_rewarded = True
        self.payments.credit_wallet(rider_id, REFERRAL_REWARD)
        self.payments.credit_wallet(profile.referred_by, REFERRAL_REWARD)
