"""Restaurant hours and menus (J1-J4)."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..core.money import ZERO, parse_money
from ..core.validation import require_bool, require_choice, require_int, require_text
from ..domain.accounts import Role
from ..domain.food import ITEM_CATEGORIES, MenuItem, Option, OptionGroup
from .accounts import AccountService
from .context import Context

TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
MAX_OPTION_DELTA = Decimal("100.00")


class MenuService:
    def __init__(self, ctx: Context, accounts: AccountService) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.accounts = accounts

    def _restaurant(self, restaurant_id: Any) -> str:
        return self.accounts.actor(restaurant_id, Role.RESTAURANT, active=False).id

    def set_hours(self, restaurant_id: Any, weekday: Any, opens: Any, closes: Any) -> None:
        restaurant = self._restaurant(restaurant_id)
        day = require_int(weekday, "weekday", 0, 6)
        for value, name in ((opens, "opens"), (closes, "closes")):
            if not isinstance(value, str) or TIME_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be HH:MM")
        if opens >= closes:
            raise ValueError("opens must be before closes")
        self.state.restaurants[restaurant].hours[day] = (opens, closes)

    def is_open(self, restaurant_id: Any) -> bool:
        """J1: active and the current time is in ``[opens, closes)`` of the current weekday."""
        account = self.accounts.of_role(restaurant_id, Role.RESTAURANT)
        if not account.is_active:
            return False
        now = self.ctx.now
        hours = self.state.restaurants[account.id].hours.get(now.weekday())
        if hours is None:
            return False
        current = now.strftime("%H:%M:%S")
        return hours[0] + ":00" <= current < hours[1] + ":00"

    def add_item(self, restaurant_id: Any, name: Any, price: Any, category: Any, prep_minutes: Any) -> str:
        restaurant = self._restaurant(restaurant_id)
        clean_name = require_text(name, "name")
        amount = parse_money(price, "price")
        if amount <= ZERO:
            raise ValueError("price must be greater than 0")
        require_choice(category, ITEM_CATEGORIES, "category")
        prep = require_int(prep_minutes, "prep_minutes", 1, 120)
        item = MenuItem(self.ctx.next_id("ITM"), restaurant, clean_name, amount, category, prep)
        self.state.items[item.id] = item
        self.state.restaurants[restaurant].item_ids.append(item.id)
        return item.id

    def item(self, item_id: Any) -> MenuItem:
        item = self.state.items.get(item_id) if isinstance(item_id, str) else None
        if item is None:
            raise KeyError(f"unknown menu item {item_id!r}")
        return item

    def add_group(self, item_id: Any, name: Any, required: Any, min_choices: Any, max_choices: Any) -> str:
        item = self.item(item_id)
        clean_name = require_text(name, "name")
        require_bool(required, "required")
        low = require_int(min_choices, "min_choices", 0)
        high = require_int(max_choices, "max_choices", 1)
        if low > high:
            raise ValueError("min_choices must not exceed max_choices")
        if required and low < 1:
            raise ValueError("a required group needs min_choices >= 1")
        group = OptionGroup(self.ctx.next_id("GRP"), item.id, clean_name, required, low, high)
        self.state.groups[group.id] = group
        item.group_ids.append(group.id)
        return group.id

    def add_option(self, group_id: Any, name: Any, price_delta: Any) -> str:
        group = self.state.groups.get(group_id) if isinstance(group_id, str) else None
        if group is None:
            raise KeyError(f"unknown option group {group_id!r}")
        clean_name = require_text(name, "name")
        delta = parse_money(price_delta, "price_delta")
        if not ZERO <= delta <= MAX_OPTION_DELTA:
            raise ValueError("price_delta must be from 0 to 100")
        option = Option(self.ctx.next_id("OPT"), group.id, clean_name, delta)
        self.state.options[option.id] = option
        group.option_ids.append(option.id)
        return option.id

    def set_available(self, restaurant_id: Any, item_id: Any, available: Any) -> None:
        account = self.accounts.get(restaurant_id)
        item = self.item(item_id)
        self.accounts.require_role(account, Role.RESTAURANT)
        if item.restaurant_id != account.id:
            raise PermissionError("the item belongs to another restaurant")
        item.available = require_bool(available, "available")

    def menu(self, restaurant_id: Any) -> list[dict[str, Any]]:
        account = self.accounts.of_role(restaurant_id, Role.RESTAURANT)
        listed = []
        for item_id in self.state.restaurants[account.id].item_ids:
            item = self.state.items[item_id]
            groups = []
            for group_id in item.group_ids:
                group = self.state.groups[group_id]
                options = [
                    {"id": option.id, "name": option.name, "price_delta": option.price_delta}
                    for option in (self.state.options[option_id] for option_id in group.option_ids)
                ]
                groups.append(
                    {"id": group.id, "name": group.name, "required": group.required, "min_choices": group.min_choices,
                     "max_choices": group.max_choices, "options": options}
                )  # fmt: skip
            listed.append(
                {"id": item.id, "name": item.name, "price": item.price, "category": item.category,
                 "available": item.available, "groups": groups}
            )  # fmt: skip
        return listed
