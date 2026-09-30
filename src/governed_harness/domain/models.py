from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    ActorType,
    DecisionKind,
    ErrorKind,
    EvidenceKind,
    FindingSeverity,
    MemoryLevel,
    MetricQuality,
    PhaseId,
    ResultStatus,
    ValidationKind,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        alias_generator=_to_camel,
    )


class Actor(StrictModel):
    actor_type: ActorType
    actor_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    display_name: str | None = None
    version: str | None = None


HARNESS_ACTOR = Actor(actor_type=ActorType.HARNESS, actor_id="harness.core")


class Provenance(StrictModel):
    actor: Actor
    core_version: str
    configuration_digest: str | None = None
    workflow_digest: str | None = None
    policy_digest: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_digest: str | None = None
    plugin_id: str | None = None
    plugin_version: str | None = None
    source_refs: tuple[str, ...] = ()


class HarnessErrorRecord(StrictModel):
    error_id: str
    kind: ErrorKind
    message: str
    actor: Actor
    occurred_at: datetime = Field(default_factory=utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


class Requirement(StrictModel):
    requirement_id: str
    text: str = Field(min_length=1, max_length=8000)
    source: str = "human"


class AcceptanceCriterion(StrictModel):
    criterion_id: str
    text: str = Field(min_length=1, max_length=8000)
    verification_hint: str | None = Field(default=None, max_length=4000)
    priority: Literal["MUST", "SHOULD", "COULD"] = "MUST"


class FilePatch(StrictModel):
    path: str
    operation: Literal["create", "replace", "append", "delete"]
    content: str | None = None
    expected_sha256: str | None = None

    @field_validator("path")
    @classmethod
    def path_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {"", "."}:
            raise ValueError("patch path must be a safe workspace-relative path")
        return value

    @model_validator(mode="after")
    def content_matches_operation(self) -> FilePatch:
        if self.operation in {"create", "replace", "append"} and self.content is None:
            raise ValueError("content is required for create/replace/append")
        return self


class ImplementationInstruction(StrictModel):
    mode: Literal["none", "patch", "command"] = "none"
    patches: tuple[FilePatch, ...] = ()
    argv: tuple[str, ...] = ()
    cwd: str = "."

    @model_validator(mode="after")
    def validate_mode(self) -> ImplementationInstruction:
        if self.mode == "patch" and not self.patches:
            raise ValueError("patch mode requires at least one patch")
        if self.mode == "command" and not self.argv:
            raise ValueError("command mode requires argv")
        return self


class Task(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    project_id: str
    title: str = Field(min_length=1, max_length=300)
    intent: str = Field(min_length=1, max_length=16000)
    constraints: tuple[str, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    implementation: ImplementationInstruction = Field(default_factory=ImplementationInstruction)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("acceptance_criteria")
    @classmethod
    def require_acceptance(cls, value: tuple[AcceptanceCriterion, ...]) -> tuple[AcceptanceCriterion, ...]:
        if not value:
            raise ValueError("at least one acceptance criterion is required")
        return value


class PlanStep(StrictModel):
    step_id: str
    description: str
    capabilities: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()


class Plan(StrictModel):
    plan_id: str
    execution_id: str
    task_id: str
    steps: tuple[PlanStep, ...]
    risks: tuple[str, ...] = ()
    validator_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    provenance: Provenance


class CapabilityGrant(StrictModel):
    grant_id: str
    execution_id: str
    actor: Actor
    capability: str
    scope: tuple[str, ...]
    issued_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    conditions: dict[str, Any] = Field(default_factory=dict)
    revoked_at: datetime | None = None
    approval_required: bool = False

    def active_at(self, now: datetime) -> bool:
        return self.revoked_at is None and self.issued_at <= now < self.expires_at


class ConfigurationSnapshot(StrictModel):
    snapshot_id: str
    execution_id: str
    content_digest: str
    artifact_ref: str
    created_at: datetime = Field(default_factory=utc_now)


class PhaseExecution(StrictModel):
    phase_execution_id: str
    execution_id: str
    phase_id: PhaseId
    status: ResultStatus = ResultStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_digest: str | None = None
    output_digest: str | None = None
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    summary: str = ""
    errors: tuple[HarnessErrorRecord, ...] = ()


class Execution(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    execution_id: str
    project_id: str
    task_id: str
    workspace: str
    status: ResultStatus = ResultStatus.PENDING
    current_phase: PhaseId = PhaseId.INTENT
    attempt: int = Field(default=1, ge=1)
    configuration_digest: str
    workflow_digest: str
    policy_digest: str
    configuration_snapshot_ref: str
    workflow_ref: str
    baseline_revision: str | None = None
    change_set_digest: str | None = None
    gate_evaluation_id: str | None = None
    human_decision_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    terminal_reason: str | None = None


class ToolInvocation(StrictModel):
    invocation_id: str
    execution_id: str
    phase_id: PhaseId
    actor: Actor
    tool_id: str
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    started_at: datetime
    finished_at: datetime
    status: ResultStatus
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    resource_usage_ref: str | None = None
    provenance: Provenance


class AgentInvocation(StrictModel):
    invocation_id: str
    execution_id: str
    phase_id: PhaseId
    actor: Actor
    provider: str
    model: str | None = None
    session_id: str | None = None
    started_at: datetime
    finished_at: datetime
    status: ResultStatus
    prompt_digest: str
    context_manifest_ref: str | None = None
    tool_invocation_ids: tuple[str, ...] = ()
    usage_ref: str | None = None
    output_ref: str | None = None
    error: HarnessErrorRecord | None = None


class ChangedFile(StrictModel):
    path: str
    status: Literal["ADDED", "MODIFIED", "DELETED", "RENAMED", "UNTRACKED"]
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    before_digest: str | None = None
    after_digest: str | None = None


class ChangeSet(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    change_set_id: str
    execution_id: str
    baseline_revision: str | None = None
    current_revision: str | None = None
    files: tuple[ChangedFile, ...]
    diff_ref: str
    digest: str
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(StrictModel):
    artifact_id: str
    execution_id: str
    kind: str
    media_type: str
    uri: str
    digest: str
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    redacted: bool = False
    provenance: Provenance
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(StrictModel):
    evidence_id: str
    execution_id: str
    phase_id: PhaseId
    kind: EvidenceKind
    artifact_ref: str
    digest: str
    summary: str
    observed_at: datetime = Field(default_factory=utc_now)
    provenance: Provenance
    supports: tuple[str, ...] = ()


class FindingLocation(StrictModel):
    path: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class Finding(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    finding_id: str
    execution_id: str
    validator_id: str
    rule_id: str
    category: str
    severity: FindingSeverity
    message: str
    location: FindingLocation | None = None
    evidence_refs: tuple[str, ...] = ()
    recommendation: str | None = None
    introduced: bool | None = None
    created_at: datetime = Field(default_factory=utc_now)
    provenance: Provenance


class ValidationResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    validation_result_id: str
    execution_id: str
    validator_id: str
    change_set_digest: str
    status: ResultStatus
    kind: ValidationKind
    mandatory: bool = True
    summary: str
    finding_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...]
    tool_invocation_ids: tuple[str, ...] = ()
    started_at: datetime
    finished_at: datetime
    errors: tuple[HarnessErrorRecord, ...] = ()
    provenance: Provenance

    @field_validator("evidence_refs")
    @classmethod
    def require_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("a validation result must reference evidence")
        return value


class GateEvaluation(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    gate_evaluation_id: str
    execution_id: str
    gate_id: str
    status: ResultStatus
    change_set_digest: str
    policy_digest: str
    input_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    requires_human_decision: bool
    evaluated_at: datetime = Field(default_factory=utc_now)
    provenance: Provenance


class HumanDecision(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    decision_id: str
    execution_id: str
    gate_evaluation_id: str
    actor: Actor
    decision: DecisionKind
    rationale: str
    change_set_digest: str
    configuration_digest: str
    policy_digest: str
    decided_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None


class ResourceUsage(StrictModel):
    usage_id: str
    execution_id: str
    invocation_id: str | None = None
    wall_time_ms: int = Field(ge=0)
    cpu_time_ms: int | None = Field(default=None, ge=0)
    max_rss_bytes: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    quality: MetricQuality
    limitations: tuple[str, ...] = ()


class MemoryRecord(StrictModel):
    memory_id: str
    project_id: str
    task_id: str | None = None
    execution_id: str | None = None
    level: MemoryLevel
    key: str
    value: dict[str, Any]
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)
    valid_until: datetime | None = None
    supersedes: str | None = None
    sensitive: bool = False
    approved: bool = False


class RetrospectiveObservation(StrictModel):
    observation_id: str
    statement: str
    evidence_refs: tuple[str, ...]


class Recommendation(StrictModel):
    recommendation_id: str
    category: str
    statement: str
    rationale: str
    evidence_refs: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    requires_human_review: bool = True


class Retrospective(StrictModel):
    retrospective_id: str
    execution_id: str
    observations: tuple[RetrospectiveObservation, ...]
    recommendations: tuple[Recommendation, ...]
    generated_at: datetime = Field(default_factory=utc_now)
    applied_automatically: Literal[False] = False
    provenance: Provenance
