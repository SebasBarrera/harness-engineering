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

from governed_harness.domain.enums import FindingSeverity, PhaseId


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
    units: tuple[WorkspaceUnitConfig, ...] = ()
    snapshot: SnapshotMode | None = None
    baseline: BaselineMode | None = None
    snapshot_cache: bool | None = Field(default=None, alias="snapshotCache")

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, alias in (
            ("snapshot", "snapshot"),
            ("baseline", "baseline"),
            ("snapshot_cache", "snapshotCache"),
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
    default: Literal["deny"] = "deny"
    grants: tuple[CapabilityRule, ...] = ()


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
    "extended_redaction": "extendedRedaction",
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
    max_parallel: int = Field(default=2, alias="maxParallel", ge=1, le=32)
    allow_network: bool = Field(default=False, alias="allowNetwork")
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
            if not (path.startswith("/") or path == "~" or path.startswith("~/")):
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
    ``off`` skips the assessment."""

    criteria_policy: CriteriaPolicy = Field(default=DEFAULT_CRITERIA_POLICY, alias="criteriaPolicy")


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

    @model_serializer(mode="wrap")
    def _omit_absent_parsers(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.output_parsers is None:
            data.pop("output_parsers", None)
            data.pop("outputParsers", None)
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

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, alias in (("exceptions", "exceptions"), ("exception_days", "exceptionDays")):
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(alias, None)
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
    retention: dict[str, Any] = Field(default_factory=dict)
    intake: IntakeConfig | None = None
    verification: VerificationConfig | None = None
    review: ReviewConfig | None = None
    notifications: NotificationsConfig | None = None
    retrospective: RetrospectiveConfig | None = None
    toolchain: ToolchainConfig | None = None
    provenance: ProvenanceConfig | None = None
    delivery: DeliveryConfig | None = None

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
            "review",
            "notifications",
            "retrospective",
            "toolchain",
            "provenance",
            "delivery",
        ):
            if getattr(self, section) is None:
                data.pop(section, None)
        return data


class DetectorMarker(ConfigModel):
    marker: str
    weight: float = Field(gt=0, le=1)


OutputParser = Literal["auto", "sarif", "junit", "ruff", "mypy", "eslint", "tsc", "pytest", "none"]
IssueLevel = Literal["error", "warning", "note"]


class ValidatorDefinition(ConfigModel):
    """A validator of a profile or, since 1.1, of the project (``toolchain.validators``).

    The keys added in 1.1 (``parser``, ``severity``, ``failureSeverity``, ``passEnv``) are
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


class WorkflowPhaseDefinition(ConfigModel):
    phase_id: PhaseId = Field(alias="id")
    purpose: str
    depends_on: tuple[PhaseId, ...] = Field(default=(), alias="dependsOn")
    required: bool = True
    parallelizable: bool = False
    allowed_capabilities: tuple[str, ...] = Field(default=(), alias="allowedCapabilities")
    validators: tuple[str, ...] = ()
    exit_gate: str = Field(alias="exitGate")
    timeout_seconds: int = Field(default=1800, alias="timeoutSeconds", ge=1)
    max_attempts: int = Field(default=1, alias="maxAttempts", ge=1, le=20)


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
    invariants: tuple[str, ...] = ()

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
