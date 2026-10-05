"""Pending-decision inbox and webhook notifications (``notifications.webhooks``).

A webhook receives a JSON ``POST`` when a run starts waiting for a human decision on a new
ChangeSet digest (``decision.pending``), when a run is closed, rejected or cancelled
(``run.finished``) and, if asked, when an exception is granted (``exception.granted``). The
payload carries identifiers, statuses and digests: never a rationale, a validator output, the
webhook URL or an environment value. Delivery is best effort with bounded retries: a failure is
recorded and never changes the run. Each outcome is kept as a ``notification`` record (outside
the run's event chain, so a notification after closure does not extend a closed chain)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from governed_harness import __version__
from governed_harness.configuration.models import WebhookConfig
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    ClarificationRequest,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    Task,
    utc_now,
)
from governed_harness.intake import task_digest
from governed_harness.orchestration.engine import EngineServices

RETRY_BASE_SECONDS = 0.5
RETRY_MAX_SECONDS = 5.0
_ERROR_CHARS = 200


def awaiting_decision(execution: Execution) -> bool:
    return execution.current_phase is PhaseId.DECISION and execution.status is ResultStatus.BLOCKED


def finished(services: EngineServices, execution: Execution) -> bool:
    if execution.status in {ResultStatus.PASSED, ResultStatus.CANCELLED}:
        return True
    if execution.status is ResultStatus.FAILED and execution.human_decision_id:
        decision = services.state.get("decision", execution.human_decision_id, HumanDecision)
        return decision.decision is DecisionKind.REJECT
    return False


def payload(
    services: EngineServices, execution: Execution, event: str, **extra: Any
) -> dict[str, Any]:
    gate = (
        services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
        if execution.gate_evaluation_id
        else None
    )
    task = services.state.get("task", execution.task_id, Task)
    body: dict[str, Any] = {
        "schemaVersion": "1.0",
        "event": event,
        "occurredAt": utc_now().isoformat(),
        "harnessVersion": __version__,
        "projectId": execution.project_id,
        "executionId": execution.execution_id,
        "taskId": execution.task_id,
        "taskTitle": task.title,
        "status": execution.status.value,
        "currentPhase": execution.current_phase.value,
        "changeSetDigest": execution.change_set_digest,
        "gateStatus": gate.status.value if gate else None,
        "next": f"harness review --run {execution.execution_id}",
    }
    body.update(extra)
    return body


def _target(hook: WebhookConfig) -> str:
    return f"env:{hook.url_env}" if hook.url_env else "url"


def _post(url: str, body: bytes, timeout: float) -> int:
    request = urllib.request.Request(  # noqa: S310 - the scheme is checked to be http(s)
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"governed-agent-harness/{__version__}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310
        return int(response.status)


def deliver(hook: WebhookConfig, body: dict[str, Any]) -> dict[str, Any]:
    """POST ``body`` with retries; returns the outcome without the URL."""
    url = hook.url if hook.url is not None else os.environ.get(hook.url_env or "", "")
    outcome: dict[str, Any] = {"target": _target(hook), "attempts": 0}
    if not url:
        return {
            **outcome,
            "status": "FAILED",
            "error": f"environment variable {hook.url_env} is not set",
        }
    if not url.startswith(("http://", "https://")):
        return {**outcome, "status": "FAILED", "error": "the webhook URL is not http(s)"}
    data = json.dumps(body, sort_keys=True).encode("utf-8")
    error = ""
    for attempt in range(hook.retries + 1):
        outcome["attempts"] = attempt + 1
        try:
            status = _post(url, data, hook.timeout_seconds)
            outcome["httpStatus"] = status
            if 200 <= status < 300:
                return {**outcome, "status": "DELIVERED"}
            error = f"HTTP {status}"
        except urllib.error.HTTPError as failure:
            outcome["httpStatus"] = failure.code
            error = f"HTTP {failure.code}"
            failure.close()
        except (urllib.error.URLError, OSError, ValueError) as failure:
            error = type(failure).__name__
        if attempt < hook.retries:
            time.sleep(min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2**attempt))
    return {**outcome, "status": "FAILED", "error": error[:_ERROR_CHARS]}


def notify(
    services: EngineServices, execution: Execution, event: str, **extra: Any
) -> list[dict[str, Any]]:
    hooks = [
        (index, hook)
        for index, hook in enumerate(services.resolved.project.webhooks)
        if event in hook.events
    ]
    if not hooks:
        return []
    body = payload(services, execution, event, **extra)
    outcomes = []
    for index, hook in hooks:
        outcome = {
            "notificationId": new_id("notification"),
            "executionId": execution.execution_id,
            "event": event,
            "webhook": index,
            "recordedAt": utc_now().isoformat(),
            **deliver(hook, body),
        }
        services.state.put(
            "notification",
            outcome["notificationId"],
            outcome,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        outcomes.append(outcome)
    return outcomes


def notify_transition(services: EngineServices, execution: Execution) -> list[dict[str, Any]]:
    """Send ``decision.pending`` once per ChangeSet digest and ``run.finished`` once per run."""
    if not services.resolved.project.webhooks:
        return []
    if awaiting_decision(execution) and execution.change_set_digest:
        key = f"notified:decision.pending:{execution.execution_id}:{execution.change_set_digest}"
        if services.state.get_flag(key) is None:
            services.state.set_flag(key, utc_now().isoformat())
            return notify(services, execution, "decision.pending")
    elif finished(services, execution):
        key = f"notified:run.finished:{execution.execution_id}"
        if services.state.get_flag(key) is None:
            services.state.set_flag(key, utc_now().isoformat())
            return notify(services, execution, "run.finished")
    return []


def _waiting_since(execution: Execution) -> datetime:
    return execution.updated_at


def inbox(services: EngineServices) -> list[dict[str, Any]]:
    """Runs of the project that wait for a person: a decision in DECISION, or answers to
    clarification questions in INTENT. Oldest first."""
    project_id = services.resolved.project.project_id
    names = services.resolved.effective_policies.get("findingBlockSeverities", ["HIGH", "CRITICAL"])
    blocking = {str(name) for name in names}
    entries: list[dict[str, Any]] = []
    executions = services.state.list("execution", Execution, project_id=project_id)
    requests = services.state.list(
        "clarification_request", ClarificationRequest, project_id=project_id
    )
    now = utc_now()
    for execution in executions:
        task = services.state.get("task", execution.task_id, Task)
        base = {
            "executionId": execution.execution_id,
            "taskId": execution.task_id,
            "taskTitle": task.title,
            "waitingSince": _waiting_since(execution).isoformat(),
            "waitingHours": round((now - _waiting_since(execution)).total_seconds() / 3600, 1),
        }
        if awaiting_decision(execution):
            gate = (
                services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
                if execution.gate_evaluation_id
                else None
            )
            ids = {
                ref.removeprefix("record://finding/")
                for ref in (gate.input_refs if gate else ())
                if ref.startswith("record://finding/")
            }
            blocking_count = sum(
                1
                for item in services.state.list(
                    "finding", Finding, execution_id=execution.execution_id
                )
                if item.finding_id in ids and item.severity.value in blocking
            )
            entries.append(
                {
                    **base,
                    "kind": "decision",
                    "gateStatus": gate.status.value if gate else None,
                    "changeSetDigest": execution.change_set_digest,
                    "blockingFindings": blocking_count,
                    "next": f"harness review --run {execution.execution_id}",
                }
            )
        elif execution.current_phase is PhaseId.INTENT and execution.status is ResultStatus.BLOCKED:
            current = task_digest(task)
            open_requests = [
                item
                for item in requests
                if item.task_id == task.task_id and item.task_digest == current
            ]
            if open_requests:
                entries.append(
                    {
                        **base,
                        "kind": "clarification",
                        "questions": len(open_requests[-1].questions),
                        "next": f"harness task questions --task {task.task_id}",
                    }
                )
    return sorted(entries, key=lambda item: item["waitingSince"])
