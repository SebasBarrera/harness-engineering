"""Decomposition of large tasks into governed sub-tasks in PLANNING (#39, N10).

A single governed task with dozens of requirements was too large for one implementation call:
weaker models delivered partial work, and the verification only saw the end. Under
``planning.decomposition: agent``, a task with more requirements than ``planning.threshold``
gets a plan from a provider (call kind ``plan``, read-only): ordered sub-tasks that partition the
task's requirements, each with its criteria and constraints. The harness validates the partition
(every requirement exactly once), stores the plan with its digest and waits for a person to
approve it (``harness plan decide``, digest-bound). An approved plan is reused when the run
resumes.

The sub-tasks then run in order on the same workspace, each through IMPLEMENTATION and
VERIFICATION with its own correction budget and its own gate (``subtask-<n>``), recorded as a
child of the run (``subtask.started`` / ``subtask.completed``). A sub-task whose gate does not
pass stops the run (dependent sub-tasks do not start). The parent reaches INDEPENDENT_REVIEW
and DECISION when every sub-task passed. The task's own constraints apply to every sub-task.

Adaptive granularity (``planning.granularity: adaptive``): a capable model
(``planning.coarseModels``) starts with the whole task; only when that coarse attempt fails
its corrections does the run return to PLANNING and decompose (split on failure). Other models
decompose from the start above the threshold."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.agent_results import DEFAULT_COARSE_MODELS
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
)
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    AcceptanceCriterion,
    Actor,
    Execution,
    Finding,
    PhaseExecution,
    PlanStep,
    Task,
    ValidationResult,
    utc_now,
)
from governed_harness.evidence import sha256_json
from governed_harness.gates import GatePolicy
from governed_harness.intake import task_digest
from governed_harness.orchestration.engine_types import PhaseOutcome

if TYPE_CHECKING:
    from governed_harness.events.sqlite_store import StoredEvent
    from governed_harness.orchestration.hosts import ResultsHost

MALFORMED_RULE = "planning.plan-malformed"
DEFAULT_MAX_SUBTASKS = 12


@dataclass(frozen=True)
class SubTask:
    title: str
    requirements: tuple[str, ...]
    criteria: tuple[str, ...]
    constraints: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "requirements": list(self.requirements),
            "criteria": list(self.criteria),
            "constraints": list(self.constraints),
        }


def validate_plan(task: Task, result: dict[str, Any], max_subtasks: int) -> list[SubTask]:
    """The sub-tasks of a ``plan`` result; raises ``ValueError`` unless they partition the
    task's requirements (every requirement exactly once) and name only known criteria."""
    raw = result.get("subtasks")
    if not isinstance(raw, list) or not raw:
        raise ValueError("the plan result needs a non-empty 'subtasks' list")
    if len(raw) > max_subtasks:
        raise ValueError(f"the plan has {len(raw)} sub-tasks; at most {max_subtasks} are allowed")
    known = [item.requirement_id for item in task.requirements]
    criteria = {item.criterion_id for item in task.acceptance_criteria}
    seen: dict[str, int] = {}
    items: list[SubTask] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"sub-task {index} is not an object")
        title = str(entry.get("title") or "").strip()
        if not title:
            raise ValueError(f"sub-task {index} has no title")
        requirements = entry.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"sub-task {index} has no requirements")
        for requirement in requirements:
            if requirement not in known:
                raise ValueError(f"sub-task {index} names an unknown requirement {requirement!r}")
            if requirement in seen:
                raise ValueError(
                    f"requirement {requirement} is in sub-tasks {seen[requirement]} and {index}"
                )
            seen[requirement] = index
        named = [str(item) for item in entry.get("criteria") or []]
        unknown = sorted(set(named) - criteria)
        if unknown:
            raise ValueError(f"sub-task {index} names unknown criteria: {', '.join(unknown)}")
        constraints = [str(item) for item in entry.get("constraints") or [] if str(item).strip()]
        items.append(
            SubTask(
                title[:300], tuple(str(r) for r in requirements), tuple(named), tuple(constraints)
            )
        )
    missing = [item for item in known if item not in seen]
    if missing:
        raise ValueError(f"requirements in no sub-task: {', '.join(missing)}")
    return items


class Decomposition:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def config(self) -> Any:
        return self.results.project.planning

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.config.enabled)

    def _key(self, execution: Execution) -> str:
        return f"decomposition:{execution.execution_id}"

    def state(self, execution: Execution) -> dict[str, Any] | None:
        value = self.results.flag_json(self._key(execution))
        return value if isinstance(value, dict) else None

    def approved(self, execution: Execution) -> list[SubTask] | None:
        state = self.state(execution)
        if not state or state.get("status") != "APPROVED":
            return None
        return [
            SubTask(
                item["title"],
                tuple(item["requirements"]),
                tuple(item["criteria"]),
                tuple(item["constraints"]),
            )
            for item in state["subtasks"]
        ]

    def index(self, execution: Execution) -> int:
        return int(self.results.s.state.get_flag(f"subtask:{execution.execution_id}") or 0)

    # ----- PLANNING ------------------------------------------------------------------------
    def _coarse(self, execution: Execution, task: Task) -> bool:
        """Whether adaptive granularity lets the implementing model start with the whole task."""
        config = self.config
        if config.granularity != "adaptive":
            return False
        if self.results.s.state.get_flag(f"replan:{execution.execution_id}"):
            return False
        model = self.results.implement_model(execution, task)
        coarse = config.coarse_models if config.coarse_models is not None else DEFAULT_COARSE_MODELS
        return bool(model) and model in coarse

    def plan(self, execution: Execution, phase: PhaseExecution, task: Task) -> PhaseOutcome | None:
        """Request, validate and hold for approval the decomposition of a large task."""
        if not self.enabled:
            return None
        results = self.results
        replan = bool(results.s.state.get_flag(f"replan:{execution.execution_id}"))
        threshold = self.config.requirement_threshold
        size = len(task.requirements)
        state = self.state(execution)
        digest = task_digest(task)
        if state and state.get("taskDigest") == digest:
            if state["status"] == "APPROVED":
                return None
            if state["status"] == "REJECTED":
                return None
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The decomposition into {len(state['subtasks'])} sub-task(s) waits for a "
                f"person: harness plan decide --run {execution.execution_id} --decision "
                f"APPROVE --digest {state['digest']}",
                (state["ref"],),
            )
        if size < 2 or (size <= threshold and not replan):
            return None
        if not replan and self._coarse(execution, task):
            results.s.events.append(
                execution.execution_id,
                "planning.decomposition.deferred",
                {"requirements": size, "threshold": threshold, "reason": "coarse model first"},
            )
            return None
        max_subtasks = self.config.max_subtasks or DEFAULT_MAX_SUBTASKS
        outcome = results.call_agent(
            execution,
            phase,
            "plan",
            {"threshold": threshold, "maxSubtasks": max_subtasks, "replan": replan},
            task=task,
            instruction_values={"maxSubtasks": max_subtasks},
        )
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The planning call did not answer ({outcome.status}): {outcome.summary}",
                outcome.evidence_refs,
            )
        try:
            subtasks = validate_plan(task, outcome.result, max_subtasks)
        except ValueError as error:
            results.record_finding(
                execution,
                validator_id="planning.decomposition",
                rule_id=MALFORMED_RULE,
                category="planning",
                severity=FindingSeverity.HIGH,
                message=f"The proposed decomposition is not valid: {error}",
                evidence_refs=outcome.evidence_refs,
                recommendation="Fix the planner's answer and continue the run.",
            )
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The proposed decomposition is not valid: {error}",
                outcome.evidence_refs,
            )
        plan_digest = sha256_json(
            {"taskDigest": digest, "subtasks": [item.as_dict() for item in subtasks]}
        )
        record = {
            "taskDigest": digest,
            "digest": plan_digest,
            "status": "PROPOSED",
            "subtasks": [item.as_dict() for item in subtasks],
            "invocationId": outcome.invocation_id,
            "replan": replan,
        }
        ref = results.record_json(
            execution,
            PhaseId.PLANNING,
            record,
            kind="decomposition-plan",
            summary=f"Proposed decomposition into {len(subtasks)} sub-task(s)",
            supports=tuple(item.requirement_id for item in task.requirements),
        )
        results.set_flag_json(self._key(execution), {**record, "ref": ref})
        results.s.events.append(
            execution.execution_id,
            "planning.decomposition.proposed",
            {"digest": plan_digest, "subtasks": len(subtasks), "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"The decomposition into {len(subtasks)} sub-task(s) waits for a person: harness "
            f"plan decide --run {execution.execution_id} --decision APPROVE --digest "
            f"{plan_digest}",
            (ref,),
        )

    def decide(
        self,
        execution: Execution,
        *,
        decision: DecisionKind,
        digest: str,
        actor: Actor,
        rationale: str,
    ) -> dict[str, Any]:
        """A person's approval or rejection of the proposed plan, bound to its digest."""
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a person can decide a decomposition plan")
        if decision not in {DecisionKind.APPROVE, DecisionKind.REJECT}:
            raise PolicyViolationError("a decomposition plan is approved or rejected")
        state = self.state(execution)
        if not state or state.get("status") != "PROPOSED":
            raise NotFoundError(f"run {execution.execution_id} has no proposed decomposition")
        if state["digest"] != digest:
            raise PolicyViolationError(
                "the digest does not match the proposed decomposition; the plan may have changed"
            )
        status = "APPROVED" if decision is DecisionKind.APPROVE else "REJECTED"
        state = {**state, "status": status, "decidedBy": actor.actor_id, "rationale": rationale}
        self.results.set_flag_json(self._key(execution), state)
        if status == "APPROVED":
            self.results.s.state.set_flag(f"decomposed:{execution.execution_id}", "1")
            self.results.s.state.set_flag(f"subtask:{execution.execution_id}", "0")
        self.results.s.events.append(
            execution.execution_id,
            "planning.decomposition.decided",
            {"decision": decision.value, "digest": digest, "rationale": rationale},
            actor=actor,
        )
        return state

    def plan_steps(self, execution: Execution) -> tuple[PlanStep, ...]:
        subtasks = self.approved(execution) or []
        return tuple(
            PlanStep(
                step_id=new_id("step"),
                description=(
                    f"Sub-task {number} of {len(subtasks)}: {item.title} "
                    f"(requirements {', '.join(item.requirements)})"
                ),
                capabilities=("filesystem.read", "filesystem.write", "process.execute"),
                expected_evidence=("agent invocation", "ChangeSet", f"gate subtask-{number}"),
            )
            for number, item in enumerate(subtasks, start=1)
        )

    # ----- IMPLEMENTATION and VERIFICATION -------------------------------------------------
    def subtask_task(self, execution: Execution, task: Task) -> Task:
        """The task an implement call of a decomposed run receives: the current sub-task's
        requirements, criteria and constraints plus the task's own constraints."""
        subtasks = self.approved(execution)
        if not subtasks:
            return task
        index = self.index(execution)
        if index >= len(subtasks):
            # Every sub-task passed: a correction after DECISION addresses the whole task.
            return task
        item = subtasks[index]
        if index == 0 or not self._started(execution, index):
            self._start(execution, index, item, len(subtasks))
        criteria = [c for c in task.acceptance_criteria if c.criterion_id in item.criteria]
        if not criteria:
            criteria = [
                AcceptanceCriterion(
                    criterion_id=f"subtask-{index + 1}",
                    text=(
                        f"Requirements {', '.join(item.requirements)} are implemented and the "
                        "tests of the earlier sub-tasks still pass."
                    ),
                )
            ]
        wanted = set(item.requirements)
        return task.model_copy(
            update={
                "title": f"{task.title} - sub-task {index + 1} of {len(subtasks)}: {item.title}"[
                    :300
                ],
                "requirements": tuple(r for r in task.requirements if r.requirement_id in wanted),
                "acceptance_criteria": tuple(criteria),
                "constraints": tuple(dict.fromkeys((*task.constraints, *item.constraints))),
                "metadata": {
                    **task.metadata,
                    "subtask": {
                        "index": index + 1,
                        "count": len(subtasks),
                        "parentTaskId": task.task_id,
                    },
                },
            }
        )

    def verification_task(self, execution: Execution, task: Task) -> Task:
        """Under decomposition, the requirements of the sub-tasks implemented so far (what a
        sub-task's VERIFICATION can expect tests for)."""
        subtasks = self.approved(execution)
        if not subtasks:
            return task
        done = {
            requirement
            for item in subtasks[: self.index(execution) + 1]
            for requirement in item.requirements
        }
        return task.model_copy(
            update={"requirements": tuple(r for r in task.requirements if r.requirement_id in done)}
        )

    def _started(self, execution: Execution, index: int) -> bool:
        return any(
            event.event_type == "subtask.started" and event.payload.get("index") == index + 1
            for event in self.results.s.events.list(execution.execution_id)
        )

    def _start(self, execution: Execution, index: int, item: SubTask, count: int) -> None:
        if self._started(execution, index):
            return
        self.results.s.events.append(
            execution.execution_id,
            "subtask.started",
            {"index": index + 1, "count": count, **item.as_dict()},
        )

    def budget_start(self, events: list[StoredEvent]) -> int:
        """Position after which a sub-task's corrections are counted (0 without decomposition)."""
        start = 0
        for position, event in enumerate(events):
            if event.event_type == "subtask.started":
                start = position
        return start

    def after_passed_verification(self, execution: Execution) -> bool:
        """Evaluate the finished sub-task's gate; hand the workspace to the next sub-task."""
        subtasks = self.approved(execution)
        if not subtasks:
            return False
        results = self.results
        engine = results.engine
        execution = engine.get_execution(execution.execution_id)
        index = self.index(execution)
        if index >= len(subtasks):
            return False
        digest = execution.change_set_digest or ""
        validations: list[ValidationResult] = engine._latest_validations(
            execution.execution_id, digest
        )
        ids = {finding_id for item in validations for finding_id in item.finding_ids}
        findings = [
            item
            for item in results.s.state.list(
                "finding", Finding, execution_id=execution.execution_id
            )
            if item.finding_id in ids
        ]
        names = results.s.resolved.effective_policies.get(
            "findingBlockSeverities", ["HIGH", "CRITICAL"]
        )
        gate = engine.gate_engine.evaluate(
            execution_id=execution.execution_id,
            gate_id=f"subtask-{index + 1}",
            change_set_digest=digest,
            policy_digest=execution.policy_digest,
            validations=validations,
            findings=findings,
            policy=GatePolicy(
                require_human_decision=False,
                blocking_severities=tuple(FindingSeverity(str(name)) for name in names),
            ),
            provenance=engine._provenance(execution),
        )
        status = getattr(gate, "status", ResultStatus.ERROR)
        results.s.events.append(
            execution.execution_id,
            "subtask.completed",
            {
                "index": index + 1,
                "count": len(subtasks),
                "title": subtasks[index].title,
                "requirements": list(subtasks[index].requirements),
                "changeSetDigest": digest,
                "gateStatus": status,
                "reasonCodes": list(getattr(gate, "reason_codes", ())),
            },
        )
        if status is not ResultStatus.PASSED:
            engine._save_execution(
                execution.model_copy(
                    update={
                        "status": ResultStatus.FAILED,
                        "current_phase": PhaseId.VERIFICATION,
                        "terminal_reason": (
                            f"Sub-task {index + 1} did not pass its gate ({status}); the "
                            "sub-tasks after it do not start"
                        ),
                        "updated_at": utc_now(),
                    }
                )
            )
            return False
        if index + 1 >= len(subtasks):
            results.s.state.set_flag(f"subtask:{execution.execution_id}", str(index + 1))
            return False
        results.s.state.set_flag(f"subtask:{execution.execution_id}", str(index + 1))
        transition = engine.state_machine.authorize_next_subtask(PhaseId.VERIFICATION)
        engine._save_execution(
            execution.model_copy(
                update={
                    "status": ResultStatus.PENDING,
                    "current_phase": transition.target,
                    "gate_evaluation_id": None,
                    "human_decision_id": None,
                    "updated_at": utc_now(),
                }
            )
        )
        self._start(execution, index + 1, subtasks[index + 1], len(subtasks))
        return True

    def replan_after_failure(self, execution: Execution) -> bool:
        """Adaptive granularity: a coarse attempt whose corrections are exhausted returns to
        PLANNING to be decomposed (once). Only the attempt of a model listed in
        ``planning.coarseModels`` is coarse (#77): another model's failure stops the run, it is
        not sent back to PLANNING."""
        if not self.enabled or self.config.granularity != "adaptive":
            return False
        results = self.results
        if results.s.state.get_flag(f"replan:{execution.execution_id}") or self.approved(execution):
            return False
        task = results.engine.run_task(execution)
        if len(task.requirements) < 2 or not self._coarse(execution, task):
            return False
        results.s.state.set_flag(f"replan:{execution.execution_id}", "1")
        results.s.state.set_flag(self._key(execution), "")
        transition = results.engine.state_machine.authorize_replanning(PhaseId.VERIFICATION)
        latest = results.engine.get_execution(execution.execution_id)
        results.engine._save_execution(
            latest.model_copy(
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
        results.s.events.append(
            execution.execution_id,
            "planning.split-on-failure",
            {"requirements": len(task.requirements)},
        )
        return True
