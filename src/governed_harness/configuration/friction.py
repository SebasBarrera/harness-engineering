"""Configuration of low-friction small changes and local metrics (since 1.1, issue #58).

Every section and key here is optional. A key that is absent keeps the 1.0.0 behaviour and is
left out of the serialized configuration, so the configuration snapshot (and its digest) of a
project written before these settings existed does not change. ``harness init`` writes the
``friction`` section; ``metrics`` only configures ``harness metrics`` (a price table for
providers that report tokens without cost, and the optional narrative command), which runs
without it.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)

from governed_harness.configuration.agent_results import off_from_yaml

SizeName = Literal["S", "M", "L"]


class _Section(BaseModel):
    """A section whose unset (``None``) keys are left out of the serialized configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


FastLaneStep = Literal[
    "ambiguityReview",
    "decomposition",
    "agentReview",
    "preflight",
    "mutation",
    "acceptanceTests",
]
FAST_LANE_STEPS: tuple[FastLaneStep, ...] = (
    "ambiguityReview",
    "decomposition",
    "agentReview",
    "preflight",
    "mutation",
    "acceptanceTests",
)
DEFAULT_FAST_LANE_SKIP: tuple[FastLaneStep, ...] = (
    "ambiguityReview",
    "decomposition",
    "agentReview",
    "preflight",
    "mutation",
)
"""What a fast-lane run leaves out when ``fastLane.skip`` is not set (the list of issue #58):
the agent's ambiguity review, the decomposition, the agent review unless a signal asks for it,
the preflight on the baseline and the light mutation. ``acceptanceTests`` can be added; it is
not left out by default because the frozen acceptance tests are evidence of the criteria."""


class FastVerificationConfig(_Section):
    """Faster VERIFICATION in the fast lane (#58, item 5). Nothing here skips a mandatory
    validator before the decision: the affected tests run first and stop a failing attempt
    early, the full suite still runs before the gate, independent validators run side by side,
    and a validator result is reused only for the same validator, ChangeSet, baseline and
    configuration."""

    affected_tests_first: bool | None = Field(default=None, alias="affectedTestsFirst")
    parallel: bool | None = None
    cache: bool | None = None


class FastLaneConfig(_Section):
    """Risk-proportional governance (#58, item 1). ``mode: auto`` classifies each task at
    INTENT: size ``S`` by the router (#44) and no risk flag takes the fast lane, everything
    else the full flow; the lane and why are recorded. ``skip`` lists what the fast lane leaves
    out. A run leaves the fast lane (``lane.escalated``) when its ChangeSet shows a risk factor
    or outgrows ``S``; the human decision and the mandatory validators are never skipped."""

    mode: Literal["auto", "off"] | None = None
    skip: tuple[FastLaneStep, ...] | None = None
    verification: FastVerificationConfig | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.mode == "auto"

    @property
    def skipped(self) -> tuple[FastLaneStep, ...]:
        return DEFAULT_FAST_LANE_SKIP if self.skip is None else self.skip


DEFAULT_PRE_AUTHORIZATION_HOURS = 24
MAX_PRE_AUTHORIZATION_HOURS = 168


class PreAuthorizationConfig(_Section):
    """Pre-authorised approval (#58, item 3). ``mode: allow`` lets a person, when they confirm
    the operational contract (``harness task confirm --pre-approve`` or ``harness do
    --pre-approve``), approve in advance under a condition with an expiry. The approval is a
    human decision recorded in advance, bound to the contract digest and the condition (gate
    passed, no risk factor, size ``S``); when the condition does not hold at DECISION, the
    person is asked as usual."""

    mode: Literal["allow", "off"] | None = None
    default_hours: int | None = Field(
        default=None, alias="defaultHours", ge=1, le=MAX_PRE_AUTHORIZATION_HOURS
    )
    max_hours: int | None = Field(default=None, alias="maxHours", ge=1, le=MAX_PRE_AUTHORIZATION_HOURS)

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.mode == "allow"

    @property
    def hours(self) -> int:
        return self.default_hours or DEFAULT_PRE_AUTHORIZATION_HOURS

    @property
    def limit_hours(self) -> int:
        return self.max_hours or MAX_PRE_AUTHORIZATION_HOURS


class FrictionTarget(_Section):
    """The friction a task of one size should cost: human interactions and wall-clock
    minutes from the first event of its first run to the end of its last."""

    interactions: int | None = Field(default=None, ge=0, le=1000)
    minutes: float | None = Field(default=None, gt=0, le=525_600)


DEFAULT_FRICTION_TARGETS: dict[str, dict[str, float]] = {
    "S": {"interactions": 1, "minutes": 30},
    "M": {"interactions": 3, "minutes": 240},
    "L": {"interactions": 6, "minutes": 1440},
}
"""Targets ``harness init`` writes: starting points to calibrate, not measured optima."""


class FrictionConfig(_Section):
    """Low friction for small changes (#58, items 1, 3, 5, 6 and 7)."""

    fast_lane: FastLaneConfig | None = Field(default=None, alias="fastLane")
    pre_authorization: PreAuthorizationConfig | None = Field(
        default=None, alias="preAuthorization"
    )
    change_types: bool | None = Field(default=None, alias="changeTypes")
    """Documentation-only or configuration-only ChangeSets (detected from the paths) do not
    require new tests or requirement traceability."""
    targets: dict[SizeName, FrictionTarget] | None = None
    """Friction targets per task size, reported by ``harness metrics`` and the brief."""


_PRICE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")


class ModelPrice(_Section):
    """Prices in US dollars per million tokens. Used only to estimate the cost of calls whose
    provider reported tokens without a cost; the estimate is labelled as such and never stored
    on a run or counted against the budget."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache: float | None = Field(default=None, ge=0)
    """Price of cached input tokens; without it cached tokens are priced as input."""


class NarrativeConfig(_Section):
    """The optional narrative summary of a period (#58, item 12): one call, on demand only
    (``harness metrics --narrative``), to a command that reads the prompt and the metrics on
    standard input and prints the summary."""

    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1, le=3600)

    @property
    def timeout(self) -> int:
        return self.timeout_seconds or 300


class MetricsConfig(_Section):
    """``harness metrics`` settings: the price table (by model id, or ``provider/model``) and
    the narrative command."""

    prices: dict[str, ModelPrice] | None = None
    narrative: NarrativeConfig | None = None

    @field_validator("prices")
    @classmethod
    def _price_keys(cls, value: dict[str, ModelPrice] | None) -> dict[str, ModelPrice] | None:
        for key in value or {}:
            if not _PRICE_KEY.match(key):
                raise ValueError(f"not a model id: {key!r}")
        return value


__all__ = [
    "DEFAULT_FAST_LANE_SKIP",
    "DEFAULT_FRICTION_TARGETS",
    "DEFAULT_PRE_AUTHORIZATION_HOURS",
    "FAST_LANE_STEPS",
    "MAX_PRE_AUTHORIZATION_HOURS",
    "FastLaneConfig",
    "FastLaneStep",
    "FastVerificationConfig",
    "FrictionConfig",
    "FrictionTarget",
    "MetricsConfig",
    "ModelPrice",
    "NarrativeConfig",
    "PreAuthorizationConfig",
    "SizeName",
]
