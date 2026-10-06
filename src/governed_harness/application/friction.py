"""Low friction for small changes (#58) in the application layer: ``harness do``, the
pre-authorised approval at the contract confirmation, batch decisions from the inbox, the
plan-approval checkpoint (#8) and the ``friction`` summary of ``harness config validate``."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.friction import (
    DEFAULT_FRICTION_TARGETS,
    FrictionConfig,
)
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, NotFoundError, PolicyViolationError
from governed_harness.domain.models import (
    AcceptanceCriterion,
    Actor,
    ClarificationRequest,
    Execution,
    Task,
)
from governed_harness.intake import task_digest

if TYPE_CHECKING:
    from governed_harness.configuration.models import ProjectConfiguration
    from governed_harness.orchestration.engine import EngineServices

MAX_TEXT = 16000


def friction_summary(project: ProjectConfiguration) -> dict[str, Any]:
    """The effective friction settings (absent keys resolve to the 1.0.0 behaviour)."""
    config = project.friction or FrictionConfig()
    lane = config.fast_lane
    verification = lane.verification if lane else None
    pre = config.pre_authorization
    return {
        "fastLane": {
            "mode": (lane.mode if lane else None) or "off",
            "skip": list(lane.skipped) if lane and lane.enabled else [],
            "affectedTestsFirst": bool(verification and verification.affected_tests_first),
            "parallel": bool(verification and verification.parallel),
            "cache": bool(verification and verification.cache),
        },
        "preAuthorization": {
            "mode": (pre.mode if pre else None) or "off",
            "defaultHours": pre.hours if pre and pre.enabled else None,
            "maxHours": pre.limit_hours if pre and pre.enabled else None,
        },
        "changeTypes": bool(config.change_types),
        "planApproval": config.plan_approval or "off",
        "targets": {
            size: target.model_dump(mode="json", by_alias=True)
            for size, target in sorted((config.targets or {}).items())
        }
        or {size: dict(values) for size, values in DEFAULT_FRICTION_TARGETS.items()},
        "targetsSource": "configured" if config.targets else "default",
    }


# ----- harness do ---------------------------------------------------------------------------------
def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return words[:40].strip("_") or "change"


def task_from_text(
    text: str,
    *,
    project_id: str,
    criteria: tuple[str, ...] = (),
    owned_paths: tuple[str, ...] = (),
    title: str | None = None,
    task_id: str | None = None,
) -> Task:
    """A task from one sentence, with sensible defaults: the text is the intent and, without
    ``criteria``, the acceptance criterion; the title is its first line."""
    body = text.strip()
    if not body:
        raise ConfigurationError("harness do needs the change to make, as text")
    if len(body) > MAX_TEXT:
        raise ConfigurationError(f"the text is longer than {MAX_TEXT} characters")
    first = body.splitlines()[0].strip()
    chosen_title = (title or first)[:300]
    identifier = task_id or f"task_do_{_slug(first)}_{secrets.token_hex(3)}"
    stated = tuple(item.strip() for item in criteria if item.strip()) or (body,)
    metadata: dict[str, Any] = {"source": "harness do"}
    if owned_paths:
        metadata["ownedPaths"] = list(dict.fromkeys(owned_paths))
    return Task(
        task_id=identifier,
        project_id=project_id,
        title=chosen_title,
        intent=body,
        acceptance_criteria=tuple(
            AcceptanceCriterion(criterion_id=f"ac_{index}", text=item)
            for index, item in enumerate(stated, start=1)
        ),
        metadata=metadata,
    )


def open_questions(services: EngineServices, execution: Execution) -> list[dict[str, Any]]:
    """The questions INTENT asked about the current revision of the run's task."""
    task = services.state.get("task", execution.task_id, Task)
    current = task_digest(task)
    requests = sorted(
        (
            item
            for item in services.state.list(
                "clarification_request", ClarificationRequest, execution_id=execution.execution_id
            )
            if item.task_digest == current
        ),
        key=lambda item: item.created_at,
    )
    if not requests:
        return []
    return [
        {"questionId": item.question_id, "text": item.text, "ruleId": item.rule_id}
        for item in requests[-1].questions
    ]


def preauthorize_run(
    services: EngineServices,
    execution: Execution,
    actor: Actor,
    *,
    hours: int | None,
    rationale: str,
    identity_source: str | None,
    with_contract: bool,
) -> dict[str, Any]:
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    record = engine.friction.preauthorize(
        execution,
        actor,
        hours=hours,
        rationale=rationale,
        identity_source=identity_source,
        with_contract=with_contract,
    )
    engine.anchor_chain(execution.execution_id)
    return record.model_dump(mode="json", by_alias=True)


def latest_intent_run(services: EngineServices, task_id: str) -> Execution:
    task = services.state.get("task", task_id, Task)
    runs = [
        item
        for item in services.state.list("execution", Execution, project_id=task.project_id)
        if item.task_id == task_id and item.current_phase is PhaseId.INTENT
    ]
    if not runs:
        raise NotFoundError(f"task {task_id} has no run in INTENT")
    return max(runs, key=lambda item: item.created_at)


# ----- batch decisions ----------------------------------------------------------------------------
@dataclass(frozen=True)
class BatchItem:
    """One decision of a batch: a run, the decision and the digest the person saw."""

    run: str
    decision: DecisionKind
    digest: str
    rationale: str | None = None


def parse_batch_item(value: str, decision: DecisionKind) -> BatchItem:
    """``RUN=DIGEST`` of ``--approve``, ``--reject`` or ``--request-changes``."""
    run, separator, digest = value.partition("=")
    if not separator or not run.strip() or not digest.strip().startswith("sha256:"):
        raise ConfigurationError(f"expected RUN=sha256:DIGEST, got {value!r}")
    return BatchItem(run.strip(), decision, digest.strip())


def load_batch_file(raw: Any) -> list[BatchItem]:
    """The decisions of a batch file: a list of ``run``, ``decision``, ``changeSetDigest`` and
    optional ``rationale`` (or the same under a top-level ``decisions`` key)."""
    if isinstance(raw, dict):
        raw = raw.get("decisions")
    if not isinstance(raw, list) or not raw:
        raise ConfigurationError("a batch file is a non-empty list of decisions")
    items = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ConfigurationError(f"decision {index} must be an object")
        try:
            decision = DecisionKind(str(entry.get("decision", "")).upper())
        except ValueError as error:
            raise ConfigurationError(f"decision {index}: unknown decision") from error
        run = str(entry.get("run") or entry.get("executionId") or "").strip()
        digest = str(entry.get("changeSetDigest") or entry.get("digest") or "").strip()
        if not run or not digest.startswith("sha256:"):
            raise ConfigurationError(f"decision {index} needs run and changeSetDigest")
        rationale = entry.get("rationale")
        items.append(BatchItem(run, decision, digest, str(rationale) if rationale else None))
    return items


def plan_state(services: EngineServices, execution_id: str) -> dict[str, Any] | None:
    """The plan waiting at (or past) the plan-approval checkpoint of a run."""
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    state = engine.friction.plan_approval_state(execution)
    if state is None:
        return None
    return {"executionId": execution_id, **state}


def decide_plan_checkpoint(
    services: EngineServices,
    execution_id: str,
    *,
    decision: DecisionKind,
    digest: str,
    actor: Actor,
    rationale: str,
    continue_after: bool,
) -> dict[str, Any]:
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    if execution.current_phase is not PhaseId.PLANNING:
        raise PolicyViolationError(
            f"run {execution_id} is in {execution.current_phase.value}, not PLANNING"
        )
    record = engine.friction.decide_plan(
        execution, decision=decision, digest=digest, actor=actor, rationale=rationale
    )
    result: dict[str, Any] = {"executionId": execution_id, "planApproval": record}
    if continue_after and decision is DecisionKind.APPROVE:
        result["execution"] = engine.continue_execution(execution_id).model_dump(
            mode="json", by_alias=True
        )
    else:
        engine.anchor_chain(execution_id)
        result["execution"] = engine.get_execution(execution_id).model_dump(
            mode="json", by_alias=True
        )
    return result


def run_summary(execution: Execution) -> dict[str, Any]:
    return {
        "executionId": execution.execution_id,
        "status": execution.status.value,
        "currentPhase": execution.current_phase.value,
        "changeSetDigest": execution.change_set_digest,
        "awaitingDecision": execution.current_phase is PhaseId.DECISION
        and execution.status is ResultStatus.BLOCKED,
    }


__all__ = [
    "BatchItem",
    "decide_plan_checkpoint",
    "friction_summary",
    "latest_intent_run",
    "load_batch_file",
    "open_questions",
    "parse_batch_item",
    "plan_state",
    "preauthorize_run",
    "run_summary",
    "task_from_text",
]
