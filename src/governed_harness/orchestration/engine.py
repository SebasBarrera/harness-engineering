from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from governed_harness import __version__
from governed_harness.agents import (
    AgentProvider,
    CommandAgentConfiguration,
    CommandAgentProvider,
    SimulatedAgentContext,
    SimulatedAgentProvider,
)
from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.models import ResolvedConfiguration, ValidatorDefinition
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    EvidenceKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
)
from governed_harness.domain.errors import (
    ConfigurationError,
    NotFoundError,
    PolicyViolationError,
)
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    HARNESS_ACTOR,
    Actor,
    Artifact,
    ChangedFile,
    ChangeSet,
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationRecord,
    ClarificationRequest,
    ConfigurationSnapshot,
    Evidence,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Plan,
    PlanStep,
    Provenance,
    Task,
    ToolInvocation,
    ValidationResult,
    utc_now,
)
from governed_harness.events import SQLiteEventStore
from governed_harness.evidence import LocalArtifactStore, sha256_json
from governed_harness.gates import GateEngine, GatePolicy
from governed_harness.intake import (
    ClarificationInput,
    assess_intent,
    no_acceptance_criteria,
    revise_task,
    task_digest,
)
from governed_harness.memory import MemoryStore, context_manifest
from governed_harness.orchestration.state_machine import NormativeStateMachine
from governed_harness.profiles import detect_profiles
from governed_harness.retrospective import RetrospectiveEngine
from governed_harness.runtime import (
    CancellationToken,
    GitAdapter,
    PatchApplier,
    SafeProcessRunner,
    WorkspaceDiff,
    WorkspaceSnapshot,
    WorkspaceSnapshotter,
)
from governed_harness.storage import SQLiteStateStore
from governed_harness.telemetry import MetricsProjector
from governed_harness.validators import (
    IndependentReviewValidator,
    ValidationContext,
    ValidatorRegistry,
)

NON_HUMAN_ACTOR_PREFIXES = ("agent.", "validator.", "harness.")
"""Actor id namespaces the harness assigns to agents, validators and itself."""


@dataclass(frozen=True)
class EnginePaths:
    workspace: Path
    harness_dir: Path
    database: Path
    artifact_dir: Path

    @classmethod
    def from_workspace(cls, workspace: Path) -> EnginePaths:
        root = workspace.resolve(strict=True)
        harness_dir = root / ".harness"
        harness_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return cls(root, harness_dir, harness_dir / "state.db", harness_dir / "artifacts")


@dataclass
class EngineServices:
    resolved: ResolvedConfiguration
    paths: EnginePaths
    state: SQLiteStateStore
    events: SQLiteEventStore
    artifacts: LocalArtifactStore

    @classmethod
    def open(cls, resolved: ResolvedConfiguration) -> EngineServices:
        paths = EnginePaths.from_workspace(resolved.workspace_root)
        return cls(
            resolved=resolved,
            paths=paths,
            state=SQLiteStateStore(paths.database),
            events=SQLiteEventStore(paths.database),
            artifacts=LocalArtifactStore(paths.artifact_dir),
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
    def __init__(self, services: EngineServices) -> None:
        self.s = services
        self.state_machine = NormativeStateMachine()
        self.gate_engine = GateEngine()
        self.validators = ValidatorRegistry()
        self.retrospective_engine = RetrospectiveEngine()

    # ----- creation and lifecycle -------------------------------------------------
    def create_execution(self, task: Task, provider: str | None = None) -> Execution:
        if task.project_id != self.s.resolved.project.project_id:
            raise ConfigurationError(
                f"task project {task.project_id} does not match {self.s.resolved.project.project_id}"
            )
        execution_id = new_id("run")
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
        self.s.events.append(
            execution_id,
            "run.created",
            {
                "taskId": task.task_id,
                "projectId": task.project_id,
                "configurationDigest": configuration_digest,
                "workflowDigest": workflow_digest,
                "policyDigest": policy_digest,
                "provider": provider or self.s.resolved.project.agent_provider,
            },
        )
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
        execution = self.get_execution(execution_id)
        if execution.status in {ResultStatus.PASSED, ResultStatus.CANCELLED}:
            return execution
        if self.is_cancelled(execution_id):
            return self._cancel_execution(execution)
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
                outcome = self._run_phase(execution, handler)
                execution = self.get_execution(execution_id)
                if outcome.status is not ResultStatus.PASSED:
                    return execution
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
        return self._cancel_execution(execution)

    def decide(
        self,
        *,
        execution_id: str,
        decision: DecisionKind,
        change_set_digest: str,
        actor_id: str,
        rationale: str,
    ) -> HumanDecision:
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
        actor = Actor(actor_type=ActorType.HUMAN, actor_id=actor_id)
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
        updated = execution.model_copy(
            update={"human_decision_id": record.decision_id, "updated_at": utc_now()}
        )
        if decision is DecisionKind.REQUEST_CHANGES:
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
        elif decision is DecisionKind.REJECT:
            updated = updated.model_copy(
                update={
                    "status": ResultStatus.FAILED,
                    "terminal_reason": "Rejected by human decision",
                    "updated_at": utc_now(),
                }
            )
        else:
            updated = updated.model_copy(
                update={"status": ResultStatus.PENDING, "updated_at": utc_now()}
            )
        self._save_execution(updated)
        return record

    def clarify(
        self, *, task_id: str, clarification: ClarificationInput, actor: Actor
    ) -> tuple[ClarificationRecord, Task]:
        """Answer the open clarification request of a task and store the revised task.

        The answers and the revision are recorded on the event chain of the run whose INTENT
        asked the questions; ``continue_execution`` then assesses the revised task."""
        if actor.actor_type is not ActorType.HUMAN or actor.actor_id.startswith(
            NON_HUMAN_ACTOR_PREFIXES
        ):
            raise PolicyViolationError("only a human actor can answer clarification questions")
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
        )
        self.s.state.put("task", task_id, revision.task, project_id=task.project_id)
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
        return record, revision.task

    # ----- phases -----------------------------------------------------------------
    def _run_phase(
        self, execution: Execution, handler: Callable[[Execution, PhaseExecution], PhaseOutcome]
    ) -> PhaseOutcome:
        phase_id = execution.current_phase
        attempt = 1 + len(
            [
                phase
                for phase in self.s.state.list(
                    "phase", PhaseExecution, execution_id=execution.execution_id
                )
                if phase.phase_id is phase_id
            ]
        )
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
                outcome = handler(running, phase)
        except Exception as error:
            outcome = PhaseOutcome(ResultStatus.ERROR, f"{type(error).__name__}: {error}")
            self.s.events.append(
                execution.execution_id,
                "phase.error",
                {"phaseId": phase_id, "errorType": type(error).__name__, "message": str(error)},
                phase_execution_id=phase.phase_execution_id,
            )
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
        self.s.events.append(
            execution.execution_id,
            "phase.completed",
            {
                "phaseId": phase_id,
                "attempt": attempt,
                "status": outcome.status,
                "summary": outcome.summary,
                "evidenceRefs": list(outcome.evidence_refs),
            },
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
        task = self.get_task(execution.task_id)
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
        if not questions:
            return PhaseOutcome(
                ResultStatus.PASSED,
                "Intent is structured and identifiable",
                (evidence.artifact_ref,),
            )
        request_evidence = self._request_clarification(
            execution, phase, task, questions, "enforce" if policy == "enforce" else "warn"
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
    ) -> Evidence:
        request = ClarificationRequest(
            request_id=new_id("clarifyrequest"),
            execution_id=execution.execution_id,
            task_id=task.task_id,
            task_digest=task_digest(task),
            policy=policy,
            questions=questions,
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
            f"Clarification request: {len(questions)} question(s)",
            supports=tuple(dict.fromkeys(question.target for question in questions)),
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
        detections = detect_profiles(self.s.paths.workspace)
        selected = {profile.profile_id for profile in self.s.resolved.profiles}
        if not any(item.profile_id in selected and item.confidence > 0 for item in detections):
            return PhaseOutcome(
                ResultStatus.BLOCKED, "Configured profile was not detected in workspace"
            )
        git = GitAdapter(self.s.paths.workspace).state()
        snapshot = WorkspaceSnapshotter(self.s.paths.workspace).snapshot()
        snapshot_ref = self.s.artifacts.put_json(
            self._snapshot_to_dict(snapshot),
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
        return PhaseOutcome(
            ResultStatus.PASSED,
            "Workspace and baseline discovered",
            (evidence.artifact_ref, snapshot_ref.uri),
        )

    def _phase_specification(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        task = self.get_task(execution.task_id)
        contract = {
            "taskId": task.task_id,
            "requirements": [item.model_dump(mode="json") for item in task.requirements],
            "acceptanceCriteria": [
                item.model_dump(mode="json") for item in task.acceptance_criteria
            ],
            "constraints": list(task.constraints),
            "digest": sha256_json(
                {
                    "requirements": [item.model_dump(mode="json") for item in task.requirements],
                    "acceptance": [
                        item.model_dump(mode="json") for item in task.acceptance_criteria
                    ],
                    "constraints": list(task.constraints),
                }
            ),
        }
        contract_ref = self.s.artifacts.put_json(contract, metadata={"kind": "acceptance-contract"})
        evidence = self._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.INTENT,
            contract_ref,
            "Versioned acceptance contract",
            supports=tuple(item.criterion_id for item in task.acceptance_criteria),
        )
        return PhaseOutcome(
            ResultStatus.PASSED, "Acceptance contract frozen", (evidence.artifact_ref,)
        )

    def _phase_planning(self, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
        task = self.get_task(execution.task_id)
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
        plan = Plan(
            plan_id=new_id("plan"),
            execution_id=execution.execution_id,
            task_id=task.task_id,
            steps=steps,
            risks=("Repository content is untrusted", "Approval becomes stale after any change"),
            validator_ids=tuple(item.validator_id for item in self.s.resolved.effective_validators)
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
        task = self.get_task(execution.task_id)
        plan_id = self.s.state.get_flag(f"plan:{execution.execution_id}")
        if not plan_id:
            return PhaseOutcome(ResultStatus.BLOCKED, "No approved plan exists")
        plan = self.s.state.get("plan", plan_id, Plan)
        provider_id = self.s.state.get_flag(f"provider:{execution.execution_id}") or "simulated"
        if provider_id == "simulated":
            provider: AgentProvider = SimulatedAgentProvider()
            actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.simulated", version="1")
        else:
            provider_config = self.s.resolved.project.agent_providers.get(provider_id)
            if provider_config is None:
                return PhaseOutcome(
                    ResultStatus.BLOCKED,
                    f"Provider {provider_id!r} is not configured",
                )
            provider = CommandAgentProvider(
                CommandAgentConfiguration(
                    provider_id=provider_id,
                    argv_prefix=provider_config.command,
                    model=provider_config.model,
                )
            )
            actor = Actor(actor_type=ActorType.AGENT, actor_id=f"agent.{provider_id}", version="1")
        grants = grants_from_rules(
            execution.execution_id, actor, self.s.resolved.effective_capabilities
        )
        cancellation = CancellationToken(lambda: self.is_cancelled(execution.execution_id))
        runner = SafeProcessRunner(self.s.paths.workspace)
        context_uri = self.s.state.get_flag(f"context:{execution.execution_id}")
        memory_context: dict[str, Any] | None = None
        if context_uri:
            manifest = json.loads(self.s.artifacts.get(context_uri))
            memory_context = {"records": manifest["records"], "digest": manifest["digest"]}
        result = provider.implement(
            task,
            plan,
            SimulatedAgentContext(
                execution_id=execution.execution_id,
                workspace=self.s.paths.workspace,
                grants=grants,
                artifact_store=self.s.artifacts,
                process_runner=runner,
                patch_applier=PatchApplier(self.s.paths.workspace),
                provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                timeout_seconds=self.s.resolved.project.runtime.command_timeout_seconds,
                max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
                cancellation=cancellation,
                context_manifest_ref=context_uri,
                memory_context=memory_context,
            ),
        )
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
        if result.status is not ResultStatus.PASSED:
            return PhaseOutcome(
                result.status, result.summary, (result.output_ref,) if result.output_ref else ()
            )
        change_set = self._refresh_changeset(execution)
        if not change_set.files and not bool(
            self.s.resolved.effective_policies.get("allowEmptyChangeSet", False)
        ):
            return PhaseOutcome(ResultStatus.FAILED, "Implementation produced no ChangeSet")
        return PhaseOutcome(
            ResultStatus.PASSED,
            f"Candidate ChangeSet contains {len(change_set.files)} file(s)",
            (change_set.diff_ref,),
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
                    task=self.get_task(execution.task_id),
                    change_set=change_set,
                    definition=definition,
                    grants=grants,
                    artifact_store=self.s.artifacts,
                    process_runner=SafeProcessRunner(self.s.paths.workspace),
                    provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                    cancellation=CancellationToken(
                        lambda: self.is_cancelled(execution.execution_id)
                    ),
                    max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
                )
            )
            self._save_validator_output(execution, output)
            outputs.append(output)
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
        output = IndependentReviewValidator().execute(
            ValidationContext(
                execution_id=execution.execution_id,
                workspace=self.s.paths.workspace,
                task=self.get_task(execution.task_id),
                change_set=change_set,
                definition=definition,
                grants=grants,
                artifact_store=self.s.artifacts,
                process_runner=SafeProcessRunner(self.s.paths.workspace),
                provenance=self._provenance(execution).model_copy(update={"actor": actor}),
                cancellation=CancellationToken(lambda: self.is_cancelled(execution.execution_id)),
                max_output_bytes=self.s.resolved.project.runtime.max_output_bytes,
                raw_diff=self._compute_owned_diff(execution).unified_diff.decode(
                    "utf-8", "replace"
                ),
            )
        )
        self._save_validator_output(execution, output)
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
        execution = self.get_execution(execution.execution_id)
        if current_decision:
            if current_decision.change_set_digest != change_set.digest:
                return PhaseOutcome(
                    ResultStatus.BLOCKED, "Human decision is stale after ChangeSet modification"
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

    # ----- helpers ----------------------------------------------------------------
    def _compute_owned_diff(self, execution: Execution) -> WorkspaceDiff:
        """Compute the task-owned diff without persisting unredacted content."""

        baseline_uri = self.s.state.get_flag(f"baseline:{execution.execution_id}")
        if not baseline_uri:
            raise NotFoundError("baseline snapshot is missing")
        before = self._snapshot_from_dict(json.loads(self.s.artifacts.get(baseline_uri)))
        after = WorkspaceSnapshotter(self.s.paths.workspace).snapshot()
        task = self.get_task(execution.task_id)
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
        return WorkspaceSnapshotter(self.s.paths.workspace).diff(before, after)

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
        if execution.gate_evaluation_id:
            gate = self.s.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
            if gate.change_set_digest == change_set.digest:
                return gate
        # Only the latest attempt of each validator for the current digest counts: a failure
        # caused by the environment and fixed before a retry must not keep the gate closed.
        # Earlier attempts stay in the record and in the trace as history.
        latest: dict[str, ValidationResult] = {}
        for item in self.s.state.list(
            "validation", ValidationResult, execution_id=execution.execution_id
        ):
            if item.change_set_digest != change_set.digest:
                continue
            previous = latest.get(item.validator_id)
            if previous is None or item.finished_at >= previous.finished_at:
                latest[item.validator_id] = item
        validations = list(latest.values())
        findings = [
            item
            for item in self.s.state.list("finding", Finding, execution_id=execution.execution_id)
            if any(item.finding_id in validation.finding_ids for validation in validations)
        ]
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
