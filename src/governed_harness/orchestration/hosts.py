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
    def friction(self) -> FrictionPort: ...

    @property
    def gate_engine(self) -> GateEngine: ...

    @property
    def ladder(self) -> LadderPort: ...

    @property
    def provenance(self) -> ProvenancePort: ...

    @property
    def results(self) -> ResultsHost: ...

    @property
    def s(self) -> EngineServices: ...

    @property
    def sandbox_host(self) -> SandboxHost: ...

    @property
    def snapshots(self) -> SnapshotStore: ...

    @property
    def state_machine(self) -> NormativeStateMachine: ...

    @property
    def validators(self) -> ValidatorRegistry: ...

    def baseline_digests(self, execution: Execution) -> dict[str, str]: ...

    def current_change_set(self, execution_id: str) -> ChangeSet: ...

    def get_execution(self, execution_id: str) -> Execution: ...

    def is_cancelled(self, execution_id: str) -> bool: ...

    def run_task(self, execution: Execution) -> Task: ...

    def _agent_network_allowed(self) -> bool: ...

    def _bounded_definition(self, definition: ValidatorDefinition) -> ValidatorDefinition: ...

    def _bounded_timeout(self, seconds: int) -> int: ...

    def _build_provider(
        self, execution: Execution, phase: PhaseExecution, provider_id: str
    ) -> tuple[AgentProvider, Actor, SandboxPlan | None, tuple[str, ...]] | PhaseOutcome: ...

    def _compute_owned_diff(self, execution: Execution) -> WorkspaceDiff: ...

    def _current_contract_digest(self, execution: Execution) -> str | None: ...

    def _external_provider(self, execution_id: str) -> bool: ...

    def _feedback_applies(self, execution_id: str) -> bool: ...

    def _latest_validations(self, execution_id: str, digest: str) -> list[ValidationResult]: ...

    def _protected_paths(self) -> tuple[Path, ...]: ...

    def _provenance(self, execution: Execution) -> Provenance: ...

    def _record_evidence(
        self,
        execution: Execution,
        phase_id: PhaseId,
        kind: EvidenceKind,
        artifact_ref: Any,
        summary: str,
        *,
        supports: tuple[str, ...] = ...,
    ) -> Evidence: ...

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
    ) -> str: ...

    def _record_provider_retry(
        self,
        execution: Execution,
        phase: PhaseExecution,
        result: AgentExecutionResult,
        cause: str,
        retry: int,
    ) -> None: ...

    def _runner(self, execution: Execution) -> SafeProcessRunner: ...

    def _save_agent_result(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None: ...

    def _save_execution(self, execution: Execution) -> None: ...

    def _save_tool(self, execution: Execution, tool: ToolInvocation) -> None: ...

    def _save_validator_output(self, execution: Execution, output: Any) -> None: ...

    def _transient_cause(self, result: AgentExecutionResult) -> str | None: ...

    def _wait_for_retry(self, execution_id: str, delay: float) -> bool: ...

    @staticmethod
    def _snapshot_from_dict(value: dict[str, Any]) -> WorkspaceSnapshot: ...

    @staticmethod
    def _snapshot_to_dict(snapshot: WorkspaceSnapshot) -> dict[str, Any]: ...


class ResultsHost(Protocol):
    """The :class:`AgentResults` as its helpers use it."""

    @property
    def acceptance(self) -> AcceptancePort: ...

    @property
    def active(self) -> bool: ...

    @property
    def agent_review(self) -> AgentReviewPort: ...

    @property
    def architecture(self) -> ArchitecturePort: ...

    @property
    def corrections(self) -> CorrectionsPort: ...

    @property
    def engine(self) -> EngineHost: ...

    @property
    def engineering(self) -> EngineeringPort: ...

    @property
    def panel(self) -> PanelPort: ...

    @property
    def project(self) -> ProjectConfiguration: ...

    @property
    def project_setup(self) -> ProjectSetupPort: ...

    @property
    def s(self) -> EngineServices: ...

    @property
    def secrets_in_context(self) -> bool: ...

    @property
    def stop_line(self) -> StopLinePort: ...

    @property
    def verification(self) -> VerificationPort: ...

    def after_agent_call(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None: ...

    def baseline_changes(self, execution: Execution) -> WorkspaceDiff | None: ...

    def baseline_contents(self, execution: Execution) -> Contents | None: ...

    def baseline_snapshot(self, execution: Execution) -> WorkspaceSnapshot | None: ...

    def baseline_text(self, execution: Execution) -> Callable[[str], str | None]: ...

    def before_agent_call(
        self, execution: Execution, phase: PhaseExecution, kind: CallKind
    ) -> PhaseOutcome | None: ...

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
    ) -> AgentCallOutcome: ...

    def change_requests(self, execution: Execution) -> list[dict[str, Any]]: ...

    def flag_json(self, key: str) -> Any: ...

    def implement_model(self, execution: Execution, task: Task) -> str | None: ...

    def provider_for(self, execution: Execution, kind: CallKind) -> str: ...

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
    ) -> Finding: ...

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
    ) -> str: ...

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
    ) -> ValidationResult: ...

    def required_acknowledgements(self, execution: Execution, digest: str) -> list[str]: ...

    def set_flag_json(self, key: str, value: Any) -> None: ...

    def task_signals(self, execution: Execution, task: Task) -> TaskSignals: ...


class AcceptancePort(Protocol):
    """The acceptance tests (``verification.acceptanceTests``) as siblings use them."""

    def state(self, execution: Execution) -> dict[str, Any] | None: ...

    def validation(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput | None: ...


class AgentReviewPort(Protocol):
    """The single independent reviewer as the review panel uses it."""

    def blocking_severities(self) -> set[FindingSeverity]: ...


class ArchitecturePort(Protocol):
    """The architecture flow as siblings use it."""

    def request_extra(self, execution: Execution) -> dict[str, Any] | None: ...

    def rules(self) -> LayerRules | None: ...


class CorrectionsPort(Protocol):
    """The reproduce-first corrections as the verification checks use them."""

    def validation(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput | None: ...


class EngineeringPort(Protocol):
    """The engineering settings (standards, testing, TDD) as siblings use them."""

    @property
    def configured(self) -> bool: ...

    def bdd_command(self) -> tuple[str, ...]: ...

    def features_directory(self) -> str: ...

    def review_extra(
        self, execution: Execution, change_set: ChangeSet
    ) -> tuple[dict[str, Any], str]: ...

    def setup_record(self, project_id: str) -> dict[str, Any]: ...

    def strategy(self, project_id: str) -> Strategy: ...

    def tdd_validation(
        self,
        execution: Execution,
        change_set: ChangeSet,
        diff: list[DiffFile],
        outputs_so_far: list[ValidatorOutput],
    ) -> ValidatorOutput | None: ...

    def technologies(self) -> tuple[str, ...]: ...


class FrictionPort(Protocol):
    """The friction settings as the engine's helpers use them."""

    @property
    def active(self) -> bool: ...

    def skips(self, execution: Execution, step: str, phase_id: PhaseId | None = ...) -> bool: ...

    def tests_exempt(
        self, execution: Execution, change_set: ChangeSet, check: str
    ) -> str | None: ...


class LadderIntakePort(Protocol):
    """The ladder's intake step as the friction settings use it."""

    def defaults(
        self, execution: Execution | None = ..., task: Task | None = ...
    ) -> dict[str, Any]: ...


class LadderPort(Protocol):
    """The verification ladder as the engine's helpers use it."""

    @property
    def active(self) -> bool: ...

    @property
    def intake(self) -> LadderIntakePort: ...

    def after_decision(self, execution: Execution, record: HumanDecision) -> None: ...

    def located_paths(self, execution: Execution) -> list[str]: ...

    def manual_items(self, task: Task) -> list[dict[str, str]]: ...


class PanelPort(Protocol):
    """The review panel as the single reviewer uses it."""

    @property
    def configured(self) -> bool: ...

    def run(
        self, execution: Execution, phase: PhaseExecution, change_set: ChangeSet
    ) -> ReviewOutcome: ...


class ProjectSetupPort(Protocol):
    """The project setup questions as the intent review uses them."""

    def questions(
        self, execution: Execution, phase: PhaseExecution, task: Task, start: int
    ) -> tuple[ClarificationQuestion, ...]: ...


class ProvenancePort(Protocol):
    """The provenance recorder as the review panel uses it."""

    @property
    def snapshots_enabled(self) -> bool: ...

    def attribute(self, execution: Execution, change_set: ChangeSet, phase_id: PhaseId) -> None: ...


class StopLinePort(Protocol):
    """The stop-the-line checks as the verification checks use them."""

    def owned_paths_output(
        self, execution: Execution, change_set: ChangeSet
    ) -> ValidatorOutput | None: ...


class VerificationPort(Protocol):
    """The verification checks as siblings use them."""

    def risk_actions(self) -> dict[str, str]: ...

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
    ) -> ValidatorOutput: ...


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
