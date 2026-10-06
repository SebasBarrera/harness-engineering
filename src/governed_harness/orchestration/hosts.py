"""What the engine's helpers need from the objects that own them (#69).

The helpers of :class:`~governed_harness.orchestration.engine.RunEngine` (the agent results, the
friction settings, the verification ladder, the exit gates, provenance) and the helpers of
:class:`~governed_harness.orchestration.agent_results.AgentResults` (one per agent-results
setting) type their owner against these protocols instead of importing it, so that the owner
imports them without a module cycle. A helper that reaches a sibling through its owner (the
engineering settings from the acceptance tests, the ladder from the friction settings) types the
sibling against a port here for the same reason.

Every member is one a helper uses, with the owner's signature; the underscore members are engine
internals the helpers called before the split. The owners satisfy the protocols structurally,
which mypy checks where they pass ``self`` to a helper."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from governed_harness.agents import AgentExecutionResult, AgentProvider
    from governed_harness.agents.requests import CallKind
    from governed_harness.agents.routing import TaskSignals
    from governed_harness.checks.layers import LayerRules
    from governed_harness.checks.model import DiffFile, Issue
    from governed_harness.configuration.models import ProjectConfiguration, ValidatorDefinition
    from governed_harness.domain.enums import (
        EvidenceKind,
        FindingSeverity,
        PhaseId,
        ResultStatus,
        ValidationKind,
    )
    from governed_harness.domain.models import (
        Actor,
        ChangeSet,
        ClarificationQuestion,
        Evidence,
        Execution,
        FeedbackDecision,
        FeedbackGate,
        FeedbackTrigger,
        Finding,
        HumanDecision,
        PhaseExecution,
        Provenance,
        Task,
        ToolInvocation,
        ValidationResult,
    )
    from governed_harness.gates import GateEngine
    from governed_harness.orchestration.engine_types import (
        AgentCallOutcome,
        EngineServices,
        PhaseOutcome,
        ReviewOutcome,
        Strategy,
    )
    from governed_harness.orchestration.state_machine import NormativeStateMachine
    from governed_harness.orchestration.workspace_ops import Contents
    from governed_harness.runtime import SafeProcessRunner, WorkspaceSnapshot
    from governed_harness.runtime.sandbox import SandboxHost, SandboxPlan
    from governed_harness.runtime.snapshots import SnapshotStore
    from governed_harness.runtime.workspace import WorkspaceDiff
    from governed_harness.validators import ValidatorOutput, ValidatorRegistry


class EngineHost(Protocol):
    """The :class:`RunEngine` as its helpers use it."""

    @property
    def friction(self) -> FrictionPort:
        """The friction settings of the run (#58)."""

    @property
    def gate_engine(self) -> GateEngine:
        """The gate evaluation of DECISION."""

    @property
    def ladder(self) -> LadderPort:
        """The verification ladder (#55)."""

    @property
    def provenance(self) -> ProvenancePort:
        """The provenance recorder of the ChangeSet's files."""

    @property
    def results(self) -> ResultsHost:
        """The agent-results settings of the run."""

    @property
    def s(self) -> EngineServices:
        """The project's configuration, paths and stores."""

    @property
    def sandbox_host(self) -> SandboxHost:
        """The host the agent sandbox is built for."""

    @property
    def snapshots(self) -> SnapshotStore:
        """The workspace snapshots of the run."""

    @property
    def state_machine(self) -> NormativeStateMachine:
        """The normative phase transitions."""

    @property
    def validators(self) -> ValidatorRegistry:
        """The validators available to VERIFICATION."""

    def baseline_digests(self, execution: Execution) -> dict[str, str]:
        """Path to digest of the run's baseline snapshot."""

    def current_change_set(self, execution_id: str) -> ChangeSet:
        """The run's current ChangeSet."""

    def get_execution(self, execution_id: str) -> Execution:
        """The stored run."""

    def is_cancelled(self, execution_id: str) -> bool:
        """Whether the run was cancelled."""

    def run_task(self, execution: Execution) -> Task:
        """The task revision the run works on."""

    def _agent_network_allowed(self) -> bool:
        """Whether the agent sandbox allows outbound network."""

    def _bounded_definition(self, definition: ValidatorDefinition) -> ValidatorDefinition:
        """The validator definition within the phase's time budget."""

    def _bounded_timeout(self, seconds: int) -> int:
        """A timeout within the phase's time budget."""

    def _build_provider(
        self, execution: Execution, phase: PhaseExecution, provider_id: str
    ) -> tuple[AgentProvider, Actor, SandboxPlan | None, tuple[str, ...]] | PhaseOutcome:
        """The provider of a run, or the outcome when it cannot start."""

    def _compute_owned_diff(self, execution: Execution) -> WorkspaceDiff:
        """The task-owned diff, without persisting unredacted content."""

    def _current_contract_digest(self, execution: Execution) -> str | None:
        """The digest of the frozen acceptance contract."""

    def _external_provider(self, execution_id: str) -> bool:
        """Whether the run uses a configured command provider."""

    def _feedback_applies(self, execution_id: str) -> bool:
        """Whether provider feedback applies to the run."""

    def _latest_validations(self, execution_id: str, digest: str) -> list[ValidationResult]:
        """The latest validation of each validator for a digest."""

    def _protected_paths(self) -> tuple[Path, ...]:
        """Paths the agent sandbox keeps read-only."""

    def _provenance(self, execution: Execution) -> Provenance:
        """The provenance of the run's records."""

    def _record_evidence(
        self,
        execution: Execution,
        phase_id: PhaseId,
        kind: EvidenceKind,
        artifact_ref: Any,
        summary: str,
        *,
        supports: tuple[str, ...] = ...,
    ) -> Evidence:
        """Record an artifact as evidence of a phase."""

    def _record_feedback(
        self,
        execution: Execution,
        phase_id: PhaseId,
        *,
        trigger: FeedbackTrigger,
        change_set_digest: str,
        gate: FeedbackGate,
        validations: list[ValidationResult],
        findings: list[Finding],
        decision: FeedbackDecision | None = ...,
    ) -> str:
        """Store the feedback for the next IMPLEMENTATION attempt."""

    def _record_provider_retry(
        self,
        execution: Execution,
        phase: PhaseExecution,
        result: AgentExecutionResult,
        cause: str,
        retry: int,
    ) -> None:
        """Record a retry of a transient provider failure."""

    def _runner(self, execution: Execution) -> SafeProcessRunner:
        """The process runner of the run."""

    def _save_agent_result(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None:
        """Record what an agent call did."""

    def _save_execution(self, execution: Execution) -> None:
        """Store the run."""

    def _save_tool(self, execution: Execution, tool: ToolInvocation) -> None:
        """Record a tool invocation."""

    def _save_validator_output(self, execution: Execution, output: Any) -> None:
        """Record a validator's result and findings."""

    def _transient_cause(self, result: AgentExecutionResult) -> str | None:
        """The pattern that marks a failed provider call as transient."""

    def _wait_for_retry(self, execution_id: str, delay: float) -> bool:
        """Wait before a retry; ``False`` when the run is cancelled."""

    @staticmethod
    def _snapshot_from_dict(value: dict[str, Any]) -> WorkspaceSnapshot:
        """A workspace snapshot from its stored form."""

    @staticmethod
    def _snapshot_to_dict(snapshot: WorkspaceSnapshot) -> dict[str, Any]:
        """The stored form of a workspace snapshot."""


class ResultsHost(Protocol):
    """The :class:`AgentResults` as its helpers use it."""

    @property
    def acceptance(self) -> AcceptancePort:
        """The acceptance tests (``verification.acceptanceTests``)."""

    @property
    def active(self) -> bool:
        """Whether any agent-results setting is configured."""

    @property
    def agent_review(self) -> AgentReviewPort:
        """The single independent reviewer."""

    @property
    def architecture(self) -> ArchitecturePort:
        """The architecture flow (#56)."""

    @property
    def corrections(self) -> CorrectionsPort:
        """The reproduce-first corrections."""

    @property
    def engine(self) -> EngineHost:
        """The engine that owns these settings."""

    @property
    def engineering(self) -> EngineeringPort:
        """The standards, principles and testing strategy (#56)."""

    @property
    def panel(self) -> PanelPort:
        """The review panel (#57)."""

    @property
    def project(self) -> ProjectConfiguration:
        """The project configuration."""

    @property
    def project_setup(self) -> ProjectSetupPort:
        """The project setup questions (#56)."""

    @property
    def s(self) -> EngineServices:
        """The project's configuration, paths and stores."""

    @property
    def secrets_in_context(self) -> bool:
        """Whether secrets may reach an agent's context."""

    @property
    def stop_line(self) -> StopLinePort:
        """The stop-the-line checks."""

    @property
    def verification(self) -> VerificationPort:
        """The verification checks."""

    def after_agent_call(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None:
        """Account for a finished agent call."""

    def baseline_changes(self, execution: Execution) -> WorkspaceDiff | None:
        """Every change since DISCOVERY, with a unified diff."""

    def baseline_contents(self, execution: Execution) -> Contents | None:
        """The recorded bytes of the baseline's files."""

    def baseline_snapshot(self, execution: Execution) -> WorkspaceSnapshot | None:
        """The workspace as DISCOVERY recorded it."""

    def baseline_text(self, execution: Execution) -> Callable[[str], str | None]:
        """The baseline text of a path, when it had one."""

    def before_agent_call(
        self, execution: Execution, phase: PhaseExecution, kind: CallKind
    ) -> PhaseOutcome | None:
        """Fail closed before an agent call when a limit is crossed."""

    def call_agent(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        payload: dict[str, Any],
        *,
        task: Task,
        instruction_values: dict[str, Any] | None = ...,
        instructions_suffix: str = ...,
        validate: Callable[[dict[str, Any]], object] | None = ...,
    ) -> AgentCallOutcome:
        """Send a read-only request and return its structured result (retried once when the
        answer breaks its contract, under ``runtime.contractRetry``)."""

    def change_requests(self, execution: Execution) -> list[dict[str, Any]]:
        """The structured change requests of the run."""

    def flag_json(self, key: str) -> Any:
        """A JSON flag of the state store."""

    def implement_model(self, execution: Execution, task: Task) -> str | None:
        """The model an implement call would use."""

    def provider_for(self, execution: Execution, kind: CallKind) -> str:
        """The provider of a call kind."""

    def record_finding(
        self,
        execution: Execution,
        *,
        validator_id: str,
        rule_id: str,
        category: str,
        severity: FindingSeverity,
        message: str,
        path: str | None = ...,
        line: int | None = ...,
        evidence_refs: tuple[str, ...] = ...,
        recommendation: str | None = ...,
        introduced: bool | None = ...,
        actor: Actor | None = ...,
    ) -> Finding:
        """Record a finding of the run."""

    def record_json(
        self,
        execution: Execution,
        phase_id: PhaseId,
        value: Any,
        *,
        kind: str,
        summary: str,
        evidence_kind: EvidenceKind = ...,
        supports: tuple[str, ...] = ...,
    ) -> str:
        """Store a JSON artifact as evidence of a phase."""

    def record_validation(
        self,
        execution: Execution,
        *,
        validator_id: str,
        digest: str,
        status: ResultStatus,
        kind: ValidationKind,
        mandatory: bool,
        summary: str,
        findings: tuple[Finding, ...] = ...,
        evidence_refs: tuple[str, ...],
        started_at: Any = ...,
    ) -> ValidationResult:
        """Record a validation of a ChangeSet digest."""

    def required_acknowledgements(self, execution: Execution, digest: str) -> list[str]:
        """The risk factors a decision must acknowledge."""

    def set_flag_json(self, key: str, value: Any) -> None:
        """Store a JSON flag."""

    def task_signals(self, execution: Execution, task: Task) -> TaskSignals:
        """Size signals of the task known before the call."""


class AcceptancePort(Protocol):
    """The acceptance tests (``verification.acceptanceTests``) as siblings use them."""

    def state(self, execution: Execution) -> dict[str, Any] | None:
        """The acceptance tests' state of the run."""

    def validation(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput | None:
        """The acceptance tests as a validation of the ChangeSet."""


class AgentReviewPort(Protocol):
    """The single independent reviewer as the review panel uses it."""

    def blocking_severities(self) -> set[FindingSeverity]:
        """The finding severities that block."""


class ArchitecturePort(Protocol):
    """The architecture flow as siblings use it."""

    def request_extra(self) -> dict[str, Any] | None:
        """What the architecture adds to an agent request."""

    def rules(self) -> LayerRules | None:
        """The layer rules of the project."""


class CorrectionsPort(Protocol):
    """The reproduce-first corrections as the verification checks use them."""

    def validation(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput | None:
        """The latest correction attempt as a validation."""


class EngineeringPort(Protocol):
    """The engineering settings (standards, testing, TDD) as siblings use them."""

    @property
    def configured(self) -> bool:
        """Whether any engineering setting is configured."""

    def bdd_command(self) -> tuple[str, ...]:
        """The command that runs the feature files."""

    def features_directory(self) -> str:
        """Where the feature files live."""

    def review_extra(self, change_set: ChangeSet) -> tuple[dict[str, Any], str]:
        """The checklist the review call gains."""

    def setup_record(self, project_id: str) -> dict[str, Any]:
        """The project setup answers."""

    def strategy(self, project_id: str) -> Strategy:
        """The effective testing strategy."""

    def tdd_validation(
        self,
        execution: Execution,
        change_set: ChangeSet,
        diff: list[DiffFile],
        outputs_so_far: list[ValidatorOutput],
    ) -> ValidatorOutput | None:
        """The TDD check as a validation of the ChangeSet."""

    def technologies(self) -> tuple[str, ...]:
        """The technologies of the project."""


class FrictionPort(Protocol):
    """The friction settings as the engine's helpers use them."""

    @property
    def active(self) -> bool:
        """Whether a friction setting that acts on a run is configured."""

    def skips(self, execution: Execution, step: str, phase_id: PhaseId | None = ...) -> bool:
        """Whether the fast lane leaves a step out of the run."""

    def tests_exempt(self, execution: Execution, change_set: ChangeSet, check: str) -> str | None:
        """Why a test requirement does not apply to the ChangeSet."""


class LadderIntakePort(Protocol):
    """The ladder's intake step as the friction settings use it."""

    def defaults(
        self, execution: Execution | None = ..., task: Task | None = ...
    ) -> dict[str, Any]:
        """What the configuration says about a contract's open items."""


class LadderPort(Protocol):
    """The verification ladder as the engine's helpers use it."""

    @property
    def active(self) -> bool:
        """Whether any ladder setting that acts on a run is configured."""

    @property
    def intake(self) -> LadderIntakePort:
        """The intake step of the ladder."""

    def after_decision(self, execution: Execution, record: HumanDecision) -> None:
        """Recompute the certification with the ticked items."""

    def located_paths(self, execution: Execution) -> list[str]:
        """The paths the locate call found."""

    def manual_items(self, task: Task) -> list[dict[str, str]]:
        """What only a person can verify."""


class PanelPort(Protocol):
    """The review panel as the single reviewer uses it."""

    @property
    def configured(self) -> bool:
        """Whether the review panel is configured."""

    def run(
        self, execution: Execution, phase: PhaseExecution, change_set: ChangeSet
    ) -> ReviewOutcome:
        """Review the ChangeSet with the panel."""


class ProjectSetupPort(Protocol):
    """The project setup questions as the intent review uses them."""

    def questions(
        self, execution: Execution, phase: PhaseExecution, start: int
    ) -> tuple[ClarificationQuestion, ...]:
        """The project setup questions to ask in INTENT."""


class ProvenancePort(Protocol):
    """The provenance recorder as the review panel uses it."""

    @property
    def snapshots_enabled(self) -> bool:
        """Whether agent snapshots are recorded."""

    def attribute(self, execution: Execution, change_set: ChangeSet, phase_id: PhaseId) -> None:
        """Record who produced each file of the ChangeSet."""


class StopLinePort(Protocol):
    """The stop-the-line checks as the verification checks use them."""

    def owned_paths_output(
        self, execution: Execution, change_set: ChangeSet
    ) -> ValidatorOutput | None:
        """The owned-paths check as a validation."""


class VerificationPort(Protocol):
    """The verification checks as siblings use them."""

    def risk_actions(self) -> dict[str, str]:
        """The action of each risk factor."""

    def _output(
        self,
        execution: Execution,
        change_set: ChangeSet,
        validator_id: str,
        policy: str,
        issues: list[Issue],
        label: str,
        *,
        started: datetime,
        extra_evidence: tuple[str, ...] = ...,
        mandatory: bool | None = ...,
        report: dict[str, Any] | None = ...,
    ) -> ValidatorOutput:
        """A verification check's result as a validator output."""


__all__ = [
    "AcceptancePort",
    "AgentReviewPort",
    "ArchitecturePort",
    "CorrectionsPort",
    "EngineHost",
    "EngineeringPort",
    "FrictionPort",
    "LadderIntakePort",
    "LadderPort",
    "PanelPort",
    "ProjectSetupPort",
    "ProvenancePort",
    "ResultsHost",
    "StopLinePort",
    "VerificationPort",
]
