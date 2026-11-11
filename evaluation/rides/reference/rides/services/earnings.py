"""Worker earnings ledger, weekly bonus and payouts (P1-P4, M4)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from ..core.money import ZERO
from ..core.validation import require_datetime
from ..domain.accounts import WORKER_ROLES, Role
from ..domain.money import EarningEntry
from .accounts import AccountService
from .context import Context

WEEKLY_BONUS = Decimal("30.00")
WEEKLY_BONUS_RIDES = 20
WEEK = timedelta(days=7)


def week_start_of(moment: datetime) -> datetime:
    """Monday 00:00 of the week containing ``moment``."""
    monday = moment.date() - timedelta(days=moment.weekday())
    return datetime.combine(monday, time())


class EarningsService:
    def __init__(self, ctx: Context, accounts: AccountService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts

    def _ledger(self, worker_id: str) -> list[EarningEntry]:
        return self.state.earnings.setdefault(worker_id, [])

    def record_trip(self, worker_id: str, ref: str, fares: Decimal, tips: Decimal, fees: Decimal) -> None:
        """A completed ride or delivered order, at the current time."""
        self._ledger(worker_id).append(EarningEntry(worker_id, self.ctx.now, ref, True, fares, tips, fees))
        if self.state.accounts[worker_id].role == Role.DRIVER:
            self._weekly_bonus(worker_id)

    def record_fee(self, worker_id: str, ref: str, fee: Decimal) -> None:
        """G3: a rider's cancellation fee paid to the driver, at the cancellation time."""
        self._ledger(worker_id).append(EarningEntry(worker_id, self.ctx.now, ref, False, fees=fee))

    def add_tip(self, worker_id: str, ref: str, tip: Decimal) -> None:
        """H4: a later tip belongs to the trip it was given for."""
        for entry in self._ledger(worker_id):
            if entry.trip and entry.ref == ref:
                entry.tips += tip
                return

    def _weekly_bonus(self, driver_id: str) -> None:
        """P3: the ride that makes 20 in a Monday-to-Monday week earns the bonus."""
        start = week_start_of(self.ctx.now)
        trips = sum(1 for entry in self._ledger(driver_id) if entry.trip and start <= entry.at < start + WEEK)
        if trips == WEEKLY_BONUS_RIDES:
            self._ledger(driver_id).append(EarningEntry(driver_id, self.ctx.now, "bonus", False, bonus=WEEKLY_BONUS))

    def entries(self, start: datetime, end: datetime) -> list[EarningEntry]:
        return [entry for ledger in self.state.earnings.values() for entry in ledger if start <= entry.at < end]

    def summary(self, worker_id: str, start: datetime, end: datetime) -> dict[str, Any]:
        trips, fares, tips, fees, bonus = 0, ZERO, ZERO, ZERO, ZERO
        for entry in self.state.earnings.get(worker_id, []):
            if start <= entry.at < end:
                trips += 1 if entry.trip else 0
                fares += entry.fares
                tips += entry.tips
                fees += entry.fees
                bonus += entry.bonus
        return {"trips": trips, "fares": fares, "tips": tips, "fees": fees, "bonus": bonus,
                "total": fares + tips + fees + bonus}  # fmt: skip

    def earnings(self, worker_id: Any, start: Any, end: Any) -> dict[str, Any]:
        account = self.accounts.get(worker_id)
        if account.role not in WORKER_ROLES:
            raise ValueError(f"{account.id} is not a driver or courier")
        begin = require_datetime(start, "start")
        finish = require_datetime(end, "end")
        if finish < begin:
            raise ValueError("end must not be before start")
        return self.summary(account.id, begin, finish)

    def run_payouts(self, admin_id: Any, week_start: Any) -> dict[str, Decimal]:
        """P4: pay every worker the earnings of the week, once per week."""
        admin = self.accounts.admin(admin_id)
        monday = self._monday(week_start)
        start = datetime.combine(monday, time())
        if self.ctx.now < start + WEEK:
            raise ValueError("the week has not ended yet")
        if monday in self.state.payouts:
            raise ValueError("payouts already ran for that week")
        paid: dict[str, Decimal] = {}
        for account in self.state.accounts.values():
            if account.role in WORKER_ROLES:
                amount = self.summary(account.id, start, start + WEEK)["total"]
                if amount > ZERO:
                    paid[account.id] = amount
        self.state.payouts[monday] = dict(paid)
        for worker_id in paid:
            self.ctx.notify(worker_id, "payout", monday.isoformat())
        self.ctx.audit(admin.id, "run_payouts", monday.isoformat())
        return paid

    @staticmethod
    def _monday(week_start: Any) -> date:
        """P4: a ``date`` (not a ``datetime``) that is a Monday."""
        if isinstance(week_start, datetime) or not isinstance(week_start, date) or week_start.weekday() != 0:
            raise ValueError("week_start must be a date that is a Monday")
        monday: date = week_start
        return monday
