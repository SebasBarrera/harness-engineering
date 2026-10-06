"""Application helpers of the verification ladder and delivery hygiene (#55): what ``harness
verification``, ``harness task confirm``, ``harness evidence attach`` and ``harness config lint``
do, and the sections the decision brief and the inbox add."""

from __future__ import annotations

import mimetypes
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, NotFoundError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    CertificationRecord,
    ClarificationRequest,
    DeferredVerification,
    Execution,
    HumanAttachment,
    HumanDecision,
    Task,
    utc_now,
)
from governed_harness.intake import task_digest
from governed_harness.ladder.external import EvidenceFormatError, read_evidence
from governed_harness.ladder.instructions import lint_instructions

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices, RunEngine

MAX_ATTACHMENT_BYTES = 50_000_000
_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-ndjson")


# ----- harness verification show / decide ----------------------------------------------------
def verification_state(services: EngineServices, execution_id: str) -> dict[str, Any]:
    """The verification plan, the preflight, the certification, the deferred items and the
    manual checklist of a run."""
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    task = engine.run_task(execution)
    ladder = engine.ladder
    preflight = engine.results.flag_json(f"preflight:{execution_id}")
    plan: dict[str, Any] | None = None
    if isinstance(preflight, dict) and isinstance(preflight.get("ref"), str):
        import json

        plan = json.loads(services.artifacts.get(preflight["ref"]))
    certification = ladder.latest_certification(execution_id, execution.change_set_digest)
    return {
        "executionId": execution_id,
        "status": execution.status.value,
        "currentPhase": execution.current_phase.value,
        "changeSetDigest": execution.change_set_digest,
        "ladder": {
            "mode": ladder.config.mode if ladder.config else "off",
            "defaultLevel": ladder.config.required_default.value if ladder.config else None,
        },
        "preflight": plan,
        "preflightDecision": ladder.preflight_decision(execution),
        "certification": certification.model_dump(mode="json", by_alias=True)
        if certification
        else None,
        "deferred": [
            item.model_dump(mode="json", by_alias=True)
            for item in ladder.deferred_items(execution, None)
        ],
        "checklist": ladder.manual_items(task),
        "contract": contract_state(services, execution),
        "interruptions": ladder.intake.interruptions(execution),
    }


def decide_preflight(
    services: EngineServices,
    execution_id: str,
    *,
    actor: Actor,
    rationale: str,
) -> dict[str, Any]:
    """A person decides to continue a run whose preflight is UNAVAILABLE without the rungs it
    cannot reach: those criteria are never certified (``WAIVED``) and the unavailable probes are
    not run. The decision is recorded on the run's chain."""
    from governed_harness.orchestration.engine import RunEngine

    if actor.actor_type is not ActorType.HUMAN:
        raise PolicyViolationError("only a person can decide to continue uncertified")
    if not rationale.strip():
        raise PolicyViolationError("continuing uncertified requires a rationale")
    engine = RunEngine(services)
    execution = engine.get_execution(execution_id)
    preflight = engine.results.flag_json(f"preflight:{execution_id}")
    if (
        execution.current_phase is not PhaseId.PLANNING
        or not isinstance(preflight, dict)
        or preflight.get("status") != "UNAVAILABLE"
    ):
        raise PolicyViolationError(
            f"run {execution_id} is not waiting on an UNAVAILABLE preflight "
            f"({execution.status.value} in {execution.current_phase.value})"
        )
    import json

    plan = json.loads(services.artifacts.get(preflight["ref"]))
    criteria = sorted(
        item["criterionId"]
        for item in plan.get("criteria", [])
        if item.get("route") == "unreachable" and item.get("declared")
    )
    probes = sorted(
        probe_id
        for probe_id, value in (plan.get("probes") or {}).items()
        if value.get("readiness") == "UNAVAILABLE"
    )
    decision = {
        "decisionId": new_id("preflightdecision"),
        "decision": "CONTINUE_UNCERTIFIED",
        "actorId": actor.actor_id,
        "rationale": rationale.strip(),
        "criteria": criteria,
        "probes": probes,
        "preflightRef": preflight["ref"],
        "decidedAt": utc_now().isoformat(),
    }
    engine.results.set_flag_json(f"preflightdecision:{execution_id}", decision)
    services.events.append(execution_id, "verification.preflight.decided", decision, actor=actor)
    engine.anchor_chain(execution_id)
    return decision


# ----- the operational contract ----------------------------------------------------------------
def contract_state(services: EngineServices, execution: Execution) -> dict[str, Any] | None:
    import json

    raw = services.state.get_flag(f"contractcurrent:{execution.execution_id}")
    if not raw:
        return None
    current = json.loads(raw)
    summary = json.loads(services.artifacts.get(current["ref"]))
    confirmed_raw = services.state.get_flag(f"contractconfirmed:{execution.execution_id}")
    confirmed = json.loads(confirmed_raw) if confirmed_raw else None
    return {
        **summary,
        "confirmed": bool(confirmed and confirmed.get("digest") == summary["digest"]),
        "confirmedBy": confirmed.get("actorId") if confirmed else None,
    }


def confirm_contract(
    services: EngineServices, *, task_id: str, digest: str, actor: Actor
) -> dict[str, Any]:
    """A person confirms the operational contract of a task's current revision, bound to its
    digest; INTENT under ``operationalContract: enforce`` then passes."""
    import json

    from governed_harness.orchestration.engine import RunEngine

    if actor.actor_type is not ActorType.HUMAN:
        raise PolicyViolationError("only a person can confirm the operational contract")
    engine = RunEngine(services)
    task = engine.get_task(task_id)
    runs = [
        item
        for item in services.state.list("execution", Execution, project_id=task.project_id)
        if item.task_id == task_id and item.current_phase is PhaseId.INTENT
    ]
    if not runs:
        raise NotFoundError(f"task {task_id} has no run in INTENT")
    execution = max(runs, key=lambda item: item.created_at)
    raw = services.state.get_flag(f"contractcurrent:{execution.execution_id}")
    if not raw:
        raise NotFoundError(f"run {execution.execution_id} has no operational contract yet")
    current = json.loads(raw)
    if current.get("taskDigest") != task_digest(engine.run_task(execution)):
        raise PolicyViolationError(
            "the task changed after its contract was summarized; run harness run continue first"
        )
    if current.get("digest") != digest:
        raise PolicyViolationError(
            f"the digest does not match the current contract ({current.get('digest')})"
        )
    record = {"digest": digest, "actorId": actor.actor_id, "confirmedAt": utc_now().isoformat()}
    engine.results.set_flag_json(f"contractconfirmed:{execution.execution_id}", record)
    services.events.append(
        execution.execution_id,
        "contract.confirmed",
        {"digest": digest, "taskDigest": current.get("taskDigest")},
        actor=actor,
    )
    engine.anchor_chain(execution.execution_id)
    return {
        "executionId": execution.execution_id,
        **record,
        "next": f"harness run continue --run {execution.execution_id}",
    }


# ----- harness evidence attach -----------------------------------------------------------------
def _media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def attach_evidence(
    services: EngineServices,
    *,
    file: Path,
    actor: Actor,
    execution_id: str | None = None,
    task_id: str | None = None,
    item: str | None = None,
    manual: bool = False,
    evidence_format: str = "auto",
    case: str | None = None,
    commit: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Attach evidence: close a deferred verification of a run with a JUnit, SARIF or CI
    status report, or store a person's attachment (``manual``) bound to a run's ChangeSet and
    a checklist item, or to a task revision as intake context."""
    from governed_harness.orchestration.engine import RunEngine

    if actor.actor_type is not ActorType.HUMAN:
        raise PolicyViolationError("only a person attaches evidence")
    if (execution_id is None) == (task_id is None):
        raise ConfigurationError("attach to a run (--run) or to a task (--task), not both")
    if not file.is_file():
        raise NotFoundError(f"file not found: {file}")
    data = file.read_bytes()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ConfigurationError(f"the file is larger than {MAX_ATTACHMENT_BYTES} bytes")
    engine = RunEngine(services)
    if task_id is not None:
        if not manual:
            raise ConfigurationError("a task takes a person's attachment: use --manual")
        task = engine.get_task(task_id)
        return _attach_manual(services, engine, task, None, file, data, actor, item, note)
    assert execution_id is not None
    execution = engine.get_execution(execution_id)
    task = engine.run_task(execution)
    if manual:
        return _attach_manual(services, engine, task, execution, file, data, actor, item, note)
    if item is None:
        raise ConfigurationError(
            "name the deferred item to close with --item (for example D-ac_e2e)"
        )
    return _close_deferred(
        services, engine, execution, item, file, data, actor, evidence_format, case, commit
    )


def _attach_manual(
    services: EngineServices,
    engine: RunEngine,
    task: Task,
    execution: Execution | None,
    file: Path,
    data: bytes,
    actor: Actor,
    item: str | None,
    note: str | None,
) -> dict[str, Any]:
    media = _media_type(file)
    if item is not None:
        known = {entry["itemId"] for entry in engine.ladder.manual_items(task)}
        if item not in known:
            raise ConfigurationError(
                f"unknown checklist item {item}; the task's items are "
                + (", ".join(sorted(known)) or "none")
            )
    ref = services.artifacts.put(
        data,
        media_type=media,
        metadata={"kind": "human-attachment", "fileName": file.name},
        redact=media.startswith(_TEXT_TYPES),
    )
    attachment = HumanAttachment(
        attachment_id=new_id("attachment"),
        project_id=task.project_id,
        task_id=task.task_id,
        task_digest=task_digest(task) if execution is None else None,
        execution_id=execution.execution_id if execution else None,
        change_set_digest=execution.change_set_digest if execution else None,
        item_id=item,
        artifact_ref=ref.uri,
        digest=ref.digest,
        media_type=media,
        size_bytes=ref.size_bytes,
        file_name=file.name,
        note=note,
        actor=actor,
    )
    services.state.put(
        "human_attachment",
        attachment.attachment_id,
        attachment,
        execution_id=execution.execution_id if execution else None,
        project_id=task.project_id,
    )
    if execution is not None:
        engine._record_evidence(
            execution,
            execution.current_phase,
            _evidence_kind(),
            ref,
            f"Attachment {file.name}" + (f" for {item}" if item else ""),
            supports=(item,) if item else (),
        )
        services.events.append(
            execution.execution_id,
            "evidence.attached",
            attachment.model_dump(mode="json", by_alias=True),
            actor=actor,
        )
        engine.anchor_chain(execution.execution_id)
    return attachment.model_dump(mode="json", by_alias=True)


def _evidence_kind() -> Any:
    from governed_harness.domain.enums import EvidenceKind

    return EvidenceKind.HUMAN_DECISION


def _close_deferred(
    services: EngineServices,
    engine: RunEngine,
    execution: Execution,
    item_id: str,
    file: Path,
    data: bytes,
    actor: Actor,
    evidence_format: str,
    case: str | None,
    commit: str | None,
) -> dict[str, Any]:
    items = [
        entry for entry in engine.ladder.deferred_items(execution, None) if entry.item_id == item_id
    ]
    if not items:
        raise NotFoundError(f"run {execution.execution_id} has no deferred item {item_id}")
    current = [entry for entry in items if entry.change_set_digest == execution.change_set_digest]
    deferred = max(current or items, key=lambda entry: entry.created_at)
    if deferred.status == "EXPIRED":
        _expire(services, execution, deferred)
        raise PolicyViolationError(
            f"deferred item {item_id} expired at {deferred.expires_at.isoformat()}; the "
            "criterion is not certified. Run the task again to verify it"
        )
    if deferred.status != "PENDING":
        raise PolicyViolationError(f"deferred item {item_id} is already {deferred.status}")
    try:
        verdict = read_evidence(data, evidence_format, case)
    except EvidenceFormatError as error:
        raise ConfigurationError(f"{file.name}: {error}") from error
    claimed = commit or verdict.commit
    if (
        deferred.commit
        and claimed
        and not deferred.commit.startswith(claimed)
        and not claimed.startswith(deferred.commit)
    ):
        raise PolicyViolationError(
            f"the evidence is about commit {claimed}, the item is bound to {deferred.commit}"
        )
    ref = services.artifacts.put(
        data,
        media_type="application/xml" if verdict.kind == "junit" else "application/json",
        metadata={"kind": f"deferred-evidence-{verdict.kind}", "fileName": file.name},
    )
    closed = deferred.model_copy(
        update={
            "status": "PASSED" if verdict.passed else "FAILED",
            "closed_at": utc_now(),
            "evidence_kind": verdict.kind,
            "evidence_ref": ref.uri,
            "evidence_digest": ref.digest,
            "summary": verdict.summary,
            "closed_by": actor,
            "commit": deferred.commit or claimed,
        }
    )
    services.state.put(
        "deferred_verification",
        closed.deferred_id,
        closed,
        execution_id=execution.execution_id,
        project_id=execution.project_id,
    )
    from governed_harness.domain.enums import EvidenceKind

    engine._record_evidence(
        execution,
        PhaseId.CLOSURE if execution.status is ResultStatus.PASSED else execution.current_phase,
        EvidenceKind.TEST_REPORT,
        ref,
        f"Deferred {item_id}: {verdict.summary}",
        supports=(deferred.criterion_id,),
    )
    services.events.append(
        execution.execution_id,
        "verification.deferred.closed",
        {
            "deferredId": closed.deferred_id,
            "itemId": item_id,
            "status": closed.status,
            "evidenceKind": verdict.kind,
            "evidenceRef": ref.uri,
            "summary": verdict.summary,
            "commit": closed.commit,
        },
        actor=actor,
    )
    certification = engine.ladder.after_evidence(engine.get_execution(execution.execution_id))
    engine.anchor_chain(execution.execution_id)
    return {
        "deferred": closed.model_dump(mode="json", by_alias=True),
        "certification": certification.status if certification else None,
    }


def _expire(services: EngineServices, execution: Execution, deferred: DeferredVerification) -> None:
    stored = services.state.get("deferred_verification", deferred.deferred_id, DeferredVerification)
    if stored.status != "PENDING":
        return
    services.state.put(
        "deferred_verification",
        stored.deferred_id,
        stored.model_copy(update={"status": "EXPIRED"}),
        execution_id=execution.execution_id,
        project_id=execution.project_id,
    )
    services.events.append(
        execution.execution_id,
        "verification.deferred.expired",
        {"deferredId": stored.deferred_id, "itemId": stored.item_id},
    )


# ----- the brief and the inbox -----------------------------------------------------------------
def brief_sections(services: EngineServices, execution: Execution) -> dict[str, Any]:
    """The sections the ladder settings add to the decision brief; empty for a run that
    recorded none of them, so the brief of other runs keeps its form."""
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    ladder = engine.ladder
    if not ladder.active:
        return {}
    sections: dict[str, Any] = {}
    certification = ladder.latest_certification(execution.execution_id, execution.change_set_digest)
    if certification is not None:
        sections["certification"] = {
            "status": certification.status,
            "trigger": certification.trigger,
            "changeSetDigest": certification.change_set_digest,
            "criteria": [
                {
                    "criterionId": item.criterion_id,
                    "required": item.required.value,
                    "achieved": item.achieved.value if item.achieved else None,
                    "declared": item.declared,
                    "status": item.status,
                    "reason": item.reason,
                    "evidence": [entry.source for entry in item.evidence],
                    "pending": list(item.pending),
                }
                for item in certification.criteria
            ],
        }
    preflight = engine.results.flag_json(f"preflight:{execution.execution_id}")
    if isinstance(preflight, dict):
        sections["preflight"] = {
            "status": preflight.get("status"),
            "reasons": preflight.get("reasons") or [],
            "decision": ladder.preflight_decision(execution),
        }
    deferred = ladder.deferred_items(execution, execution.change_set_digest)
    if deferred:
        sections["deferred"] = [
            {
                "itemId": item.item_id,
                "criterionId": item.criterion_id,
                "where": item.where,
                "status": item.status,
                "commit": item.commit,
                "expiresAt": item.expires_at.isoformat(),
                "next": f"harness evidence attach --run {execution.execution_id} --item "
                f"{item.item_id} --file REPORT",
            }
            for item in deferred
        ]
    task = engine.run_task(execution)
    checklist = ladder.manual_items(task)
    if checklist:
        decisions = [
            item
            for item in services.state.list(
                "decision", HumanDecision, execution_id=execution.execution_id
            )
            if item.change_set_digest == execution.change_set_digest
        ]
        ticked = (
            set(max(decisions, key=lambda item: item.decided_at).checked_items)
            if decisions
            else set()
        )
        attachments = services.state.list(
            "human_attachment", HumanAttachment, execution_id=execution.execution_id
        )
        sections["checklist"] = [
            {
                **item,
                "checked": item["itemId"] in ticked,
                "attachments": [
                    entry.file_name for entry in attachments if entry.item_id == item["itemId"]
                ],
            }
            for item in checklist
        ]
    contract = contract_state(services, execution)
    if contract is not None:
        sections["contract"] = contract
    interruptions = ladder.intake.interruptions(execution)
    if interruptions is not None:
        sections["interruptions"] = interruptions
    return sections


def not_verified_lines(sections: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    certification = sections.get("certification")
    if certification:
        for item in certification["criteria"]:
            if item["status"] != "CERTIFIED":
                lines.append(
                    f"criterion {item['criterionId']} is {item['status']} on the ladder: "
                    f"{item['reason']}"
                )
    for item in sections.get("checklist") or []:
        if not item["checked"]:
            lines.append(f"checklist item {item['itemId']} needs a person: {item['text']}")
    return lines


def inbox_entries(services: EngineServices) -> list[dict[str, Any]]:
    """Deferred verifications waiting for evidence (and the ones that expired), and runs whose
    preflight waits for a person."""
    project_id = services.resolved.project.project_id
    now = utc_now()
    entries: list[dict[str, Any]] = []
    for item in services.state.list(
        "deferred_verification", DeferredVerification, project_id=project_id
    ):
        if item.status != "PENDING":
            continue
        expired = item.expires_at <= now
        execution = services.state.get("execution", item.execution_id, Execution)
        if item.change_set_digest != execution.change_set_digest:
            continue
        entries.append(
            {
                "executionId": item.execution_id,
                "taskId": item.task_id,
                "taskTitle": services.state.get("task", item.task_id, Task).title,
                "kind": "deferred",
                "itemId": item.item_id,
                "where": item.where,
                "status": "EXPIRED" if expired else "PENDING",
                "warning": f"expired at {item.expires_at.isoformat()}"
                if expired
                else (
                    f"expires within {(item.expires_at - now).days + 1} day(s)"
                    if item.expires_at - now <= timedelta(days=2)
                    else None
                ),
                "waitingSince": item.created_at.isoformat(),
                "waitingHours": round((now - item.created_at).total_seconds() / 3600, 1),
                "next": f"harness evidence attach --run {item.execution_id} --item {item.item_id} "
                "--file REPORT",
            }
        )
    for execution in services.state.list("execution", Execution, project_id=project_id):
        if (
            execution.current_phase is not PhaseId.PLANNING
            or execution.status is not ResultStatus.BLOCKED
        ):
            continue
        raw = services.state.get_flag(f"preflight:{execution.execution_id}")
        if not raw or '"UNAVAILABLE"' not in raw:
            continue
        if services.state.get_flag(f"preflightdecision:{execution.execution_id}"):
            continue
        entries.append(
            {
                "executionId": execution.execution_id,
                "taskId": execution.task_id,
                "taskTitle": services.state.get("task", execution.task_id, Task).title,
                "kind": "preflight",
                "waitingSince": execution.updated_at.isoformat(),
                "waitingHours": round((now - execution.updated_at).total_seconds() / 3600, 1),
                "next": f"harness verification show --run {execution.execution_id}",
            }
        )
    return entries


def open_contract_requests(services: EngineServices, task: Task) -> list[ClarificationRequest]:
    digest = task_digest(task)
    return [
        item
        for item in services.state.list(
            "clarification_request", ClarificationRequest, project_id=task.project_id
        )
        if item.task_id == task.task_id and item.task_digest == digest and item.contract
    ]


# ----- harness config lint ------------------------------------------------------------------
def config_lint(services_or_root: Any) -> dict[str, Any]:
    """Contradictions between the harness configuration and the agent instruction files."""
    from governed_harness.configuration.ladder import DEFAULT_INSTRUCTION_FILES

    resolved = services_or_root
    project = resolved.project
    instructions = project.instructions
    files = list(
        instructions.files if instructions and instructions.files else DEFAULT_INSTRUCTION_FILES
    )
    precedence = list(
        instructions.precedence
        if instructions and instructions.precedence
        else ("harness", *DEFAULT_INSTRUCTION_FILES)
    )
    verification = project.verification
    coverage = (
        verification.test_quality.diff_coverage
        if verification and verification.test_quality and verification.test_quality.diff_coverage
        else None
    )
    report = lint_instructions(
        resolved.workspace_root, files, precedence, coverage_threshold=coverage
    )
    return {"projectId": project.project_id, "workspace": str(resolved.workspace_root), **report}


def certification_of(services: EngineServices, execution_id: str) -> CertificationRecord | None:
    records = services.state.list("certification", CertificationRecord, execution_id=execution_id)
    return max(records, key=lambda item: item.created_at) if records else None


__all__ = [
    "attach_evidence",
    "brief_sections",
    "certification_of",
    "config_lint",
    "confirm_contract",
    "contract_state",
    "decide_preflight",
    "inbox_entries",
    "not_verified_lines",
    "verification_state",
]
