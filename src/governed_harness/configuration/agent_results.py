"""Configuration of the agent-results settings (since 1.1, issues #37-#44 and #52).

Every section and key here is optional. A key that is absent keeps the 1.0.0 behaviour and is
left out of the serialized configuration, so the configuration snapshot (and its digest) of a
project written before these settings existed does not change. ``harness init`` writes them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from governed_harness.domain.enums import FindingSeverity


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


Policy = Literal["enforce", "warn", "off"]
CallKind = Literal["implement", "clarify", "review", "plan", "acceptance", "architecture"]


class AgentCallConfig(_Section):
    """Which provider, model and effort an agent call of one kind uses.

    ``provider`` names an entry of ``agentProviders`` (default: the run's provider);
    ``model`` and ``effort`` are passed to the provider in the request (``routing``) and take
    precedence over ``agentRouting``."""

    provider: str | None = None
    model: str | None = None
    effort: str | None = None


# ----- INTENT (#37) -------------------------------------------------------------------------
AmbiguityReview = Literal["agent", "off"]


def off_from_yaml(value: Any) -> Any:
    # YAML 1.1 (PyYAML) reads a bare ``off`` as false.
    return "off" if value is False else value


# ----- VERIFICATION (#40, #52) --------------------------------------------------------------
class ForbiddenImport(_Section):
    """Modules under ``source`` must not import modules under ``target`` (dotted prefixes)."""

    source: str = Field(alias="from", min_length=1)
    target: str = Field(alias="to", min_length=1)


class ArchitectureConfig(_Section):
    """Engineering limits checked on the Python files of the ChangeSet (#40)."""

    max_module_lines: int | None = Field(default=None, alias="maxModuleLines", ge=1)
    max_function_lines: int | None = Field(default=None, alias="maxFunctionLines", ge=1)
    max_complexity: int | None = Field(default=None, alias="maxComplexity", ge=1)
    forbidden_imports: tuple[ForbiddenImport, ...] | None = Field(
        default=None, alias="forbiddenImports"
    )
    severity: FindingSeverity | None = None


class TestQualityConfig(_Section):
    """Test-quality checks on the ChangeSet (#52): assertion-free tests, coverage of changed
    lines, declared interface methods without tests and a second, reordered test run."""

    __test__ = False  # not a pytest test class

    assertions: bool | None = None
    diff_coverage: float | None = Field(default=None, alias="diffCoverage", ge=0, le=100)
    interface_tests: bool | None = Field(default=None, alias="interfaceTests")
    flaky_reruns: int | None = Field(default=None, alias="flakyReruns", ge=0, le=5)
    severity: FindingSeverity | None = None


class SarifInput(_Section):
    """A SARIF file an external scanner writes into the workspace (path relative to it)."""

    path: str = Field(min_length=1)
    tool: str | None = None
    required: bool | None = None

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.replace("\\", "/").split("/"):
            raise ValueError(f"a SARIF path must be relative to the workspace: {value!r}")
        return value


class InvariantCheck(_Section):
    """A command that must pass on every VERIFICATION, including each sub-task's."""

    invariant_id: str = Field(alias="id", min_length=1)
    command: tuple[str, ...]

    @field_validator("command")
    @classmethod
    def _not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("an invariant needs a command")
        return value


AcceptanceMode = Literal["agent", "off"]
DEFAULT_ACCEPTANCE_DIRECTORY = "tests/acceptance"


class AcceptanceTestsConfig(_Section):
    """Independent, frozen acceptance tests (#52, N4): written from the criteria by a separate
    call (``author``), approved by a person, checked to fail before the change and to pass,
    unchanged, after it."""

    mode: AcceptanceMode | None = None
    author: AgentCallConfig | None = None
    directory: str | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @field_validator("directory")
    @classmethod
    def _relative(cls, value: str | None) -> str | None:
        if value is not None and (
            value.startswith("/") or ".." in value.replace("\\", "/").split("/")
        ):
            raise ValueError(f"the acceptance directory must be relative: {value!r}")
        return value

    @property
    def enabled(self) -> bool:
        return self.mode == "agent"

    @property
    def path(self) -> str:
        return (self.directory or DEFAULT_ACCEPTANCE_DIRECTORY).rstrip("/")


RiskAction = Literal["block", "acknowledge", "inform", "off"]

RISK_FACTORS: tuple[str, ...] = (
    "newDependency",
    "authentication",
    "destructiveMigration",
    "publicContract",
    "network",
    "floatMoney",
    "sensitiveLogging",
    "deletedWithoutTests",
)
"""Risk factors of a sensitive change (#52), in the order they are reported."""

DEFAULT_RISK_ACTIONS: dict[str, RiskAction] = {
    "newDependency": "acknowledge",
    "authentication": "acknowledge",
    "destructiveMigration": "block",
    "publicContract": "acknowledge",
    "network": "acknowledge",
    "floatMoney": "block",
    "sensitiveLogging": "block",
    "deletedWithoutTests": "inform",
}
"""What ``harness init`` writes for ``verification.riskFactors``."""


# ----- PLANNING (#39) ------------------------------------------------------------------------
Decomposition = Literal["agent", "off"]
Granularity = Literal["fixed", "adaptive"]

DEFAULT_DECOMPOSITION_THRESHOLD = 12
DEFAULT_COARSE_MODELS: tuple[str, ...] = (
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "gpt-6.1-sol",
)
"""Models ``harness init`` lets start with a coarse (undecomposed) task under
``planning.granularity: adaptive``; a starting point from the evaluation, not a statement about
which models an account can use."""


class PlanningConfig(_Section):
    """Decomposition of large tasks into governed sub-tasks (#39)."""

    decomposition: Decomposition | None = None
    threshold: int | None = Field(default=None, ge=1, le=1000)
    planner: AgentCallConfig | None = None
    granularity: Granularity | None = None
    coarse_models: tuple[str, ...] | None = Field(default=None, alias="coarseModels")
    max_subtasks: int | None = Field(default=None, alias="maxSubtasks", ge=2, le=100)

    @field_validator("decomposition", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.decomposition == "agent"

    @property
    def requirement_threshold(self) -> int:
        return self.threshold or DEFAULT_DECOMPOSITION_THRESHOLD


# ----- IMPLEMENTATION context (#41) ----------------------------------------------------------
ContextManifestMode = Literal["auto", "off"]
DEFAULT_CONTEXT_MAX_FILES = 40
DEFAULT_CONTEXT_MAX_BYTES = 400_000


class ContextConfig(_Section):
    """The bounded context manifest sent to the agent (#41)."""

    manifest: ContextManifestMode | None = None
    max_files: int | None = Field(default=None, alias="maxFiles", ge=1, le=1000)
    max_bytes: int | None = Field(default=None, alias="maxBytes", ge=1024)

    @field_validator("manifest", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.manifest == "auto"


# ----- Budget (#42) --------------------------------------------------------------------------
class BudgetLimits(_Section):
    cost_usd: float | None = Field(default=None, alias="costUsd", gt=0)
    tokens: int | None = Field(default=None, ge=1)
    wall_seconds: int | None = Field(default=None, alias="wallSeconds", ge=1)


class BudgetConfig(_Section):
    """Limits per agent call, per task (all its runs) and per run (#42)."""

    per_call: BudgetLimits | None = Field(default=None, alias="perCall")
    per_task: BudgetLimits | None = Field(default=None, alias="perTask")
    per_run: BudgetLimits | None = Field(default=None, alias="perRun")
    warn_at: float | None = Field(default=None, alias="warnAt", gt=0, lt=1)

    @property
    def warning_ratio(self) -> float:
        return self.warn_at or 0.8


# ----- Memory (#43) --------------------------------------------------------------------------
LearnMode = Literal["auto", "off"]


class MemoryConfig(_Section):
    """Lessons from findings that caused a correction (#43)."""

    learn_from_findings: LearnMode | None = Field(default=None, alias="learnFromFindings")
    auto_approve_recurring: bool | None = Field(default=None, alias="autoApproveRecurring")
    recurrence_runs: int | None = Field(default=None, alias="recurrenceRuns", ge=2, le=100)
    max_lessons: int | None = Field(default=None, alias="maxLessons", ge=1, le=100)

    @field_validator("learn_from_findings", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.learn_from_findings == "auto"


# ----- Routing (#44) -------------------------------------------------------------------------
RoutingMode = Literal["fixed", "tiered"]
SizeClass = Literal["S", "M", "L"]
ProviderFamily = Literal["claude-code", "codex", "generic"]


class Rung(_Section):
    model: str = Field(min_length=1)
    effort: str | None = None


class SizeThresholds(_Section):
    """Upper bounds of ``S`` and ``M`` for each signal; above the ``M`` bound a task is ``L``."""

    requirements: tuple[int, int] | None = None
    files: tuple[int, int] | None = None
    loc: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _ordered(self) -> SizeThresholds:
        for name in ("requirements", "files", "loc"):
            pair = getattr(self, name)
            if pair is not None and not 0 <= pair[0] <= pair[1]:
                raise ValueError(f"{name} thresholds must be ascending non-negative numbers")
        return self


class FamilyTable(_Section):
    """The rung of each call kind and size for one provider family, and its escalation
    ladder (effort before model)."""

    implement: dict[SizeClass, Rung] | None = None
    clarify: Rung | None = None
    plan: Rung | None = None
    review: Rung | None = None
    ladder: tuple[Rung, ...] | None = None


class AgentRoutingConfig(_Section):
    """Model and effort of each agent call (#44): ``fixed`` keeps the provider's own model,
    ``tiered`` chooses from the tables by call kind and task size and escalates on quality
    failures."""

    mode: RoutingMode | None = None
    thresholds: SizeThresholds | None = None
    families: dict[str, ProviderFamily] | None = None
    tables: dict[ProviderFamily, FamilyTable] | None = None
    max_escalations: int | None = Field(default=None, alias="maxEscalations", ge=0, le=10)
    risk_paths: tuple[str, ...] | None = Field(default=None, alias="riskPaths")


DEFAULT_SIZE_THRESHOLDS: dict[str, tuple[int, int]] = {
    "requirements": (5, 15),
    "files": (5, 20),
    "loc": (2000, 10000),
}
"""Starting thresholds ``harness init`` writes (S up to the first value, M up to the second);
to be calibrated with ``harness routing calibrate``, not a measured optimum."""

DEFAULT_ROUTING_TABLES: dict[str, dict[str, Any]] = {
    "claude-code": {
        "implement": {
            "S": {"model": "claude-sonnet-5-5", "effort": "medium"},
            "M": {"model": "claude-sonnet-5-5", "effort": "high"},
            "L": {"model": "claude-opus-5-5", "effort": "high"},
        },
        "clarify": {"model": "claude-sonnet-5-5", "effort": "medium"},
        "plan": {"model": "claude-opus-5-5", "effort": "high"},
        "review": {"model": "claude-opus-5-5", "effort": "high"},
        "ladder": [
            {"model": "claude-sonnet-5-5", "effort": "medium"},
            {"model": "claude-sonnet-5-5", "effort": "high"},
            {"model": "claude-opus-5-5", "effort": "high"},
            {"model": "claude-opus-5-5", "effort": "xhigh"},
        ],
    },
    "codex": {
        "implement": {
            "S": {"model": "gpt-6-luna", "effort": "high"},
            "M": {"model": "gpt-6.1-sol", "effort": "medium"},
            "L": {"model": "gpt-6.1-sol", "effort": "high"},
        },
        "clarify": {"model": "gpt-6.1-sol", "effort": "medium"},
        "plan": {"model": "gpt-6.1-sol", "effort": "high"},
        "review": {"model": "gpt-6.1-sol", "effort": "high"},
        "ladder": [
            {"model": "gpt-6-luna", "effort": "high"},
            {"model": "gpt-6.1-sol", "effort": "medium"},
            {"model": "gpt-6.1-sol", "effort": "high"},
            {"model": "gpt-6.1-sol", "effort": "xhigh"},
        ],
    },
}
"""Starting tables (``.local`` research of 2026-10-04, summarised in docs): full model ids, effort
raised before the model, ``max``/``ultra`` left out. Availability for an account is not checked."""
