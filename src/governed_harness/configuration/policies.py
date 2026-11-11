"""Profile policies that take effect under ``governance.applyProfilePolicies`` (#51)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_harness.domain.errors import ConfigurationError

UNAVAILABLE_STATUS_POLICIES = ("missingTestCommand", "missingTestScript")
"""Status of an unavailable mandatory validator: a missing executable or Python module
(``missingTestCommand``) or a missing package script (``missingTestScript``)."""
UNAVAILABLE_STATUSES = ("BLOCKED", "FAILED")


def coverage_minimum(policies: Mapping[str, Any]) -> float | None:
    """The configured line-coverage minimum, ``None`` when ``coverage`` is absent or is not a
    threshold (the Python profile's ``optional_for_research_prototype`` is a declaration)."""
    value = policies.get("coverage")
    if not isinstance(value, Mapping):
        return None
    minimum = value.get("minimumPercent")
    if isinstance(minimum, bool) or not isinstance(minimum, int | float):
        raise ConfigurationError("policies.coverage.minimumPercent must be a number from 0 to 100")
    if not 0 <= float(minimum) <= 100:
        raise ConfigurationError("policies.coverage.minimumPercent must be from 0 to 100")
    return float(minimum)


def validate_profile_policies(policies: Mapping[str, Any]) -> None:
    """Refuse values the harness cannot apply (configuration error, exit code 2)."""
    for key in UNAVAILABLE_STATUS_POLICIES:
        value = policies.get(key)
        if value is not None and value not in UNAVAILABLE_STATUSES:
            raise ConfigurationError(
                f"policies.{key} must be one of {', '.join(UNAVAILABLE_STATUSES)}: {value!r}"
            )
    coverage_minimum(policies)
