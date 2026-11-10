"""Configuration of the agent contract (wave 11, #97).

``agentContract`` turns on, for every agent call of a run, the versioned request and response
contract (``agents/contract.py``): the minimal request of each call kind, the limits of the
response, one cap of iterations per agent and a token budget and time limit derived from the
size of the task. Absent, the run keeps the earlier behaviour and its configuration digest;
``harness init`` writes the section."""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import Field, field_validator

from governed_harness.configuration.agent_results import SizeClass, _Section, off_from_yaml

AgentContractMode = Literal["enforce", "off"]

MAX_ITERATIONS_CEILING: Final = 6
"""The most iterations a configuration may allow an agent: a larger cap is rejected."""
DEFAULT_MAX_ITERATIONS: Final = 5
DEFAULT_SUMMARY_CHARS: Final = 600
DEFAULT_FIELD_CHARS: Final = 4000

DEFAULT_RESULT_BYTES: Final[dict[str, int]] = {
    "implement": 4_096,
    "clarify": 32_768,
    "plan": 32_768,
    "locate": 32_768,
    "review": 65_536,
    "architecture": 65_536,
    "acceptance": 262_144,
}
"""Largest ``result`` object (serialized JSON, UTF-8 bytes) a call kind may return."""

DEFAULT_SIZE_BUDGETS: Final[dict[str, tuple[int, int]]] = {
    "S": (50_000, 600),
    "M": (200_000, 1_800),
    "L": (800_000, 5_400),
}
"""Token budget and time limit (seconds) of one call by task size: starting values in a 1:4:16
ratio, to be calibrated from the recorded usage (``harness routing calibrate``), not a measured
optimum."""


class SizeBudget(_Section):
    """The token budget and the time limit of one agent call for a task size."""

    tokens: int | None = Field(default=None, ge=1000)
    wall_seconds: int | None = Field(default=None, alias="wallSeconds", ge=1)


class ResponseLimits(_Section):
    """Caps on what an agent may send back to its parent (per field)."""

    summary_chars: int | None = Field(default=None, alias="summaryChars", ge=40, le=5000)
    result_bytes: int | None = Field(default=None, alias="resultBytes", ge=256)
    field_chars: int | None = Field(default=None, alias="fieldChars", ge=80)


class AgentContractConfig(_Section):
    """The agent contract of a run (wave 11, #97).

    * ``maxIterations``: the cap of iterations of one agent in a run, at most 6 (default 5);
      an iteration is one call to the agent with its own outcome;
    * ``budgets``: token budget and time limit of a call by task size (S, M, L);
    * ``limits``: caps of the response (summary characters, result bytes, characters of any
      text field of the result)."""

    mode: AgentContractMode = "enforce"
    max_iterations: int | None = Field(
        default=None, alias="maxIterations", ge=1, le=MAX_ITERATIONS_CEILING
    )
    budgets: dict[SizeClass, SizeBudget] | None = None
    limits: ResponseLimits | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.mode == "enforce"

    @property
    def iterations(self) -> int:
        return self.max_iterations or DEFAULT_MAX_ITERATIONS

    def budget_for(self, size: SizeClass) -> tuple[int, int]:
        """The token budget and the time limit in seconds of a call for a task of ``size``."""
        tokens, seconds = DEFAULT_SIZE_BUDGETS[size]
        own = (self.budgets or {}).get(size)
        if own is None:
            return tokens, seconds
        return own.tokens or tokens, own.wall_seconds or seconds

    @property
    def summary_chars(self) -> int:
        return (self.limits.summary_chars if self.limits else None) or DEFAULT_SUMMARY_CHARS

    @property
    def field_chars(self) -> int:
        return (self.limits.field_chars if self.limits else None) or DEFAULT_FIELD_CHARS

    def result_bytes(self, kind: str) -> int:
        own = self.limits.result_bytes if self.limits else None
        return own or DEFAULT_RESULT_BYTES.get(kind, DEFAULT_RESULT_BYTES["clarify"])
