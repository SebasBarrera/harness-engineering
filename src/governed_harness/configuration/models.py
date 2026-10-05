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
from governed_harness.domain.enums import FindingSeverity, PhaseId


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class WorkspaceUnitConfig(ConfigModel):
    unit_id: str = Field(alias="unitId")
    root: str
    profile: str | None = None


class WorkspaceConfig(ConfigModel):
    root: str = ".."
    units: tuple[WorkspaceUnitConfig, ...] = Field(
        default=(),
        description="Declarative: declared units are not used by the engine.",
        json_schema_extra={"x-declarative": True},
    )


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
    "gate_contract": "gateContract",
    "reproduce_first": "reproduceFirst",
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
        description="Declarative: phases and validators run one at a time.",
        json_schema_extra={"x-declarative": True},
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

    gate_contract: bool | None = Field(default=None, alias="gateContract")
    """Since 1.1 (#52): the implement request carries the gate contract (validators, review
    rules, blocking severities, the workspace path and the ``harness check`` command) and the
    agent's permissions derived from the capability grants."""
    reproduce_first: bool | None = Field(default=None, alias="reproduceFirst")
    """Since 1.1 (#52): a correction attempt that changes nothing is a finding, and a correction
    after REQUEST_CHANGES must add a test that fails before it and passes after it."""

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


class AgentProviderConfiguration(ConfigModel):
    kind: Literal["command"] = "command"
    command: tuple[str, ...]
    model: str | None = None

    @field_validator("command")
    @classmethod
    def command_must_not_be_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("agent provider command must not be empty")
        return value


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

    @field_validator("ambiguity_review", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def agent_review_enabled(self) -> bool:
        return self.ambiguity_review == "agent"

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name in ("ambiguity_review", "clarify_agent", "validate_answers"):
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
      evidence."""

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


class ValidatorDefinition(ConfigModel):
    validator_id: str = Field(alias="id")
    command: tuple[str, ...] | None = None
    mandatory: bool = True
    when_available: bool = Field(default=False, alias="whenAvailable")
    script: str | None = None
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1)


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
    depends_on: tuple[PhaseId, ...] = Field(
        default=(),
        alias="dependsOn",
        description="Declarative: the phase order is fixed by the state machine.",
        json_schema_extra={"x-declarative": True},
    )
    required: bool = True
    parallelizable: bool = Field(
        default=False,
        description="Declarative: phases run one at a time.",
        json_schema_extra={"x-declarative": True},
    )
    allowed_capabilities: tuple[str, ...] = Field(
        default=(),
        alias="allowedCapabilities",
        description="Declarative: capabilities are granted per run, not per phase (issue #4).",
        json_schema_extra={"x-declarative": True},
    )
    validators: tuple[str, ...] = Field(
        default=(),
        description="Declarative: the validators come from the profiles and the project.",
        json_schema_extra={"x-declarative": True},
    )
    exit_gate: str = Field(
        alias="exitGate",
        description=(
            "Name of the condition the phase must meet to pass; recorded with each attempt "
            "(exitGate, exitGateMet) under governance.applyWorkflowSettings."
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
