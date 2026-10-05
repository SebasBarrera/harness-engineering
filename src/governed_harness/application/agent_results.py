"""Application helpers of the agent-results settings (#37-#44, #52): parsing of decision
options and the commands that act on a run (budget, quarantine, plan approval, check,
routing calibration)."""

from __future__ import annotations

from collections.abc import Sequence

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import ChangeRequestItem


def parse_change_requests(values: Sequence[str]) -> tuple[ChangeRequestItem, ...]:
    """``description::condition`` items of a structured REQUEST_CHANGES, numbered ``CR-1``...
    An item without ``::`` is a ``text`` item (shown and sent, not checked by a tool)."""
    items: list[ChangeRequestItem] = []
    for number, value in enumerate(values, start=1):
        description, separator, condition = value.partition("::")
        if not description.strip():
            raise ConfigurationError(f"change request {number} needs a description")
        try:
            items.append(
                ChangeRequestItem(
                    item_id=f"CR-{number}",
                    description=description.strip(),
                    condition=condition.strip() if separator else "text",
                )
            )
        except ValueError as error:
            raise ConfigurationError(f"invalid change request {number}: {error}") from error
    return tuple(items)
