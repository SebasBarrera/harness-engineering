"""Application helpers of the agent-results settings (#37-#44, #52): parsing of decision
options and the commands that act on a run (budget, quarantine, plan approval, check,
routing calibration)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from governed_harness.domain.actors import require_human_actor
from governed_harness.domain.enums import ActorType, DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, NotFoundError, PolicyViolationError
from governed_harness.domain.models import Actor, ChangeRequestItem

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices


def plan_state(services: EngineServices, execution_id: str) -> dict[str, Any]:
    """The decomposition of a run (``planning.decomposition``) and the progress of its
    sub-tasks."""
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    state = engine.results.decomposition.state(execution)
    if state is None:
        raise NotFoundError(f"run {execution_id} has no decomposition")
    completed = [
        event.payload
        for event in services.events.list(execution_id)
        if event.event_type == "subtask.completed"
    ]
    return {
        "executionId": execution_id,
        "status": state["status"],
        "digest": state["digest"],
        "subtasks": state["subtasks"],
        "currentSubtask": engine.results.decomposition.index(execution) + 1,
        "completed": completed,
    }


def decide_plan(
    services: EngineServices,
    execution_id: str,
    *,
    decision: DecisionKind,
    digest: str,
    actor_id: str,
    rationale: str,
    continue_after: bool,
) -> dict[str, Any]:
    """Approve or reject a proposed decomposition (a person only, bound to its digest)."""
    from governed_harness.orchestration.engine import RunEngine

    require_human_actor(actor_id, "decide a decomposition plan")
    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    state = engine.results.decomposition.decide(
        execution,
        decision=decision,
        digest=digest,
        actor=Actor(actor_type=ActorType.HUMAN, actor_id=actor_id),
        rationale=rationale,
    )
    result: dict[str, Any] = {"executionId": execution_id, "plan": state}
    if continue_after:
        result["execution"] = engine.continue_execution(execution_id).model_dump(
            mode="json", by_alias=True
        )
    else:
        engine.anchor_chain(execution_id)
    return result


def quarantine_run(services: EngineServices, execution_id: str, actor_id: str) -> dict[str, Any]:
    """Quarantine the changes of a run that stopped without approval and restore the baseline.

    Refused for a run that passed, that waits in DECISION (a person decides there) or whose
    project does not set ``governance.stopTheLine``."""
    from governed_harness.orchestration.engine import RunEngine

    require_human_actor(actor_id, "quarantine a run's changes")
    if not services.resolved.project.governance_settings.stop_the_line:
        raise ConfigurationError("run quarantine needs governance.stopTheLine in project.yaml")
    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    if execution.status is ResultStatus.PASSED:
        raise PolicyViolationError(f"run {execution_id} was approved and closed")
    if execution.current_phase in {PhaseId.DECISION, PhaseId.CLOSURE} and (
        execution.status is not ResultStatus.FAILED
    ):
        raise PolicyViolationError(
            f"run {execution_id} waits for a decision; reject it or request changes instead"
        )
    actor = Actor(actor_type=ActorType.HUMAN, actor_id=actor_id)
    services.events.append(execution_id, "workspace.quarantine.requested", {}, actor=actor)
    record = engine.results.stop_line.quarantine(execution, f"quarantined by {actor_id}")
    engine.anchor_chain(execution_id)
    return {
        "executionId": execution_id,
        "quarantined": record is not None,
        "record": record,
    }


def parse_change_requests(values: Sequence[str]) -> tuple[ChangeRequestItem, ...]:
    """``description::condition`` items of a structured REQUEST_CHANGES, numbered ``CR-1``...
    An item without ``::`` is a ``text`` item (shown and sent, not checked by a tool)."""
    items: list[ChangeRequestItem] = []
    for number, value in enumerate(values, start=1):
        description, separator, condition = value.partition("::")
        if not description.strip():
            raise ConfigurationError(f"change request {number} needs a description")
        try:
            items.append(
                ChangeRequestItem(
                    item_id=f"CR-{number}",
                    description=description.strip(),
                    condition=condition.strip() if separator else "text",
                )
            )
        except ValueError as error:
            raise ConfigurationError(f"invalid change request {number}: {error}") from error
    return tuple(items)
