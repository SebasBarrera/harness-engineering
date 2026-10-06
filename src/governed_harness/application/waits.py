"""The waits for a person before DECISION, as inbox entries (#73).

A run that stops before DECISION to wait for a person exits 6, and before #73 the inbox listed
only the clarification questions, the deferred verifications and the preflight among them: a plan
waiting at the plan-approval checkpoint, a proposed decomposition, proposed acceptance tests, the
architecture options or inferred layer rules and an operational contract to confirm were missing.
Each of them is an entry here with its kind, the digest the answer binds to and the command that
answers it. The entries read what the run recorded; nothing is evaluated or changed."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.models import Execution, Task, utc_now
from governed_harness.intake import task_digest
from governed_harness.orchestration.architecture import read_state as architecture_state
from governed_harness.orchestration.friction import PLAN_APPROVAL_FLAG
from governed_harness.orchestration.verification_checks import RISK_ACK_FLAG

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices

DECOMPOSITION_FLAG = "decomposition"
ACCEPTANCE_FLAG = "acceptance"
RATIONALE = '--rationale "..."'

Wait = Callable[["EngineServices", Execution, Task], dict[str, Any] | None]


def _flag(services: EngineServices, key: str) -> dict[str, Any] | None:
    raw = services.state.get_flag(key)
    if not raw:
        return None
    value = json.loads(raw)
    return value if isinstance(value, dict) else None


def _decide(command: str, run: str, digest: str, choice: str = "--decision APPROVE") -> str:
    return f"harness {command} decide --run {run} {choice} --digest {digest} {RATIONALE}"


def _plan(services: EngineServices, execution: Execution, task: Task) -> dict[str, Any] | None:
    """The plan-approval checkpoint (``friction.planApproval``) waits for a person."""
    if execution.current_phase is not PhaseId.PLANNING:
        return None
    state = _flag(services, f"{PLAN_APPROVAL_FLAG}:{execution.execution_id}")
    if not state or state.get("status") != "PENDING":
        return None
    digest = str(state.get("digest"))
    return {
        "kind": "plan",
        "digest": digest,
        "summary": "plan approval: " + "; ".join(str(item) for item in state.get("reasons") or []),
        "next": _decide("plan", execution.execution_id, digest),
    }


def _decomposition(
    services: EngineServices, execution: Execution, task: Task
) -> dict[str, Any] | None:
    """A decomposition PLANNING proposed (``planning.decomposition``) waits for a person."""
    if execution.current_phase is not PhaseId.PLANNING:
        return None
    state = _flag(services, f"{DECOMPOSITION_FLAG}:{execution.execution_id}")
    if not state or state.get("status") != "PROPOSED":
        return None
    if state.get("taskDigest") != task_digest(task):
        return None
    digest = str(state.get("digest"))
    return {
        "kind": "decomposition",
        "digest": digest,
        "summary": f"{len(state.get('subtasks') or [])} sub-task(s) proposed",
        "next": _decide("plan", execution.execution_id, digest),
    }


def _acceptance(
    services: EngineServices, execution: Execution, task: Task
) -> dict[str, Any] | None:
    """Acceptance tests SPECIFICATION proposed (``verification.acceptanceTests``) wait."""
    if execution.current_phase is not PhaseId.SPECIFICATION:
        return None
    state = _flag(services, f"{ACCEPTANCE_FLAG}:{execution.execution_id}")
    if not state or state.get("status") != "PROPOSED":
        return None
    digest = str(state.get("digest"))
    return {
        "kind": "acceptance",
        "digest": digest,
        "summary": f"{len(state.get('tests') or [])} acceptance test file(s) proposed",
        "next": _decide("acceptance", execution.execution_id, digest),
    }


def _architecture(
    services: EngineServices, execution: Execution, task: Task
) -> dict[str, Any] | None:
    """Architecture options (a new project, INTENT) or inferred layer rules (an existing
    project, DISCOVERY) wait for a person."""
    state = architecture_state(services.paths.harness_dir)
    if not state:
        return None
    status = state.get("status")
    digest = str(state.get("digest"))
    run = execution.execution_id
    if status == "OPTIONS" and execution.current_phase is PhaseId.INTENT:
        options = [str(item.get("id")) for item in state.get("options") or []]
        return {
            "kind": "architecture",
            "digest": digest,
            "summary": f"{len(options)} architecture option(s): {', '.join(options)}",
            "next": _decide("architecture", run, digest, "--option <id>"),
        }
    if status == "PROPOSED" and execution.current_phase is PhaseId.DISCOVERY:
        layers = (state.get("rules") or {}).get("layers") or []
        return {
            "kind": "architecture",
            "digest": digest,
            "summary": f"{len(layers)} inferred layer rule(s) to approve",
            "next": _decide("architecture", run, digest),
        }
    return None


def _contract(services: EngineServices, execution: Execution, task: Task) -> dict[str, Any] | None:
    """An operational contract waits for its confirmation (``intake.operationalContract:
    enforce``); a contract with items not settled waits for answers (a clarification) instead."""
    intake = services.resolved.project.intake
    if execution.current_phase is not PhaseId.INTENT or not intake:
        return None
    if intake.operational_contract != "enforce":
        return None
    current = _flag(services, f"contractcurrent:{execution.execution_id}")
    if not current or current.get("taskDigest") != task_digest(task):
        return None
    digest = str(current.get("digest"))
    confirmed = _flag(services, f"contractconfirmed:{execution.execution_id}")
    if confirmed and confirmed.get("digest") == digest:
        return None
    summary = json.loads(services.artifacts.get(str(current["ref"])))
    if summary.get("missing"):
        return None
    return {
        "kind": "contract",
        "digest": digest,
        "summary": "operational contract to confirm",
        "next": f"harness task confirm --task {task.task_id} --digest {digest}",
    }


WAITS: tuple[Wait, ...] = (_contract, _architecture, _acceptance, _decomposition, _plan)


def _base(execution: Execution, task: Task, now: datetime) -> dict[str, Any]:
    return {
        "executionId": execution.execution_id,
        "taskId": execution.task_id,
        "taskTitle": task.title,
        "waitingSince": execution.updated_at.isoformat(),
        "waitingHours": round((now - execution.updated_at).total_seconds() / 3600, 1),
    }


def waiting_entries(services: EngineServices) -> list[dict[str, Any]]:
    """The runs of the project blocked before DECISION on a wait for a person, one entry per
    wait (kind, digest, summary and the command that answers it)."""
    project_id = services.resolved.project.project_id
    now = utc_now()
    entries: list[dict[str, Any]] = []
    for execution in services.state.list("execution", Execution, project_id=project_id):
        if execution.status is not ResultStatus.BLOCKED:
            continue
        task = services.state.get("task", execution.task_id, Task)
        for wait in WAITS:
            entry = wait(services, execution, task)
            if entry is not None:
                entries.append({**_base(execution, task, now), **entry})
    return entries


def risk_acknowledgements(services: EngineServices, execution: Execution) -> list[str]:
    """The risk factors of the run's ChangeSet a decision must acknowledge
    (``verification.riskFactors`` with ``acknowledge``)."""
    raw = services.state.get_flag(
        f"{RISK_ACK_FLAG}:{execution.execution_id}:{execution.change_set_digest}"
    )
    value = json.loads(raw) if raw else None
    return [str(item) for item in value] if isinstance(value, list) else []


__all__ = ["risk_acknowledgements", "waiting_entries"]
