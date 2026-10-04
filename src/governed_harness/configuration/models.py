from __future__ import annotations

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

from governed_harness.domain.enums import PhaseId


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class WorkspaceUnitConfig(ConfigModel):
    unit_id: str = Field(alias="unitId")
    root: str
    profile: str | None = None


class WorkspaceConfig(ConfigModel):
    root: str = ".."
    units: tuple[WorkspaceUnitConfig, ...] = ()


class CapabilityRule(ConfigModel):
    capability: str
    scope: tuple[str, ...]
    approval_required: bool = Field(default=False, alias="approvalRequired")
    conditions: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesConfig(ConfigModel):
    default: Literal["deny"] = "deny"
    grants: tuple[CapabilityRule, ...] = ()


AgentSandboxMode = Literal["enforce", "off"]

DEFAULT_AGENT_SANDBOX: AgentSandboxMode = "off"
"""Mode of a project.yaml without ``runtime.agentSandbox`` (files written before 1.1)."""

DEFAULT_SANDBOX_WRITE_PATHS: tuple[tuple[str, str], ...] = (
    ("/tmp", "Shared temporary directory (/private/tmp on macOS): compilers, npm and git use it."),
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

    @model_serializer(mode="wrap")
    def _omit_absent_sandbox(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Like the intake section: a file without the sandbox keys serializes as before, so the
        # configuration snapshot (and its digest) of a project written for 1.0.0 does not change.
        data: dict[str, Any] = handler(self)
        for name, alias in (
            ("agent_sandbox", "agentSandbox"),
            ("sandbox_write_paths", "sandboxWritePaths"),
        ):
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
    ``off`` skips the assessment."""

    criteria_policy: CriteriaPolicy = Field(default=DEFAULT_CRITERIA_POLICY, alias="criteriaPolicy")


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

    @model_serializer(mode="wrap")
    def _omit_absent_intake(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # A file without the section serializes as before, so the configuration snapshot
        # (and its digest) of a project written for 1.0.0 does not change.
        data: dict[str, Any] = handler(self)
        if self.intake is None:
            data.pop("intake", None)
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
