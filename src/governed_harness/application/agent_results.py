"""Application helpers of the agent-results settings (#37-#44, #52): parsing of decision
options and the commands that act on a run (budget, quarantine, plan approval, check,
routing calibration)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from governed_harness.domain.actors import require_human_actor
from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.models import Actor, ChangeRequestItem

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices


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
