"""Support tickets and refunds (R1, R2)."""

from __future__ import annotations

from typing import Any

from ..core.money import ZERO, parse_money
from ..domain.accounts import Role
from ..domain.food import Order
from ..domain.records import TICKET_KINDS, Ticket
from ..domain.rides import Ride
from .accounts import AccountService
from .context import Context
from .payments import PaymentService


class SupportService:
    def __init__(self, ctx: Context, accounts: AccountService, payments: PaymentService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts
        self.payments = payments

    def _subject(self, ref_id: Any) -> Ride | Order:
        if isinstance(ref_id, str):
            subject = self.state.rides.get(ref_id) or self.state.orders.get(ref_id)
            if subject is not None:
                return subject
        raise KeyError(f"unknown ride or order {ref_id!r}")

    def report(self, actor_id: Any, ref_id: Any, kind: Any, text: Any) -> str:
        actor = self.accounts.get(actor_id)
        subject = self._subject(ref_id)
        allowed = {subject.rider_id}
        if isinstance(subject, Ride) and subject.driver_id is not None:
            allowed.add(subject.driver_id)
        if actor.id not in allowed:
            raise PermissionError("only the rider (or the driver of a ride) can report an issue")
        if kind not in TICKET_KINDS:
            raise ValueError(f"kind must be one of {', '.join(TICKET_KINDS)}")
        if kind == "missing_items" and not isinstance(subject, Order):
            raise ValueError("missing_items applies to orders only")
        if not isinstance(text, str) or not 10 <= len(text.strip()) <= 1000:
            raise ValueError("text must have 10 to 1000 characters")
        ticket = Ticket(self.ctx.next_id("TCK"), actor.id, subject.id, kind, text.strip(), self.ctx.now)
        self.state.tickets[ticket.id] = ticket
        if kind == "safety":
            for account in self.state.accounts.values():
                if account.role == Role.ADMIN:
                    self.ctx.notify(account.id, "safety_ticket", ticket.id)
        return ticket.id

    def resolve(self, admin_id: Any, ticket_id: Any, refund_amount: Any = ZERO) -> None:
        admin = self.accounts.get(admin_id)
        ticket = self.state.tickets.get(ticket_id) if isinstance(ticket_id, str) else None
        if ticket is None:
            raise KeyError(f"unknown ticket {ticket_id!r}")
        self.accounts.require_admin(admin)
        if ticket.resolved:
            raise ValueError("the ticket is already resolved")
        refund = parse_money(refund_amount, "refund_amount")
        subject = self._subject(ticket.ref)
        if refund < ZERO or refund > self.payments.net_charged(subject.id):
            raise ValueError("the refund cannot exceed what was charged minus earlier refunds")
        if refund > ZERO:
            self.payments.refund(subject.rider_id, subject.payment_method_id, refund, subject.id)
            subject.refunded += refund
        ticket.resolved = True
        ticket.resolved_by = admin.id
        ticket.refund = refund
        self.ctx.audit(admin.id, "resolve_ticket", ticket.id)
