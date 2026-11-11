"""Account approval, suspension and reactivation by admins (A5, B2, Q3)."""

from __future__ import annotations

from typing import Any

from ..core.validation import require_text
from ..domain.accounts import WORKER_ROLES, AccountStatus, Role
from .accounts import AccountService
from .context import Context
from .fleet import FleetService


class AdminService:
    def __init__(self, ctx: Context, accounts: AccountService, fleet: FleetService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.fleet = fleet

    def approve(self, admin_id: Any, account_id: Any) -> None:
        admin = self.accounts.get(admin_id)
        account = self.accounts.get(account_id)
        self.accounts.require_admin(admin)
        if account.status != AccountStatus.PENDING:
            raise ValueError(f"only pending accounts can be approved, {account.id} is {account.status}")
        if account.role == Role.DRIVER:
            profile = self.state.drivers[account.id]
            if not profile.vehicle_ids:
                raise ValueError("a driver needs at least one vehicle to be approved")
            if not profile.license_valid(self.ctx.now.date()):
                raise ValueError("the driver license has expired")
        account.status = AccountStatus.ACTIVE
        self.ctx.audit(admin.id, "approve", account.id)

    def suspend(self, admin_id: Any, account_id: Any, reason: Any) -> None:
        admin = self.accounts.get(admin_id)
        account = self.accounts.get(account_id)
        self.accounts.require_admin(admin)
        if account.id == admin.id:
            raise ValueError("an admin cannot suspend itself")
        text = require_text(reason, "reason")
        if account.status == AccountStatus.SUSPENDED:
            raise ValueError(f"{account.id} is already suspended")
        account.status = AccountStatus.SUSPENDED
        account.suspension_reason = text
        if account.role in WORKER_ROLES:
            self.fleet.force_offline(account.id)
        self.ctx.audit(admin.id, "suspend", account.id)

    def reactivate(self, admin_id: Any, account_id: Any) -> None:
        admin = self.accounts.get(admin_id)
        account = self.accounts.get(account_id)
        self.accounts.require_admin(admin)
        if account.status not in (AccountStatus.SUSPENDED, AccountStatus.UNDER_REVIEW):
            raise ValueError(f"only suspended or under review accounts can be reactivated ({account.status})")
        account.status = AccountStatus.ACTIVE
        account.suspension_reason = None
        self.ctx.audit(admin.id, "reactivate", account.id)
