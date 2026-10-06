"""Application helpers of the agent-results settings (#37-#44, #52): parsing of decision
options and the commands that act on a run (budget, quarantine, plan approval, check,
routing calibration)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.models import ProjectConfiguration
from governed_harness.domain.actors import require_human_actor
from governed_harness.domain.enums import ActorType, DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, NotFoundError, PolicyViolationError
from governed_harness.domain.models import Actor, ChangeRequestItem

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices


def routing_calibration(services: EngineServices) -> dict[str, Any]:
    """Cost per approved task by provider family, call kind, size, model and effort, from the
    routing decisions and the usage the providers reported (N14). The suggested table is a
    suggestion: nothing is applied."""
    from governed_harness.agents.routing import CalibrationRow, calibrate, suggest_table
    from governed_harness.domain.models import AgentInvocation, Execution, ResourceUsage

    project_id = services.resolved.project.project_id
    usages = {
        item.invocation_id: item
        for item in services.state.list("resource_usage", ResourceUsage, project_id=project_id)
        if item.invocation_id
    }
    invocations = services.state.list("agent_invocation", AgentInvocation, project_id=project_id)
    rows: list[CalibrationRow] = []
    for execution in services.state.list("execution", Execution, project_id=project_id):
        approved = execution.status is ResultStatus.PASSED
        # Since #85 every reviewer of the review panel records its decision; reviewers on the
        # same model share the run's review invocations, so they count once.
        reviewers: set[tuple[Any, ...]] = set()
        for event in services.events.list(execution.execution_id):
            if event.event_type != "agent.routing.decided" or not _counted(
                event.payload, reviewers
            ):
                continue
            payload = event.payload
            kind = str(payload.get("callKind"))
            model = payload.get("model")
            costs = [
                usages[item.invocation_id].cost_usd
                for item in invocations
                if item.execution_id == execution.execution_id
                and (item.call_kind or "implement") == kind
                and (model is None or item.model == model)
                and item.invocation_id in usages
                and usages[item.invocation_id].cost_usd is not None
            ]
            rows.append(
                CalibrationRow(
                    family=str(payload.get("family")),
                    call_kind=kind,
                    size=payload.get("size"),
                    model=model,
                    effort=payload.get("effort"),
                    cost_usd=sum(item for item in costs if item is not None) if costs else None,
                    approved=approved,
                    run_id=execution.execution_id,
                )
            )
    groups = calibrate(rows)
    return {
        "projectId": project_id,
        "decisions": len(rows),
        "groups": groups,
        "suggestedTables": suggest_table(groups),
        "note": (
            "Cost per approved task from the recorded decisions and reported usage; the "
            "suggested tables need at least two approved runs per group and are not applied."
        ),
    }


def _counted(payload: dict[str, Any], reviewers: set[tuple[Any, ...]]) -> bool:
    """Whether a routing decision is a calibration row: a reviewer's decision counts once per
    call kind, model and effort of the run (``reviewers`` collects them)."""
    if "reviewer" not in payload:
        return True
    key = (payload.get("callKind"), payload.get("model"), payload.get("effort"))
    if key in reviewers:
        return False
    reviewers.add(key)
    return True


def budget_state(services: EngineServices, execution_id: str) -> dict[str, Any]:
    """Usage of a run and its task against the configured limits (``budget``)."""
    from governed_harness.orchestration.engine import RunEngine

    if services.resolved.project.budget is None:
        raise ConfigurationError("budget needs the budget section in project.yaml")
    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    check = engine.results.budget_check(execution)
    return {
        "executionId": execution_id,
        "usage": {
            "run": engine.results.run_usage(execution).as_dict(),
            "task": engine.results.task_usage(execution).as_dict(),
        },
        "limits": services.resolved.project.budget.model_dump(mode="json", by_alias=True),
        "raised": engine.results.raised_limits(execution),
        "remaining": check.remaining if check else {},
        "exceeded": [item.as_dict() for item in check.exceeded] if check else [],
    }


def raise_budget(
    services: EngineServices,
    execution_id: str,
    *,
    scope: str,
    metric: str,
    limit: float,
    actor_id: str,
    rationale: str,
) -> dict[str, Any]:
    """A person raises a limit of the run (recorded on its chain) so the run may continue."""
    from governed_harness.orchestration.budget import METRICS, SCOPES, effective_limit
    from governed_harness.orchestration.engine import RunEngine

    require_human_actor(actor_id, "raise a budget limit")
    config = services.resolved.project.budget
    if config is None:
        raise ConfigurationError("budget raise needs the budget section in project.yaml")
    if scope not in SCOPES or metric not in METRICS:
        raise ConfigurationError(
            f"scope must be one of {', '.join(SCOPES)} and metric one of {', '.join(METRICS)}"
        )
    if not rationale.strip():
        raise PolicyViolationError("raising a budget limit requires a rationale")
    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    raised = engine.results.raised_limits(execution)
    current = effective_limit(config, scope, metric, raised)
    if current is not None and limit <= current:
        raise PolicyViolationError(
            f"the new {scope} {metric} limit {limit:g} is not above the current {current:g}"
        )
    raised.setdefault(scope, {})[metric] = float(limit)
    engine.results.set_flag_json(f"budgetraise:{execution_id}", raised)
    services.events.append(
        execution_id,
        "budget.raised",
        {
            "scope": scope,
            "metric": metric,
            "previousLimit": current,
            "limit": float(limit),
            "rationale": rationale.strip(),
        },
        actor=Actor(actor_type=ActorType.HUMAN, actor_id=actor_id),
    )
    engine.anchor_chain(execution_id)
    return budget_state(services, execution_id)


def acceptance_state(services: EngineServices, execution_id: str) -> dict[str, Any]:
    """The acceptance tests proposed or frozen for a run (``verification.acceptanceTests``)."""
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    state = engine.results.acceptance.state(engine.get_execution(execution_id))
    if state is None:
        raise NotFoundError(f"run {execution_id} has no acceptance tests")
    return {"executionId": execution_id, **state}


def decide_acceptance(
    services: EngineServices,
    execution_id: str,
    *,
    decision: DecisionKind,
    digest: str,
    actor_id: str,
    rationale: str,
    continue_after: bool,
) -> dict[str, Any]:
    """Approve (write and freeze) or reject the proposed acceptance tests (a person only)."""
    from governed_harness.orchestration.engine import RunEngine

    require_human_actor(actor_id, "decide acceptance tests")
    engine = RunEngine(services)
    state = engine.results.acceptance.decide(
        engine.get_execution(execution_id),
        decision=decision,
        digest=digest,
        actor=Actor(actor_type=ActorType.HUMAN, actor_id=actor_id),
        rationale=rationale,
    )
    result: dict[str, Any] = {"executionId": execution_id, "acceptanceTests": state}
    if continue_after:
        result["execution"] = engine.continue_execution(execution_id).model_dump(
            mode="json", by_alias=True
        )
    else:
        engine.anchor_chain(execution_id)
    return result


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


def _off_or_value(value: Any) -> Any:
    """A setting as ``config validate`` shows it: ``off`` when absent, a section as JSON."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value if value is not None else "off"


def agent_results_summary(project: ProjectConfiguration) -> dict[str, Any]:
    """Effective agent-results settings for ``config validate``; ``off`` (or false) where a
    key is absent, which is the 1.0.0 behaviour."""
    intake = project.intake
    verification = project.verification
    review = project.review
    runtime = project.runtime
    governance = project.governance_settings

    off = _off_or_value
    return {
        "ambiguityReview": off(intake.ambiguity_review if intake else None),
        "validateAnswers": bool(intake and intake.validate_answers),
        "agentReview": off(review.agent_review if review else None),
        "structuredChanges": bool(review and review.structured_changes),
        "gateContract": bool(runtime.gate_contract),
        "reproduceFirst": bool(runtime.reproduce_first),
        "stopTheLine": off(governance.stop_the_line),
        "phasePermissions": bool(governance.phase_permissions),
        "checks": {
            name: (
                value.model_dump(mode="json", by_alias=True)
                if hasattr(value, "model_dump")
                else [item.model_dump(mode="json", by_alias=True) for item in value]
                if isinstance(value, tuple)
                else value
            )
            for name, value in (
                (field.alias or key, getattr(verification, key))
                for key, field in type(verification).model_fields.items()
                if key not in {"requirement_traceability", "output_parsers"}
            )
            if value is not None
        }
        if verification is not None
        else {},
        "planning": project.planning.model_dump(mode="json", by_alias=True)
        if project.planning
        else {"decomposition": "off"},
        "context": project.context.model_dump(mode="json", by_alias=True)
        if project.context
        else {"manifest": "off"},
        "budget": project.budget.model_dump(mode="json", by_alias=True) if project.budget else None,
        "memory": project.memory.model_dump(mode="json", by_alias=True)
        if project.memory
        else {"learnFromFindings": "off"},
        "agentRouting": project.agent_routing.model_dump(mode="json", by_alias=True)
        if project.agent_routing
        else {"mode": "fixed"},
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
