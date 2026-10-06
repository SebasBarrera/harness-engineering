"""Evaluation of the workflow's exit gates under ``governance.enforceWorkflow`` (#3).

After a phase attempt returns ``PASSED`` the engine evaluates the phase's ``exitGate`` and the
condition of the transition it takes (``WorkflowGraph.exit_conditions``) from what the run
recorded: evidence, flags, the plan, the ChangeSet, validations, the human decision and events.
Each check is deterministic and reads only the run's records; it does not repeat the phase's
policy decisions (an INTENT that passed with clarification warnings still passes), it checks
that the phase left the record the next phase relies on. Every name is listed with what it
checks in ``configuration.workflow_rules.EXIT_GATE_CONDITIONS``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from governed_harness.configuration.workflow_rules import (
    EXIT_GATE_CONDITIONS,
    canonical_condition,
)
from governed_harness.domain.enums import DecisionKind, EvidenceKind, PhaseId, ResultStatus
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.domain.models import (
    Evidence,
    Execution,
    HumanDecision,
    PhaseExecution,
    Plan,
    ValidationResult,
    utc_now,
)

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import EngineHost

REVIEW_VALIDATOR_ID = "review.independent"
APPROVALS = frozenset({DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION})
_NO_CHANGESET = "the run has no current ChangeSet"


@dataclass(frozen=True)
class ExitGateCheck:
    condition: str
    met: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"condition": self.condition, "met": self.met, "reason": self.reason}


class ExitGateEvaluator:
    def __init__(self, engine: EngineHost) -> None:
        self.engine = engine
        self._checks: dict[str, Callable[[Execution, PhaseExecution], str | None]] = {
            "intent_complete": self._intent_complete,
            "discovery_sufficient": self._discovery_sufficient,
            "specification_approved": self._specification_approved,
            "plan_authorized": self._plan_authorized,
            "candidate_changeset": self._candidate_changeset,
            "verification_passed": self._verification_passed,
            "review_complete": self._review_complete,
            "decision_approved": self._decision_approved,
            "run_closed": self._run_closed,
        }
        missing = set(EXIT_GATE_CONDITIONS) - set(self._checks)
        if missing:  # pragma: no cover - a programming error caught by the unit tests
            raise RuntimeError(f"exit gates without a check: {sorted(missing)}")

    def evaluate(self, condition: str, execution_id: str, phase: PhaseExecution) -> ExitGateCheck:
        """Evaluate one condition for the attempt ``phase`` of the run. An unknown name is
        unmet (the resolver rejects it before a run starts)."""
        name = canonical_condition(condition)
        check = self._checks.get(name)
        if check is None:
            return ExitGateCheck(name, False, f"unknown condition {condition!r}")
        execution = self.engine.get_execution(execution_id)
        problem = check(execution, phase)
        if problem is None:
            return ExitGateCheck(name, True, EXIT_GATE_CONDITIONS[name])
        return ExitGateCheck(name, False, problem)

    # ----- records -----------------------------------------------------------------------
    def _evidence(self, execution: Execution, phase_id: PhaseId, kind: EvidenceKind) -> bool:
        return any(
            item.phase_id is phase_id and item.kind is kind
            for item in self.engine.s.state.list(
                "evidence", Evidence, execution_id=execution.execution_id
            )
        )

    def _attempt_validations(
        self, execution: Execution, phase: PhaseExecution
    ) -> dict[str, ValidationResult]:
        """The latest validation of each validator recorded during this attempt for the
        current ChangeSet."""
        started = phase.started_at
        latest: dict[str, ValidationResult] = {}
        for item in self.engine.s.state.list(
            "validation", ValidationResult, execution_id=execution.execution_id
        ):
            if item.change_set_digest != execution.change_set_digest:
                continue
            if started is not None and item.finished_at < started:
                continue
            previous = latest.get(item.validator_id)
            if previous is None or item.finished_at >= previous.finished_at:
                latest[item.validator_id] = item
        return latest

    # ----- checks: None when met, otherwise the reason -------------------------------------
    def _intent_complete(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not self.engine.run_task(execution).acceptance_criteria:
            return "the run's task has no acceptance criteria"
        if not self._evidence(execution, PhaseId.INTENT, EvidenceKind.INTENT):
            return "INTENT recorded no intent evidence"
        return None

    def _discovery_sufficient(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not self.engine.s.state.get_flag(f"baseline:{execution.execution_id}"):
            return "DISCOVERY recorded no baseline snapshot"
        if not execution.baseline_revision:
            return "DISCOVERY recorded no baseline revision"
        return None

    def _specification_approved(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not self._evidence(execution, PhaseId.SPECIFICATION, EvidenceKind.INTENT):
            return "SPECIFICATION recorded no acceptance contract"
        try:
            self.engine._current_contract_digest(execution)
        except PolicyViolationError as error:
            return str(error)
        return None

    def _plan_authorized(self, execution: Execution, phase: PhaseExecution) -> str | None:
        plan_id = self.engine.s.state.get_flag(f"plan:{execution.execution_id}")
        if not plan_id:
            return "PLANNING stored no plan for the run"
        try:
            plan = self.engine.s.state.get("plan", plan_id, Plan)
        except NotFoundError:
            return f"the run's plan {plan_id} is not recorded"
        if plan.execution_id != execution.execution_id:
            return f"plan {plan_id} belongs to run {plan.execution_id}"
        return None

    def _candidate_changeset(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not execution.change_set_digest:
            return _NO_CHANGESET
        try:
            change_set = self.engine.current_change_set(execution.execution_id)
        except NotFoundError:
            return "the run's ChangeSet is not recorded"
        if change_set.digest != execution.change_set_digest:
            return "the recorded ChangeSet is not the run's current one"
        allow_empty = bool(self.engine.s.resolved.effective_policies.get("allowEmptyChangeSet"))
        if not change_set.files and not allow_empty:
            return "the current ChangeSet is empty"
        return None

    def _verification_passed(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not execution.change_set_digest:
            return _NO_CHANGESET
        validations = self._attempt_validations(execution, phase)
        if not validations:
            return "VERIFICATION recorded no validation for the current ChangeSet"
        failing = sorted(
            item.validator_id
            for item in validations.values()
            if item.mandatory and item.status is not ResultStatus.PASSED
        )
        if failing:
            return f"mandatory validation(s) did not pass: {', '.join(failing)}"
        return None

    def _review_complete(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not execution.change_set_digest:
            return _NO_CHANGESET
        if REVIEW_VALIDATOR_ID not in self._attempt_validations(execution, phase):
            return "the independent review recorded no result for the current ChangeSet"
        return None

    def _decision_approved(self, execution: Execution, phase: PhaseExecution) -> str | None:
        if not execution.human_decision_id:
            return "no current human decision"
        try:
            decision = self.engine.s.state.get(
                "decision", execution.human_decision_id, HumanDecision
            )
        except NotFoundError:
            return f"human decision {execution.human_decision_id} is not recorded"
        if decision.decision not in APPROVALS:
            return f"the current human decision is {decision.decision.value}, not an approval"
        if decision.change_set_digest != execution.change_set_digest:
            return "the human decision is bound to another ChangeSet digest"
        if decision.expires_at is not None and decision.expires_at <= utc_now():
            return f"the human decision expired at {decision.expires_at.isoformat()}"
        return None

    def _run_closed(self, execution: Execution, phase: PhaseExecution) -> str | None:
        closed = any(
            event.event_type == "run.closed"
            and event.payload.get("changeSetDigest") == execution.change_set_digest
            for event in self.engine.s.events.list(execution.execution_id)
        )
        return None if closed else "CLOSURE recorded no run.closed for the current ChangeSet"
