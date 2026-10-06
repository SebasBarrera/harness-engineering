"""Cards, wallets, holds, captures, charges and refunds (O1-O4, R2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..core.money import ZERO, parse_money
from ..core.validation import require_int
from ..domain.accounts import Role
from ..domain.money import DECLINING_CARD, Card, Hold, Payment, PaymentKind, Wallet
from .accounts import AccountService
from .context import Context

MIN_TOP_UP = Decimal("5.00")
MAX_TOP_UP = Decimal("500.00")


def luhn_valid(number: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(number)):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def card_brand(number: str) -> str:
    if number.startswith("4"):
        return "visa"
    if 51 <= int(number[:2]) <= 55:
        return "mastercard"
    if number[:2] in ("34", "37"):
        return "amex"
    raise ValueError("unsupported card brand")


PaymentMethod = Card | Wallet


class PaymentService:
    def __init__(self, ctx: Context, accounts: AccountService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts

    # --- methods (O1, O2) ----------------------------------------------------------------

    def add_card(self, rider_id: Any, number: Any, exp_month: Any, exp_year: Any, cvc: Any) -> str:
        rider = self.accounts.actor(rider_id, Role.RIDER, active=False)
        if not isinstance(number, str) or not number.isascii() or not number.isdigit() or not 13 <= len(number) <= 19:
            raise ValueError("card number must have 13 to 19 digits")
        if not luhn_valid(number):
            raise ValueError("card number fails the Luhn check")
        brand = card_brand(number)
        month = require_int(exp_month, "exp_month", 1, 12)
        year = require_int(exp_year, "exp_year")
        now = self.ctx.now
        if (year, month) < (now.year, now.month):
            raise ValueError("the card has expired")
        cvc_length = 4 if brand == "amex" else 3
        if not isinstance(cvc, str) or not cvc.isascii() or not cvc.isdigit() or len(cvc) != cvc_length:
            raise ValueError(f"cvc must have {cvc_length} digits")
        card = Card(self.ctx.next_id("PM"), rider.id, brand, number[-4:], month, year, number == DECLINING_CARD)
        self.state.cards[card.id] = card
        return card.id

    def wallet_of(self, rider_id: str) -> Wallet:
        return self.state.wallets["WAL-" + rider_id.split("-")[1]]

    def methods(self, rider_id: Any) -> list[dict[str, Any]]:
        rider = self.accounts.of_role(rider_id, Role.RIDER)
        wallet = self.wallet_of(rider.id)
        listed: list[dict[str, Any]] = [{"id": wallet.id, "kind": "wallet", "brand": None, "last4": None}]
        for card in self.state.cards.values():
            if card.rider_id == rider.id:
                listed.append({"id": card.id, "kind": "card", "brand": card.brand, "last4": card.last4})
        return listed

    def balance(self, rider_id: Any) -> Decimal:
        """O2: the balance minus active holds."""
        return self.wallet_of(self.accounts.of_role(rider_id, Role.RIDER).id).free_balance

    def lookup(self, method_id: Any) -> PaymentMethod:
        """An unknown method raises ``KeyError``."""
        found: PaymentMethod | None = None
        if isinstance(method_id, str):
            found = self.state.cards.get(method_id) or self.state.wallets.get(method_id)
        if found is None:
            raise KeyError(f"unknown payment method {method_id!r}")
        return found

    @staticmethod
    def require_owner(rider_id: str, method: PaymentMethod) -> PaymentMethod:
        """O3: a payment method of another rider raises ``PermissionError``."""
        if method.rider_id != rider_id:
            raise PermissionError("the payment method belongs to another rider")
        return method

    def method(self, rider_id: str, method_id: Any) -> PaymentMethod:
        return self.require_owner(rider_id, self.lookup(method_id))

    def top_up(self, rider_id: Any, card_id: Any, amount: Any) -> None:
        rider = self.accounts.get(rider_id)
        method = self.lookup(card_id)
        self.accounts.require_role(rider, Role.RIDER)
        self.require_owner(rider.id, method)
        if not isinstance(method, Card):
            raise ValueError("top-ups are charged to a card")
        value = parse_money(amount)
        if not MIN_TOP_UP <= value <= MAX_TOP_UP:
            raise ValueError("top-ups are from 5.00 to 500.00")
        self.ensure_chargeable(method, value)
        wallet = self.wallet_of(rider.id)
        wallet.balance += value
        self._record(rider.id, PaymentKind.TOP_UP, method.id, method.id, value)

    # --- holds, captures and charges (O3) ------------------------------------------------

    def ensure_chargeable(self, method: PaymentMethod, amount: Decimal) -> None:
        """Raise ``ValueError`` when a hold or charge of ``amount`` would fail; change nothing."""
        if isinstance(method, Card):
            if method.declines:
                raise ValueError("the card was declined")
        elif method.free_balance < amount:
            raise ValueError("insufficient wallet balance")

    def place_hold(self, rider_id: str, method_id: str, amount: Decimal, ref: str) -> str:
        method = self.method(rider_id, method_id)
        self.ensure_chargeable(method, amount)
        hold = Hold(self.ctx.next_id("HLD"), rider_id, method.id, amount, ref)
        if isinstance(method, Wallet):
            method.held += amount
        self.state.holds[hold.id] = hold
        return hold.id

    def release(self, hold_id: str | None) -> None:
        hold = self.state.holds.get(hold_id) if hold_id else None
        if hold is None or not hold.active:
            return
        hold.active = False
        wallet = self.state.wallets.get(hold.method_id)
        if wallet is not None:
            wallet.held -= hold.amount

    def capture(self, hold_id: str, amount: Decimal, kind: str, ref: str) -> None:
        """Capture ``amount`` against a hold and release the rest (H3, L3, L4).

        A capture above the hold (a recomputed fare or a wait fee) is taken in full; a wallet
        balance may then go below zero.
        """
        hold = self.state.holds[hold_id]
        self.release(hold_id)
        wallet = self.state.wallets.get(hold.method_id)
        if wallet is not None:
            wallet.balance -= amount
        self._record(hold.rider_id, kind, ref, hold.method_id, amount)

    def charge(self, rider_id: str, method_id: str, amount: Decimal, kind: str, ref: str) -> None:
        """A direct charge without a hold (tips, I3 fee). Checked first with ``ensure_chargeable``."""
        method = self.method(rider_id, method_id)
        self.ensure_chargeable(method, amount)
        if isinstance(method, Wallet):
            method.balance -= amount
        self._record(rider_id, kind, ref, method.id, amount)

    def refund(self, rider_id: str, method_id: str, amount: Decimal, ref: str) -> None:
        wallet = self.state.wallets.get(method_id)
        if wallet is not None:
            wallet.balance += amount
        self._record(rider_id, PaymentKind.REFUND, ref, method_id, -amount)

    def credit_wallet(self, rider_id: str, amount: Decimal) -> None:
        self.wallet_of(rider_id).balance += amount

    def _record(self, rider_id: str, kind: str, ref: str, method_id: str, amount: Decimal) -> None:
        if amount == ZERO:
            return
        payment = Payment(self.ctx.next_id("PAY"), rider_id, kind, ref, method_id, amount, self.ctx.now)
        self.state.payments[payment.id] = payment

    def net_charged(self, ref: str) -> Decimal:
        """Everything charged for a ride or order (tips included) minus refunds (R2)."""
        return sum((payment.amount for payment in self.state.payments.values() if payment.ref == ref), ZERO)

    def history(self, account_id: Any) -> list[dict[str, Any]]:
        """O4: the rider's payments in time order (creation order is time order)."""
        account = self.accounts.get(account_id)
        return [
            {
                "id": payment.id,
                "kind": str(payment.kind),
                "ref": payment.ref,
                "method_id": payment.method_id,
                "amount": payment.amount,
                "at": payment.at,
            }
            for payment in self.state.payments.values()
            if payment.rider_id == account.id
        ]
