"""Registration, authentication and account lookups (A1-A8)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from ..core.security import hash_password, verify_password
from ..core.validation import (
    age_on,
    require_choice,
    require_date,
    require_location,
    require_match,
    require_text,
)
from ..domain.accounts import (
    COURIER_VEHICLES,
    WORKER_ROLES,
    Account,
    AccountStatus,
    CourierProfile,
    DriverProfile,
    RestaurantProfile,
    RiderProfile,
    Role,
    WorkerState,
)
from ..domain.money import Wallet
from .context import Context

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE_RE = re.compile(r"\+[1-9]\d{7,14}")
LICENSE_RE = re.compile(r"[A-Z0-9]{6,12}")
REFERRAL_RE = re.compile(r"REF([0-9]{6})")
MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)
MIN_RIDER_AGE = 16


class AccountService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.state = ctx.state

    # --- lookups -------------------------------------------------------------------------

    def get(self, account_id: Any) -> Account:
        account = self.state.accounts.get(account_id) if isinstance(account_id, str) else None
        if account is None:
            raise KeyError(f"unknown account {account_id!r}")
        return account

    # X8: callers look every identifier up first (``KeyError``), then check permissions
    # (``PermissionError``), then everything else (``ValueError``).

    @staticmethod
    def require_role(account: Account, *roles: str) -> Account:
        """An account acting in another role is not allowed to act (``PermissionError``)."""
        if account.role not in roles:
            raise PermissionError(f"{account.id} is not allowed to do this")
        return account

    @staticmethod
    def require_active(account: Account) -> Account:
        """X8: an account that is not active where one is needed raises ``ValueError``."""
        if not account.is_active:
            raise ValueError(f"{account.id} is {account.status}")
        return account

    def actor(self, account_id: Any, *roles: str, active: bool = True) -> Account:
        """Look up, check the role and (optionally) that the account is active, in that order.
        Use only when the call has no other identifier to look up."""
        account = self.require_role(self.get(account_id), *roles)
        return self.require_active(account) if active else account

    def admin(self, admin_id: Any) -> Account:
        return self.actor(admin_id, Role.ADMIN)

    def require_admin(self, account: Account) -> Account:
        return self.require_active(self.require_role(account, Role.ADMIN))

    def of_role(self, account_id: Any, role: str) -> Account:
        """An account used as a target that must have ``role`` (``ValueError`` otherwise)."""
        account = self.get(account_id)
        if account.role != role:
            raise ValueError(f"{account.id} is not a {role}")
        return account

    def view(self, account_id: Any) -> dict[str, Any]:
        account = self.get(account_id)
        return {
            "id": account.id,
            "role": str(account.role),
            "name": account.name,
            "email": account.email,
            "phone": account.masked_phone,
            "status": str(account.status),
        }

    def referral_code(self, rider_id: Any) -> str:
        return self.state.riders[self.of_role(rider_id, Role.RIDER).id].referral_code

    # --- registration --------------------------------------------------------------------

    def _identity(self, name: Any, email: Any, phone: Any, password: Any) -> tuple[str, str, str]:
        clean_name = require_text(name, "name", 80)
        if not isinstance(email, str):
            raise ValueError("email must be a string")
        clean_email = email.strip().lower()
        require_match(clean_email, EMAIL_RE, "email")
        if clean_email in self.ctx.idx.emails:
            raise ValueError("email already registered")
        require_match(phone, PHONE_RE, "phone")
        if phone in self.ctx.idx.phones:
            raise ValueError("phone already registered")
        if (
            not isinstance(password, str)
            or len(password) < 10
            or not any(char.isascii() and char.isalpha() for char in password)
            or not any(char in "0123456789" for char in password)
        ):
            raise ValueError("password needs 10 characters with at least one ASCII letter and one ASCII digit")
        return clean_name, clean_email, phone

    def _create(self, role: str, prefix: str, identity: tuple[str, str, str], password: str) -> Account:
        name, email, phone = identity
        credential = hash_password(password)
        status = AccountStatus.ACTIVE if role in (Role.ADMIN, Role.RIDER) else AccountStatus.PENDING
        account = Account(self.ctx.next_id(prefix), role, name, email, phone, status, self.ctx.now)
        self.state.accounts[account.id] = account
        self.state.credentials[account.id] = credential
        self.ctx.idx.emails[email] = account.id
        self.ctx.idx.phones[phone] = account.id
        if role in WORKER_ROLES:
            self.state.workers[account.id] = WorkerState(account.id, role)
        return account

    def create_admin(self, name: Any, email: Any, phone: Any, password: Any) -> str:
        identity = self._identity(name, email, phone, password)
        return self._create(Role.ADMIN, "ADM", identity, password).id

    def register_rider(
        self, name: Any, email: Any, phone: Any, password: Any, birth_date: Any = None, referral_code: Any = None
    ) -> str:
        identity = self._identity(name, email, phone, password)
        birth = None
        if birth_date is not None:
            birth = require_date(birth_date, "birth_date")
            if age_on(birth, self.ctx.now.date()) < MIN_RIDER_AGE:
                raise ValueError("riders must be at least 16 years old")
        referrer = None
        if referral_code is not None:
            referrer = self._referrer(referral_code)
        account = self._create(Role.RIDER, "RID", identity, password)
        self.state.riders[account.id] = RiderProfile(account.id, birth, referrer)
        wallet_id = "WAL-" + account.id.split("-")[1]
        self.state.wallets[wallet_id] = Wallet(wallet_id, account.id)
        return account.id

    def _referrer(self, code: Any) -> str:
        match = REFERRAL_RE.fullmatch(code) if isinstance(code, str) else None  # X7: not normalized
        rider_id = f"RID-{match.group(1)}" if match else None
        if rider_id is None or rider_id not in self.state.riders:
            raise ValueError("unknown referral code")
        return rider_id

    def register_driver(
        self, name: Any, email: Any, phone: Any, password: Any, license_number: Any, license_expires: Any
    ) -> str:
        identity = self._identity(name, email, phone, password)
        require_match(license_number, LICENSE_RE, "license_number")
        expires = require_date(license_expires, "license_expires")
        if expires <= self.ctx.now.date():
            raise ValueError("license_expires must be after the current date")
        account = self._create(Role.DRIVER, "DRV", identity, password)
        self.state.drivers[account.id] = DriverProfile(account.id, license_number, expires)
        return account.id

    def register_courier(self, name: Any, email: Any, phone: Any, password: Any, vehicle: Any) -> str:
        identity = self._identity(name, email, phone, password)
        require_choice(vehicle, COURIER_VEHICLES, "vehicle")
        account = self._create(Role.COURIER, "CUR", identity, password)
        self.state.couriers[account.id] = CourierProfile(account.id, vehicle)
        return account.id

    def register_restaurant(self, name: Any, email: Any, phone: Any, password: Any, location: Any) -> str:
        identity = self._identity(name, email, phone, password)
        point = require_location(location)
        account = self._create(Role.RESTAURANT, "RST", identity, password)
        self.state.restaurants[account.id] = RestaurantProfile(account.id, point)
        return account.id

    # --- authentication (A3) -------------------------------------------------------------

    def authenticate(self, email: Any, password: Any) -> str:
        """Failures are recorded even though the call raises (A3 overrides X3 here)."""
        if not isinstance(email, str) or not isinstance(password, str):
            raise PermissionError("invalid credentials")
        account_id = self.ctx.idx.emails.get(email.strip().lower())
        if account_id is None:
            raise PermissionError("invalid credentials")
        account = self.state.accounts[account_id]
        now = self.ctx.now
        if account.is_locked(now):
            raise PermissionError("account locked")
        if not verify_password(password, self.state.credentials[account_id]):
            account.failed_logins += 1
            if account.failed_logins >= MAX_FAILED_LOGINS:
                account.failed_logins = 0
                account.locked_until = now + LOCK_DURATION
            raise PermissionError("invalid credentials")
        if account.status == AccountStatus.SUSPENDED:
            raise PermissionError("account suspended")
        account.failed_logins = 0
        account.locked_until = None
        return account.id
