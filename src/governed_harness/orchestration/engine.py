from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from governed_harness import __version__
from governed_harness.agents import (
    AgentExecutionResult,
    AgentProvider,
    CommandAgentConfiguration,
    CommandAgentProvider,
    SimulatedAgentContext,
    SimulatedAgentProvider,
)
from governed_harness.agents.environment import (
    ProviderEnvironment,
    provider_environment,
    secret_values,
)
from governed_harness.agents.native import native_provider
from governed_harness.agents.session import SESSION_PROVIDER, SessionAgentProvider
from governed_harness.capabilities import grants_from_rules
from governed_harness.capabilities.authorizer import contained_path
from governed_harness.capabilities.phase import PhasePolicy, phase_scope
from governed_harness.capabilities.repository import (
    DestructivePolicy,
    destructive_scope,
    repository_policies,
)
from governed_harness.configuration.loader import BUILTIN_PROFILE_IDS
from governed_harness.configuration.models import (
    ResolvedConfiguration,
    ValidatorDefinition,
    WorkflowPhaseDefinition,
)
from governed_harness.delivery.closure import ClosureCommit, create_closure_commit
from governed_harness.delivery.vcs import VcsError
from governed_harness.domain.actors import (
    NON_HUMAN_ACTOR_PREFIXES as NON_HUMAN_ACTOR_PREFIXES,  # re-exported for callers
)
from governed_harness.domain.actors import IdentitySource, require_human_actor
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    EvidenceKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.errors import (
    ConfigurationError,
    NotFoundError,
    PolicyViolationError,
)
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    FEEDBACK_RATIONALE_CHARS,
    HARNESS_ACTOR,
    Actor,
    Artifact,
    ChangedFile,
    ChangeRequestItem,
    ChangeSet,
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationRecord,
    ClarificationRequest,
    ConfigurationSnapshot,
    Evidence,
    ExceptionRecord,
    Execution,
    FeedbackDecision,
    FeedbackGate,
    FeedbackTrigger,
    Finding,
    FindingLocation,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Plan,
    PlanStep,
    Provenance,
    ProviderFeedback,
    Task,
    ToolInvocation,
    ValidationResult,
    utc_now,
)
from governed_harness.events import AnchorStore, SQLiteEventStore
from governed_harness.evidence import LocalArtifactStore, SecretRedactor, sha256_json
from governed_harness.gates import GateEngine, GatePolicy
from governed_harness.gates.exceptions import apply_exceptions, exception_ids
from governed_harness.intake import (
    ClarificationInput,
    assess_intent,
    no_acceptance_criteria,
    revise_task,
    task_digest,
)
from governed_harness.memory import MemoryStore, context_manifest
from governed_harness.orchestration.agent_results import AgentResults
from governed_harness.orchestration.feedback import (
    TRANSIENT_SCAN_BYTES,
    FeedbackBuilder,
    failing_validations,
    head,
    transient_cause,
    verification_reason_codes,
)
from governed_harness.orchestration.provenance import ProvenanceRecorder
from governed_harness.orchestration.state_machine import NormativeStateMachine
from governed_harness.profiles import detect_profiles
from governed_harness.retrospective import RetrospectiveEngine
from governed_harness.retrospective.causes import CauseAnalysis
from governed_harness.retrospective.causes import analyze as analyze_causes
from governed_harness.runtime import (
    CancellationToken,
    GitAdapter,
    PatchApplier,
    SafeProcessRunner,
    WorkspaceDiff,
    WorkspaceSnapshot,
)
from governed_harness.runtime.guard import IGNORED_PATTERNS, ExcludedPathGuard
from governed_harness.runtime.lease import terminate_process_group
from governed_harness.runtime.sandbox import (
    SandboxHost,
    SandboxPlan,
    SandboxUnavailable,
    build_sandbox,
    denied_writes,
)
from governed_harness.runtime.snapshots import (
    SNAPSHOT_CACHE_SEAL,
    SnapshotSettings,
    SnapshotStore,
)
from governed_harness.runtime.state_location import isolation_marker, resolve_state_location
from governed_harness.storage import SQLiteStateStore
from governed_harness.telemetry import MetricsProjector
from governed_harness.validators import (
    TRACEABILITY_VALIDATOR_ID,
    IndependentReviewValidator,
    RequirementTraceabilityValidator,
    TraceabilityOutput,
    ValidationContext,
    ValidatorRegistry,
)
from governed_harness.validators.base import ValidatorOutput
from governed_harness.validators.coverage import CoverageValidator, coverage_minimum

FAILED_ATTEMPT_STATUSES = frozenset(
    {
        ResultStatus.FAILED,
        ResultStatus.ERROR,
        ResultStatus.TIMED_OUT,
        ResultStatus.INTERRUPTED,
    }
)
"""Phase results that use up one of the workflow's ``maxAttempts``."""
DEFAULT_VALIDATOR_TIMEOUT_SECONDS = 900

WORKSPACE_GUARD_ID = "harness.workspace-guard"
"""Validator id of the excluded-path check (``governance.protectExcludedPaths``)."""
OUT_OF_CHANGESET_RULE = "workspace.out-of-changeset-write"

UNSUPPORTED_CLAIM_RULE = "agent.unsupported-claim"
"""Rule id of the finding recorded when an agent reported success and verification failed."""
CLAIM_CHECK_ID = "harness.claim-check"
"""Validator id of that finding: the harness compares the claim with recorded results."""


REJECTED_REASON = "Rejected by human decision"


def run_is_open(execution: Execution) -> bool:
    """Whether a run may still change: not closed, not cancelled and not rejected. A run that
    failed or is blocked can be resumed with ``run continue`` and stays open."""
    if execution.status in {ResultStatus.PASSED, ResultStatus.CANCELLED}:
        return False
    return not (
        execution.status is ResultStatus.FAILED and execution.terminal_reason == REJECTED_REASON
    )


def acceptance_contract_digest(task: Task) -> str:
    """Digest of the acceptance contract SPECIFICATION freezes: requirements, acceptance
    criteria and constraints of a task revision."""
    return sha256_json(
        {
            "requirements": [item.model_dump(mode="json") for item in task.requirements],
            "acceptance": [item.model_dump(mode="json") for item in task.acceptance_criteria],
            "constraints": list(task.constraints),
        }
    )


class _ProcessLedger:
    """Records the process groups a run's runner starts (flag ``process:<run>``), so that a
    recovery after a killed harness can terminate the ones still running."""

    def __init__(self, state: SQLiteStateStore, execution_id: str) -> None:
        self.state = state
        self.key = f"process:{execution_id}"

    def started(self, pid: int, argv: tuple[str, ...]) -> None:
        recorded = json.loads(self.state.get_flag(self.key) or "{}")
        recorded[str(pid)] = {
            "pgid": pid,
            "host": socket.gethostname(),
            "argv0": argv[0] if argv else "",
            "startedAt": utc_now().isoformat(),
        }
        self.state.set_flag(self.key, json.dumps(recorded, sort_keys=True))

    def finished(self, pid: int) -> None:
        recorded = json.loads(self.state.get_flag(self.key) or "{}")
        if recorded.pop(str(pid), None) is not None:
            self.state.set_flag(self.key, json.dumps(recorded, sort_keys=True))


@dataclass(frozen=True)
class EnginePaths:
    workspace: Path
    harness_dir: Path
    database: Path
    artifact_dir: Path
    state_root: Path | None = None
    """The run registry outside the workspace (``runtime.stateDir`` or an isolated run's
    origin, #55); ``None`` when the state lives in ``.harness/`` as in 1.0.0."""

    @classmethod
    def from_workspace(cls, workspace: Path) -> EnginePaths:
        root = workspace.resolve(strict=True)
        harness_dir = root / ".harness"
        harness_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return cls(root, harness_dir, harness_dir / "state.db", harness_dir / "artifacts")

    @classmethod
    def for_project(cls, resolved: ResolvedConfiguration) -> EnginePaths:
        """The paths of a resolved project: ``.harness/`` for the workspace's own files and the
        state location ``runtime.stateDir`` (or an isolation marker) names for the registry."""
        paths = cls.from_workspace(resolved.workspace_root)
        location = resolve_state_location(
            paths.workspace, resolved.project.project_id, resolved.project.runtime.state_dir
        )
        if not location.external:
            return paths
        return cls(
            paths.workspace,
            paths.harness_dir,
            location.database,
            location.artifacts,
            location.root,
        )


@dataclass
class EngineServices:
    resolved: ResolvedConfiguration
    paths: EnginePaths
    state: SQLiteStateStore
    events: SQLiteEventStore
    artifacts: LocalArtifactStore

    @classmethod
    def open(cls, resolved: ResolvedConfiguration) -> EngineServices:
        paths = EnginePaths.for_project(resolved)
        return cls(
            resolved=resolved,
            paths=paths,
            state=SQLiteStateStore(paths.database),
            events=SQLiteEventStore(paths.database),
            artifacts=LocalArtifactStore(
                paths.artifact_dir,
                SecretRedactor(
                    literals=secret_values(resolved.project),
                    extended=bool(resolved.project.runtime.extended_redaction),
                ),
            ),
        )

    def close(self) -> None:
        self.state.close()
        self.events.close()


@dataclass(frozen=True)
class PhaseOutcome:
    status: ResultStatus
    summary: str
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


class RunEngine:
    def __init__(self, services: EngineServices, sandbox_host: SandboxHost | None = None) -> None:
        self.s = services
        self.state_machine = NormativeStateMachine()
        self.gate_engine = GateEngine()
        self.validators = ValidatorRegistry()
        self.retrospective_engine = RetrospectiveEngine()
        self._sandbox_host = sandbox_host
        self.provenance = ProvenanceRecorder(self)
        self.snapshots = SnapshotStore(
            services.paths.workspace,
            services.artifacts,
            SnapshotSettings.from_config(services.resolved.project.workspace),
            services.paths.harness_dir,
            seal=(
                lambda: services.state.get_flag(SNAPSHOT_CACHE_SEAL),
                lambda digest: services.state.set_flag(SNAPSHOT_CACHE_SEAL, digest),
            ),
        )
        self._phase_deadline: float | None = None
        self.results = AgentResults(self)
        # Imported here: the ladder modules type against the engine, so a module-level import
        # would close an import cycle.
        from governed_harness.orchestration.ladder import VerificationLadder

        self.ladder = VerificationLadder(self)

    @property
    def sandbox_host(self) -> SandboxHost:
        """The host the agent sandbox is built for; detected on first use unless injected."""
        if self._sandbox_host is None:
            self._sandbox_host = SandboxHost.detect()
        return self._sandbox_host

    # ----- creation and lifecycle -------------------------------------------------
    def create_execution(
        self, task: Task, provider: str | None = None, *, execution_id: str | None = None
    ) -> Execution:
        if task.project_id != self.s.resolved.project.project_id:
            raise ConfigurationError(
                f"task project {task.project_id} does not match {self.s.resolved.project.project_id}"
            )
        if self.results.active:
            # governance.stopTheLine: block refuses a run on top of unapproved changes.
            self.results.stop_line.check_new_run(task.project_id)
        execution_id = execution_id or new_id("run")
        resolved_dict = self.s.resolved.model_dump(mode="json", by_alias=True)
        config_ref = self.s.artifacts.put_json(
            resolved_dict,
            metadata={"kind": "configuration-snapshot", "executionId": execution_id},
        )
        workflow_dict = self.s.resolved.workflow.model_dump(mode="json", by_alias=True)
        workflow_ref = self.s.artifacts.put_json(
            workflow_dict,
            metadata={"kind": "workflow", "executionId": execution_id},
        )
        configuration_digest = sha256_json(resolved_dict)
        workflow_digest = sha256_json(workflow_dict)
        policy_digest = sha256_json(self.s.resolved.effective_policies)
        execution = Execution(
            execution_id=execution_id,
            project_id=task.project_id,
            task_id=task.task_id,
            workspace=str(self.s.paths.workspace),
            status=ResultStatus.PENDING,
            current_phase=PhaseId.INTENT,
            configuration_digest=configuration_digest,
            workflow_digest=workflow_digest,
            policy_digest=policy_digest,
            configuration_snapshot_ref=config_ref.uri,
            workflow_ref=workflow_ref.uri,
        )
        self.s.state.put(
            "execution",
            execution.execution_id,
            execution,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        snapshot = ConfigurationSnapshot(
            snapshot_id=new_id("configsnapshot"),
            execution_id=execution_id,
            content_digest=configuration_digest,
            artifact_ref=config_ref.uri,
        )
        self.s.state.put(
            "configuration_snapshot",
            snapshot.snapshot_id,
            snapshot,
            execution_id=execution_id,
            project_id=execution.project_id,
        )
        self.s.state.set_flag(
            f"provider:{execution_id}", provider or self.s.resolved.project.agent_provider
        )
        created: dict[str, Any] = {
            "taskId": task.task_id,
            "projectId": task.project_id,
            "configurationDigest": configuration_digest,
            "workflowDigest": workflow_digest,
            "policyDigest": policy_digest,
            "provider": provider or self.s.resolved.project.agent_provider,
        }
        if self._pins_task():
            created.update(self._pin_task_revision(execution_id, task))
        self.s.events.append(execution_id, "run.created", created)
        self._record_artifact(
            execution,
            config_ref,
            kind="CONFIGURATION",
            provenance=self._provenance(execution),
        )
        self._record_artifact(
            execution,
            workflow_ref,
            kind="WORKFLOW",
            provenance=self._provenance(execution),
        )
        return execution

    def continue_execution(self, execution_id: str) -> Execution:
        try:
            execution = self._continue_execution(execution_id)
            if self.results.active:
                # governance.stopTheLine and memory.learnFromFindings at the end of a step.
                execution = self.results.after_run(execution)
            return execution
        finally:
            self.anchor_chain(execution_id)

    def anchor_chain(self, execution_id: str) -> None:
        """Copy the head of the run's event chain to the place ``governance.chainAnchor``
        names, so that ``harness verify`` detects a chain whose last events were deleted. A
        chain that does not verify is never anchored, and a failed write never stops a run:
        ``harness verify`` reports the anchor as absent."""
        mode = self.s.resolved.project.governance_settings.chain_anchor
        if not mode or mode == "off":
            return
        check = self.s.events.check_chain(execution_id)
        if not check.valid or check.head_sequence is None or check.head_digest is None:
            return
        store = AnchorStore(mode, self.s.paths.workspace, self.s.resolved.project.project_id)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            store.record(execution_id, check.head_sequence, check.head_digest)

    def _continue_execution(self, execution_id: str) -> Execution:
        execution = self.get_execution(execution_id)
        if execution.status in {ResultStatus.PASSED, ResultStatus.CANCELLED}:
            return execution
        if self.is_cancelled(execution_id):
            return self._cancel_execution(execution)
        if self._leases_workspace():
            blocked = self.recover_interrupted(execution_id)
            if blocked is not None:
                return blocked
        while True:
            execution = self.get_execution(execution_id)
            if execution.current_phase is PhaseId.DECISION:
                outcome = self._run_phase(execution, self._phase_decision)
                execution = self.get_execution(execution_id)
                if outcome.status is ResultStatus.BLOCKED:
                    return execution
            elif execution.current_phase is PhaseId.CLOSURE:
                self._run_phase(execution, self._phase_closure)
                return self.get_execution(execution_id)
            else:
                handler = {
                    PhaseId.INTENT: self._phase_intent,
                    PhaseId.DISCOVERY: self._phase_discovery,
                    PhaseId.SPECIFICATION: self._phase_specification,
                    PhaseId.PLANNING: self._phase_planning,
                    PhaseId.IMPLEMENTATION: self._phase_implementation,
                    PhaseId.VERIFICATION: self._phase_verification,
                    PhaseId.INDEPENDENT_REVIEW: self._phase_independent_review,
                }[execution.current_phase]
                phase_id = execution.current_phase
                outcome = self._run_phase(execution, handler)
                execution = self.get_execution(execution_id)
                if outcome.status is not ResultStatus.PASSED:
                    if phase_id is PhaseId.VERIFICATION and self._after_failed_verification(
                        execution, outcome
                    ):
                        continue
                    if (
                        phase_id is PhaseId.INDEPENDENT_REVIEW
                        and self.results.active
                        and self.results.after_failed_review(execution, outcome.summary)
                    ):
                        continue
                    return execution
                if (
                    phase_id is PhaseId.VERIFICATION
                    and self.results.active
                    and self.results.after_passed_verification(execution)
                ):
                    # planning.decomposition: the next sub-task starts (#39).
                    continue
            if execution.status in {
                ResultStatus.PASSED,
                ResultStatus.CANCELLED,
                ResultStatus.ERROR,
            }:
                return execution

    def cancel(self, execution_id: str, actor_id: str = "human.local") -> Execution:
        execution = self.get_execution(execution_id)
        self.s.state.set_flag(f"cancel:{execution_id}", "1")
        actor = Actor(actor_type=ActorType.HUMAN, actor_id=actor_id)
        self.s.events.append(execution_id, "run.cancellation.requested", {}, actor=actor)
        try:
            cancelled = self._cancel_execution(execution)
            if self.results.active:
                self.results.stop_line.stop(cancelled, "cancelled")
            return cancelled
        finally:
            self.anchor_chain(execution_id)

    def decide(
        self,
        *,
        execution_id: str,
        decision: DecisionKind,
        change_set_digest: str,
        actor_id: str,
        rationale: str,
        actor_display_name: str | None = None,
        identity_source: IdentitySource | None = None,
        expires_at: datetime | None = None,
        acknowledged_risks: tuple[str, ...] = (),
        change_requests: tuple[ChangeRequestItem, ...] = (),
        checked_items: tuple[str, ...] = (),
    ) -> HumanDecision:
        require_human_actor(actor_id, f"decide {decision.value} on a gate")
        execution = self.get_execution(execution_id)
        if execution.current_phase is not PhaseId.DECISION:
            raise PolicyViolationError("human decisions are accepted only in DECISION")
        current_change_set = self._refresh_changeset(execution)
        execution = self.get_execution(execution_id)
        if current_change_set.digest != change_set_digest:
            raise PolicyViolationError(
                "decision digest does not match the current ChangeSet; prior approval is stale"
            )
        if not execution.gate_evaluation_id:
            # Materialize the gate if the run stopped immediately before evaluation.
            self.continue_execution(execution_id)
            execution = self.get_execution(execution_id)
        if not execution.gate_evaluation_id:
            raise PolicyViolationError("no current gate evaluation exists")
        gate = self.s.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
        if gate.change_set_digest != change_set_digest:
            raise PolicyViolationError("gate was evaluated for a different ChangeSet")
        if decision is DecisionKind.APPROVE and gate.status is not ResultStatus.PASSED:
            raise PolicyViolationError(
                "APPROVE is only valid for a passed automatic gate; use APPROVE_EXCEPTION with rationale"
            )
        if decision is DecisionKind.APPROVE_EXCEPTION and not rationale.strip():
            raise PolicyViolationError("exception approval requires a rationale")
        if acknowledged_risks or change_requests or self.results.active:
            # Risk factors to acknowledge and structured change requests (#52).
            self.results.check_decision(
                execution, decision, change_set_digest, acknowledged_risks, change_requests
            )
        if checked_items or self.ladder.active:
            # The manual checklist (review.manualChecklist, #55).
            self.ladder.check_decision(execution, decision, checked_items)
        contract_digest = self._current_contract_digest(execution)
        decided_at = utc_now()
        expiry = self.s.resolved.project.governance_settings.decision_expiry_hours
        actor = Actor(
            actor_type=ActorType.HUMAN, actor_id=actor_id, display_name=actor_display_name
        )
        record = HumanDecision(
            decision_id=new_id("decision"),
            execution_id=execution_id,
            gate_evaluation_id=gate.gate_evaluation_id,
            actor=actor,
            decision=decision,
            rationale=rationale.strip() or decision.value,
            change_set_digest=change_set_digest,
            configuration_digest=execution.configuration_digest,
            policy_digest=execution.policy_digest,
            acceptance_contract_digest=contract_digest,
            identity_source=identity_source,
            decided_at=decided_at,
            # One expiry, HumanDecision.expires_at: the exception's when one is recorded
            # (review.exceptions, which the exception ledger reuses), otherwise the decision's
            # validity (governance.decisionExpiryHours).
            expires_at=expires_at
            if expires_at is not None
            else (decided_at + timedelta(hours=expiry) if expiry else None),
            acknowledged_risks=tuple(dict.fromkeys(acknowledged_risks)),
            change_requests=change_requests,
            checked_items=tuple(dict.fromkeys(checked_items)),
        )
        self.s.state.put(
            "decision",
            record.decision_id,
            record,
            execution_id=execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution_id,
            "human.decision.recorded",
            record.model_dump(mode="json"),
            actor=actor,
        )
        if self.ladder.active and decision in {
            DecisionKind.APPROVE,
            DecisionKind.APPROVE_EXCEPTION,
        }:
            self.ladder.after_decision(execution, record)
        updated = execution.model_copy(
            update={"human_decision_id": record.decision_id, "updated_at": utc_now()}
        )
        if decision is DecisionKind.REQUEST_CHANGES:
            if change_requests:
                self.results.open_change_requests(execution, record)
            transition = self.state_machine.authorize_correction(PhaseId.DECISION)
            updated = updated.model_copy(
                update={
                    "status": ResultStatus.PENDING,
                    "current_phase": transition.target,
                    "gate_evaluation_id": None,
                    "human_decision_id": None,
                    "updated_at": utc_now(),
                }
            )
            self.s.events.append(
                execution_id,
                "correction.authorized",
                {
                    "decisionId": record.decision_id,
                    "invalidatedPhases": [phase.value for phase in transition.invalidated],
                    "approvedChangeSetDigest": change_set_digest,
                },
                actor=actor,
            )
            if self._feedback_applies(execution_id):
                validations, findings = self._gate_inputs(gate)
                self._record_feedback(
                    updated,
                    PhaseId.DECISION,
                    trigger="CHANGES_REQUESTED",
                    change_set_digest=change_set_digest,
                    gate=FeedbackGate(
                        gate_id="delivery_candidate",
                        gate_evaluation_id=gate.gate_evaluation_id,
                        status=gate.status,
                        reason_codes=gate.reason_codes,
                    ),
                    validations=validations,
                    findings=findings,
                    decision=FeedbackDecision(
                        decision=decision,
                        rationale=head(
                            record.rationale
                            + "".join(
                                f"\n{item.item_id} (blocking, verified by {item.condition}): "
                                f"{item.description}"
                                for item in change_requests
                            ),
                            FEEDBACK_RATIONALE_CHARS,
                        ),
                        actor_id=actor.actor_id,
                    ),
                )
        elif decision is DecisionKind.REJECT:
            updated = updated.model_copy(
                update={
                    "status": ResultStatus.FAILED,
                    "terminal_reason": REJECTED_REASON,
                    "updated_at": utc_now(),
                }
            )
        else:
            updated = updated.model_copy(
                update={"status": ResultStatus.PENDING, "updated_at": utc_now()}
            )
        self._save_execution(updated)
        if decision is DecisionKind.REJECT and self.results.active:
            self.results.stop_line.stop(updated, "rejected")
        self.anchor_chain(execution_id)
        return record

    def clarify(
        self, *, task_id: str, clarification: ClarificationInput, actor: Actor
    ) -> tuple[ClarificationRecord, Task]:
        """Answer the open clarification request of a task and store the revised task.

        The answers and the revision are recorded on the event chain of the run whose INTENT
        asked the questions; ``continue_execution`` then assesses the revised task."""
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a human actor can answer clarification questions")
        require_human_actor(actor.actor_id, "answer clarification questions")
        task = self.get_task(task_id)
        executions = [
            item
            for item in self.s.state.list("execution", Execution, project_id=task.project_id)
            if item.task_id == task_id
        ]
        advanced = [item for item in executions if item.current_phase is not PhaseId.INTENT]
        if advanced:
            raise PolicyViolationError(
                f"task {task_id} has a run past INTENT ({advanced[0].execution_id}); "
                "clarify a new task instead"
            )
        previous_digest = task_digest(task)
        requests = sorted(
            (
                item
                for item in self.s.state.list(
                    "clarification_request", ClarificationRequest, project_id=task.project_id
                )
                if item.task_id == task_id and item.task_digest == previous_digest
            ),
            key=lambda item: item.created_at,
        )
        if not requests:
            raise NotFoundError(
                f"no open clarification request for the current revision of task {task_id}"
            )
        request = requests[-1]
        revision = revise_task(task, request.questions, clarification)
        execution = self.get_execution(request.execution_id)
        by_id = {question.question_id: question for question in request.questions}
        previous_ref = self.s.artifacts.put_json(
            task.model_dump(mode="json"), metadata={"kind": "task-intent"}
        )
        revised_ref = self.s.artifacts.put_json(
            revision.task.model_dump(mode="json"), metadata={"kind": "task-intent"}
        )
        record = ClarificationRecord(
            clarification_id=new_id("clarification"),
            execution_id=execution.execution_id,
            task_id=task_id,
            request_id=request.request_id,
            actor=actor,
            answers=tuple(
                ClarificationAnswer(
                    question_id=question_id,
                    rule_id=by_id[question_id].rule_id,
                    target=by_id[question_id].target,
                    question=by_id[question_id].text,
                    answer=answer.strip(),
                )
                for question_id, answer in sorted(
                    clarification.answers.items(), key=lambda item: int(item[0][2:])
                )
            ),
            replaced_criteria=revision.replaced_criteria,
            added_criteria=revision.added_criteria,
            added_requirements=revision.added_requirements,
            previous_task_digest=previous_digest,
            task_digest=task_digest(revision.task),
            previous_task_ref=previous_ref.uri,
            task_ref=revised_ref.uri,
            contract_answers=dict(clarification.contract or {}),
        )
        self.s.state.put("task", task_id, revision.task, project_id=task.project_id)
        if self._pins_task():
            # A revision through clarify is the one way the task of an open run may change.
            self.s.state.set_flag(f"taskrev:{execution.execution_id}", revised_ref.uri)
        self.s.state.put(
            "clarification",
            record.clarification_id,
            record,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        record_ref = self.s.artifacts.put_json(
            record.model_dump(mode="json", by_alias=True),
            metadata={"kind": "clarification-record", "executionId": execution.execution_id},
        )
        self._record_evidence(
            execution,
            PhaseId.INTENT,
            EvidenceKind.HUMAN_DECISION,
            record_ref,
            f"Clarification: {len(record.answers)} answer(s), task revised",
            supports=(request.request_id, task_id),
        )
        self.s.events.append(
            execution.execution_id,
            "intent.clarified",
            record.model_dump(mode="json"),
            actor=actor,
        )
        if clarification.confirm_contract and self.ladder.active:
            # The person confirms the operational contract with the answers (#55).
            self.ladder.intake.confirm_revision(execution, revision.task, actor)
        if self.results.active:
            # intake.projectSetup (#56): answers to P1 questions become the project's setup.
            self.results.project_setup.store_answers(execution, record)
        self.anchor_chain(execution.execution_id)
        return record, revision.task

    # ----- phases -----------------------------------------------------------------
    def _run_phase(
        self, execution: Execution, handler: Callable[[Execution, PhaseExecution], PhaseOutcome]
    ) -> PhaseOutcome:
        phase_id = execution.current_phase
        previous = [
            phase
            for phase in self.s.state.list(
                "phase", PhaseExecution, execution_id=execution.execution_id
            )
            if phase.phase_id is phase_id
        ]
        attempt = 1 + len(previous)
        definition = self._phase_definition(phase_id)
        if definition is not None:
            failed = sum(1 for item in previous if item.status in FAILED_ATTEMPT_STATUSES)
            if failed >= definition.max_attempts:
                return self._attempts_exhausted(execution, definition, failed)
            self._phase_deadline = time.monotonic() + definition.timeout_seconds
        else:
            self._phase_deadline = None
        phase = PhaseExecution(
            phase_execution_id=new_id("phase"),
            execution_id=execution.execution_id,
            phase_id=phase_id,
            status=ResultStatus.RUNNING,
            attempt=attempt,
            started_at=utc_now(),
        )
        self.s.state.put(
            "phase",
            phase.phase_execution_id,
            phase,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        running = execution.model_copy(
            update={"status": ResultStatus.RUNNING, "updated_at": utc_now()}
        )
        self._save_execution(running)
        self.s.events.append(
            execution.execution_id,
            "phase.started",
            {"phaseId": phase_id, "attempt": attempt},
            phase_execution_id=phase.phase_execution_id,
        )
        try:
            if self.is_cancelled(execution.execution_id):
                outcome = PhaseOutcome(ResultStatus.CANCELLED, "Cancellation requested")
            else:
                with (
                    phase_scope(self._phase_policy(running, phase)),
                    destructive_scope(self._destructive_policy(running)),
                ):
                    outcome = handler(running, phase)
        except (KeyboardInterrupt, SystemExit) as interruption:
            # The harness itself is stopping (Ctrl-C, or SIGTERM under the workspace lease):
            # record the phase as interrupted so that a later run continue recovers it.
            if self._leases_workspace():
                self._mark_interrupted(
                    execution.execution_id,
                    phase,
                    f"The harness was interrupted ({type(interruption).__name__})",
                )
            raise
        except Exception as error:
            outcome = PhaseOutcome(ResultStatus.ERROR, f"{type(error).__name__}: {error}")
            self.s.events.append(
                execution.execution_id,
                "phase.error",
                {"phaseId": phase_id, "errorType": type(error).__name__, "message": str(error)},
                phase_execution_id=phase.phase_execution_id,
            )
        if definition is not None:
            outcome = self._apply_phase_settings(definition, phase, outcome)
        completed = phase.model_copy(
            update={
                "status": outcome.status,
                "finished_at": utc_now(),
                "evidence_refs": outcome.evidence_refs,
                "artifact_refs": outcome.artifact_refs,
                "summary": outcome.summary,
                "output_digest": sha256_json(
                    {
                        "status": outcome.status,
                        "summary": outcome.summary,
                        "evidenceRefs": outcome.evidence_refs,
                    }
                ),
            }
        )
        self.s.state.put(
            "phase",
            completed.phase_execution_id,
            completed,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        completed_payload: dict[str, Any] = {
            "phaseId": phase_id,
            "attempt": attempt,
            "status": outcome.status,
            "summary": outcome.summary,
            "evidenceRefs": list(outcome.evidence_refs),
        }
        if definition is not None:
            # The exit gate the attempt met (PASSED) or did not meet; the condition itself is
            # evaluated by the phase.
            completed_payload["exitGate"] = definition.exit_gate
            completed_payload["exitGateMet"] = outcome.status is ResultStatus.PASSED
            completed_payload["timeoutSeconds"] = definition.timeout_seconds
            completed_payload["maxAttempts"] = definition.max_attempts
        self.s.events.append(
            execution.execution_id,
            "phase.completed",
            completed_payload,
            phase_execution_id=phase.phase_execution_id,
        )
        latest = self.get_execution(execution.execution_id)
        if outcome.status is ResultStatus.PASSED:
            if phase_id is PhaseId.CLOSURE:
                updated = latest.model_copy(
                    update={"status": ResultStatus.PASSED, "updated_at": utc_now()}
                )
            else:
                transition = self.state_machine.advance(phase_id, ResultStatus.PASSED)
                updated = latest.model_copy(
                    update={
                        "status": ResultStatus.PENDING,
                        "current_phase": transition.target,
                        "updated_at": utc_now(),
                    }
                )
        else:
            updated = latest.model_copy(
                update={
                    "status": outcome.status,
                    "terminal_reason": outcome.summary
                    if outcome.status in {ResultStatus.ERROR, ResultStatus.CANCELLED}
                    else None,
                    "updated_at": utc_now(),
                }
            )
        self._save_execution(updated)
        return outcome

    def _phase_intent(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        task = self.run_task(execution)
        task_ref = self.s.artifacts.put_json(
            task.model_dump(mode="json"), metadata={"kind": "task-intent"}
        )
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.INTENT,
            task_ref,
            "Structured intent and acceptance criteria",
            supports=(task.task_id,),
        )
        policy = self.s.resolved.project.criteria_policy
        # A task without acceptance criteria (accepted under enforce, rule C0) never passes
        # INTENT, whatever the policy says now: there is nothing to verify the work against.
        if no_acceptance_criteria(task):
            policy = "enforce"
        questions = assess_intent(task) if policy != "off" else ()
        review_refs: tuple[str, ...] = ()
        if self.results.active and policy != "off":
            # Agent review of ambiguity and completeness, and the check of earlier answers
            # (intake.ambiguityReview, intake.validateAnswers; #37).
            review = self.results.intent.questions(execution, phase, task, questions)
            review_refs = review.evidence_refs
            if review.blocked is not None:
                return PhaseOutcome(
                    ResultStatus.BLOCKED, review.blocked, (evidence.artifact_ref, *review_refs)
                )
            questions = questions + review.questions
        contract: dict[str, Any] | None = None
        ladder_block: str | None = None
        if self.ladder.active:
            # Localisation, the operational contract and the interruption budget (#55).
            intake = self.ladder.intent(execution, phase, task, questions)
            review_refs = (*review_refs, *intake.evidence_refs)
            questions = questions + intake.questions
            contract = intake.contract
            ladder_block = intake.blocked
            if ladder_block is not None and contract is None:
                return PhaseOutcome(
                    ResultStatus.BLOCKED, ladder_block, (evidence.artifact_ref, *review_refs)
                )
        if not questions and ladder_block is None:
            if self.results.active:
                # architecture.mode: agent (#56): options for a new project, chosen by a person.
                advised = self.results.architecture.advise(execution, phase, task)
                if advised is not None:
                    return advised
            return PhaseOutcome(
                ResultStatus.PASSED,
                "Intent is structured and identifiable",
                (evidence.artifact_ref, *review_refs),
            )
        request_evidence = self._request_clarification(
            execution,
            phase,
            task,
            questions,
            "enforce" if policy == "enforce" or ladder_block else "warn",
            contract=contract,
        )
        if ladder_block is not None and not (questions and policy == "enforce"):
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                ladder_block,
                (evidence.artifact_ref, request_evidence.artifact_ref),
            )
        if policy == "enforce":
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"Intent needs clarification: {len(questions)} question(s)",
                (evidence.artifact_ref, request_evidence.artifact_ref),
            )
        for question in questions:
            self._record_clarification_finding(execution, question, request_evidence)
        return PhaseOutcome(
            ResultStatus.PASSED,
            f"Intent is structured and identifiable; {len(questions)} clarification "
            "question(s) recorded as warnings",
            (evidence.artifact_ref, request_evidence.artifact_ref),
        )

    def _request_clarification(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        questions: tuple[ClarificationQuestion, ...],
        policy: Literal["enforce", "warn"],
        *,
        contract: dict[str, Any] | None = None,
    ) -> Evidence:
        request = ClarificationRequest(
            request_id=new_id("clarifyrequest"),
            execution_id=execution.execution_id,
            task_id=task.task_id,
            task_digest=task_digest(task),
            policy=policy,
            questions=questions,
            contract=contract,
        )
        self.s.state.put(
            "clarification_request",
            request.request_id,
            request,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        request_ref = self.s.artifacts.put_json(
            request.model_dump(mode="json", by_alias=True),
            metadata={"kind": "clarification-request", "executionId": execution.execution_id},
        )
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.INTENT,
            request_ref,
            f"Clarification request: {len(questions)} question(s)"
            + (" and the operational contract" if contract is not None else ""),
            supports=tuple(dict.fromkeys(question.target for question in questions))
            or (task.task_id,),
        )
        self.s.events.append(
            execution.execution_id,
            "intent.clarification.requested",
            request.model_dump(mode="json"),
            phase_execution_id=phase.phase_execution_id,
        )
        return evidence

    def _record_clarification_finding(
        self, execution: Execution, question: ClarificationQuestion, evidence: Evidence
    ) -> None:
        finding = Finding(
            finding_id=new_id("finding"),
            execution_id=execution.execution_id,
            validator_id="intake.clarification",
            rule_id=question.rule_id,
            category="intent-clarification",
            severity=FindingSeverity.LOW,
            message=question.text,
            evidence_refs=(evidence.artifact_ref,),
            recommendation=(
                f"Answer {question.question_id} with harness task clarify, or set "
                "intake.criteriaPolicy to enforce to block INTENT until it is answered."
            ),
            provenance=self._provenance(execution),
        )
        self.s.state.put(
            "finding",
            finding.finding_id,
            finding,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "finding.recorded",
            finding.model_dump(mode="json"),
            actor=finding.provenance.actor,
        )

    def _phase_discovery(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        detections = detect_profiles(
            self.s.paths.workspace,
            [
                profile
                for profile in self.s.resolved.profiles
                if profile.profile_id not in BUILTIN_PROFILE_IDS
            ],
        )
        selected = {profile.profile_id for profile in self.s.resolved.profiles}
        if not any(item.profile_id in selected and item.confidence > 0 for item in detections):
            return PhaseOutcome(
                ResultStatus.BLOCKED, "Configured profile was not detected in workspace"
            )
        git = GitAdapter(self.s.paths.workspace).state()
        snapshot = self.snapshots.take(for_storage=True)
        snapshot_ref = self.snapshots.store(
            snapshot,
            metadata={"kind": "workspace-baseline", "executionId": execution.execution_id},
        )
        self.s.state.set_flag(f"baseline:{execution.execution_id}", snapshot_ref.uri)
        discovery = {
            "detections": [item.__dict__ for item in detections],
            "selectedProfiles": sorted(selected),
            "git": git.__dict__,
            "baselineDigest": snapshot.digest,
            "baselineRef": snapshot_ref.uri,
        }
        discovery_ref = self.s.artifacts.put_json(discovery, metadata={"kind": "discovery"})
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.CONFIGURATION,
            discovery_ref,
            "Workspace technology, Git state and baseline snapshot",
        )
        updated = execution.model_copy(
            update={"baseline_revision": git.head or snapshot.digest, "updated_at": utc_now()}
        )
        self._save_execution(updated)
        if self.ladder.active:
            # The environment preflight (environment, #55).
            blocked = self.ladder.discovery(updated, phase, snapshot.digest)
            if blocked is not None:
                return PhaseOutcome(
                    blocked.status,
                    blocked.summary,
                    (evidence.artifact_ref, snapshot_ref.uri, *blocked.evidence_refs),
                )
        if self.results.active:
            # architecture.mode: agent (#56): one cached survey per existing project.
            surveyed = self.results.architecture.survey(updated, phase, self.run_task(updated))
            if surveyed is not None:
                return surveyed
        return PhaseOutcome(
            ResultStatus.PASSED,
            "Workspace and baseline discovered",
            (evidence.artifact_ref, snapshot_ref.uri),
        )

    def _phase_specification(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        task = self.run_task(execution)
        digest = acceptance_contract_digest(task)
        contract = {
            "taskId": task.task_id,
            "requirements": [item.model_dump(mode="json") for item in task.requirements],
            "acceptanceCriteria": [
                item.model_dump(mode="json") for item in task.acceptance_criteria
            ],
            "constraints": list(task.constraints),
            "digest": digest,
        }
        contract_ref = self.s.artifacts.put_json(contract, metadata={"kind": "acceptance-contract"})
        if self._pins_task():
            self.s.state.set_flag(f"contract:{execution.execution_id}", digest)
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.INTENT,
            contract_ref,
            "Versioned acceptance contract",
            supports=tuple(item.criterion_id for item in task.acceptance_criteria),
        )
        if self.results.active:
            # verification.acceptanceTests (#52): independent tests a person approves.
            blocked = self.results.acceptance.propose(execution, phase, task)
            if blocked is not None:
                return blocked
        return PhaseOutcome(
            ResultStatus.PASSED, "Acceptance contract frozen", (evidence.artifact_ref,)
        )

    def _phase_planning(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        task = self.run_task(execution)
        if self.results.active:
            # planning.decomposition (#39): a large task waits for an approved plan.
            blocked = self.results.decomposition.plan(execution, phase, task)
            if blocked is not None:
                return blocked
        if self.ladder.active:
            # The verification plan and the preflight on the baseline (#55).
            blocked = self.ladder.planning(execution, phase, task)
            if blocked is not None:
                return blocked
        selection = MemoryStore(self.s.state).select(
            project_id=execution.project_id,
            task_id=task.task_id,
            execution_id=execution.execution_id,
        )
        manifest = context_manifest(selection.records, selection.exclusions)
        context_ref = self.s.artifacts.put_json(manifest, metadata={"kind": "context-manifest"})
        self.s.state.set_flag(f"context:{execution.execution_id}", context_ref.uri)
        steps = (
            PlanStep(
                step_id=new_id("step"),
                description=f"Apply implementation instruction using mode {task.implementation.mode}",
                capabilities=("filesystem.read", "filesystem.write", "process.execute"),
                expected_evidence=("agent invocation", "ChangeSet"),
            ),
            PlanStep(
                step_id=new_id("step"),
                description="Execute technology-profile validators",
                capabilities=("filesystem.read", "process.execute"),
                expected_evidence=tuple(
                    item.validator_id for item in self.s.resolved.effective_validators
                ),
            ),
            PlanStep(
                step_id=new_id("step"),
                description="Perform independent review and evaluate policy",
                capabilities=("filesystem.read", "approval.request"),
                expected_evidence=("findings", "gate evaluation", "human decision"),
            ),
        )
        plan_steps: tuple[PlanStep, ...] = steps
        if self.results.active:
            plan_steps = (*self.results.decomposition.plan_steps(execution), *steps)
        plan = Plan(
            plan_id=new_id("plan"),
            execution_id=execution.execution_id,
            task_id=task.task_id,
            steps=plan_steps,
            risks=("Repository content is untrusted", "Approval becomes stale after any change"),
            validator_ids=tuple(item.validator_id for item in self.s.resolved.effective_validators)
            + (
                (TRACEABILITY_VALIDATOR_ID,)
                if self.s.resolved.project.requirement_traceability != "off"
                else ()
            )
            + ("review.independent",),
            provenance=self._provenance(execution),
        )
        plan_ref = self.s.artifacts.put_json(
            plan.model_dump(mode="json"), metadata={"kind": "plan"}
        )
        self.s.state.put(
            "plan",
            plan.plan_id,
            plan,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.state.set_flag(f"plan:{execution.execution_id}", plan.plan_id)
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.PLAN,
            plan_ref,
            "Plan, capabilities, validators and risks",
            supports=(task.task_id,),
        )
        return PhaseOutcome(
            ResultStatus.PASSED,
            "Plan and context manifest recorded",
            (evidence.artifact_ref, context_ref.uri),
        )

    def _phase_implementation(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        if self._leases_workspace():
            # The workspace as this attempt found it: an interrupted attempt is undone to it
            # before IMPLEMENTATION runs again, so a change is never implemented twice on top.
            snapshot = self.snapshots.take(for_storage=True)
            ref = self.snapshots.store(
                snapshot,
                metadata={"kind": "implementation-start", "executionId": execution.execution_id},
            )
            self.s.state.set_flag(
                f"implstart:{execution.execution_id}",
                json.dumps({"phaseExecutionId": phase.phase_execution_id, "snapshotRef": ref.uri}),
            )
        task = self.run_task(execution)
        plan_id = self.s.state.get_flag(f"plan:{execution.execution_id}")
        if not plan_id:
            return PhaseOutcome(ResultStatus.BLOCKED, "No approved plan exists")
        plan = self.s.state.get("plan", plan_id, Plan)
        provider_id = self.s.state.get_flag(f"provider:{execution.execution_id}") or "simulated"
        built = self._build_provider(execution, phase, provider_id)
        if isinstance(built, PhaseOutcome):
            return built
        provider, actor, sandbox, sandbox_refs = built
        if self.results.active:
            blocked = self.results.before_agent_call(execution, phase, "implement")
            if blocked is not None:
                return blocked
            # runtime.reproduceFirst: the workspace as the correction attempt found it.
            self.results.corrections.start(execution, phase)
        grants = grants_from_rules(
            execution.execution_id, actor, self.s.resolved.effective_capabilities
        )
        cancellation = CancellationToken(lambda: self.is_cancelled(execution.execution_id))
        runner = self._runner(execution)
        context_uri = self.s.state.get_flag(f"context:{execution.execution_id}")
        memory_context: dict[str, Any] | None = None
        if context_uri:
            manifest = json.loads(self.s.artifacts.get(context_uri))
            memory_context = {"records": manifest["records"], "digest": manifest["digest"]}
        runtime = self.s.resolved.project.runtime
        request_extra: dict[str, Any] | None = None
        if self.results.active:
            task = self.results.implementation_task(execution, task)
            request_extra = self.results.implement_extras(
                execution, phase, task, provider_id, actor, grants
            )
        if self.ladder.active:
            # Locations of the locate call and a person's intake attachments, only when there
            # are any (#55).
            ladder_extra = self.ladder.implement_extras(execution, task)
            if ladder_extra:
                request_extra = {**(request_extra or {}), **ladder_extra}
        context = SimulatedAgentContext(
            execution_id=execution.execution_id,
            workspace=self.s.paths.workspace,
            grants=grants,
            artifact_store=self.s.artifacts,
            process_runner=runner,
            patch_applier=PatchApplier(self.s.paths.workspace),
            provenance=self._provenance(execution).model_copy(update={"actor": actor}),
            timeout_seconds=self._bounded_timeout(runtime.command_timeout_seconds),
            max_output_bytes=runtime.max_output_bytes,
            cancellation=cancellation,
            context_manifest_ref=context_uri,
            memory_context=memory_context,
            feedback=self._pending_feedback(execution, phase),
            request_extra=request_extra,
        )
        guard = (
            ExcludedPathGuard(
                self.s.paths.workspace,
                include_ignored=self.snapshots.settings.git_listing,
            )
            if self._protects_excluded_paths()
            else None
        )
        guard_before = guard.fingerprint() if guard else None
        retries = 0
        try:
            while True:
                self.provenance.before_invocation(execution)
                result = provider.implement(task, plan, context)
                self._save_agent_result(execution, phase, result)
                self.provenance.after_invocation(execution, result)
                cause = (
                    self._transient_cause(result)
                    if isinstance(provider, CommandAgentProvider) and retries < runtime.retry_limit
                    else None
                )
                if cause is None:
                    break
                retries += 1
                # The repeated call runs through the same provider, so under the same sandbox
                # prefix; a write the sandbox denied on the failed call is still reported.
                if sandbox is not None:
                    self._record_denied_writes(execution, result.tool_invocations)
                self._record_provider_retry(execution, phase, result, cause, retries)
                if not self._wait_for_retry(execution.execution_id, runtime.retry_delay_seconds):
                    return PhaseOutcome(ResultStatus.CANCELLED, "Cancellation requested")
        finally:
            if guard is not None and guard_before is not None:
                self._check_excluded_paths(execution, phase, guard, guard_before)
        if self.results.active:
            self.results.after_agent_call(execution, phase, result)
            if result.status is ResultStatus.PASSED:
                self.results.corrections.end(execution, phase, result)
        if (
            result.status is ResultStatus.PASSED
            and runtime.claim_check_enabled
            and self._external_provider(execution.execution_id)
        ):
            self.s.state.set_flag(
                f"claim:{execution.execution_id}",
                json.dumps(
                    {
                        "invocationId": result.invocation.invocation_id,
                        "status": result.status.value,
                        "summary": result.summary,
                        "outputRef": result.output_ref,
                    }
                ),
            )
        if result.status is not ResultStatus.PASSED:
            if sandbox is not None:
                self._record_denied_writes(execution, result.tool_invocations)
            self.provenance.record_self_report(execution, phase, result, None)
            return PhaseOutcome(
                result.status,
                result.summary,
                ((result.output_ref,) if result.output_ref else ()) + sandbox_refs,
            )
        change_set = self._refresh_changeset(execution)
        self.provenance.record_self_report(execution, phase, result, change_set)
        if not change_set.files and not bool(
            self.s.resolved.effective_policies.get("allowEmptyChangeSet", False)
        ):
            return PhaseOutcome(
                ResultStatus.FAILED, "Implementation produced no ChangeSet", sandbox_refs
            )
        return PhaseOutcome(
            ResultStatus.PASSED,
            f"Candidate ChangeSet contains {len(change_set.files)} file(s)",
            (change_set.diff_ref, *sandbox_refs),
        )

    def _build_provider(
        self, execution: Execution, phase: PhaseExecution, provider_id: str
    ) -> tuple[AgentProvider, Actor, SandboxPlan | None, tuple[str, ...]] | PhaseOutcome:
        """The provider of a run (or of one call kind), with the agent sandbox when it is
        enforced; a phase outcome when the provider cannot be started."""
        sandbox: SandboxPlan | None = None
        sandbox_refs: tuple[str, ...] = ()
        if provider_id == "simulated":
            provider: AgentProvider = SimulatedAgentProvider()
            actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.simulated", version="1")
            return provider, actor, sandbox, sandbox_refs
        if provider_id == SESSION_PROVIDER:
            # Embedded mode (#56): the agent session that drives the harness implements.
            def changed() -> list[str]:
                diff = self._compute_owned_diff(execution)
                return [item.path for item in diff.changes]

            actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.session", version="1")
            return SessionAgentProvider(changed), actor, sandbox, sandbox_refs
        provider_config = self.s.resolved.project.agent_providers.get(provider_id)
        if provider_config is None:
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"Provider {provider_id!r} is not configured",
            )
        if self.s.resolved.project.runtime.effective_agent_sandbox == "enforce":
            try:
                sandbox = build_sandbox(
                    self.s.paths.workspace,
                    self.s.resolved.project.runtime.sandbox_write_paths or (),
                    self.sandbox_host,
                    protected=self._protected_paths(),
                    allow_network=self._agent_network_allowed(),
                )
            except SandboxUnavailable as error:
                self._record_sandbox_finding(
                    execution,
                    rule_id="sandbox.unavailable",
                    severity=FindingSeverity.HIGH,
                    message=f"The agent sandbox is enforced but unavailable: {error}",
                    recommendation=(
                        "Run on macOS (sandbox-exec) or on Linux with bubblewrap (bwrap) "
                        "installed, or set runtime.agentSandbox to off to run the agent "
                        "with the user's permissions."
                    ),
                )
                return PhaseOutcome(
                    ResultStatus.BLOCKED,
                    f"Agent sandbox unavailable: {error}; the provider was not started",
                )
            sandbox_refs = (self._record_sandbox_evidence(execution, phase, sandbox),)
        environment = None
        if provider_config.pass_env is not None or provider_config.env is not None:
            environment = provider_environment(provider_config)
            if environment.missing:
                return PhaseOutcome(
                    ResultStatus.BLOCKED,
                    f"Provider {provider_id!r} needs environment variable(s) "
                    f"{', '.join(environment.missing)} (env fromEnv); the provider was not "
                    "started",
                )
            sandbox_refs = (
                *sandbox_refs,
                self._record_provider_environment(execution, phase, provider_id, environment),
            )
        configuration = CommandAgentConfiguration(
            provider_id=provider_id,
            argv_prefix=provider_config.effective_command,
            model=provider_config.model,
            sandbox_prefix=sandbox.prefix if sandbox else (),
            environment=environment,
            self_report=bool(self.s.resolved.project.provenance_settings.self_report),
            extra_args=provider_config.args or (),
        )
        provider = (
            native_provider(provider_config.kind, configuration)
            if provider_config.native
            else CommandAgentProvider(configuration)
        )
        actor = Actor(actor_type=ActorType.AGENT, actor_id=f"agent.{provider_id}", version="1")
        return provider, actor, sandbox, sandbox_refs

    # ----- capabilities per phase (#4) ----------------------------------------------------
    def _phase_policy(self, execution: Execution, phase: PhaseExecution) -> PhasePolicy | None:
        """Under ``governance.phaseCapabilities``: what grants made while this phase runs may
        carry, recorded as evidence of the phase attempt."""
        resolved = self.s.resolved
        if not resolved.project.governance_settings.phase_capabilities:
            return None
        definition = next(
            (item for item in resolved.workflow.phases if item.phase_id is phase.phase_id), None
        )
        launch = {
            f"agent.{provider_id}": (" ".join(config.effective_command),)
            for provider_id, config in resolved.project.agent_providers.items()
            if config.effective_command
        }
        policy = PhasePolicy(
            phase=phase.phase_id.value,
            allowed=frozenset(definition.allowed_capabilities if definition else ()),
            launch=launch,
        )
        provider = self.s.state.get_flag(f"provider:{execution.execution_id}")
        description = policy.describe(
            resolved.effective_capabilities, f"agent.{provider}" if provider else None
        )
        ref = self.s.artifacts.put_json(
            description,
            metadata={"kind": "phase-capabilities", "executionId": execution.execution_id},
        )
        self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.OTHER,
            ref,
            f"Capabilities of {phase.phase_id.value}: {', '.join(sorted(policy.allowed)) or 'none'}",
        )
        self.s.events.append(
            execution.execution_id,
            "capabilities.resolved",
            {**description, "evidenceRef": ref.uri},
            phase_execution_id=phase.phase_execution_id,
        )
        return policy

    def _destructive_policy(self, execution: Execution) -> DestructivePolicy | None:
        """Under ``governance.applyRepositoryPolicies`` with ``destructiveActionsDefault: deny``
        (#5): destructive commands are refused unless granted, each attempt a finding."""
        if not repository_policies(self.s.resolved).deny_destructive:
            return None

        def denied(actor: Actor, argv: Sequence[str], reason: str) -> None:
            self.results.record_finding(
                execution,
                validator_id="harness.capabilities",
                rule_id="capabilities.destructive-denied",
                category="security",
                severity=FindingSeverity.HIGH,
                message=(
                    f"{actor.actor_id} tried a destructive command ({reason}): "
                    f"{' '.join(argv)[:200]}"
                ),
                recommendation=(
                    "Grant process.destructive for this command in project.yaml if it is "
                    "intended (policies.destructiveActionsDefault: deny)."
                ),
            )

        return DestructivePolicy(self.s.paths.workspace, denied)

    # ----- declared settings (#51) -------------------------------------------------------
    def _phase_definition(self, phase_id: PhaseId) -> WorkflowPhaseDefinition | None:
        """The workflow definition of a phase when ``governance.applyWorkflowSettings`` is on."""
        if not self.s.resolved.project.governance_settings.apply_workflow_settings:
            return None
        return next(
            (item for item in self.s.resolved.workflow.phases if item.phase_id is phase_id), None
        )

    def _attempts_exhausted(
        self, execution: Execution, definition: WorkflowPhaseDefinition, failed: int
    ) -> PhaseOutcome:
        reason = (
            f"{definition.phase_id} is not started again: {failed} failed attempt(s) reached its "
            f"maxAttempts ({definition.max_attempts})"
        )
        self.s.events.append(
            execution.execution_id,
            "phase.attempts.exhausted",
            {
                "phaseId": definition.phase_id,
                "failedAttempts": failed,
                "maxAttempts": definition.max_attempts,
            },
        )
        latest = self.get_execution(execution.execution_id)
        self._save_execution(
            latest.model_copy(
                update={
                    "status": ResultStatus.BLOCKED,
                    "terminal_reason": reason,
                    "updated_at": utc_now(),
                }
            )
        )
        return PhaseOutcome(ResultStatus.BLOCKED, reason)

    def _apply_phase_settings(
        self, definition: WorkflowPhaseDefinition, phase: PhaseExecution, outcome: PhaseOutcome
    ) -> PhaseOutcome:
        """An attempt that outlived ``timeoutSeconds`` is ``TIMED_OUT``."""
        started = phase.started_at or utc_now()
        elapsed = (utc_now() - started).total_seconds()
        if outcome.status is ResultStatus.PASSED and elapsed > definition.timeout_seconds:
            return PhaseOutcome(
                ResultStatus.TIMED_OUT,
                f"{definition.phase_id} took {elapsed:.0f} s, more than its timeoutSeconds "
                f"({definition.timeout_seconds})",
                outcome.evidence_refs,
                outcome.artifact_refs,
            )
        return outcome

    def _remaining_budget(self) -> float | None:
        deadline = self._phase_deadline
        if deadline is None:
            return None
        return max(1.0, deadline - time.monotonic())

    def _bounded_timeout(self, seconds: int) -> int:
        remaining = self._remaining_budget()
        return seconds if remaining is None else max(1, min(seconds, int(remaining)))

    def _bounded_definition(self, definition: ValidatorDefinition) -> ValidatorDefinition:
        if self._remaining_budget() is None:
            return definition
        own = definition.timeout_seconds or DEFAULT_VALIDATOR_TIMEOUT_SECONDS
        return definition.model_copy(update={"timeout_seconds": self._bounded_timeout(own)})

    def _profile_policies_apply(self) -> bool:
        return bool(self.s.resolved.project.governance_settings.apply_profile_policies)

    def _unavailable_status(self, key: str) -> ResultStatus | None:
        """``missingTestCommand`` or ``missingTestScript`` as the status of an unavailable
        mandatory validator (``governance.applyProfilePolicies``); ``None`` keeps BLOCKED."""
        if not self._profile_policies_apply():
            return None
        value = self.s.resolved.effective_policies.get(key)
        return ResultStatus(str(value)) if value is not None else None

    def _coverage_minimum(self) -> float | None:
        if not self._profile_policies_apply():
            return None
        return coverage_minimum(self.s.resolved.effective_policies)

    def _verify_coverage(
        self, execution: Execution, change_set: ChangeSet, minimum: float
    ) -> ValidatorOutput:
        validator = CoverageValidator(minimum)
        actor = Actor(
            actor_type=ActorType.TOOL, actor_id=f"validator.{validator.validator_id}", version="1"
        )
        data_dir = self.s.paths.harness_dir / "coverage"
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output = validator.execute(
            ValidationContext(
                execution_id=execution.execution_id,
                workspace=self.s.paths.workspace,
                task=self.run_task(execution),
                change_set=change_set,
                definition=self._bounded_definition(validator.definition()),
                grants=grants_from_rules(
                    execution.execution_id, actor, self.s.resolved.effective_capabilities
                ),
                artifact_store=self.s.artifacts,
                process_runner=self._runner(execution),
                provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                cancellation=CancellationToken(lambda: self.is_cancelled(execution.execution_id)),
                max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
                missing_command_status=self._unavailable_status("missingTestCommand"),
                missing_script_status=self._unavailable_status("missingTestScript"),
            ),
            data_file=data_dir / f"{execution.execution_id}.coverage",
        )
        self._save_validator_output(execution, output)
        return output

    def _agent_network_allowed(self) -> bool:
        """``runtime.allowNetwork`` reaches the agent sandbox under
        ``governance.applyNetworkPolicy``; otherwise network access stays allowed (1.0.0)."""
        if not self.s.resolved.project.governance_settings.apply_network_policy:
            return True
        return self.s.resolved.project.runtime.allow_network

    # ----- interruption and recovery (governance.workspaceLease) ----------------------
    def _leases_workspace(self) -> bool:
        return bool(self.s.resolved.project.governance_settings.workspace_lease)

    def _runner(self, execution: Execution) -> SafeProcessRunner:
        if not self._leases_workspace():
            return SafeProcessRunner(self.s.paths.workspace)
        return SafeProcessRunner(
            self.s.paths.workspace,
            observer=_ProcessLedger(self.s.state, execution.execution_id),
            terminate_on_exit=True,
        )

    def _mark_interrupted(
        self, execution_id: str, phase: PhaseExecution, reason: str
    ) -> PhaseExecution:
        interrupted = phase.model_copy(
            update={
                "status": ResultStatus.INTERRUPTED,
                "finished_at": utc_now(),
                "summary": reason,
            }
        )
        latest = self.get_execution(execution_id)
        self.s.state.put(
            "phase",
            interrupted.phase_execution_id,
            interrupted,
            execution_id=execution_id,
            project_id=latest.project_id,
        )
        self.s.events.append(
            execution_id,
            "phase.completed",
            {
                "phaseId": phase.phase_id,
                "attempt": phase.attempt,
                "status": ResultStatus.INTERRUPTED,
                "summary": reason,
                "evidenceRefs": [],
            },
            phase_execution_id=phase.phase_execution_id,
        )
        self._save_execution(
            latest.model_copy(
                update={
                    "status": ResultStatus.INTERRUPTED,
                    "terminal_reason": reason,
                    "updated_at": utc_now(),
                }
            )
        )
        return interrupted

    def recover_interrupted(self, execution_id: str) -> Execution | None:
        """Recover a run a harness left behind before its phases run again (under the
        workspace lease, no other harness process is executing them):

        * a phase still ``RUNNING`` is marked ``INTERRUPTED``;
        * the process groups the killed harness started and that still run are terminated;
        * the workspace is restored to the state an interrupted IMPLEMENTATION attempt found,
          so the change is not implemented twice on top of itself.

        Returns the run when it cannot be recovered safely (it is then ``BLOCKED``), ``None``
        when the phases may run."""
        orphans = [
            item
            for item in self.s.state.list("phase", PhaseExecution, execution_id=execution_id)
            if item.status is ResultStatus.RUNNING
        ]
        terminated = self._terminate_orphans(execution_id)
        for phase in orphans:
            self._mark_interrupted(
                execution_id, phase, "The harness stopped while the phase was running"
            )
        interrupted = [
            item
            for item in self.s.state.list("phase", PhaseExecution, execution_id=execution_id)
            if item.status is ResultStatus.INTERRUPTED
            and not self.s.state.get_flag(f"recovered:{item.phase_execution_id}")
        ]
        if not interrupted and not terminated:
            return None
        restored: list[str] = []
        unrestorable: list[str] = []
        for phase in interrupted:
            if phase.phase_id is PhaseId.IMPLEMENTATION:
                done, failed = self._restore_implementation_start(execution_id, phase)
                restored.extend(done)
                unrestorable.extend(failed)
            self.s.state.set_flag(f"recovered:{phase.phase_execution_id}", "1")
        self.s.events.append(
            execution_id,
            "run.recovered",
            {
                "interruptedPhases": [
                    {"phaseExecutionId": item.phase_execution_id, "phaseId": item.phase_id}
                    for item in interrupted
                ],
                "terminatedProcessGroups": terminated,
                "restoredPaths": restored,
                "unrestorablePaths": unrestorable,
            },
        )
        latest = self.get_execution(execution_id)
        if unrestorable:
            blocked = latest.model_copy(
                update={
                    "status": ResultStatus.BLOCKED,
                    "terminal_reason": (
                        "An interrupted IMPLEMENTATION left files the harness cannot restore: "
                        + ", ".join(unrestorable[:10])
                    ),
                    "updated_at": utc_now(),
                }
            )
            self._save_execution(blocked)
            return blocked
        if latest.status in {ResultStatus.INTERRUPTED, ResultStatus.RUNNING}:
            self._save_execution(
                latest.model_copy(
                    update={
                        "status": ResultStatus.PENDING,
                        "terminal_reason": None,
                        "updated_at": utc_now(),
                    }
                )
            )
        return None

    def _terminate_orphans(self, execution_id: str) -> list[int]:
        key = f"process:{execution_id}"
        recorded = json.loads(self.s.state.get_flag(key) or "{}")
        host = socket.gethostname()
        terminated = [
            int(entry["pgid"])
            for entry in recorded.values()
            if entry.get("host") == host and terminate_process_group(int(entry["pgid"]))
        ]
        if recorded:
            self.s.state.set_flag(key, "{}")
        return terminated

    def _restore_implementation_start(
        self, execution_id: str, phase: PhaseExecution
    ) -> tuple[list[str], list[str]]:
        raw = self.s.state.get_flag(f"implstart:{execution_id}")
        if not raw:
            return [], []
        start = json.loads(raw)
        if start.get("phaseExecutionId") != phase.phase_execution_id:
            return [], []
        stored = self.snapshots.load(start["snapshotRef"])
        before = stored.snapshot
        diff = self.snapshots.diff(stored, self.snapshots.take())
        restored: list[str] = []
        unrestorable: list[str] = []
        for change in diff.changes:
            target = contained_path(self.s.paths.workspace, Path(change.path))
            previous = before.files.get(change.path)
            content = self.snapshots.content(stored, change.path) if previous else None
            if previous is None:
                target.unlink(missing_ok=True)
                restored.append(change.path)
            elif content is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                restored.append(change.path)
            else:
                unrestorable.append(change.path)
        return restored, unrestorable

    # ----- excluded paths (governance.protectExcludedPaths) ---------------------------
    def _protects_excluded_paths(self) -> bool:
        return bool(self.s.resolved.project.governance_settings.protect_excluded_paths)

    def _protected_paths(self) -> tuple[Path, ...]:
        """Paths the agent sandbox keeps read-only: the harness state and Git of the workspace
        (``governance.protectExcludedPaths``) and the run registry when it lives outside the
        workspace (``runtime.stateDir``, #55)."""
        external = (self.s.paths.state_root,) if self.s.paths.state_root is not None else ()
        if not self._protects_excluded_paths():
            return external
        return (self.s.paths.harness_dir, self.s.paths.workspace / ".git", *external)

    def _check_excluded_paths(
        self,
        execution: Execution,
        phase: PhaseExecution,
        guard: ExcludedPathGuard,
        before: dict[str, str],
    ) -> None:
        """Compare the fingerprints of what the ChangeSet excludes before and after the agent
        ran. The comparison is IMPLEMENTATION evidence; a change is a CRITICAL finding that the
        gate of every later ChangeSet of the run receives."""
        after = guard.fingerprint()
        changes = guard.compare(before, after)
        record = {
            "guardedDirectories": sorted(guard.guarded),
            "ignoredPatterns": list(IGNORED_PATTERNS),
            "before": {"files": len(before), "digest": sha256_json(before)},
            "after": {"files": len(after), "digest": sha256_json(after)},
            "changes": [{"path": item.path, "status": item.status} for item in changes],
        }
        ref = self.s.artifacts.put_json(
            record,
            metadata={"kind": "excluded-path-fingerprint", "executionId": execution.execution_id},
        )
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.CONFIGURATION,
            ref,
            f"Paths outside the ChangeSet: {len(after)} fingerprinted, {len(changes)} changed",
        )
        if not changes:
            return
        shown = ", ".join(f"{item.path} ({item.status})" for item in changes[:10])
        more = f" and {len(changes) - 10} more" if len(changes) > 10 else ""
        finding = Finding(
            finding_id=new_id("finding"),
            execution_id=execution.execution_id,
            validator_id=WORKSPACE_GUARD_ID,
            rule_id=OUT_OF_CHANGESET_RULE,
            category="workspace-integrity",
            severity=FindingSeverity.CRITICAL,
            message=(
                f"The agent changed {len(changes)} path(s) the ChangeSet does not show: "
                f"{shown}{more}"
            ),
            location=FindingLocation(path=changes[0].path),
            evidence_refs=(evidence.artifact_ref,),
            recommendation=(
                "Inspect these paths (a Git hook runs on the next commit; a changed dependency "
                "changes what the tests run) and restore them before deciding; only approve an "
                "exception for a change you made on purpose."
            ),
            provenance=self._provenance(execution),
        )
        self.s.state.put(
            "finding",
            finding.finding_id,
            finding,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "finding.recorded",
            finding.model_dump(mode="json"),
            actor=finding.provenance.actor,
        )
        key = f"guard:{execution.execution_id}"
        recorded = json.loads(self.s.state.get_flag(key) or "[]")
        self.s.state.set_flag(key, json.dumps([*recorded, finding.finding_id]))

    def _workspace_guard_validation(self, execution: Execution, digest: str) -> None:
        """A failed mandatory validation for the ChangeSet the gate evaluates, carrying every
        out-of-ChangeSet write of the run, so the gate fails until a person decides."""
        finding_ids = tuple(
            json.loads(self.s.state.get_flag(f"guard:{execution.execution_id}") or "[]")
        )
        if not finding_ids:
            return
        if any(
            item.validator_id == WORKSPACE_GUARD_ID and item.finding_ids == finding_ids
            for item in self._latest_validations(execution.execution_id, digest)
        ):
            return
        findings = [self.s.state.get("finding", item, Finding) for item in finding_ids]
        now = utc_now()
        result = ValidationResult(
            validation_result_id=new_id("validation"),
            execution_id=execution.execution_id,
            validator_id=WORKSPACE_GUARD_ID,
            change_set_digest=digest,
            status=ResultStatus.FAILED,
            kind=ValidationKind.POLICY_VIOLATION,
            mandatory=True,
            summary=f"{len(finding_ids)} agent invocation(s) wrote outside the ChangeSet",
            finding_ids=finding_ids,
            evidence_refs=tuple(ref for item in findings for ref in item.evidence_refs),
            started_at=now,
            finished_at=now,
            provenance=self._provenance(execution),
        )
        self.s.state.put(
            "validation",
            result.validation_result_id,
            result,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "validation.completed",
            result.model_dump(mode="json"),
            actor=result.provenance.actor,
        )

    def _record_sandbox_evidence(
        self, execution: Execution, phase: PhaseExecution, sandbox: SandboxPlan
    ) -> str:
        """Record the confinement the provider runs under: mechanism, profile and its digest,
        and the resolved write paths (IMPLEMENTATION evidence and an ``agent.sandbox.applied``
        event)."""
        record = sandbox.evidence()
        ref = self.s.artifacts.put_json(
            record, metadata={"kind": "agent-sandbox", "executionId": execution.execution_id}
        )
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.CONFIGURATION,
            ref,
            f"Agent sandbox: {sandbox.mechanism}, {len(sandbox.allowed_paths)} writable path(s)",
        )
        self.s.events.append(
            execution.execution_id,
            "agent.sandbox.applied",
            {
                "mechanism": sandbox.mechanism,
                "profileDigest": sandbox.profile_digest,
                "allowedPaths": [item.path for item in sandbox.allowed_paths],
                "evidenceRef": evidence.artifact_ref,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return evidence.artifact_ref

    def _record_provider_environment(
        self,
        execution: Execution,
        phase: PhaseExecution,
        provider_id: str,
        environment: ProviderEnvironment,
    ) -> str:
        """The names (never the values) of the variables the provider receives."""
        record = {"provider": provider_id, **environment.evidence()}
        ref = self.s.artifacts.put_json(
            record, metadata={"kind": "provider-environment", "executionId": execution.execution_id}
        )
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.CONFIGURATION,
            ref,
            f"Provider environment: {len(environment.passed)} passed, "
            f"{len(environment.values)} set",
        )
        self.s.events.append(
            execution.execution_id,
            "agent.environment.applied",
            record,
            phase_execution_id=phase.phase_execution_id,
        )
        return evidence.artifact_ref

    def _record_denied_writes(
        self, execution: Execution, tools: tuple[ToolInvocation, ...]
    ) -> None:
        """A provider that failed under the sandbox and reports a denied write on standard
        error gets a ``sandbox.write-denied`` finding naming the path when it can be read."""
        for tool in tools:
            if not tool.stderr_ref:
                continue
            found, paths = denied_writes(self.s.artifacts.get(tool.stderr_ref))
            if not found:
                continue
            named = ", ".join(paths) if paths else "a path not named in the output"
            self._record_sandbox_finding(
                execution,
                rule_id="sandbox.write-denied",
                severity=FindingSeverity.MEDIUM,
                message=f"The agent sandbox denied a write: {named}",
                recommendation=(
                    "If the agent needs this path, add it to runtime.sandboxWritePaths; "
                    "otherwise keep the agent's changes inside the workspace."
                ),
                path=paths[0] if paths else None,
                evidence_refs=(tool.stderr_ref,),
            )

    def _record_sandbox_finding(
        self,
        execution: Execution,
        *,
        rule_id: str,
        severity: FindingSeverity,
        message: str,
        recommendation: str,
        path: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        finding = Finding(
            finding_id=new_id("finding"),
            execution_id=execution.execution_id,
            validator_id="harness.sandbox",
            rule_id=rule_id,
            category="agent-sandbox",
            severity=severity,
            message=message,
            location=FindingLocation(path=path) if path else None,
            evidence_refs=evidence_refs,
            recommendation=recommendation,
            provenance=self._provenance(execution),
        )
        self.s.state.put(
            "finding",
            finding.finding_id,
            finding,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "finding.recorded",
            finding.model_dump(mode="json"),
            actor=finding.provenance.actor,
        )

    def _phase_verification(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        change_set = self._refresh_changeset(execution)
        if not change_set.files:
            return PhaseOutcome(ResultStatus.FAILED, "Current ChangeSet is empty")
        outputs = []
        for definition in self.s.resolved.effective_validators:
            actor = Actor(
                actor_type=ActorType.TOOL,
                actor_id=f"validator.{definition.validator_id}",
                version="1",
            )
            grants = grants_from_rules(
                execution.execution_id, actor, self.s.resolved.effective_capabilities
            )
            output = self.validators.create(definition.validator_id).execute(
                ValidationContext(
                    execution_id=execution.execution_id,
                    workspace=self.s.paths.workspace,
                    task=self.run_task(execution),
                    change_set=change_set,
                    definition=self._bounded_definition(definition),
                    grants=grants,
                    artifact_store=self.s.artifacts,
                    process_runner=self._runner(execution),
                    provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                    cancellation=CancellationToken(
                        lambda: self.is_cancelled(execution.execution_id)
                    ),
                    max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
                    missing_command_status=self._unavailable_status("missingTestCommand"),
                    missing_script_status=self._unavailable_status("missingTestScript"),
                    parse_output=self.s.resolved.project.output_parsers_enabled,
                )
            )
            self._save_validator_output(execution, output)
            outputs.append(output)
        coverage = self._coverage_minimum()
        if coverage is not None:
            outputs.append(self._verify_coverage(execution, change_set, coverage))
        policy = self.s.resolved.project.requirement_traceability
        if policy != "off":
            outputs.append(
                self._verify_requirement_traceability(execution, phase, change_set, policy)
            )
        if self.results.active:
            # Deterministic checks of the agent-results settings, then the comparison of
            # failing validators with the baseline (verification.differential, #7).
            outputs.extend(self.results.verification.run(execution, phase, change_set))
            outputs = self.results.after_verification(execution, phase, change_set, outputs)
        if self.ladder.active:
            # Probes, light mutation and the certification of the ChangeSet (#55).
            outputs.extend(self.ladder.verification(execution, phase, change_set, outputs))
        mandatory_non_passed = [
            output.result
            for output in outputs
            if output.result.mandatory and output.result.status is not ResultStatus.PASSED
        ]
        evidence = tuple(ref for output in outputs for ref in output.result.evidence_refs)
        if mandatory_non_passed:
            status = self._worst_status([item.status for item in mandatory_non_passed])
            return PhaseOutcome(
                status,
                f"{len(mandatory_non_passed)} mandatory validator(s) did not pass",
                evidence,
            )
        return PhaseOutcome(ResultStatus.PASSED, f"Executed {len(outputs)} validator(s)", evidence)

    def _verify_requirement_traceability(
        self,
        execution: Execution,
        phase: PhaseExecution,
        change_set: ChangeSet,
        policy: Literal["enforce", "warn"],
    ) -> TraceabilityOutput:
        """Relate the task's identified requirements to the tests of the workspace; the mapping
        is recorded as VERIFICATION evidence and each untraced requirement as a finding."""
        task = self.run_task(execution)
        if self.results.active:
            # Under decomposition, the requirements of the sub-tasks implemented so far.
            task = self.results.decomposition.verification_task(execution, task)
        validator = RequirementTraceabilityValidator(
            policy, (profile.technology for profile in self.s.resolved.profiles)
        )
        actor = Actor(
            actor_type=ActorType.TOOL, actor_id=f"validator.{validator.validator_id}", version="1"
        )
        output = validator.execute(
            ValidationContext(
                execution_id=execution.execution_id,
                workspace=self.s.paths.workspace,
                task=task,
                change_set=change_set,
                definition=ValidatorDefinition(
                    id=validator.validator_id, mandatory=policy == "enforce"
                ),
                grants=grants_from_rules(
                    execution.execution_id, actor, self.s.resolved.effective_capabilities
                ),
                artifact_store=self.s.artifacts,
                process_runner=self._runner(execution),
                provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                cancellation=CancellationToken(lambda: self.is_cancelled(execution.execution_id)),
                max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
            )
        )
        if output.report is not None and output.report_ref is not None:
            self._record_evidence(
                execution,
                phase.phase_id,
                EvidenceKind.TEST_REPORT,
                output.report_ref,
                f"Requirement traceability: {output.result.summary}",
                supports=tuple(item.requirement_id for item in output.report.requirements),
            )
        self._save_validator_output(execution, output)
        return output

    def _phase_independent_review(
        self, execution: Execution, phase: PhaseExecution
    ) -> PhaseOutcome:
        change_set = self.current_change_set(execution.execution_id)
        actor = Actor(
            actor_type=ActorType.TOOL, actor_id="validator.independent-review", version="1"
        )
        definition = ValidatorDefinition(id="review.independent", mandatory=True)
        grants = grants_from_rules(
            execution.execution_id, actor, self.s.resolved.effective_capabilities
        )
        skip = (
            frozenset({"review.possible-secret"})
            if self.results.active and self.results.secrets_in_context
            else frozenset()
        )
        output = IndependentReviewValidator(skip).execute(
            ValidationContext(
                execution_id=execution.execution_id,
                workspace=self.s.paths.workspace,
                task=self.run_task(execution),
                change_set=change_set,
                definition=definition,
                grants=grants,
                artifact_store=self.s.artifacts,
                process_runner=self._runner(execution),
                provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                cancellation=CancellationToken(lambda: self.is_cancelled(execution.execution_id)),
                max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
                raw_diff=self._compute_owned_diff(execution).unified_diff.decode(
                    "utf-8", "replace"
                ),
            )
        )
        self._save_validator_output(execution, output)
        if self.results.active:
            # review.agentReview (#38): a second reviewer after the deterministic checks.
            review = self.results.agent_review.run(execution, phase, change_set)
            if review.blocking and self.results.review_correction_available(execution):
                return PhaseOutcome(
                    ResultStatus.FAILED,
                    f"The agent review found {len(review.blocking)} blocking finding(s)",
                    output.result.evidence_refs,
                )
        return PhaseOutcome(
            ResultStatus.PASSED,
            output.result.summary,
            output.result.evidence_refs,
        )

    def _phase_decision(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        previous_digest = execution.change_set_digest
        change_set = self._refresh_changeset(execution)
        execution = self.get_execution(execution.execution_id)
        # A later change to an owned path invalidates previous gate/approval.
        if previous_digest and previous_digest != change_set.digest:
            execution = execution.model_copy(
                update={
                    "gate_evaluation_id": None,
                    "human_decision_id": None,
                    "updated_at": utc_now(),
                }
            )
            self._save_execution(execution)
            self.s.events.append(
                execution.execution_id,
                "approval.invalidated",
                {
                    "previousDigest": previous_digest,
                    "currentDigest": change_set.digest,
                },
            )
        current_decision = (
            self.s.state.get("decision", execution.human_decision_id, HumanDecision)
            if execution.human_decision_id
            else None
        )
        gate = self._current_or_evaluate_gate(execution, change_set)
        self.provenance.attribute(execution, change_set, PhaseId.DECISION)
        execution = self.get_execution(execution.execution_id)
        if current_decision:
            if current_decision.change_set_digest != change_set.digest:
                return PhaseOutcome(
                    ResultStatus.BLOCKED, "Human decision is stale after ChangeSet modification"
                )
            if current_decision.acceptance_contract_digest is not None:
                try:
                    contract = self._current_contract_digest(execution)
                except PolicyViolationError as error:
                    return PhaseOutcome(ResultStatus.BLOCKED, str(error))
                if contract != current_decision.acceptance_contract_digest:
                    return PhaseOutcome(
                        ResultStatus.BLOCKED,
                        "Human decision is stale: it is bound to another acceptance contract",
                    )
            if (
                current_decision.expires_at is not None
                and current_decision.expires_at <= utc_now()
                and current_decision.decision is not DecisionKind.REJECT
            ):
                # The expiry of an exception (review.exceptions) or of any decision
                # (governance.decisionExpiryHours): a new decision is required.
                kind = (
                    "Exception"
                    if current_decision.decision is DecisionKind.APPROVE_EXCEPTION
                    else "Human decision"
                )
                return PhaseOutcome(
                    ResultStatus.BLOCKED,
                    f"{kind} {current_decision.decision_id} expired at "
                    f"{current_decision.expires_at.isoformat()}; a new decision is required",
                )
            if current_decision.decision in {DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION}:
                return PhaseOutcome(
                    ResultStatus.PASSED,
                    f"Human decision {current_decision.decision} is bound to the current ChangeSet",
                    (f"record://decision/{current_decision.decision_id}",),
                )
            if current_decision.decision is DecisionKind.REJECT:
                return PhaseOutcome(ResultStatus.FAILED, "Execution rejected by human decision")
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"Human decision required for gate {gate.gate_evaluation_id} and digest {change_set.digest}",
            (f"record://gate/{gate.gate_evaluation_id}",),
        )

    def _phase_closure(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        execution = self.get_execution(execution.execution_id)
        if not execution.human_decision_id or not execution.change_set_digest:
            return PhaseOutcome(ResultStatus.BLOCKED, "Closure requires a current human decision")
        decision = self.s.state.get("decision", execution.human_decision_id, HumanDecision)
        if not self.state_machine.approval_is_current(
            decision.change_set_digest, execution.change_set_digest
        ):
            return PhaseOutcome(ResultStatus.BLOCKED, "Approval does not match current ChangeSet")
        delivered = self._deliver(execution, phase, decision)
        if delivered is not None:
            return delivered
        self.s.events.verify_chain(execution.execution_id)
        trace = self.s.events.export_jsonl(execution.execution_id)
        trace_ref = self.s.artifacts.put(
            trace,
            media_type="application/x-ndjson",
            metadata={"kind": "execution-trace", "executionId": execution.execution_id},
        )
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.TRACE,
            trace_ref,
            "Immutable event-chain trace",
        )
        # Emit closure before computing the retrospective so metrics include the complete primary workflow.
        self.s.events.append(
            execution.execution_id,
            "run.closed",
            {"status": "PASSED", "changeSetDigest": execution.change_set_digest},
        )
        metrics = MetricsProjector(self.s.state).project(
            execution.execution_id, self.s.events.list(execution.execution_id)
        )
        metrics_ref = self.s.artifacts.put_json(
            {key: value.as_dict() for key, value in metrics.items()},
            metadata={"kind": "metrics", "executionId": execution.execution_id},
        )
        retrospective = self.retrospective_engine.generate(
            execution_id=execution.execution_id,
            metrics=metrics,
            evidence_refs=(trace_ref.uri, metrics_ref.uri),
            provenance=self._provenance(execution),
            analysis=self.cause_analysis(execution),
            trigger="CLOSED" if self.s.resolved.project.causal_retrospective else None,
        )
        self.s.state.put(
            "retrospective",
            retrospective.retrospective_id,
            retrospective,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        retrospective_ref = self.s.artifacts.put_json(
            retrospective.model_dump(mode="json"),
            metadata={"kind": "retrospective", "executionId": execution.execution_id},
        )
        self._record_evidence(
            execution,
            PhaseId.RETROSPECTIVE,
            EvidenceKind.RETROSPECTIVE,
            retrospective_ref,
            "Evidence-based recommendations requiring human review",
        )
        self.s.events.append(
            execution.execution_id,
            "retrospective.generated",
            {
                "retrospectiveId": retrospective.retrospective_id,
                "recommendationCount": len(retrospective.recommendations),
                "appliedAutomatically": False,
            },
        )
        return PhaseOutcome(
            ResultStatus.PASSED,
            "Execution closed with verified trace and non-mutating retrospective",
            (evidence.artifact_ref, metrics_ref.uri, retrospective_ref.uri),
        )

    def _deliver(
        self, execution: Execution, phase: PhaseExecution, decision: HumanDecision
    ) -> PhaseOutcome | None:
        """``delivery.closureCommit``: write the approved ChangeSet as a commit with trailers.
        ``None`` lets CLOSURE go on; an outcome stops it."""
        delivery = self.s.resolved.project.delivery_settings
        change_set = self.current_change_set(execution.execution_id)
        if delivery.mode == "off":
            if self.ladder.active:
                # Stage the run's files when nothing is committed (delivery.stage, #55).
                skipped = ClosureCommit(mode="off", status="SKIPPED", reason="closureCommit off")
                return self.ladder.delivery.deliver(execution, phase, decision, skipped, change_set)
            return None
        task = self.run_task(execution)
        if isolation_marker(self.s.paths.workspace) is not None:
            # An isolated run (#55) commits on its own worktree's branch.
            delivery = delivery.model_copy(update={"closure_commit": "head"})
        elif self.ladder.active and task.contract is not None and task.contract.branch:
            # The branch the operational contract names (#55).
            delivery = delivery.model_copy(update={"branch": task.contract.branch})
        try:
            commit = create_closure_commit(
                self.s.paths.workspace,
                delivery,
                execution_id=execution.execution_id,
                task=self.run_task(execution),
                change_set=change_set,
                decision=decision,
            )
        except VcsError as error:
            self.s.events.append(
                execution.execution_id,
                "delivery.commit.failed",
                {"mode": delivery.mode, "reason": str(error)},
                phase_execution_id=phase.phase_execution_id,
            )
            return PhaseOutcome(ResultStatus.BLOCKED, f"Closure commit not created: {error}")
        record = commit.as_dict() | {"changeSetDigest": change_set.digest}
        ref = self.s.artifacts.put_json(
            record, metadata={"kind": "closure-commit", "executionId": execution.execution_id}
        )
        summary = (
            f"Closure commit {commit.commit[:12]} ({commit.status.lower()})"
            + (f" on branch {commit.branch}" if commit.branch else " on the current branch")
            if commit.commit
            else f"Closure commit skipped: {commit.reason}"
        )
        self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.OTHER,
            ref,
            summary,
            supports=(change_set.change_set_id, decision.decision_id),
        )
        self.s.events.append(
            execution.execution_id,
            "delivery.commit.skipped" if commit.status == "SKIPPED" else "delivery.commit.created",
            record,
            phase_execution_id=phase.phase_execution_id,
        )
        if self.ladder.active:
            # Deferred verifications are bound to the commit; push, pull request, comment or
            # staging follow the operational contract (#55).
            if commit.commit:
                self.ladder.bind_commit(execution, change_set.digest, commit.commit)
            return self.ladder.delivery.deliver(execution, phase, decision, commit, change_set)
        return None

    # ----- correction loop --------------------------------------------------------
    def _external_provider(self, execution_id: str) -> bool:
        """Whether the run uses a configured command provider. The simulated provider is
        deterministic and reads no feedback: the correction loop does not apply to it. Nor does
        it to the embedded ``session`` provider (#56): the session reads the findings and edits
        before it continues the run."""
        provider = self.s.state.get_flag(f"provider:{execution_id}") or "simulated"
        return provider not in {"simulated", SESSION_PROVIDER}

    def _feedback_applies(self, execution_id: str) -> bool:
        return self.s.resolved.project.runtime.feedback_enabled and self._external_provider(
            execution_id
        )

    def _after_failed_verification(self, execution: Execution, outcome: PhaseOutcome) -> bool:
        """Record what a failed VERIFICATION says about the agent's claim and, while the
        ``runtime.verificationCorrections`` budget lasts, send the run back to IMPLEMENTATION.

        Only a ``FAILED`` verification with failing mandatory validators qualifies: a blocked,
        timed-out or erroring validator is not something the agent can correct. Returns whether
        a correction cycle was authorized; otherwise the run stops as it did before."""
        runtime = self.s.resolved.project.runtime
        if (
            outcome.status is not ResultStatus.FAILED
            or not execution.change_set_digest
            or not self._external_provider(execution.execution_id)
        ):
            return False
        validations = self._latest_validations(execution.execution_id, execution.change_set_digest)
        failing = failing_validations(validations)
        if not failing:
            return False
        if runtime.claim_check_enabled:
            self._record_unsupported_claim(execution, failing)
        events = self.s.events.list(execution.execution_id)
        if self.results.active:
            # planning.decomposition (#39): each sub-task has its own correction budget.
            events = events[self.results.decomposition.budget_start(events) :]
        used = sum(
            1
            for event in events
            if event.event_type == "correction.authorized"
            and event.payload.get("trigger") == "VERIFICATION_FAILED"
        )
        limit = runtime.correction_limit
        failed_ids = [item.validator_id for item in failing]
        if used >= limit:
            if limit > 0:
                self.s.events.append(
                    execution.execution_id,
                    "correction.exhausted",
                    {
                        "trigger": "VERIFICATION_FAILED",
                        "cycles": used,
                        "maxCycles": limit,
                        "failedValidators": failed_ids,
                        "changeSetDigest": execution.change_set_digest,
                    },
                )
            # planning.granularity: adaptive (#39) decomposes a coarse attempt that failed.
            return self.results.active and self.results.replan_after_failure(execution)
        feedback_ref: str | None = None
        if self._feedback_applies(execution.execution_id):
            findings = [
                item
                for item in self.s.state.list(
                    "finding", Finding, execution_id=execution.execution_id
                )
                if any(item.finding_id in validation.finding_ids for validation in validations)
            ]
            feedback_ref = self._record_feedback(
                execution,
                PhaseId.VERIFICATION,
                trigger="VERIFICATION_FAILED",
                change_set_digest=execution.change_set_digest,
                gate=FeedbackGate(
                    gate_id="verification",
                    status=outcome.status,
                    reason_codes=verification_reason_codes(failing),
                ),
                validations=validations,
                findings=findings + self._claim_findings(execution, failing),
            )
        transition = self.state_machine.authorize_verification_correction(PhaseId.VERIFICATION)
        self._save_execution(
            execution.model_copy(
                update={
                    "status": ResultStatus.PENDING,
                    "current_phase": transition.target,
                    "gate_evaluation_id": None,
                    "human_decision_id": None,
                    "terminal_reason": None,
                    "updated_at": utc_now(),
                }
            )
        )
        self.s.events.append(
            execution.execution_id,
            "correction.authorized",
            {
                "trigger": "VERIFICATION_FAILED",
                "cycle": used + 1,
                "maxCycles": limit,
                "failedValidators": failed_ids,
                "changeSetDigest": execution.change_set_digest,
                "invalidatedPhases": [phase.value for phase in transition.invalidated],
                "feedbackRef": feedback_ref,
            },
        )
        if self.results.active:
            # agentRouting (#44): a quality failure climbs the escalation ladder.
            self.results.escalate(execution, "VERIFICATION_FAILED")
        return True

    def _record_unsupported_claim(
        self, execution: Execution, failing: list[ValidationResult]
    ) -> None:
        """An agent that reported success on work that then failed verification made a claim
        the evidence does not support. The finding is evidence for the reviewer and the metrics;
        it is not an input of the gate, which evaluates the current ChangeSet."""
        key = f"claim:{execution.execution_id}"
        raw = self.s.state.get_flag(key)
        if not raw:
            return
        self.s.state.set_flag(key, "")
        claim = json.loads(raw)
        summary = head(str(claim.get("summary", "")), 500)
        failed = ", ".join(f"{item.validator_id} {item.status}" for item in failing)
        output_ref = claim.get("outputRef")
        finding = Finding(
            finding_id=new_id("finding"),
            execution_id=execution.execution_id,
            validator_id=CLAIM_CHECK_ID,
            rule_id=UNSUPPORTED_CLAIM_RULE,
            category="agent-claim",
            severity=self.s.resolved.project.runtime.claim_severity,
            message=(
                f"The agent reported {claim.get('status', 'PASSED')} ({summary!r}) "
                f"but verification failed: {failed}"
            ),
            evidence_refs=tuple(
                ([output_ref] if isinstance(output_ref, str) else [])
                + [item.evidence_refs[0] for item in failing]
            ),
            recommendation=(
                "Compare the agent's summary with the validator output before trusting its "
                "reports; the verified outcome is what the gate evaluates."
            ),
            provenance=self._provenance(execution),
        )
        self.s.state.put(
            "finding",
            finding.finding_id,
            finding,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "finding.recorded",
            finding.model_dump(mode="json"),
            actor=finding.provenance.actor,
        )

    def _claim_findings(
        self, execution: Execution, failing: list[ValidationResult]
    ) -> list[Finding]:
        """Unsupported-claim findings that cite the failing validations of this verification."""
        cited = {item.evidence_refs[0] for item in failing}
        return [
            item
            for item in self.s.state.list("finding", Finding, execution_id=execution.execution_id)
            if item.rule_id == UNSUPPORTED_CLAIM_RULE and cited & set(item.evidence_refs)
        ]

    def _gate_inputs(self, gate: GateEvaluation) -> tuple[list[ValidationResult], list[Finding]]:
        validations: list[ValidationResult] = []
        findings: list[Finding] = []
        for ref in gate.input_refs:
            if ref.startswith("record://validation/"):
                validations.append(
                    self.s.state.get(
                        "validation", ref.removeprefix("record://validation/"), ValidationResult
                    )
                )
            elif ref.startswith("record://finding/"):
                findings.append(
                    self.s.state.get("finding", ref.removeprefix("record://finding/"), Finding)
                )
        return validations, findings

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
        decision: FeedbackDecision | None = None,
    ) -> str:
        """Store the feedback for the next IMPLEMENTATION attempt as evidence and remember it
        for the provider request."""
        attempt = 1 + sum(
            1
            for item in self.s.state.list(
                "phase", PhaseExecution, execution_id=execution.execution_id
            )
            if item.phase_id is PhaseId.IMPLEMENTATION
        )
        feedback = FeedbackBuilder(self.s.artifacts).build(
            trigger=trigger,
            attempt=max(attempt, 2),
            change_set_digest=change_set_digest,
            gate=gate,
            validations=validations,
            findings=findings,
            decision=decision,
        )
        ref = self.s.artifacts.put_json(
            feedback.model_dump(mode="json", by_alias=True),
            metadata={"kind": "provider-feedback", "executionId": execution.execution_id},
        )
        self._record_evidence(
            execution,
            phase_id,
            EvidenceKind.OTHER,
            ref,
            f"Provider feedback for IMPLEMENTATION attempt {feedback.attempt} ({trigger})",
        )
        self.s.state.set_flag(f"feedback:{execution.execution_id}", ref.uri)
        return ref.uri

    def _pending_feedback(
        self, execution: Execution, phase: PhaseExecution
    ) -> dict[str, Any] | None:
        """The latest recorded feedback, sent with every attempt that follows a correction."""
        if not self._feedback_applies(execution.execution_id):
            return None
        uri = self.s.state.get_flag(f"feedback:{execution.execution_id}")
        if not uri:
            return None
        feedback = ProviderFeedback.model_validate_json(self.s.artifacts.get(uri))
        if phase.attempt >= 2:
            feedback = feedback.model_copy(update={"attempt": phase.attempt})
        value: dict[str, Any] = feedback.model_dump(mode="json", by_alias=True)
        return value

    # ----- agent invocations ------------------------------------------------------
    def _save_agent_result(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None:
        self.s.state.put(
            "agent_invocation",
            result.invocation.invocation_id,
            result.invocation,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        for tool in result.tool_invocations:
            self._save_tool(execution, tool)
        if result.usage is not None:
            self.s.state.put(
                "resource_usage",
                result.usage.usage_id,
                result.usage,
                execution_id=execution.execution_id,
                project_id=execution.project_id,
            )
        self.s.events.append(
            execution.execution_id,
            "agent.invocation.completed",
            result.invocation.model_dump(mode="json"),
            actor=result.invocation.actor,
            phase_execution_id=phase.phase_execution_id,
        )

    def _transient_cause(self, result: AgentExecutionResult) -> str | None:
        """The configured pattern that marks a failed command-provider call as transient.

        A process the runner killed at its timeout, a cancelled call and a call that passed are
        never transient; the patterns are looked for in the end of the (redacted) stderr and
        stdout of the call, which also holds the provider's JSON result."""
        if result.status in {ResultStatus.PASSED, ResultStatus.CANCELLED} or not (
            result.tool_invocations
        ):
            return None
        tool = result.tool_invocations[0]
        if tool.timed_out or tool.cancelled:
            return None
        texts = []
        for uri in (tool.stderr_ref, tool.stdout_ref):
            if not uri:
                continue
            data = self.s.artifacts.get(uri)[-TRANSIENT_SCAN_BYTES:]
            texts.append(data.decode("utf-8", "replace"))
        return transient_cause(texts, self.s.resolved.project.runtime.transient_patterns)

    def _record_provider_retry(
        self,
        execution: Execution,
        phase: PhaseExecution,
        result: AgentExecutionResult,
        cause: str,
        retry: int,
    ) -> None:
        runtime = self.s.resolved.project.runtime
        tool = result.tool_invocations[0]
        payload = {
            "retry": retry,
            "maxRetries": runtime.retry_limit,
            "delaySeconds": runtime.retry_delay_seconds,
            "matchedPattern": cause,
            "invocationId": result.invocation.invocation_id,
            "status": result.status.value,
            "exitCode": tool.exit_code,
            "stdoutRef": tool.stdout_ref,
            "stderrRef": tool.stderr_ref,
        }
        ref = self.s.artifacts.put_json(
            payload, metadata={"kind": "provider-retry", "executionId": execution.execution_id}
        )
        self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.COMMAND,
            ref,
            f"Transient provider failure ({cause!r}); retry {retry} of {runtime.retry_limit}",
            supports=(result.invocation.invocation_id,),
        )
        self.s.events.append(
            execution.execution_id,
            "agent.invocation.retried",
            payload,
            phase_execution_id=phase.phase_execution_id,
        )

    def _wait_for_retry(self, execution_id: str, delay: float) -> bool:
        """Wait ``delay`` seconds before a retry; ``False`` when the run is cancelled meanwhile."""
        deadline = time.monotonic() + delay
        while (remaining := deadline - time.monotonic()) > 0:
            if self.is_cancelled(execution_id):
                return False
            time.sleep(min(remaining, 1.0))
        return not self.is_cancelled(execution_id)

    # ----- helpers ----------------------------------------------------------------
    def _compute_owned_diff(self, execution: Execution) -> WorkspaceDiff:
        """Compute the task-owned diff without persisting unredacted content."""

        baseline_uri = self.s.state.get_flag(f"baseline:{execution.execution_id}")
        if not baseline_uri:
            raise NotFoundError("baseline snapshot is missing")
        stored = self.snapshots.load(baseline_uri)
        before = stored.snapshot
        after = self.snapshots.take()
        task = self.run_task(execution)
        owned_paths: set[str] | None = None
        if task.implementation.mode == "patch":
            owned_paths = {patch.path for patch in task.implementation.patches}
        elif task.metadata.get("ownedPaths"):
            owned_paths = {str(path) for path in task.metadata["ownedPaths"]}
        if owned_paths is not None:
            before_files = {
                path: state for path, state in before.files.items() if path in owned_paths
            }
            after_files = {
                path: state for path, state in after.files.items() if path in owned_paths
            }
            before = WorkspaceSnapshot(
                files=before_files,
                digest=sha256_json({path: state.digest for path, state in before_files.items()}),
            )
            after = WorkspaceSnapshot(
                files=after_files,
                digest=sha256_json({path: state.digest for path, state in after_files.items()}),
            )
        stored.snapshot = before
        return self.snapshots.diff(stored, after)

    def baseline_digests(self, execution: Execution) -> dict[str, str]:
        """Path -> digest of the run's baseline snapshot."""
        baseline_uri = self.s.state.get_flag(f"baseline:{execution.execution_id}")
        if not baseline_uri:
            raise NotFoundError("baseline snapshot is missing")
        value = json.loads(self.s.artifacts.get(baseline_uri))
        return {path: str(item["digest"]) for path, item in value["files"].items()}

    def _refresh_changeset(self, execution: Execution) -> ChangeSet:
        diff = self._compute_owned_diff(execution)
        diff_ref = self.s.artifacts.put(
            diff.unified_diff,
            media_type="text/x-diff",
            metadata={"kind": "changeset-diff", "executionId": execution.execution_id},
        )
        changeset = ChangeSet(
            change_set_id=new_id("changeset"),
            execution_id=execution.execution_id,
            baseline_revision=execution.baseline_revision,
            current_revision=GitAdapter(self.s.paths.workspace).state().head,
            files=tuple(
                ChangedFile(
                    path=item.path,
                    status=cast(
                        Literal["ADDED", "MODIFIED", "DELETED", "RENAMED", "UNTRACKED"], item.status
                    ),
                    additions=item.additions,
                    deletions=item.deletions,
                    before_digest=item.before_digest,
                    after_digest=item.after_digest,
                )
                for item in diff.changes
            ),
            diff_ref=diff_ref.uri,
            digest=diff.digest,
        )
        existing = [
            item
            for item in self.s.state.list(
                "change_set", ChangeSet, execution_id=execution.execution_id
            )
            if item.digest == changeset.digest
        ]
        if existing:
            changeset = existing[-1]
        else:
            self.s.state.put(
                "change_set",
                changeset.change_set_id,
                changeset,
                execution_id=execution.execution_id,
                project_id=execution.project_id,
            )
            self.s.events.append(
                execution.execution_id,
                "changeset.created",
                changeset.model_dump(mode="json"),
            )
            self._record_artifact(
                execution,
                diff_ref,
                kind="CHANGESET_DIFF",
                provenance=self._provenance(execution),
            )
            self._record_evidence(
                execution,
                PhaseId.IMPLEMENTATION,
                EvidenceKind.CHANGESET,
                diff_ref,
                f"ChangeSet with {len(changeset.files)} changed file(s)",
                supports=(changeset.change_set_id,),
            )
        updated = self.get_execution(execution.execution_id).model_copy(
            update={"change_set_digest": changeset.digest, "updated_at": utc_now()}
        )
        self._save_execution(updated)
        return changeset

    def _current_or_evaluate_gate(
        self, execution: Execution, change_set: ChangeSet
    ) -> GateEvaluation:
        now = utc_now()
        exceptions = self._project_exceptions(execution)
        if execution.gate_evaluation_id:
            gate = self.s.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
            relied_on = set(exception_ids(gate.reason_codes))
            in_force = {item.exception_id for item in exceptions if item.active_at(now)}
            if gate.change_set_digest == change_set.digest and relied_on <= in_force:
                return gate
        self._workspace_guard_validation(execution, change_set.digest)
        validations = self._latest_validations(execution.execution_id, change_set.digest)
        findings = [
            item
            for item in self.s.state.list("finding", Finding, execution_id=execution.execution_id)
            if any(item.finding_id in validation.finding_ids for validation in validations)
        ]
        applied = apply_exceptions(findings, exceptions, now)
        findings = applied.kept
        severity_names = self.s.resolved.effective_policies.get(
            "findingBlockSeverities", ["HIGH", "CRITICAL"]
        )
        severities = tuple(FindingSeverity(str(name)) for name in severity_names)
        # Structured evaluation (keyword arguments) always returns a GateEvaluation; the
        # LegacyGateDecision branch only serves the positional `inputs` form.
        gate = cast(
            GateEvaluation,
            self.gate_engine.evaluate(
                execution_id=execution.execution_id,
                gate_id="delivery_candidate",
                change_set_digest=change_set.digest,
                policy_digest=execution.policy_digest,
                validations=validations,
                findings=findings,
                policy=GatePolicy(
                    require_human_decision=bool(
                        self.s.resolved.effective_policies.get("requireHumanDecision", True)
                    ),
                    blocking_severities=severities,
                ),
                provenance=self._provenance(execution),
            ),
        )
        if applied.used:
            gate = gate.model_copy(
                update={
                    "reason_codes": (*gate.reason_codes, *applied.reason_codes),
                    "input_refs": (
                        *gate.input_refs,
                        *(f"record://finding/{item.finding_id}" for item in applied.excepted),
                        *(f"record://exception/{item.exception_id}" for item in applied.used),
                    ),
                }
            )
        if self.ladder.active:
            # The certification of the ChangeSet (verification.ladder, #55).
            certification = self.ladder.gate_reasons(execution, change_set.digest)
            if certification:
                gate = gate.model_copy(
                    update={"reason_codes": (*gate.reason_codes, *certification)}
                )
        self.s.state.put(
            "gate",
            gate.gate_evaluation_id,
            gate,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        updated = self.get_execution(execution.execution_id).model_copy(
            update={"gate_evaluation_id": gate.gate_evaluation_id, "updated_at": utc_now()}
        )
        self._save_execution(updated)
        self.s.events.append(
            execution.execution_id,
            "gate.evaluated",
            gate.model_dump(mode="json"),
        )
        return gate

    def cause_analysis(self, execution: Execution) -> CauseAnalysis | None:
        """The causes of a run's stops and corrections under ``retrospective.causal``; ``None``
        (the 1.0.0 retrospective) without the key."""
        if not self.s.resolved.project.causal_retrospective:
            return None
        names = self.s.resolved.effective_policies.get(
            "findingBlockSeverities", ["HIGH", "CRITICAL"]
        )
        return analyze_causes(
            self.s.state,
            self.s.events.list(execution.execution_id),
            execution,
            {FindingSeverity(str(name)) for name in names},
        )

    def _project_exceptions(self, execution: Execution) -> list[ExceptionRecord]:
        """Exceptions recorded in the project, considered only under ``review.exceptions``:
        without the key the gate evaluates every finding, as in 1.0.0."""
        if not self.s.resolved.project.exceptions_enabled:
            return []
        return self.s.state.list("exception", ExceptionRecord, project_id=execution.project_id)

    def _latest_validations(self, execution_id: str, digest: str) -> list[ValidationResult]:
        # Only the latest attempt of each validator for the current digest counts: a failure
        # caused by the environment and fixed before a retry must not keep the gate closed.
        # Earlier attempts stay in the record and in the trace as history.
        latest: dict[str, ValidationResult] = {}
        for item in self.s.state.list("validation", ValidationResult, execution_id=execution_id):
            if item.change_set_digest != digest:
                continue
            previous = latest.get(item.validator_id)
            if previous is None or item.finished_at >= previous.finished_at:
                latest[item.validator_id] = item
        return list(latest.values())

    def _save_validator_output(self, execution: Execution, output: Any) -> None:
        result: ValidationResult = output.result
        self.s.state.put(
            "validation",
            result.validation_result_id,
            result,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        for finding in output.findings:
            self.s.state.put(
                "finding",
                finding.finding_id,
                finding,
                execution_id=execution.execution_id,
                project_id=execution.project_id,
            )
            self.s.events.append(
                execution.execution_id,
                "finding.recorded",
                finding.model_dump(mode="json"),
                actor=finding.provenance.actor,
            )
        for tool in output.tool_invocations:
            self._save_tool(execution, tool)
        self.s.events.append(
            execution.execution_id,
            "validation.completed",
            result.model_dump(mode="json"),
            actor=result.provenance.actor,
        )

    def _save_tool(self, execution: Execution, tool: ToolInvocation) -> None:
        self.s.state.put(
            "tool_invocation",
            tool.invocation_id,
            tool,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "tool.invocation.completed",
            tool.model_dump(mode="json"),
            actor=tool.actor,
        )

    def _record_artifact(
        self, execution: Execution, ref: Any, *, kind: str, provenance: Provenance
    ) -> Artifact:
        artifact = Artifact(
            artifact_id=new_id("artifact"),
            execution_id=execution.execution_id,
            kind=kind,
            media_type=ref.media_type,
            uri=ref.uri,
            digest=ref.digest,
            size_bytes=ref.size_bytes,
            redacted=ref.redacted,
            provenance=provenance,
            metadata=ref.metadata or {},
        )
        self.s.state.put(
            "artifact",
            artifact.artifact_id,
            artifact,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        return artifact

    def _record_evidence(
        self,
        execution: Execution,
        phase_id: PhaseId,
        kind: EvidenceKind,
        artifact_ref: Any,
        summary: str,
        *,
        supports: tuple[str, ...] = (),
    ) -> Evidence:
        self._record_artifact(
            execution,
            artifact_ref,
            kind=kind.value,
            provenance=self._provenance(execution),
        )
        evidence = Evidence(
            evidence_id=new_id("evidence"),
            execution_id=execution.execution_id,
            phase_id=phase_id,
            kind=kind,
            artifact_ref=artifact_ref.uri,
            digest=artifact_ref.digest,
            summary=summary,
            provenance=self._provenance(execution),
            supports=supports,
        )
        self.s.state.put(
            "evidence",
            evidence.evidence_id,
            evidence,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "evidence.recorded",
            evidence.model_dump(mode="json"),
        )
        return evidence

    def get_execution(self, execution_id: str) -> Execution:
        return self.s.state.get("execution", execution_id, Execution)

    def get_task(self, task_id: str) -> Task:
        return self.s.state.get("task", task_id, Task)

    def run_task(self, execution: Execution) -> Task:
        """The task revision a run works on: the revision pinned when the run was created (or
        revised through ``task clarify``) under ``governance.pinTaskRevision``, otherwise the
        stored task, re-read by every phase as in 1.0.0."""
        uri = self.s.state.get_flag(f"taskrev:{execution.execution_id}")
        if uri:
            return Task.model_validate_json(self.s.artifacts.get(uri))
        return self.get_task(execution.task_id)

    def _pins_task(self) -> bool:
        return bool(self.s.resolved.project.governance_settings.pin_task_revision)

    def _pin_task_revision(self, execution_id: str, task: Task) -> dict[str, Any]:
        ref = self.s.artifacts.put_json(
            task.model_dump(mode="json"), metadata={"kind": "task-revision"}
        )
        self.s.state.set_flag(f"taskrev:{execution_id}", ref.uri)
        return {"taskDigest": task_digest(task), "taskRevisionRef": ref.uri}

    def _current_contract_digest(self, execution: Execution) -> str | None:
        """Under ``governance.pinTaskRevision``, the digest of the acceptance contract frozen in
        SPECIFICATION after checking that the run's task still produces it; ``None`` without the
        setting (or for a run created without it)."""
        frozen = self.s.state.get_flag(f"contract:{execution.execution_id}")
        if not self._pins_task() or not frozen:
            return None
        current = acceptance_contract_digest(self.run_task(execution))
        if current != frozen:
            raise PolicyViolationError(
                "the acceptance contract of the run changed after SPECIFICATION "
                f"(frozen {frozen}, now {current}); a decision cannot be bound to it"
            )
        return frozen

    def current_change_set(self, execution_id: str) -> ChangeSet:
        execution = self.get_execution(execution_id)
        items = self.s.state.list("change_set", ChangeSet, execution_id=execution_id)
        if not items:
            raise NotFoundError(f"no ChangeSet for {execution_id}")
        if execution.change_set_digest:
            matching = [item for item in items if item.digest == execution.change_set_digest]
            if matching:
                return matching[-1]
        return items[-1]

    def is_cancelled(self, execution_id: str) -> bool:
        return self.s.state.get_flag(f"cancel:{execution_id}") == "1"

    def _cancel_execution(self, execution: Execution) -> Execution:
        updated = execution.model_copy(
            update={
                "status": ResultStatus.CANCELLED,
                "terminal_reason": "Cancellation requested",
                "updated_at": utc_now(),
            }
        )
        self._save_execution(updated)
        self.s.events.append(execution.execution_id, "run.cancelled", {})
        return updated

    def _save_execution(self, execution: Execution) -> None:
        self.s.state.put(
            "execution",
            execution.execution_id,
            execution,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )

    def _provenance(self, execution: Execution) -> Provenance:
        return Provenance(
            actor=HARNESS_ACTOR,
            core_version=__version__,
            configuration_digest=execution.configuration_digest,
            workflow_digest=execution.workflow_digest,
            policy_digest=execution.policy_digest,
        )

    @staticmethod
    def _worst_status(statuses: list[ResultStatus]) -> ResultStatus:
        order = [
            ResultStatus.ERROR,
            ResultStatus.TIMED_OUT,
            ResultStatus.BLOCKED,
            ResultStatus.CANCELLED,
            ResultStatus.INCONCLUSIVE,
            ResultStatus.FAILED,
            ResultStatus.SKIPPED,
            ResultStatus.NOT_APPLICABLE,
            ResultStatus.PASSED,
        ]
        return next(status for status in order if status in statuses)

    @staticmethod
    def _snapshot_to_dict(snapshot: WorkspaceSnapshot) -> dict[str, Any]:
        return {
            "digest": snapshot.digest,
            "files": {
                path: {
                    "path": state.path,
                    "digest": state.digest,
                    "sizeBytes": state.size_bytes,
                    "text": state.text,
                }
                for path, state in snapshot.files.items()
            },
        }

    @staticmethod
    def _snapshot_from_dict(value: dict[str, Any]) -> WorkspaceSnapshot:
        from governed_harness.runtime.workspace import FileState

        return WorkspaceSnapshot(
            files={
                path: FileState(
                    path=item["path"],
                    digest=item["digest"],
                    size_bytes=item["sizeBytes"],
                    text=item.get("text"),
                )
                for path, item in value["files"].items()
            },
            digest=value["digest"],
        )
