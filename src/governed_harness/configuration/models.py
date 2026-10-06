from __future__ import annotations

import re
from pathlib import Path
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

from governed_harness.configuration.agent_results import (
    AcceptanceTestsConfig,
    AgentCallConfig,
    AgentRoutingConfig,
    AmbiguityReview,
    ArchitectureConfig,
    BudgetConfig,
    ContextConfig,
    InvariantCheck,
    MemoryConfig,
    PlanningConfig,
    Policy,
    RiskAction,
    SarifInput,
    TestQualityConfig,
    off_from_yaml,
)
from governed_harness.configuration.api import ApiConfig
from governed_harness.configuration.engineering import (
    ArchitectureSettings,
    ForgeConfig,
    PrinciplesConfig,
    ProjectSetupMode,
    StandardsConfig,
    TestingConfig,
)
from governed_harness.configuration.friction import FrictionConfig, MetricsConfig
from governed_harness.configuration.ladder import (
    CommentPolicy,
    ContractMode,
    EnvironmentConfig,
    InstructionsConfig,
    InterruptionConfig,
    IsolationConfig,
    LadderConfig,
    MutationConfig,
    ProfileVerification,
    PullRequestConfig,
)
from governed_harness.configuration.review import ReviewPanelConfig
from governed_harness.domain.enums import FindingSeverity, PhaseId
from governed_harness.domain.models import ProbeDefinition


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class WorkspaceUnitConfig(ConfigModel):
    unit_id: str = Field(alias="unitId")
    root: str
    profile: str | None = None


SnapshotMode = Literal["walk", "git"]
BaselineMode = Literal["text", "manifest"]


class WorkspaceConfig(ConfigModel):
    """The workspace root and, since 1.1, how the workspace is read for large repositories.

    * ``snapshot: git`` lists the files through Git (tracked plus untracked files that
      ``.gitignore`` does not exclude), so ignored files such as ``.env`` or build output are
      neither hashed nor stored; ``walk`` (or absent) walks every file as in 1.0.0.
    * ``baseline: manifest`` stores the baseline as a manifest of digests; the text of a file
      is stored only when it enters the ChangeSet (taken from Git or from a per-file blob kept
      for untracked files), instead of the text of every file.
    * ``snapshotCache: true`` reuses the digest of a file whose size and modification time did
      not change since the last snapshot of the run.

    Absent keys keep the 1.0.0 behaviour and are left out of the serialized configuration."""

    root: str = ".."
    units: tuple[WorkspaceUnitConfig, ...] = Field(
        default=(),
        description="Declarative: declared units are not used by the engine.",
        json_schema_extra={"x-declarative": True},
    )
    snapshot: SnapshotMode | None = None
    baseline: BaselineMode | None = None
    snapshot_cache: bool | None = Field(default=None, alias="snapshotCache")
    isolation: IsolationConfig | None = None
    """Since #55: worktree isolation per run (``mode: worktree``)."""

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, alias in (
            ("snapshot", "snapshot"),
            ("baseline", "baseline"),
            ("snapshot_cache", "snapshotCache"),
            ("isolation", "isolation"),
        ):
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(alias, None)
        return data


class CapabilityRule(ConfigModel):
    capability: str
    scope: tuple[str, ...]
    approval_required: bool = Field(default=False, alias="approvalRequired")
    conditions: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesConfig(ConfigModel):
    """``grants`` are the project's capabilities. Under ``governance.phaseCapabilities`` (#4)
    they narrow the profiles' (``profile ∩ project``) and ``extend`` is the explicit way to add
    a scope no profile grants (a device lab's command, an agent CLI); without the key both are
    added to the profiles' scopes, as in 1.0.0."""

    default: Literal["deny"] = "deny"
    grants: tuple[CapabilityRule, ...] = ()
    extend: tuple[CapabilityRule, ...] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_extend(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.extend is None:
            data.pop("extend", None)
        return data


DEFAULT_TRANSIENT_PATTERNS: tuple[str, ...] = (
    "timed out",
    "connection reset",
    "went to sleep",
    "overloaded",
    "429",
    "529",
    "rate limit",
    "usage limit",
)
"""Messages that mark a command-provider failure as transient when
``runtime.providerTransientPatterns`` is not set (case-insensitive substrings)."""

DEFAULT_UNSUPPORTED_CLAIM_SEVERITY = FindingSeverity.MEDIUM

_OPTIONAL_RUNTIME_FIELDS = {
    "agent_sandbox": "agentSandbox",
    "sandbox_write_paths": "sandboxWritePaths",
    "verification_corrections": "verificationCorrections",
    "provider_feedback": "providerFeedback",
    "unsupported_claim_severity": "unsupportedClaimSeverity",
    "provider_retries": "providerRetries",
    "provider_retry_delay_seconds": "providerRetryDelaySeconds",
    "provider_transient_patterns": "providerTransientPatterns",
    "gate_contract": "gateContract",
    "reproduce_first": "reproduceFirst",
    "extended_redaction": "extendedRedaction",
    "state_dir": "stateDir",
}
"""Optional runtime keys left out of the serialized configuration while they are unset."""

AgentSandboxMode = Literal["enforce", "off"]

DEFAULT_AGENT_SANDBOX: AgentSandboxMode = "off"
"""Mode of a project.yaml without ``runtime.agentSandbox`` (files written before 1.1)."""

DEFAULT_SANDBOX_WRITE_PATHS: tuple[tuple[str, str], ...] = (
    # An allow-list entry of the agent's write sandbox, not a file the harness creates.
    ("/tmp", "Shared temporary directory (/private/tmp on macOS): compilers, npm and git use it."),  # nosec B108
    (
        "/var/folders",
        "Per-user temporary and cache directories of macOS (/private/var/folders); $TMPDIR "
        "lives here.",
    ),
    ("~/.claude", "Claude Code keeps its settings, session state, todos and logs here."),
    (
        "~/.claude.json*",
        "Claude Code rewrites its configuration file through temporary, backup and lock files "
        "next to it (~/.claude.json.backup, ~/.claude.json.lock, ...).",
    ),
    ("~/.cache", "XDG cache directory used by agent CLIs, pip, uv and many tools."),
    ("~/Library/Caches", "Per-user cache directory of macOS (CLI update checks, node caches)."),
    ("~/.config", "XDG configuration directory where agent CLIs keep state and credentials."),
    ("~/.npm", "npm cache and logs: npx-launched agents and MCP servers write here."),
)
"""Write paths ``harness init`` declares, with the reason for each. The workspace and the
resolved ``$TMPDIR`` are always writable and are not listed."""

_SANDBOX_PATH_FORBIDDEN = frozenset('"\\$?[]{}')


def _sandbox_mode_from_yaml(value: Any) -> Any:
    # YAML 1.1 (PyYAML) reads a bare ``off`` as false; a hand-written ``agentSandbox: off`` must
    # mean what it says.
    return "off" if value is False else value


class RuntimeConfig(ConfigModel):
    """Process bounds and, since 1.1, the agent sandbox and the feedback loop around the agent
    provider.

    The sandbox and loop settings are optional: a key that is absent keeps the 1.0.0 behaviour
    and is left out of the serialized configuration, so the snapshot digest of an existing
    project does not change. ``harness init`` writes them."""

    command_timeout_seconds: int = Field(default=900, alias="commandTimeoutSeconds", ge=1)
    max_output_bytes: int = Field(default=1_000_000, alias="maxOutputBytes", ge=1024)
    max_parallel: int = Field(
        default=2,
        alias="maxParallel",
        ge=1,
        le=32,
        description=(
            "Under governance.enforceWorkflow, the most validators declared parallelSafe that a "
            "parallelizable VERIFICATION runs at once; otherwise phases and validators run one "
            "at a time."
        ),
    )
    allow_network: bool = Field(
        default=False,
        alias="allowNetwork",
        description=(
            "Network access of the agent sandbox: false denies outbound connections under "
            "governance.applyNetworkPolicy and runtime.agentSandbox: enforce; otherwise "
            "declarative."
        ),
    )
    agent_sandbox: AgentSandboxMode | None = Field(default=None, alias="agentSandbox")
    sandbox_write_paths: tuple[str, ...] | None = Field(default=None, alias="sandboxWritePaths")

    @field_validator("agent_sandbox", mode="before")
    @classmethod
    def _bare_off_is_off(cls, value: Any) -> Any:
        return _sandbox_mode_from_yaml(value)

    @field_validator("sandbox_write_paths")
    @classmethod
    def _write_paths_are_absolute(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for path in value or ():
            if not (path == "~" or path.startswith(("/", "~/"))):
                raise ValueError(f"sandbox write path must be absolute or start with ~/: {path!r}")
            if any(ord(char) < 32 for char in path) or _SANDBOX_PATH_FORBIDDEN & set(path):
                raise ValueError(
                    f"sandbox write path must not contain quotes, backslashes, $, control "
                    f"characters or glob patterns other than a trailing *: {path!r}"
                )
            if "*" in path.rstrip("*") or path.count("*") > 1:
                raise ValueError(f"only a single trailing * is allowed: {path!r}")
            if path.rstrip("/*") == "":
                raise ValueError("the file system root is not a sandbox write path; use 'off'")
        return value

    @property
    def effective_agent_sandbox(self) -> AgentSandboxMode:
        """The agent sandbox mode: ``off`` when ``agentSandbox`` is absent."""
        return self.agent_sandbox or DEFAULT_AGENT_SANDBOX

    verification_corrections: int | None = Field(
        default=None, alias="verificationCorrections", ge=0, le=10
    )
    provider_feedback: bool | None = Field(default=None, alias="providerFeedback")
    unsupported_claim_severity: FindingSeverity | None = Field(
        default=None, alias="unsupportedClaimSeverity"
    )
    provider_retries: int | None = Field(default=None, alias="providerRetries", ge=0, le=10)
    provider_retry_delay_seconds: float | None = Field(
        default=None, alias="providerRetryDelaySeconds", ge=0, le=3600
    )
    provider_transient_patterns: tuple[str, ...] | None = Field(
        default=None, alias="providerTransientPatterns"
    )
    extended_redaction: bool | None = Field(default=None, alias="extendedRedaction")
    """Since 1.1: also redact model-API keys (``sk-ant-``, ``sk-``, ``AIza``), Slack tokens,
    JSON Web Tokens and credentials in URLs from every stored artifact and from the agent's
    summary. Absent or false keeps the 1.0.0 rules."""

    gate_contract: bool | None = Field(default=None, alias="gateContract")
    """Since 1.1 (#52): the implement request carries the gate contract (validators, review
    rules, blocking severities, the workspace path and the ``harness check`` command) and the
    agent's permissions derived from the capability grants."""
    reproduce_first: bool | None = Field(default=None, alias="reproduceFirst")
    """Since 1.1 (#52): a correction attempt that changes nothing is a finding, and a correction
    after REQUEST_CHANGES must add a test that fails before it and passes after it."""
    state_dir: str | None = Field(default=None, alias="stateDir")
    """Since #55: where the run registry (state database and artifacts) lives. ``auto`` is the
    user's data directory (``$HARNESS_STATE_DIR``, else the platform's application-data
    directory); an absolute path or one starting with ``~/`` is used as given. Absent: the
    1.0.0 ``.harness/state.db`` of the workspace."""

    @field_validator("state_dir")
    @classmethod
    def _state_dir(cls, value: str | None) -> str | None:
        if value is None or value == "auto":
            return value
        if not value.startswith(("/", "~/")) or "$" in value:
            raise ValueError(
                f"stateDir is auto, an absolute path or a path that starts with ~/: {value!r}"
            )
        return value

    @field_validator("provider_transient_patterns")
    @classmethod
    def patterns_must_not_be_blank(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and any(not item.strip() for item in value):
            raise ValueError("providerTransientPatterns must not contain blank patterns")
        return value

    @property
    def correction_limit(self) -> int:
        """Automatic corrections after a failed VERIFICATION: 0 when the key is absent."""
        return self.verification_corrections or 0

    @property
    def claim_check_enabled(self) -> bool:
        """The unsupported-claim check belongs to the verification loop: it runs when
        ``verificationCorrections`` is set, with any value."""
        return self.verification_corrections is not None

    @property
    def claim_severity(self) -> FindingSeverity:
        return self.unsupported_claim_severity or DEFAULT_UNSUPPORTED_CLAIM_SEVERITY

    @property
    def feedback_enabled(self) -> bool:
        return bool(self.provider_feedback)

    @property
    def retry_limit(self) -> int:
        return self.provider_retries or 0

    @property
    def retry_delay_seconds(self) -> float:
        return self.provider_retry_delay_seconds or 0.0

    @property
    def transient_patterns(self) -> tuple[str, ...]:
        if self.provider_transient_patterns is None:
            return DEFAULT_TRANSIENT_PATTERNS
        return self.provider_transient_patterns

    @model_serializer(mode="wrap")
    def _omit_absent_optional_settings(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        # Like the intake section: a file without the sandbox or loop keys serializes as before,
        # so the configuration snapshot (and its digest) of a project written for 1.0.0 does not
        # change.
        data: dict[str, Any] = handler(self)
        for name, alias in _OPTIONAL_RUNTIME_FIELDS.items():
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(alias, None)
        return data


AgentProviderKind = Literal["command", "claude-code", "codex", "gemini-cli", "aider"]
"""``command`` speaks the harness JSON protocol; the others are built-in adapters (since 1.1)
that run the agent CLI in its non-interactive mode and read its own output."""

NATIVE_PROVIDER_KINDS: tuple[str, ...] = ("claude-code", "codex", "gemini-cli", "aider")

NATIVE_DEFAULT_COMMANDS: dict[str, tuple[str, ...]] = {
    "claude-code": ("claude",),
    "codex": ("codex",),
    "gemini-cli": ("gemini",),
    "aider": ("aider",),
}
"""The executable a built-in adapter runs when the provider sets no ``command``."""

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class ProviderEnvReference(ConfigModel):
    """A provider environment variable whose value is read from the harness's environment
    when the provider starts: the value never appears in ``project.yaml`` or the snapshot."""

    from_env: str = Field(alias="fromEnv")

    @field_validator("from_env")
    @classmethod
    def _is_env_name(cls, value: str) -> str:
        if not _ENV_NAME.match(value):
            raise ValueError(f"fromEnv is not an environment variable name: {value!r}")
        return value


class AgentProviderConfiguration(ConfigModel):
    """An agent provider.

    Since 1.1 a provider may also declare ``kind`` (a built-in adapter), ``args`` (extra
    command-line arguments of a built-in adapter), ``passEnv`` (variables of the harness's
    environment passed as they are) and ``env`` (variables set for the provider, as a literal
    value or ``{fromEnv: NAME}``). Values that come from the environment are redacted from
    every artifact. Absent keys keep the 1.0.0 behaviour and are left out of the serialized
    configuration."""

    kind: AgentProviderKind = "command"
    command: tuple[str, ...] | None = None
    model: str | None = None
    args: tuple[str, ...] | None = None
    pass_env: tuple[str, ...] | None = Field(default=None, alias="passEnv")
    env: dict[str, str | ProviderEnvReference] | None = None

    @field_validator("command")
    @classmethod
    def command_must_not_be_empty(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and not value:
            raise ValueError("agent provider command must not be empty")
        return value

    @field_validator("pass_env")
    @classmethod
    def _pass_env_names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for name in value or ():
            if not _ENV_NAME.match(name):
                raise ValueError(f"passEnv entry is not an environment variable name: {name!r}")
        return value

    @field_validator("env")
    @classmethod
    def _env_names(
        cls, value: dict[str, str | ProviderEnvReference] | None
    ) -> dict[str, str | ProviderEnvReference] | None:
        for name in value or {}:
            if not _ENV_NAME.match(name):
                raise ValueError(f"env key is not an environment variable name: {name!r}")
        return value

    @model_validator(mode="after")
    def _command_for_command_kind(self) -> AgentProviderConfiguration:
        if self.kind == "command" and self.command is None:
            raise ValueError("a provider of kind command needs a command")
        if self.kind == "command" and self.args is not None:
            raise ValueError("args applies to the built-in adapters; put it in command")
        return self

    @property
    def native(self) -> bool:
        return self.kind in NATIVE_PROVIDER_KINDS

    @property
    def effective_command(self) -> tuple[str, ...]:
        """``command``, or the default executable of a built-in adapter."""
        return self.command or NATIVE_DEFAULT_COMMANDS.get(self.kind, ())

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if name in {"model", "kind"}:
                continue
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


CriteriaPolicy = Literal["enforce", "warn", "off"]

DEFAULT_CRITERIA_POLICY: CriteriaPolicy = "warn"
"""Policy of a project.yaml without ``intake.criteriaPolicy`` (files written before 1.1)."""


class IntakeConfig(ConfigModel):
    """How INTENT treats acceptance criteria that cannot be observed.

    ``enforce`` blocks INTENT until a person answers the clarification questions, ``warn``
    records the questions as evidence and low-severity findings and lets the run continue,
    ``off`` skips the assessment.

    Since 1.1 (#37): ``ambiguityReview: agent`` also asks an agent (call kind ``clarify``) for
    ambiguity and completeness questions once per task revision, ``clarifyAgent`` chooses its
    provider, model and effort, and ``validateAnswers`` checks a person's answers for references
    to documents or requirements the task and the workspace do not contain. Absent keys keep the
    1.0.0 behaviour."""

    criteria_policy: CriteriaPolicy = Field(default=DEFAULT_CRITERIA_POLICY, alias="criteriaPolicy")
    ambiguity_review: AmbiguityReview | None = Field(default=None, alias="ambiguityReview")
    clarify_agent: AgentCallConfig | None = Field(default=None, alias="clarifyAgent")
    validate_answers: bool | None = Field(default=None, alias="validateAnswers")
    operational_contract: ContractMode | None = Field(default=None, alias="operationalContract")
    """Since #55: the operational contract at intake. ``batch`` adds the contract questions to
    the one clarification request INTENT sends anyway and records the contract summary bound to
    the task digest; ``enforce`` also asks for every missing item and needs a person to confirm
    the summary (``harness task confirm``); ``off`` records nothing."""
    interruptions: InterruptionConfig | None = None
    """Since #55: the interruption budget and the stop conditions of a run."""

    project_setup: ProjectSetupMode | None = Field(default=None, alias="projectSetup")
    """Since 1.1 (#56): ``ask`` makes INTENT ask, once per project, the architecture, the
    testing strategy and the standards of a new project, and of an existing project whatever
    detection could not establish (rule ``P1``)."""

    @field_validator("ambiguity_review", "operational_contract", "project_setup", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def agent_review_enabled(self) -> bool:
        return self.ambiguity_review == "agent"

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name in (
            "ambiguity_review",
            "clarify_agent",
            "validate_answers",
            "operational_contract",
            "interruptions",
            "project_setup",
        ):
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(type(self).model_fields[name].alias or name, None)
        return data


RequirementTraceabilityPolicy = Literal["enforce", "warn", "off"]

DEFAULT_REQUIREMENT_TRACEABILITY: RequirementTraceabilityPolicy = "off"
"""Policy of a project.yaml without ``verification.requirementTraceability`` (files written
before 1.1): no traceability check, as in 1.0.0."""


class VerificationConfig(ConfigModel):
    """What VERIFICATION does with identified requirements that no test names.

    ``enforce`` records each one as a ``HIGH`` finding, which fails the gate under the default
    ``findingBlockSeverities``; ``warn`` records it as a ``LOW`` finding; ``off`` skips the
    check."""

    requirement_traceability: RequirementTraceabilityPolicy = Field(
        default=DEFAULT_REQUIREMENT_TRACEABILITY, alias="requirementTraceability"
    )
    output_parsers: bool | None = Field(default=None, alias="outputParsers")
    """Since 1.1: parse the output of failing command validators (JUnit XML, Ruff, Mypy, ESLint,
    tsc, SARIF, pytest) into one finding per reported problem, with file, line and rule. Absent
    or false keeps the single summary finding of 1.0.0."""

    # ----- since 1.1, agent results (#40, #52); every key absent keeps 1.0.0 -----------------
    interface: Policy | None = None
    """Conformance of a task's declared interface (``metadata.interface``): ``enforce`` makes a
    mismatch HIGH, ``warn`` LOW."""
    architecture: ArchitectureConfig | None = None
    security_patterns: bool | None = Field(default=None, alias="securityPatterns")
    """Unrestricted pickle/marshal of stored data, persisted card numbers or CVC, and weak
    password hashing are HIGH findings."""
    constraints: Policy | None = None
    """Verifiable task constraints (standard library only, no clock or random, annotated public
    API, no float money, no stubs) recognised from the task or listed in ``metadata.checks``."""
    ratchet: Policy | None = None
    """Optional validators (Ruff, Mypy) must not report more problems than on the baseline."""
    invariants: tuple[InvariantCheck, ...] | None = None
    differential: bool | None = None
    """Run a failing mandatory validator on the baseline and block only introduced failures."""
    weakened_controls: Policy | None = Field(default=None, alias="weakenedControls")
    test_quality: TestQualityConfig | None = Field(default=None, alias="testQuality")
    secrets: Literal["context", "pattern"] | None = None
    """``context``: the secret check of the independent review weighs where a literal is
    (tests, environment assignments, values the task declares) and detects evasion."""
    sarif: tuple[SarifInput, ...] | None = None
    risk_factors: dict[str, RiskAction] | None = Field(default=None, alias="riskFactors")
    acceptance_tests: AcceptanceTestsConfig | None = Field(default=None, alias="acceptanceTests")
    reverify_on_change: bool | None = Field(default=None, alias="reverifyOnChange")
    """Since 2.0 (#78): ``run continue`` on a run waiting in DECISION whose ChangeSet changed
    outside the run records the change as evidence and runs VERIFICATION again on the new
    ChangeSet. Absent or false keeps the earlier behaviour: the gate of the new ChangeSet has
    no validation of it and is ``INCONCLUSIVE``, so the change needs a new run or a
    ``REQUEST_CHANGES``."""
    # ----- since #55: the verification ladder ----------------------------------------------
    ladder: LadderConfig | None = None
    probes: tuple[ProbeDefinition, ...] | None = None
    """Behaviour probes of the project (each task may declare more)."""
    mutation: MutationConfig | None = None
    principles: PrinciplesConfig | None = None
    """Since 1.1 (#56): engineering principles as deterministic proxies (duplication, size and
    complexity, dependency direction, inheritance depth, unused public API, Boy Scout scope) and
    a checklist inside the existing review call."""

    @field_validator("probes")
    @classmethod
    def _unique_probes(
        cls, value: tuple[ProbeDefinition, ...] | None
    ) -> tuple[ProbeDefinition, ...] | None:
        ids = [item.probe_id for item in value or ()]
        if len(set(ids)) != len(ids):
            raise ValueError("probe ids must be unique")
        return value

    @field_validator("interface", "constraints", "ratchet", "weakened_controls", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @field_validator("risk_factors")
    @classmethod
    def _known_factors(cls, value: dict[str, RiskAction] | None) -> dict[str, RiskAction] | None:
        from governed_harness.configuration.agent_results import RISK_FACTORS

        unknown = sorted(set(value or {}) - set(RISK_FACTORS))
        if unknown:
            raise ValueError(
                f"unknown risk factor(s): {', '.join(unknown)}; known: {', '.join(RISK_FACTORS)}"
            )
        return value

    @model_serializer(mode="wrap")
    def _omit_absent_parsers(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if name != "requirement_traceability" and getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


DEFAULT_EXCEPTION_DAYS = 30
"""Validity of an exception when neither the decision nor ``review.exceptionDays`` sets one."""


class ReviewConfig(ConfigModel):
    """How a person's exceptions are recorded (since 1.1).

    With ``exceptions: true`` an ``APPROVE_EXCEPTION`` decision records an exception with an
    expiry, a scope (the findings it covers, by rule, path and fingerprint), optional
    alternative evidence and a follow-up. While it is in force, a later run of the project does
    not block on the findings it covers; once it expires they block again, and a run waiting in
    DECISION on an expired exception is blocked. Absent or false keeps the 1.0.0 behaviour: an
    exception is a decision with a rationale and no expiry."""

    exceptions: bool | None = None
    exception_days: int | None = Field(default=None, alias="exceptionDays", ge=1, le=365)
    agent_review: Policy | None = Field(default=None, alias="agentReview")
    """Since 1.1 (#38): a second agent (call kind ``review``) reviews the ChangeSet in
    INDEPENDENT_REVIEW; under ``enforce`` its HIGH and CRITICAL findings block the gate."""
    reviewer: AgentCallConfig | None = None
    structured_changes: bool | None = Field(default=None, alias="structuredChanges")
    """Since 1.1 (#52): REQUEST_CHANGES may carry blocking items with a verifiable condition
    that VERIFICATION checks until the run closes."""
    manual_checklist: bool | None = Field(default=None, alias="manualChecklist")
    """Since #55: items only a person can verify (``manual`` criteria and the task's
    ``checklist``) are ticked in DECISION (``gate decide --check``); APPROVE needs them all."""
    panel: ReviewPanelConfig | None = None
    """Since #57: the review panel (reviewers by domain, layered rule catalog, output
    contract, deterministic verdict) replaces the single reviewer in INDEPENDENT_REVIEW and runs
    outside governed runs with ``harness review-code``."""

    @field_validator("agent_review", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


NotificationEvent = Literal["decision.pending", "run.finished", "exception.granted"]

_WEBHOOK_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class WebhookConfig(ConfigModel):
    """A URL that receives a JSON ``POST`` when a run waits for a decision or finishes.

    ``urlEnv`` names an environment variable that holds the URL, so a URL that carries a token
    (Slack, Teams) stays out of ``project.yaml`` and of the configuration snapshot. The payload
    carries identifiers, statuses and digests only: no rationale, no output, no secret."""

    url: str | None = None
    url_env: str | None = Field(default=None, alias="urlEnv")
    events: tuple[NotificationEvent, ...] = ("decision.pending", "run.finished")
    retries: int = Field(default=2, ge=0, le=10)
    timeout_seconds: float = Field(default=5.0, alias="timeoutSeconds", gt=0, le=60)

    @model_validator(mode="after")
    def _one_target(self) -> WebhookConfig:
        if (self.url is None) == (self.url_env is None):
            raise ValueError("a webhook needs exactly one of url or urlEnv")
        if self.url is not None and not self.url.startswith(("http://", "https://")):
            raise ValueError("a webhook url must start with http:// or https://")
        if self.url_env is not None and not _WEBHOOK_ENV_NAME.match(self.url_env):
            raise ValueError(f"urlEnv is not an environment variable name: {self.url_env!r}")
        if not self.events:
            raise ValueError("a webhook needs at least one event")
        return self


class NotificationsConfig(ConfigModel):
    """Webhooks notified when a run waits for a human decision or finishes (since 1.1).
    Absent: nothing is sent, as in 1.0.0."""

    webhooks: tuple[WebhookConfig, ...] = ()


class RetrospectiveConfig(ConfigModel):
    """With ``causal: true`` (since 1.1) the retrospective attributes every blocked gate and
    correction cycle to the reason code, validator or rule that caused it, ignores optional
    validators that had no effect on the gate, and is also generated when a run is rejected or
    cancelled. Absent or false keeps the 1.0.0 retrospective."""

    causal: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.causal is None:
            data.pop("causal", None)
        return data


DeciderIdentity = Literal["git", "default"]
StopTheLine = Literal["restore", "block", "off"]
ChainAnchorMode = Literal["file", "git-note", "off"]

DEFAULT_TRUSTED_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
"""Host names ``harness init`` lets the local API answer to (``governance.trustedHosts``)."""

DEFAULT_DECISION_EXPIRY_HOURS = 72
"""Validity of a human decision that ``harness init`` writes (``governance.decisionExpiryHours``)."""


class GovernanceConfig(ConfigModel):
    """Integrity settings of the human decisions, the workspace and the record (since 1.1).

    Every key is optional: a key that is absent keeps the 1.0.0 behaviour and is left out of
    the serialized configuration, so the snapshot digest of an existing project does not
    change. ``harness init`` writes them all.

    * ``deciderIdentity``: ``git`` records the Git user (``user.email``, ``user.name``) as the
      person who decides when no ``--actor`` is given; ``default`` (or absent) keeps the fixed
      ``human.local`` and ``human.web`` identifiers.
    * ``confirmDecisionDigest``: on a terminal, ``harness gate decide`` shows the decision and
      asks for the first characters of the ChangeSet digest before recording it.
    * ``trustedHosts``: the local API answers only requests whose ``Host`` is one of these
      names (DNS-rebinding protection); absent, every host is accepted.
    * ``verifyRecords``: ``trace`` (every format) verifies the run first (event chain, records
      against events, artifact digests, anchor) and refuses to export a run that fails, and
      ``status`` adds the result of the record check.
    * ``chainAnchor``: ``file`` or ``git-note`` keeps a copy of the head of each run's event
      chain outside ``.harness`` so that ``harness verify`` detects a truncated chain.
    * ``pinTaskRevision``: a run works on the task revision it was created with (or a revision
      made through ``task clarify``), ``task create`` refuses a task id that has an open run, and
      a decision is bound to the acceptance-contract digest frozen in SPECIFICATION.
    * ``protectExcludedPaths``: what the ChangeSet leaves out (``.git``, ``.harness``, virtual
      environments, ``node_modules``, ``dist``, ``build``, symbolic links) is fingerprinted
      around every agent invocation and a change is a ``CRITICAL`` finding that fails the gate;
      the agent sandbox keeps ``.harness`` and ``.git`` read-only, and the profiles' write grants
      on ``.harness/**`` and ``.git/**`` are dropped.
    * ``workspaceLease``: one harness process at a time executes phases in a workspace
      (``.harness/lease.json`` with pid, host and heartbeat); an interrupted phase is recorded
      as ``INTERRUPTED``, and ``run continue`` terminates the process groups a killed harness
      left and restores the workspace an interrupted IMPLEMENTATION attempt found.
    * ``applyWorkflowSettings``: the workflow's per-phase ``maxAttempts``, ``timeoutSeconds`` and
      ``exitGate`` take effect.
    * ``decisionExpiryHours``: a human decision expires (``expiresAt``) that many hours after it
      is recorded; an expired decision does not let DECISION pass.
    * ``applyProfilePolicies``: the profile policies ``missingTestCommand`` and
      ``missingTestScript`` set the status of an unavailable mandatory validator, and a
      ``coverage`` policy with ``minimumPercent`` adds a mandatory coverage validator (Python).
    * ``applyNetworkPolicy``: ``runtime.allowNetwork: false`` denies outbound network access
      to the agent under ``runtime.agentSandbox: enforce``.
    * ``stopTheLine`` (#52): when a run stops without an approval (rejected, failed, timed
      out, cancelled), ``restore`` keeps its changes as a quarantined patch and restores the
      workspace to the baseline, ``block`` refuses new runs in the workspace until a person
      quarantines them (``harness run quarantine``).
    * ``phasePermissions`` (#52): every agent request carries the permissions of its call kind,
      derived from the capability grants (read-only for clarify, review and plan), recorded as
      evidence.
    * ``phaseCapabilities`` (#4): the grants are the profile's narrowed by the project's (a
      project narrows, never widens, a profile), every grant made while a phase runs keeps only
      the capabilities the phase allows (the workflow's ``allowedCapabilities``), an agent call
      outside IMPLEMENTATION is read-only and may start only its own command, and the resolved
      grants of each phase attempt are recorded as evidence.
    * ``applyRepositoryPolicies`` (#5): ``policies.repositoryContentTrusted: false`` makes
      repository content (instruction files included) quoted, untrusted context of every agent
      request with a prompt-injection notice, and the review panel flags instructions in changed
      files; ``policies.destructiveActionsDefault: deny`` refuses destructive commands (recursive
      deletes outside the workspace, force pushes, history rewrites, dropped data, ownership or
      permission changes outside the workspace) unless a ``process.destructive`` grant allows
      them, with a finding for each attempt.
    * ``enforceWorkflow`` (#3): the workflow's ``exitGate`` and transition conditions are
      evaluated from the run's records after a phase attempt passes (an unmet condition leaves
      the attempt ``BLOCKED`` with a ``phase.exit_gate.unmet`` event), ``dependsOn`` decides
      which phase may start, and ``parallelizable`` lets the validators declared
      ``parallelSafe`` of a parallelizable ``VERIFICATION`` run at once, up to
      ``runtime.maxParallel``. An unknown gate or condition is a configuration error."""

    decider_identity: DeciderIdentity | None = Field(default=None, alias="deciderIdentity")
    confirm_decision_digest: bool | None = Field(default=None, alias="confirmDecisionDigest")
    trusted_hosts: tuple[str, ...] | None = Field(default=None, alias="trustedHosts")
    verify_records: bool | None = Field(default=None, alias="verifyRecords")
    chain_anchor: ChainAnchorMode | None = Field(default=None, alias="chainAnchor")
    pin_task_revision: bool | None = Field(default=None, alias="pinTaskRevision")
    protect_excluded_paths: bool | None = Field(default=None, alias="protectExcludedPaths")
    workspace_lease: bool | None = Field(default=None, alias="workspaceLease")
    apply_workflow_settings: bool | None = Field(default=None, alias="applyWorkflowSettings")
    decision_expiry_hours: int | None = Field(
        default=None, alias="decisionExpiryHours", ge=1, le=8760
    )
    apply_profile_policies: bool | None = Field(default=None, alias="applyProfilePolicies")
    apply_network_policy: bool | None = Field(default=None, alias="applyNetworkPolicy")
    stop_the_line: StopTheLine | None = Field(default=None, alias="stopTheLine")
    phase_permissions: bool | None = Field(default=None, alias="phasePermissions")
    phase_capabilities: bool | None = Field(default=None, alias="phaseCapabilities")
    apply_repository_policies: bool | None = Field(default=None, alias="applyRepositoryPolicies")
    enforce_workflow: bool | None = Field(default=None, alias="enforceWorkflow")

    @field_validator("stop_the_line", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @field_validator("trusted_hosts")
    @classmethod
    def _hosts_are_names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for host in value or ():
            if not host.strip() or any(char.isspace() or char == "/" for char in host):
                raise ValueError(f"trusted host must be a host name or address: {host!r}")
        return value

    @property
    def git_decider(self) -> bool:
        return self.decider_identity == "git"

    @model_serializer(mode="wrap")
    def _omit_absent_settings(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


class ProjectConfiguration(ConfigModel):
    config_version: Literal["1.0"] = Field(alias="configVersion")
    project_id: str = Field(alias="projectId")
    workspace: WorkspaceConfig
    profiles: tuple[str, ...] = ("auto",)
    workflow: str = "default_development"
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    validators: tuple[str, ...] = ()
    policies: dict[str, Any] = Field(default_factory=dict)
    agent_provider: str = Field(default="simulated", alias="agentProvider")
    agent_providers: dict[str, AgentProviderConfiguration] = Field(
        default_factory=dict, alias="agentProviders"
    )
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    retention: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "artifactDays and eventDays: what harness gc removes for runs that ended longer ago."
        ),
    )
    intake: IntakeConfig | None = None
    verification: VerificationConfig | None = None
    governance: GovernanceConfig | None = None
    review: ReviewConfig | None = None
    notifications: NotificationsConfig | None = None
    retrospective: RetrospectiveConfig | None = None
    planning: PlanningConfig | None = None
    context: ContextConfig | None = None
    budget: BudgetConfig | None = None
    memory: MemoryConfig | None = None
    agent_routing: AgentRoutingConfig | None = Field(default=None, alias="agentRouting")
    toolchain: ToolchainConfig | None = None
    provenance: ProvenanceConfig | None = None
    delivery: DeliveryConfig | None = None
    environment: EnvironmentConfig | None = None
    """Since #55: the generic environment preflight of DISCOVERY and ``harness doctor``."""
    instructions: InstructionsConfig | None = None
    """Since #55: the agent instruction files ``harness config lint`` reads."""
    standards: StandardsConfig | None = None
    testing: TestingConfig | None = None
    architecture: ArchitectureSettings | None = None
    friction: FrictionConfig | None = None
    """Since #58: the fast lane, pre-authorised approval, change types and friction targets."""
    metrics: MetricsConfig | None = None
    """Since #58: the price table and the narrative command of ``harness metrics``."""
    api: ApiConfig | None = None
    """Since #18: authentication and roles of ``harness api serve``; absent, no authentication
    as in 1.0.0."""

    @property
    def friction_settings(self) -> FrictionConfig:
        """The friction settings, all absent (1.0.0 behaviour) when the section is."""
        return self.friction or FrictionConfig()

    @property
    def api_settings(self) -> ApiConfig:
        """The API settings: authentication off (1.0.0 behaviour) when the section is absent."""
        return self.api or ApiConfig(auth="off")

    @property
    def toolchain_settings(self) -> ToolchainConfig:
        return self.toolchain or ToolchainConfig()

    @property
    def provenance_settings(self) -> ProvenanceConfig:
        return self.provenance or ProvenanceConfig()

    @property
    def delivery_settings(self) -> DeliveryConfig:
        return self.delivery or DeliveryConfig()

    @property
    def governance_settings(self) -> GovernanceConfig:
        """The governance settings, all absent (1.0.0 behaviour) when the section is."""
        return self.governance or GovernanceConfig()

    @property
    def output_parsers_enabled(self) -> bool:
        """Whether failing command validators are parsed into located findings."""
        return bool(self.verification and self.verification.output_parsers)

    @property
    def exceptions_enabled(self) -> bool:
        """Whether APPROVE_EXCEPTION records a scoped exception with an expiry."""
        return bool(self.review and self.review.exceptions)

    @property
    def exception_days(self) -> int:
        days = self.review.exception_days if self.review else None
        return days or DEFAULT_EXCEPTION_DAYS

    @property
    def causal_retrospective(self) -> bool:
        return bool(self.retrospective and self.retrospective.causal)

    @property
    def webhooks(self) -> tuple[WebhookConfig, ...]:
        return self.notifications.webhooks if self.notifications else ()

    @field_validator("profiles")
    @classmethod
    def at_least_one_profile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            return ("auto",)
        return value

    @property
    def criteria_policy(self) -> CriteriaPolicy:
        """The effective acceptance-criteria policy: ``warn`` when ``intake`` is absent."""
        return self.intake.criteria_policy if self.intake else DEFAULT_CRITERIA_POLICY

    @property
    def requirement_traceability(self) -> RequirementTraceabilityPolicy:
        """The effective requirement-traceability policy: ``off`` when ``verification`` is
        absent."""
        return (
            self.verification.requirement_traceability
            if self.verification
            else DEFAULT_REQUIREMENT_TRACEABILITY
        )

    @model_serializer(mode="wrap")
    def _omit_absent_intake(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # A file without the section serializes as before, so the configuration snapshot
        # (and its digest) of a project written for 1.0.0 does not change.
        data: dict[str, Any] = handler(self)
        if self.intake is None:
            data.pop("intake", None)
        if self.verification is None:
            data.pop("verification", None)
        for section in (
            "governance",
            "review",
            "notifications",
            "retrospective",
            "planning",
            "context",
            "budget",
            "memory",
            "toolchain",
            "provenance",
            "delivery",
            "environment",
            "instructions",
            "standards",
            "testing",
            "architecture",
            "friction",
            "metrics",
            "api",
        ):
            if getattr(self, section) is None:
                data.pop(section, None)
        if self.agent_routing is None:
            data.pop("agent_routing", None)
            data.pop("agentRouting", None)
        return data


class DetectorMarker(ConfigModel):
    marker: str
    weight: float = Field(gt=0, le=1)


OutputParser = Literal[
    "auto",
    "sarif",
    "junit",
    "ruff",
    "mypy",
    "eslint",
    "tsc",
    "pytest",
    "checkstyle",
    "rubocop",
    "cargo",
    "msbuild",
    "none",
]
IssueLevel = Literal["error", "warning", "note"]


class ValidatorDefinition(ConfigModel):
    """A validator of a profile or, since 1.1, of the project (``toolchain.validators``).

    The keys added in 1.1 (``parser``, ``severity``, ``failureSeverity``, ``passEnv``,
    ``parallelSafe``) are
    left out of the serialized definition while they are absent, so a resolved configuration
    without them keeps its digest."""

    validator_id: str = Field(alias="id")
    command: tuple[str, ...] | None = None
    mandatory: bool = True
    when_available: bool = Field(default=False, alias="whenAvailable")
    script: str | None = None
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1)
    parser: OutputParser | None = None
    """Parse a failing run's output into located findings with this parser (``auto``: every
    known format, as ``verification.outputParsers``; ``none``: never). Absent: follow
    ``verification.outputParsers``."""
    severity: dict[IssueLevel, FindingSeverity] | None = None
    """Severity of a parsed issue by its level (default: error as the failure finding, warning
    LOW, note INFO)."""
    failure_severity: FindingSeverity | None = Field(default=None, alias="failureSeverity")
    """Severity of the finding of a failing run (default HIGH when mandatory, MEDIUM
    otherwise)."""
    pass_env: tuple[str, ...] | None = Field(default=None, alias="passEnv")
    """Variables of the harness's environment the command receives as they are."""
    parallel_safe: bool | None = Field(default=None, alias="parallelSafe")
    """The command writes nothing another validator reads or writes, so under
    ``governance.enforceWorkflow`` it may run at the same time as the other parallel-safe
    validators of a parallelizable VERIFICATION (#3). Absent: it runs alone, as in 1.0.0."""

    @field_validator("pass_env")
    @classmethod
    def _pass_env_names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for name in value or ():
            if not _ENV_NAME.match(name):
                raise ValueError(f"passEnv entry is not an environment variable name: {name!r}")
        return value

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, alias in (
            ("parser", "parser"),
            ("severity", "severity"),
            ("failure_severity", "failureSeverity"),
            ("pass_env", "passEnv"),
            ("parallel_safe", "parallelSafe"),
        ):
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(alias, None)
        return data


class TechnologyProfileDefinition(ConfigModel):
    profile_version: Literal["1.0"] = Field(alias="profileVersion")
    profile_id: str = Field(alias="profileId")
    technology: str
    detectors: tuple[DetectorMarker, ...]
    default_commands: dict[str, tuple[str, ...]] = Field(
        default_factory=dict, alias="defaultCommands"
    )
    default_validators: tuple[str, ...] = Field(default=(), alias="defaultValidators")
    validators: tuple[ValidatorDefinition, ...] = ()
    policies: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    verification: ProfileVerification | None = None
    """Since #55: the profile's verification capabilities per rung of the ladder. Left out of
    the serialized profile when absent."""

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.verification is None:
            data.pop("verification", None)
        return data


class WorkflowPhaseDefinition(ConfigModel):
    phase_id: PhaseId = Field(alias="id")
    purpose: str
    depends_on: tuple[PhaseId, ...] = Field(
        default=(),
        alias="dependsOn",
        description=(
            "Earlier phases whose latest attempt must have passed before this phase starts, "
            "under governance.enforceWorkflow (the order of the nine phases stays fixed)."
        ),
    )
    required: bool = True
    parallelizable: bool = Field(
        default=False,
        description=(
            "Under governance.enforceWorkflow, the phase's independent read-only work may run "
            "at once: the validators declared parallelSafe of VERIFICATION. Phases themselves "
            "and the sub-tasks of a decomposition run one at a time on the run's workspace."
        ),
    )
    allowed_capabilities: tuple[str, ...] = Field(
        default=(),
        alias="allowedCapabilities",
        description=(
            "Capabilities a grant made while the phase runs may carry, under "
            "governance.phaseCapabilities (issue #4); without it grants are per run."
        ),
    )
    validators: tuple[str, ...] = Field(
        default=(),
        description="Declarative: the validators come from the profiles and the project.",
        json_schema_extra={"x-declarative": True},
    )
    exit_gate: str = Field(
        alias="exitGate",
        description=(
            "Name of the condition the phase must meet to pass: evaluated from the run's "
            "records under governance.enforceWorkflow (an unmet one leaves the attempt "
            "BLOCKED); recorded with each attempt (exitGate, exitGateMet) under "
            "governance.applyWorkflowSettings or governance.enforceWorkflow."
        ),
    )
    timeout_seconds: int = Field(
        default=1800,
        alias="timeoutSeconds",
        ge=1,
        description=(
            "Wall-clock budget of one attempt of the phase under governance.applyWorkflowSettings."
        ),
    )
    max_attempts: int = Field(
        default=1,
        alias="maxAttempts",
        ge=1,
        le=20,
        description=(
            "Failed attempts of the phase after which it is not started again, under "
            "governance.applyWorkflowSettings."
        ),
    )


class WorkflowTransitionDefinition(ConfigModel):
    source: PhaseId = Field(alias="from")
    target: PhaseId = Field(alias="to")
    condition: str
    invalidates: tuple[PhaseId, ...] = ()


class WorkflowDefinition(ConfigModel):
    workflow_version: Literal["1.0"] = Field(alias="workflowVersion")
    workflow_id: str = Field(alias="workflowId")
    description: str = ""
    phases: tuple[WorkflowPhaseDefinition, ...]
    transitions: tuple[WorkflowTransitionDefinition, ...]
    invariants: tuple[str, ...] = Field(
        default=(),
        description="Declarative: names of invariants the engine enforces in code.",
        json_schema_extra={"x-declarative": True},
    )

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowDefinition:
        primary = tuple(phase.phase_id for phase in self.phases)
        expected = (
            PhaseId.INTENT,
            PhaseId.DISCOVERY,
            PhaseId.SPECIFICATION,
            PhaseId.PLANNING,
            PhaseId.IMPLEMENTATION,
            PhaseId.VERIFICATION,
            PhaseId.INDEPENDENT_REVIEW,
            PhaseId.DECISION,
            PhaseId.CLOSURE,
        )
        if primary != expected:
            raise ValueError(f"normative phases must remain ordered and complete: {expected}")
        return self


class ResolvedConfiguration(ConfigModel):
    project: ProjectConfiguration
    workspace_root: Path
    profiles: tuple[TechnologyProfileDefinition, ...]
    workflow: WorkflowDefinition
    effective_capabilities: tuple[CapabilityRule, ...]
    effective_validators: tuple[ValidatorDefinition, ...]
    effective_policies: dict[str, Any]
    source_files: tuple[str, ...]


# ----- 1.1 sections of wave 4 (integration and scale) ----------------------------------------
ProfileDetection = Literal["best", "all"]
InterpreterMode = Literal["system", "auto"]


def _omit_none(model: BaseModel, data: dict[str, Any]) -> dict[str, Any]:
    for name, field in type(model).model_fields.items():
        if getattr(model, name) is None:
            data.pop(name, None)
            data.pop(field.alias or name, None)
    return data


class ToolchainConfig(ConfigModel):
    """Project-defined profiles, validators and interpreter (since 1.1).

    * ``profilePaths``: YAML files (or directories of ``*.yaml`` files) with technology
      profiles in the format of the built-in ones, relative to the workspace root. ``profiles``
      may name them by ``profileId`` and ``auto`` detects them by their ``detectors``.
    * ``profileDetection: all`` selects every detected profile under ``profiles: [auto]``
      (several profiles per repository); ``best`` (or absent) selects the best one.
    * ``interpreter: auto`` runs the validators whose command starts with ``python`` with the
      project's interpreter: ``.venv`` or ``venv`` in the workspace, else ``uv run --no-sync
      python`` with ``uv.lock``, else ``poetry run python`` with ``poetry.lock``; ``system``
      (or absent) keeps ``python`` from ``PATH``.
    * ``validators``: validators of the project. An entry with the id of a selected validator
      replaces its definition (for example ``python.pytest`` with ``uv run pytest``); any other
      entry is added. Each needs a ``command`` and may set ``parser``, ``severity``,
      ``failureSeverity`` and ``passEnv``.

    Absent keys keep the 1.0.0 behaviour and are left out of the serialized configuration."""

    profile_paths: tuple[str, ...] | None = Field(default=None, alias="profilePaths")
    profile_detection: ProfileDetection | None = Field(default=None, alias="profileDetection")
    interpreter: InterpreterMode | None = None
    validators: tuple[ValidatorDefinition, ...] | None = None
    extended_profiles: bool | None = Field(default=None, alias="extendedProfiles")
    """Since #55: also detect the built-in profiles of Go, Rust, Java/Kotlin (Gradle and
    Maven), Swift and Android."""

    @field_validator("profile_paths")
    @classmethod
    def _relative_paths(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for item in value or ():
            if not item.strip() or Path(item).is_absolute() or ".." in Path(item).parts:
                raise ValueError(f"profile path must be relative to the workspace: {item!r}")
        return value

    @field_validator("validators")
    @classmethod
    def _validators_have_commands(
        cls, value: tuple[ValidatorDefinition, ...] | None
    ) -> tuple[ValidatorDefinition, ...] | None:
        seen: set[str] = set()
        for item in value or ():
            if not item.command:
                raise ValueError(f"project validator {item.validator_id!r} needs a command")
            if item.validator_id in seen:
                raise ValueError(f"project validator {item.validator_id!r} is declared twice")
            seen.add(item.validator_id)
        return value

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_none(self, handler(self))


class ProvenanceConfig(ConfigModel):
    """Provenance per component (since 1.1).

    * ``agentSnapshots``: after each agent invocation the harness records a manifest of the
      digests of the files in the ChangeSet scope. A later difference that no invocation
      produced is attributed, per file, as an out-of-band edit, and the provenance of every
      ChangeSet file (the invocation that last wrote it, or ``OUT_OF_BAND``) is recorded as
      evidence.
    * ``selfReport``: the request asks the agent for a structured self-report (assumptions,
      alternatives discarded, low-confidence areas, unrequested changes); the answer is stored
      as data of quality ``REPORTED`` and contrasted with the ChangeSet, never as a check.

    Absent keys keep the 1.0.0 behaviour and are left out of the serialized configuration."""

    agent_snapshots: bool | None = Field(default=None, alias="agentSnapshots")
    self_report: bool | None = Field(default=None, alias="selfReport")

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_none(self, handler(self))


ClosureCommitMode = Literal["off", "branch", "head"]
PublisherTransport = Literal["gh", "api"]

DEFAULT_CLOSURE_BRANCH = "harness/{runId}"
"""Branch a ``closureCommit: branch`` commit is written to."""

_BRANCH_FORBIDDEN = re.compile(r"(\.\.|[\s~^:?*\[\\]|@\{|//|^/|/$|\.lock$|^-)")


class PublisherConfig(ConfigModel):
    """Where ``harness pr publish`` posts the decision brief and the SARIF report."""

    kind: Literal["github"] = "github"
    transport: PublisherTransport = "gh"
    repository: str | None = None
    token_env: str = Field(default="GITHUB_TOKEN", alias="tokenEnv")
    api_url: str = Field(default="https://api.github.com", alias="apiUrl")
    sarif: bool = True

    @field_validator("repository")
    @classmethod
    def _owner_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError(f"repository must be owner/name: {value!r}")
        return value

    @field_validator("token_env")
    @classmethod
    def _token_env_name(cls, value: str) -> str:
        if not _ENV_NAME.match(value):
            raise ValueError(f"tokenEnv is not an environment variable name: {value!r}")
        return value

    @field_validator("api_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("apiUrl must use https (or point to localhost)")
        return value.rstrip("/")


class DeliveryConfig(ConfigModel):
    """What CLOSURE delivers to version control (since 1.1).

    * ``closureCommit: branch`` writes the approved ChangeSet as one commit on a new branch
      (``branch``, default ``harness/{runId}``) whose parent is ``HEAD``, without touching the
      working tree, the index or the current branch; ``head`` commits it on the current branch
      (only the ChangeSet paths). The commit carries the trailers ``Harness-Run``,
      ``Harness-ChangeSet`` and ``Harness-Decision`` (and ``Harness-Exception`` for an
      ``APPROVE_EXCEPTION``), and is created only if its diff recomputes to the approved
      ChangeSet digest. ``off`` (or absent): the harness never commits, as in 1.0.0.
    * ``publisher``: defaults of ``harness pr publish``."""

    closure_commit: ClosureCommitMode | None = Field(default=None, alias="closureCommit")
    branch: str | None = None
    publisher: PublisherConfig | None = None
    # ----- since #55: complete delivery -----------------------------------------------------
    stage: bool | None = None
    """When the operational contract does not authorise a push, stage the run's files (only
    them, never ``add -A``) so the change waits in the index for the person."""
    push: bool | None = None
    """Push the closure commit's branch, honouring the repository's hooks (never
    ``--no-verify``); the task's contract may say otherwise."""
    pull_request: PullRequestConfig | None = Field(default=None, alias="pullRequest")
    comment: CommentPolicy | None = None
    """Comment the decision brief on the pull request: ``notClean`` (exceptions, findings,
    partial certification), ``always`` or ``never``."""
    forge: ForgeConfig | None = None
    """Since 1.1 (#56): the forge of ``harness pr publish`` and ``harness pr create`` (GitHub,
    GitLab, Bitbucket, Azure DevOps, Gitea), detected from ``origin`` unless set."""

    @field_validator("branch")
    @classmethod
    def _branch_template(cls, value: str | None) -> str | None:
        if value is None:
            return value
        sample = value.replace("{runId}", "run_x").replace("{taskId}", "task_x")
        if not sample.strip() or _BRANCH_FORBIDDEN.search(sample) or "{" in sample:
            raise ValueError(
                f"branch must be a valid branch name; only {{runId}} and {{taskId}} are "
                f"replaced: {value!r}"
            )
        return value

    @property
    def mode(self) -> ClosureCommitMode:
        return self.closure_commit or "off"

    @property
    def branch_template(self) -> str:
        return self.branch or DEFAULT_CLOSURE_BRANCH

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_none(self, handler(self))


ProjectConfiguration.model_rebuild()
