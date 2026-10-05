from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

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
    VerificationLevel,
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


def _omit_unset(model: BaseModel, data: dict[str, Any]) -> dict[str, Any]:
    """Drop the fields of ``model`` that are ``None`` or an empty tuple or mapping, under both
    their name and their alias, so a record without them keeps its stored form and digest."""
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        if value is None or (isinstance(value, (tuple, dict)) and not value):
            data.pop(name, None)
            if field.alias:
                data.pop(field.alias, None)
            data.pop(_to_camel(name), None)
    return data


# ----- since 1.1 (#55): the verification ladder of a criterion, probes, the checklist ---------
_PROBE_ID = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
_ITEM_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
_ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
PROBE_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A ``{name}`` in a probe's command or working directory, filled from the variant."""


class CriterionVerification(StrictModel):
    """How an acceptance criterion must be verified: the rung of the ladder it requires and,
    for the rungs that need one, how that rung is reached.

    * ``probe``: the id of a probe (task or project) whose assertions show the behaviour (L3,
      or L2/L4 when the probe declares that level);
    * ``tests``: names (or parts of names) of the tests that verify the criterion (L1/L2);
    * ``deferred``: where the criterion is verified after the run (a CI job, staging, a device
      lab); it stays pending, bound to the ChangeSet digest, until evidence is attached;
    * ``manual``: what a person checks; ticked in DECISION (L5)."""

    level: VerificationLevel
    probe: str | None = Field(default=None, pattern=_PROBE_ID)
    tests: tuple[str, ...] = ()
    deferred: str | None = Field(default=None, min_length=1, max_length=1000)
    manual: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _rung_has_a_path(self) -> CriterionVerification:
        if self.level is VerificationLevel.L5 and self.manual is None:
            raise ValueError("a criterion that requires L5 needs 'manual': what a person checks")
        if any(not item.strip() for item in self.tests):
            raise ValueError("verification tests must not be blank")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_unset(self, handler(self))


class ProbeAssertion(StrictModel):
    """A generic assertion on the output of a probe.

    * ``exitCode``: the exit code ``equals`` a number;
    * ``jsonPath``: the value at ``path`` is ``present`` (true) or absent (``present: false``),
      ``equals`` a value or ``matches`` a regular expression;
    * ``differs``: the value at ``path`` (JSON) or the whole output (text) is not the same in
      every variant (``variants`` narrows the comparison);
    * ``order``: the values at ``path`` are ``ascending`` or ``descending``, or ``before``
      appears before ``after`` (in the list at ``path``, or in the text);
    * ``text``: the output ``matches`` a regular expression or ``contains`` a text."""

    kind: Literal["exitCode", "jsonPath", "differs", "order", "text"]
    path: str | None = None
    equals: Any = None
    present: bool | None = None
    matches: str | None = None
    contains: str | None = None
    order: Literal["ascending", "descending"] | None = None
    before: str | None = None
    after: str | None = None
    variants: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _complete(self) -> ProbeAssertion:
        if self.matches is not None:
            try:
                re.compile(self.matches)
            except re.error as error:
                raise ValueError(f"invalid regular expression {self.matches!r}: {error}") from error
        if self.path is not None:
            from governed_harness.ladder.jsonpath import parse_path

            parse_path(self.path)
        kind = self.kind
        if kind == "exitCode" and not isinstance(self.equals, int):
            raise ValueError("an exitCode assertion needs 'equals' with an integer")
        if kind == "jsonPath":
            if self.path is None:
                raise ValueError("a jsonPath assertion needs 'path'")
            given = [self.present is not None, self.equals is not None, self.matches is not None]
            if sum(given) != 1:
                raise ValueError(
                    "a jsonPath assertion needs exactly one of present, equals, matches"
                )
        if kind == "order" and not (
            (self.path is not None and self.order is not None)
            or (self.before is not None and self.after is not None)
        ):
            raise ValueError("an order assertion needs path and order, or before and after")
        if kind == "text" and (self.matches is None) == (self.contains is None):
            raise ValueError("a text assertion needs exactly one of matches, contains")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_unset(self, handler(self))


class ProbeVariant(StrictModel):
    """One combination of the variant matrix: the values of the command's placeholders and
    environment variables set for this run of the probe."""

    name: str = Field(min_length=1, max_length=200)
    values: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("env")
    @classmethod
    def _env_names(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not re.match(_ENV_NAME, name):
                raise ValueError(f"env key is not an environment variable name: {name!r}")
        return value


class ProbeDefinition(StrictModel):
    """A command whose observable output shows a behaviour (a CLI, a script, a local server,
    a simulator test), with assertions on that output, run once per variant of its matrix:
    on the baseline (preflight) and after the change (VERIFICATION). The command runs without
    a shell; ``{name}`` placeholders are filled from the variant. A probe that cannot run is
    ``BLOCKED``, never ``PASSED``."""

    probe_id: str = Field(alias="id", pattern=_PROBE_ID)
    command: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."
    pass_env: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    output: Literal["json", "text"] = "text"
    level: VerificationLevel = VerificationLevel.L3
    criteria: tuple[str, ...] = ()
    matrix: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    variants: tuple[ProbeVariant, ...] = ()
    assertions: tuple[ProbeAssertion, ...] = Field(min_length=1)

    @field_validator("cwd")
    @classmethod
    def _relative_cwd(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"a probe cwd must be relative to the workspace: {value!r}")
        return value

    @field_validator("pass_env")
    @classmethod
    def _pass_env_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if not re.match(_ENV_NAME, name):
                raise ValueError(f"passEnv entry is not an environment variable name: {name!r}")
        return value

    @model_validator(mode="after")
    def _variants_fill_placeholders(self) -> ProbeDefinition:
        if self.level not in {VerificationLevel.L2, VerificationLevel.L3, VerificationLevel.L4}:
            raise ValueError("a probe verifies L2, L3 or L4")
        if self.matrix and self.variants:
            raise ValueError("declare either a matrix or variants, not both")
        if any(not values for values in self.matrix.values()):
            raise ValueError("every matrix entry needs at least one value")
        needed = {
            name for item in (*self.command, self.cwd) for name in PROBE_PLACEHOLDER.findall(item)
        }
        for variant in self.expanded_variants():
            missing = sorted(needed - set(variant.values))
            if missing:
                raise ValueError(
                    f"variant {variant.name!r} does not set placeholder(s) {', '.join(missing)}"
                )
        names = [item.name for item in self.expanded_variants()]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        if any(item.kind == "differs" for item in self.assertions) and len(names) < 2:
            raise ValueError("a differs assertion needs at least two variants")
        return self

    def expanded_variants(self) -> tuple[ProbeVariant, ...]:
        """The variants in a fixed order: the declared ones, else the cartesian product of the
        matrix (keys sorted, values in their order), else one variant named ``default``."""
        if self.variants:
            return self.variants
        if not self.matrix:
            return (ProbeVariant(name="default"),)
        keys = sorted(self.matrix)
        return tuple(
            ProbeVariant(
                name=",".join(
                    f"{key}={value}" for key, value in zip(keys, combination, strict=True)
                ),
                values=dict(zip(keys, combination, strict=True)),
            )
            for combination in itertools.product(*(self.matrix[key] for key in keys))
        )

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_unset(self, handler(self))


class ChecklistItem(StrictModel):
    """Something only a person can verify (a layout, a sound, a device behaviour), ticked in
    DECISION with ``harness gate decide --check ID``."""

    item_id: str = Field(alias="id", pattern=_ITEM_ID)
    text: str = Field(min_length=1, max_length=2000)


class OperationalContract(StrictModel):
    """The operational contract of a task (#55): what the person and the harness agreed on
    before work starts. Every field is optional in the task file; the harness fills the rest
    from the project configuration and records the resulting summary, bound to the task
    digest, at INTENT."""

    objective: str | None = Field(default=None, max_length=4000)
    examples: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    definition_of_done: tuple[str, ...] = ()
    verification_level: VerificationLevel | None = None
    branch: str | None = Field(default=None, max_length=200)
    create_pull_request: bool | None = None
    push: bool | None = None
    comment: bool | None = None
    coverage_threshold: float | None = Field(default=None, ge=0, le=100)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_unset(self, handler(self))


class AcceptanceCriterion(StrictModel):
    criterion_id: str
    text: str = Field(min_length=1, max_length=8000)
    verification_hint: str | None = Field(default=None, max_length=4000)
    priority: Literal["MUST", "SHOULD", "COULD"] = "MUST"
    verification: CriterionVerification | None = None
    """Since 1.1 (#55): the rung of the verification ladder the criterion requires. Left out
    of the serialized criterion when absent, so tasks written before it keep their digest."""

    @model_serializer(mode="wrap")
    def _omit_absent_verification(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.verification is None:
            data.pop("verification", None)
        return data


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
    """A versioned unit of work.

    A task needs at least one acceptance criterion. The only exception is a task marked
    ``criteria_pending``: the task loader sets the marker when the project policy
    ``intake.criteriaPolicy`` is ``enforce`` and the task file has no criteria, so that INTENT
    asks for them (rule ``C0``) instead of the task being refused. The marker is valid only
    while the task has no criteria, and it is left out of the serialized task when it is
    false, so tasks with criteria keep their stored form and their digest."""

    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    project_id: str
    title: str = Field(min_length=1, max_length=300)
    intent: str = Field(min_length=1, max_length=16000)
    constraints: tuple[str, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    criteria_pending: bool = False
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    implementation: ImplementationInstruction = Field(default_factory=ImplementationInstruction)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    probes: tuple[ProbeDefinition, ...] = ()
    """Since 1.1 (#55): behaviour probes of this task (added to the project's)."""
    checklist: tuple[ChecklistItem, ...] = ()
    """Since 1.1 (#55): items only a person can verify, ticked in DECISION."""
    contract: OperationalContract | None = None
    """Since 1.1 (#55): the operational contract the task declares; the rest comes from the
    project configuration."""

    @field_validator("probes")
    @classmethod
    def _unique_probes(cls, value: tuple[ProbeDefinition, ...]) -> tuple[ProbeDefinition, ...]:
        ids = [item.probe_id for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("probe ids must be unique within a task")
        return value

    @field_validator("checklist")
    @classmethod
    def _unique_items(cls, value: tuple[ChecklistItem, ...]) -> tuple[ChecklistItem, ...]:
        ids = [item.item_id for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("checklist item ids must be unique within a task")
        return value

    @field_validator("acceptance_criteria")
    @classmethod
    def require_acceptance(
        cls, value: tuple[AcceptanceCriterion, ...], info: ValidationInfo
    ) -> tuple[AcceptanceCriterion, ...]:
        # criteria_pending is declared (and so validated) before acceptance_criteria.
        pending = info.data.get("criteria_pending", False) is True
        if not value and not pending:
            raise ValueError("at least one acceptance criterion is required")
        if value and pending:
            raise ValueError(
                "criteriaPending is only allowed on a task without acceptance criteria"
            )
        return value

    @model_serializer(mode="wrap")
    def _omit_false_criteria_pending(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if not self.criteria_pending:
            data.pop("criteriaPending", None)
            data.pop("criteria_pending", None)
        # Since 1.1 (#55): absent probes, checklist and contract keep the stored form and the
        # digest of tasks written before them.
        for name in ("probes", "checklist", "contract"):
            if not getattr(self, name):
                data.pop(name, None)
        return data


ClarificationRule = Literal["C0", "C1", "C2", "C3", "T1", "A1", "A2", "A3"]
"""``C0``-``C3`` and ``T1`` are the deterministic intent rules; since 1.1 ``A1`` is a question an
agent asked in its ambiguity and completeness review (``intake.ambiguityReview``, #37), ``A2``
a question about an answer that refers to something the task and the workspace do not contain
(``intake.validateAnswers``) and ``A3`` a question of the localisation call about where to
intervene (``context.locate``, #55)."""


class ClarificationQuestion(StrictModel):
    """A question raised in INTENT by the deterministic intent assessment or, since 1.1, by the
    agent review of the task or the check of earlier answers.

    ``target`` is the criterion id the question is about, ``task`` for the task as a whole,
    or ``task:<part>`` for a ``C0`` question about a task without acceptance criteria (for
    example ``task:results``). ``question_id`` is stable for a given task revision (``Q-1``,
    ``Q-2``, ...). ``category`` groups the questions of an agent review (ambiguity,
    completeness categories, consistency) and is left out when absent."""

    question_id: str = Field(pattern=r"^Q-[1-9][0-9]*$")
    rule_id: ClarificationRule
    target: str = Field(min_length=1)
    text: str = Field(min_length=1)
    category: str | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_category(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.category is None:
            data.pop("category", None)
        return data


class ClarificationRequest(StrictModel):
    """The questions INTENT asked about one revision of a task, identified by its digest.

    Since 1.1 (#55) the same request may carry the operational contract (``contract``): the
    summary of what was agreed and the items nobody settled, so that a person answers
    everything in one message. A request has at least one question or a contract."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    execution_id: str
    task_id: str
    task_digest: str
    policy: Literal["enforce", "warn"]
    questions: tuple[ClarificationQuestion, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    contract: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _asks_something(self) -> ClarificationRequest:
        if not self.questions and self.contract is None:
            raise ValueError("a clarification request needs a question or a contract")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent_contract(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.contract is None:
            data.pop("contract", None)
        return data


class ClarificationAnswer(StrictModel):
    question_id: str
    rule_id: ClarificationRule
    target: str
    question: str
    answer: str = Field(min_length=1, max_length=4000)


class ClarificationRecord(StrictModel):
    """A person's answers to a clarification request and the task revision they produced."""

    schema_version: Literal["1.0"] = "1.0"
    clarification_id: str
    execution_id: str
    task_id: str
    request_id: str
    actor: Actor
    answers: tuple[ClarificationAnswer, ...] = Field(min_length=1)
    replaced_criteria: tuple[str, ...] = ()
    added_criteria: tuple[str, ...] = ()
    added_requirements: tuple[str, ...] = ()
    previous_task_digest: str
    task_digest: str
    previous_task_ref: str
    task_ref: str
    recorded_at: datetime = Field(default_factory=utc_now)


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
    call_kind: Literal["implement", "clarify", "review", "plan", "acceptance", "locate"] | None = (
        None
    )
    """Since 1.1 (#37): the request kind; left out for an implement call sent in the 1.0 form,
    so invocations recorded without the agent-results settings keep their stored form."""
    effort: str | None = None
    """Since 1.1 (#44): the reasoning effort the router chose, when it chose one."""

    @model_serializer(mode="wrap")
    def _omit_absent_kind(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, alias in (("call_kind", "callKind"), ("effort", "effort")):
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(alias, None)
        return data


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


class ChangeRequestItem(StrictModel):
    """A blocking item of a structured REQUEST_CHANGES: what must change and the condition
    that verifies it. ``test:<pytest node id>`` must pass; ``absent:<regex>`` must not match
    any added line of the ChangeSet; ``text`` items are sent to the agent and shown to the
    reviewer but are not checked by a tool."""

    item_id: str = Field(pattern=r"^CR-[1-9][0-9]*$")
    description: str = Field(min_length=1, max_length=2000)
    condition: str = Field(min_length=1, max_length=1000)

    @field_validator("condition")
    @classmethod
    def _known_condition(cls, value: str) -> str:
        kind = value.split(":", 1)[0] if ":" in value else value
        if kind not in {"test", "absent", "text"}:
            raise ValueError("a condition is test:<node id>, absent:<regex> or text")
        if kind in {"test", "absent"} and not value.split(":", 1)[1].strip():
            raise ValueError(f"the {kind} condition needs a value")
        return value


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
    acceptance_contract_digest: str | None = None
    """Digest of the acceptance contract frozen in SPECIFICATION that the decision is bound to
    (``governance.pinTaskRevision``); left out of the serialized record when absent, so
    decisions recorded without the setting keep their stored form."""
    identity_source: Literal["explicit", "git", "fallback", "default"] | None = None
    """Where the actor id came from under ``governance.deciderIdentity: git``: ``--actor``
    (``explicit``), the Git user (``git``) or, when Git has no usable identity, the default
    actor (``fallback``). Left out when absent, like ``acceptance_contract_digest``."""
    acknowledged_risks: tuple[str, ...] = ()
    """Risk factors of the ChangeSet the person acknowledged (``verification.riskFactors``
    with ``acknowledge``, #52); left out when empty."""
    change_requests: tuple[ChangeRequestItem, ...] = ()
    """Blocking items of a structured REQUEST_CHANGES (``review.structuredChanges``, #52);
    left out when empty."""
    checked_items: tuple[str, ...] = ()
    """Manual checklist items the person ticked with this decision (``review.manualChecklist``,
    #55); left out when empty."""

    @model_serializer(mode="wrap")
    def _omit_absent_contract(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, alias in (
            ("acceptance_contract_digest", "acceptanceContractDigest"),
            ("identity_source", "identitySource"),
        ):
            if getattr(self, name) is None:
                data.pop(alias, None)
                data.pop(name, None)
        for name, alias in (
            ("acknowledged_risks", "acknowledgedRisks"),
            ("change_requests", "changeRequests"),
            ("checked_items", "checkedItems"),
        ):
            if not getattr(self, name):
                data.pop(alias, None)
                data.pop(name, None)
        return data


class ExceptionScope(StrictModel):
    """Findings an exception covers: a rule, optionally narrowed to a path and to one finding
    fingerprint (``reporting.fingerprint``)."""

    rule_id: str = Field(min_length=1)
    path: str | None = None
    fingerprint: str | None = None


class ExceptionRecord(StrictModel):
    """A person's exception to blocking findings, recorded with ``APPROVE_EXCEPTION`` when
    ``review.exceptions`` is on.

    It is bound to the decision and the ChangeSet digest it was granted on, expires at
    ``expires_at`` and covers the findings in ``scope``: while it is in force a later run of the
    project does not block on them, and once it expires they block again. Alternative evidence
    and the follow-up are what the person declared; the harness records them, it does not check
    them."""

    schema_version: Literal["1.0"] = "1.0"
    exception_id: str
    project_id: str
    execution_id: str
    decision_id: str
    gate_evaluation_id: str
    actor: Actor
    rationale: str = Field(min_length=1)
    scope: tuple[ExceptionScope, ...] = ()
    change_set_digest: str
    granted_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    alternative_evidence: str | None = Field(default=None, max_length=4000)
    follow_up: str | None = Field(default=None, max_length=1000)
    provenance: Provenance

    def active_at(self, now: datetime) -> bool:
        return self.granted_at <= now < self.expires_at


FEEDBACK_STREAM_CHARS = 4000
"""Characters kept from the end of each failing validator stream in provider feedback."""
FEEDBACK_TOTAL_CHARS = 16000
"""Characters of validator output (all streams together) one feedback block may carry."""
FEEDBACK_MAX_FINDINGS = 20
FEEDBACK_TEXT_CHARS = 1000
"""Characters kept from a finding message, a validator summary or a claim summary."""
FEEDBACK_RATIONALE_CHARS = 4000

FeedbackTrigger = Literal["VERIFICATION_FAILED", "CHANGES_REQUESTED", "REVIEW_FINDINGS"]
"""Why a correction attempt was sent back to the agent; ``REVIEW_FINDINGS`` since 1.1
(``review.agentReview``, #38)."""


class FeedbackGate(StrictModel):
    """The outcome the feedback is about: the VERIFICATION result (``gate_id`` ``verification``)
    or the delivery gate a person decided on (``delivery_candidate``)."""

    gate_id: Literal["verification", "delivery_candidate", "independent_review"]
    gate_evaluation_id: str | None = None
    status: ResultStatus
    reason_codes: tuple[str, ...]


class FeedbackFinding(StrictModel):
    rule_id: str
    severity: FindingSeverity
    validator_id: str
    location: FindingLocation | None = None
    message: str = Field(max_length=FEEDBACK_TEXT_CHARS)


class FeedbackValidator(StrictModel):
    """A mandatory validator that did not pass, with the end of its redacted output."""

    validator_id: str
    status: ResultStatus
    summary: str = Field(max_length=FEEDBACK_TEXT_CHARS)
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=FEEDBACK_STREAM_CHARS)
    stderr: str = Field(default="", max_length=FEEDBACK_STREAM_CHARS)
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class FeedbackDecision(StrictModel):
    decision: DecisionKind
    rationale: str = Field(max_length=FEEDBACK_RATIONALE_CHARS)
    actor_id: str


class ProviderFeedback(StrictModel):
    """Why the previous attempt was not accepted, sent to a command provider as ``feedback``.

    It is built when a failed VERIFICATION returns the run to IMPLEMENTATION
    (``VERIFICATION_FAILED``) or a person requests changes (``CHANGES_REQUESTED``), only when
    ``runtime.providerFeedback`` is true. Validator output is bounded: the last
    ``FEEDBACK_STREAM_CHARS`` characters of each stream and ``FEEDBACK_TOTAL_CHARS`` in all."""

    schema_version: Literal["1.0"] = "1.0"
    attempt: int = Field(ge=2)
    trigger: FeedbackTrigger
    change_set_digest: str
    gate: FeedbackGate
    findings: tuple[FeedbackFinding, ...] = Field(default=(), max_length=FEEDBACK_MAX_FINDINGS)
    omitted_findings: int = Field(default=0, ge=0)
    validators: tuple[FeedbackValidator, ...] = ()
    decision: FeedbackDecision | None = None


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


RetrospectiveTrigger = Literal["CLOSED", "REJECTED", "CANCELLED", "ON_DEMAND"]


class RetrospectiveCause(StrictModel):
    """What stopped or redirected a run, by reason code (``retrospective.causal``).

    ``subject`` is the validator, rule or provider the cause is about, never a person;
    ``attempts`` are the VERIFICATION or decision attempts in which it occurred."""

    reason_code: str
    subject: str
    phase: PhaseId
    effect: str
    occurrences: int = Field(ge=1)
    attempts: tuple[int, ...] = ()
    evidence_refs: tuple[str, ...] = ()


class Retrospective(StrictModel):
    """Non-mutating observations and recommendations about a run. ``causes`` and ``trigger``
    are filled only under ``retrospective.causal`` and are left out of the serialized record
    otherwise, so a 1.0.0 retrospective keeps its form."""

    retrospective_id: str
    execution_id: str
    observations: tuple[RetrospectiveObservation, ...]
    recommendations: tuple[Recommendation, ...]
    generated_at: datetime = Field(default_factory=utc_now)
    applied_automatically: Literal[False] = False
    provenance: Provenance
    trigger: RetrospectiveTrigger | None = None
    causes: tuple[RetrospectiveCause, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_causal_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.trigger is None:
            data.pop("trigger", None)
        if not self.causes:
            data.pop("causes", None)
        return data


OutcomeKind = Literal["INCIDENT", "REVERT", "HOTFIX", "REGRESSION", "OTHER"]


class OutcomeRecord(StrictModel):
    """Something that happened to a change after its run: an incident, a revert, a hotfix or a
    regression, linked to the run by a person. It feeds the rule health across runs; nothing
    is applied automatically."""

    schema_version: Literal["1.0"] = "1.0"
    outcome_id: str
    project_id: str
    execution_id: str
    kind: OutcomeKind
    summary: str = Field(min_length=1, max_length=4000)
    reference: str | None = Field(default=None, max_length=1000)
    change_set_digest: str | None = None
    observed_at: datetime
    recorded_at: datetime = Field(default_factory=utc_now)
    actor: Actor
    provenance: Provenance


# ----- wave 4 (since 1.1): provenance per component, agent self-report, evidence bundle -------
class SelfReportItem(StrictModel):
    path: str | None = Field(default=None, max_length=1000)
    description: str = Field(min_length=1, max_length=1000)


class SelfReportContrast(StrictModel):
    """The self-report compared with the ChangeSet recorded after the same invocation."""

    change_set_digest: str | None = None
    declared_paths_not_in_change_set: tuple[str, ...] = ()
    unrequested_changes_in_change_set: tuple[str, ...] = ()


class AgentSelfReport(StrictModel):
    """What an agent says about its own work (``provenance.selfReport``): assumptions,
    alternatives it discarded, areas where it is not confident and changes nobody asked for.

    It is data of quality ``REPORTED``: the harness stores and shows it, contrasts it with the
    ChangeSet, and never treats it as a verification."""

    schema_version: Literal["1.0"] = "1.0"
    report_id: str
    execution_id: str
    invocation_id: str
    provider: str
    quality: MetricQuality = MetricQuality.REPORTED
    assumptions: tuple[str, ...] = ()
    alternatives_discarded: tuple[str, ...] = ()
    low_confidence_areas: tuple[SelfReportItem, ...] = ()
    unrequested_changes: tuple[SelfReportItem, ...] = ()
    problems: tuple[str, ...] = ()
    """What could not be read from the agent's answer (the rest is kept)."""
    contrast: SelfReportContrast | None = None
    created_at: datetime = Field(default_factory=utc_now)


ProvenanceSource = Literal["AGENT", "OUT_OF_BAND"]


class FileProvenance(StrictModel):
    path: str
    status: Literal["ADDED", "MODIFIED", "DELETED", "RENAMED", "UNTRACKED"]
    digest: str | None = None
    source: ProvenanceSource
    invocation_id: str | None = None
    """The agent invocation after which the file had its current content (``AGENT``)."""


class OutOfBandEdit(StrictModel):
    """A ChangeSet file that changed after the last agent invocation without one."""

    path: str
    status: Literal["ADDED", "MODIFIED", "DELETED"]
    agent_digest: str | None = None
    current_digest: str | None = None
    after_invocation_id: str | None = None
    detected_in: PhaseId


class ComponentProvenance(StrictModel):
    """Who produced each file of a ChangeSet (``provenance.agentSnapshots``)."""

    schema_version: Literal["1.0"] = "1.0"
    provenance_id: str
    execution_id: str
    change_set_digest: str
    detected_in: PhaseId
    files: tuple[FileProvenance, ...] = ()
    out_of_band_edits: tuple[OutOfBandEdit, ...] = ()
    agent_snapshot_refs: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class BundleEntry(StrictModel):
    path: str
    digest: str
    size_bytes: int = Field(ge=0)


class EvidenceBundleManifest(StrictModel):
    """``manifest.json`` of a portable evidence bundle (``harness export --bundle``): what the
    archive holds and the digest of every entry. ``harness verify --bundle`` checks it without
    the workspace."""

    schema_version: Literal["1.0"] = "1.0"
    bundle_format: Literal["governed-harness-bundle/1"] = "governed-harness-bundle/1"
    execution_id: str
    project_id: str
    task_id: str
    status: ResultStatus
    current_phase: PhaseId
    change_set_digest: str | None = None
    decision_id: str | None = None
    event_count: int = Field(ge=0)
    event_chain_head: str | None = None
    core_version: str
    created_at: datetime = Field(default_factory=utc_now)
    entries: tuple[BundleEntry, ...]


# ----- since 1.1 (#55): certification, deferred verification and human attachments ---------
CertificationStatus = Literal["CERTIFIED", "PARTIAL", "NOT_CERTIFIED"]
"""Status of a run: every criterion reached its rung, some did (or are pending), none did."""
CriterionStatus = Literal["CERTIFIED", "PENDING", "NOT_CERTIFIED", "WAIVED"]
"""``PENDING``: the rung is reached only by deferred evidence or a manual check not recorded
yet; ``WAIVED``: a person decided in preflight to continue without it (never certified)."""


class LevelEvidence(StrictModel):
    """One piece of recorded evidence that a criterion reached a rung."""

    level: VerificationLevel
    source: str
    detail: str
    refs: tuple[str, ...] = ()


class CriterionCertification(StrictModel):
    criterion_id: str
    required: VerificationLevel
    declared: bool
    """Whether the criterion declares its rung (``verification.level``); a criterion without a
    declaration requires ``verification.ladder.defaultLevel`` and its gap never blocks."""
    achieved: VerificationLevel | None = None
    status: CriterionStatus
    reason: str
    evidence: tuple[LevelEvidence, ...] = ()
    pending: tuple[str, ...] = ()


class CertificationRecord(StrictModel):
    """The certification of a run's ChangeSet: for each acceptance criterion the rung it
    requires, the rung its recorded evidence reaches and why (``verification.ladder``)."""

    schema_version: Literal["1.0"] = "1.0"
    certification_id: str
    execution_id: str
    change_set_digest: str
    status: CertificationStatus
    trigger: Literal["VERIFICATION", "DECISION", "EVIDENCE"]
    criteria: tuple[CriterionCertification, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


DeferredStatus = Literal["PENDING", "PASSED", "FAILED", "EXPIRED"]


class DeferredVerification(StrictModel):
    """A criterion that only CI, staging or a device lab can verify: pending after the run,
    bound to the ChangeSet digest (and to the closure commit once there is one), until
    ``harness evidence attach`` closes it with a JUnit, SARIF or CI status report. A pending
    item expires; an expired one cannot be closed and the criterion is not certified."""

    schema_version: Literal["1.0"] = "1.0"
    deferred_id: str
    item_id: str
    project_id: str
    execution_id: str
    task_id: str
    criterion_id: str
    level: VerificationLevel
    where: str
    change_set_digest: str
    commit: str | None = None
    status: DeferredStatus = "PENDING"
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    closed_at: datetime | None = None
    evidence_kind: Literal["junit", "sarif", "ci-status"] | None = None
    evidence_ref: str | None = None
    evidence_digest: str | None = None
    summary: str | None = None
    closed_by: Actor | None = None


class HumanAttachment(StrictModel):
    """Evidence a person attached (a screenshot, a video, a log): stored as an artifact and
    bound to its digest, to the task revision (intake context) or to a run's ChangeSet and,
    optionally, to a checklist item."""

    schema_version: Literal["1.0"] = "1.0"
    attachment_id: str
    project_id: str
    task_id: str
    task_digest: str | None = None
    execution_id: str | None = None
    change_set_digest: str | None = None
    item_id: str | None = None
    artifact_ref: str
    digest: str
    media_type: str
    size_bytes: int = Field(ge=0)
    file_name: str
    note: str | None = Field(default=None, max_length=2000)
    actor: Actor
    attached_at: datetime = Field(default_factory=utc_now)
