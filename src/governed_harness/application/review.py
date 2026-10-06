"""The decision brief: everything a person needs to decide on one ChangeSet digest.

Built only from recorded data by fixed rules (no generated text): what was asked, what changed,
the risks with their location, what was verified on which digest and what was not, active
exceptions, retries and corrections, and what changed since the last decision. It reads the
record and never writes to it."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from governed_harness.domain.enums import FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import (
    AgentSelfReport,
    ChangeSet,
    ComponentProvenance,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Task,
    ValidationResult,
)
from governed_harness.orchestration.engine import EngineServices
from governed_harness.reporting.fingerprint import finding_fingerprint
from governed_harness.validators import TRACEABILITY_VALIDATOR_ID

BRIEF_SCHEMA_VERSION = "1.0"
_MAX_DIFF_BYTES = 200_000


def location_text(finding: Finding) -> str:
    location = finding.location
    if location is None or not location.path:
        return "-"
    if location.start_line:
        return f"{location.path}:{location.start_line}"
    return location.path


def latest_validations(validations: list[ValidationResult], digest: str) -> list[ValidationResult]:
    """The latest attempt of each validator on ``digest`` (the gate's rule)."""
    latest: dict[str, ValidationResult] = {}
    for item in validations:
        if item.change_set_digest != digest:
            continue
        previous = latest.get(item.validator_id)
        if previous is None or item.finished_at >= previous.finished_at:
            latest[item.validator_id] = item
    return sorted(latest.values(), key=lambda item: item.validator_id)


def blocking_severities(services: EngineServices) -> set[FindingSeverity]:
    names = services.resolved.effective_policies.get("findingBlockSeverities", ["HIGH", "CRITICAL"])
    return {FindingSeverity(str(name)) for name in names}


def _finding_entry(finding: Finding, blocking: set[FindingSeverity]) -> dict[str, Any]:
    return {
        "findingId": finding.finding_id,
        "severity": finding.severity.value,
        "ruleId": finding.rule_id,
        "validatorId": finding.validator_id,
        "location": location_text(finding),
        "message": finding.message,
        "blocking": finding.severity in blocking,
        "fingerprint": finding_fingerprint(finding),
        "recommendation": finding.recommendation,
    }


def _reason(code: str, findings: dict[str, Finding]) -> str:
    if code.startswith("BLOCKING_FINDING_"):
        finding = findings.get(code.removeprefix("BLOCKING_FINDING_"))
        if finding is None:
            return "a blocking finding"
        return (
            f"{finding.severity.value} finding {finding.rule_id} at {location_text(finding)}: "
            f"{finding.message}"
        )
    if code.startswith("EXCEPTION_APPLIED_"):
        return f"findings covered by exception {code.removeprefix('EXCEPTION_APPLIED_')}"
    if code == "ALL_MANDATORY_VALIDATIONS_PASSED":
        return "every mandatory validator passed and no finding blocks"
    if code == "NO_MANDATORY_VALIDATIONS":
        return "no mandatory validator ran: nothing establishes the change works"
    for status in ResultStatus:
        suffix = f"_{status.value}"
        if code.endswith(suffix):
            return f"mandatory validator {code.removesuffix(suffix)} is {status.value}"
    return code


def _traceability(
    services: EngineServices, validations: list[ValidationResult]
) -> dict[str, Any] | None:
    for validation in validations:
        if validation.validator_id != TRACEABILITY_VALIDATOR_ID:
            continue
        for ref in validation.evidence_refs:
            try:
                report = json.loads(services.artifacts.get(ref))
            except (OSError, ValueError):
                continue
            if isinstance(report, dict) and "requirements" in report:
                return report
    return None


def _delta(
    services: EngineServices,
    decisions: list[HumanDecision],
    current: ChangeSet | None,
    current_findings: list[Finding],
    blocking: set[FindingSeverity],
) -> dict[str, Any] | None:
    if not decisions or current is None:
        return None
    last = decisions[-1]
    since = {
        "decisionId": last.decision_id,
        "decision": last.decision.value,
        "actorId": last.actor.actor_id,
        "decidedAt": last.decided_at.isoformat(),
        "changeSetDigest": last.change_set_digest,
    }
    if last.change_set_digest == current.digest:
        return {"sinceDecision": since, "unchanged": True}
    change_sets = services.state.list("change_set", ChangeSet, execution_id=current.execution_id)
    previous = next(
        (item for item in reversed(change_sets) if item.digest == last.change_set_digest), None
    )
    before = {item.path: item for item in previous.files} if previous else {}
    after = {item.path: item for item in current.files}
    files = {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(
            path
            for path in set(after) & set(before)
            if (after[path].after_digest, after[path].status)
            != (before[path].after_digest, before[path].status)
        ),
    }
    old_findings: list[Finding] = []
    try:
        gate = services.state.get("gate", last.gate_evaluation_id, GateEvaluation)
        ids = {
            ref.removeprefix("record://finding/")
            for ref in gate.input_refs
            if ref.startswith("record://finding/")
        }
        old_findings = [
            item
            for item in services.state.list("finding", Finding, execution_id=current.execution_id)
            if item.finding_id in ids
        ]
    except Exception:  # noqa: BLE001 - a missing gate only removes the finding delta
        old_findings = []
    old = {finding_fingerprint(item): item for item in old_findings}
    new = {finding_fingerprint(item): item for item in current_findings}
    return {
        "sinceDecision": since,
        "unchanged": False,
        "files": files,
        "findingsResolved": [
            _finding_entry(old[key], blocking) for key in sorted(set(old) - set(new))
        ],
        "findingsNew": [_finding_entry(new[key], blocking) for key in sorted(set(new) - set(old))],
    }


def _next(execution: Execution, awaiting: bool) -> list[str]:
    run_id = execution.execution_id
    if awaiting and execution.change_set_digest:
        return [
            f"harness gate decide --run {run_id}   (interactive on a terminal)",
            f"harness gate decide --run {run_id} --decision APPROVE "
            f"--change-set-digest {execution.change_set_digest} --rationale '...'",
        ]
    if execution.status is ResultStatus.PASSED:
        return [f"harness trace --run {run_id} --format markdown"]
    if execution.status is ResultStatus.CANCELLED:
        return []
    return [
        f"harness run continue --run {run_id}   (after fixing the cause)",
        f"harness run cancel --run {run_id}",
    ]


def build_brief(
    services: EngineServices,
    execution_id: str,
    *,
    include_diff: bool = False,
    exceptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = services.state
    execution = state.get("execution", execution_id, Execution)
    task = state.get("task", execution.task_id, Task)
    blocking = blocking_severities(services)
    digest = execution.change_set_digest
    change_sets = state.list("change_set", ChangeSet, execution_id=execution_id)
    current = next((item for item in reversed(change_sets) if item.digest == digest), None)
    validations = state.list("validation", ValidationResult, execution_id=execution_id)
    findings = state.list("finding", Finding, execution_id=execution_id)
    by_id = {item.finding_id: item for item in findings}
    current_validations = latest_validations(validations, digest) if digest else []
    current_ids = {fid for item in current_validations for fid in item.finding_ids}
    current_findings = [by_id[fid] for fid in sorted(current_ids) if fid in by_id]
    gate = (
        state.get("gate", execution.gate_evaluation_id, GateEvaluation)
        if execution.gate_evaluation_id
        else None
    )
    decisions = sorted(
        state.list("decision", HumanDecision, execution_id=execution_id),
        key=lambda item: item.decided_at,
    )
    phases = state.list("phase", PhaseExecution, execution_id=execution_id)
    events = services.events.list(execution_id)
    awaiting = execution.current_phase is PhaseId.DECISION and execution.status in {
        ResultStatus.BLOCKED,
        ResultStatus.PENDING,
    }

    changed: dict[str, Any] = {"changeSetDigest": digest, "files": [], "totals": None}
    if current is not None:
        changed["files"] = [
            {
                "path": item.path,
                "status": item.status,
                "additions": item.additions,
                "deletions": item.deletions,
            }
            for item in current.files
        ]
        changed["totals"] = {
            "files": len(current.files),
            "additions": sum(item.additions for item in current.files),
            "deletions": sum(item.deletions for item in current.files),
        }
        changed["diffRef"] = current.diff_ref
        if include_diff:
            data = services.artifacts.get(current.diff_ref)
            changed["diff"] = data[:_MAX_DIFF_BYTES].decode("utf-8", "replace")
            changed["diffTruncated"] = len(data) > _MAX_DIFF_BYTES

    report = _traceability(services, current_validations)
    verified = {
        "changeSetDigest": digest,
        "validations": [
            {
                "validatorId": item.validator_id,
                "mandatory": item.mandatory,
                "status": item.status.value,
                "summary": item.summary,
                "attempts": sum(
                    1
                    for other in validations
                    if other.validator_id == item.validator_id and other.change_set_digest == digest
                ),
            }
            for item in current_validations
        ],
        "requirements": [
            {
                "requirementId": item["requirementId"],
                "traced": item["traced"],
                "tests": [test["nodeId"] for test in item.get("tests", [])],
            }
            for item in (report or {}).get("requirements", [])
        ],
    }
    not_verified: list[str] = []
    for item in current_validations:
        if item.status is ResultStatus.PASSED:
            continue
        if item.status in {ResultStatus.NOT_APPLICABLE, ResultStatus.SKIPPED}:
            not_verified.append(f"{item.validator_id} did not run: {item.summary}")
        elif not item.mandatory:
            not_verified.append(
                f"optional {item.validator_id} is {item.status.value} (no effect on the gate): "
                f"{item.summary}"
            )
    for item in (report or {}).get("requirements", []):
        if not item["traced"]:
            not_verified.append(f"requirement {item['requirementId']} has no test that names it")
    for requirement_id in (report or {}).get("skippedRequirementIds", []):
        not_verified.append(f"requirement {requirement_id} was not checked for a test")
    if report is None and task.requirements:
        not_verified.append(
            "requirement traceability did not run: no check that a test names each requirement"
        )
    if task.acceptance_criteria:
        not_verified.append(
            "acceptance criteria are not executed one by one: the evidence is the validators "
            "and findings above"
        )
    if any(item.validator_id == "review.independent" for item in current_validations):
        not_verified.append(
            "INDEPENDENT_REVIEW is a deterministic rule check, not a review by another person"
        )

    phase_counts = Counter(item.phase_id for item in phases)
    corrections = [item for item in events if item.event_type == "correction.authorized"]
    passed_after_retry = sorted(
        item.validator_id
        for item in current_validations
        if item.status is ResultStatus.PASSED
        and any(
            other.validator_id == item.validator_id
            and other.change_set_digest == digest
            and other.status is not ResultStatus.PASSED
            for other in validations
        )
    )
    history = {
        "implementationAttempts": phase_counts.get(PhaseId.IMPLEMENTATION, 0),
        "verificationAttempts": phase_counts.get(PhaseId.VERIFICATION, 0),
        "automaticCorrections": sum(
            1 for item in corrections if item.payload.get("trigger") == "VERIFICATION_FAILED"
        ),
        "requestedChanges": sum(
            1
            for item in corrections
            if item.payload.get("trigger") not in {"VERIFICATION_FAILED", "REVIEW_FINDINGS"}
        ),
        "providerRetries": sum(
            1 for item in events if item.event_type == "agent.invocation.retried"
        ),
        "passedAfterRetry": passed_after_retry,
        "supersededFindings": len(findings) - len(current_findings),
    }

    risks = sorted(
        (_finding_entry(item, blocking) for item in current_findings),
        key=lambda item: (
            not item["blocking"],
            -list(FindingSeverity).index(FindingSeverity(item["severity"])),
            item["location"],
        ),
    )
    review_corrections = sum(
        1 for item in corrections if item.payload.get("trigger") == "REVIEW_FINDINGS"
    )
    if review_corrections:
        history["reviewCorrections"] = review_corrections
    if any(item.validator_id == "review.agent" for item in current_validations):
        not_verified.append(
            "a second agent reviewed the ChangeSet (review.agentReview); it is not a person"
        )
    not_verified.extend(_assumption_notes(task))
    agent_results = _agent_results_section(services, execution, current_findings)
    brief: dict[str, Any] = {
        "schemaVersion": BRIEF_SCHEMA_VERSION,
        "run": {
            "executionId": execution.execution_id,
            "taskId": execution.task_id,
            "status": execution.status.value,
            "currentPhase": execution.current_phase.value,
            "awaitingDecision": awaiting,
            "changeSetDigest": digest,
            "baselineRevision": execution.baseline_revision,
            "updatedAt": execution.updated_at.isoformat(),
        },
        "asked": {
            "title": task.title,
            "intent": task.intent,
            "requirements": [
                {"requirementId": item.requirement_id, "text": item.text}
                for item in task.requirements
            ],
            "acceptanceCriteria": [
                {"criterionId": item.criterion_id, "text": item.text, "priority": item.priority}
                for item in task.acceptance_criteria
            ],
            "constraints": list(task.constraints),
            **_assumptions_section(task),
        },
        "changed": changed,
        "gate": {
            "gateEvaluationId": gate.gate_evaluation_id,
            "status": gate.status.value,
            "changeSetDigest": gate.change_set_digest,
            "reasons": [
                {"code": code, "explanation": _reason(code, by_id)} for code in gate.reason_codes
            ],
        }
        if gate
        else None,
        "risks": risks,
        "verified": verified,
        "notVerified": not_verified,
        "exceptions": exceptions or [],
        "history": history,
        "delta": _delta(services, decisions, current, current_findings, blocking),
        "decisions": [
            {
                "decision": item.decision.value,
                "actorId": item.actor.actor_id,
                "decidedAt": item.decided_at.isoformat(),
                "changeSetDigest": item.change_set_digest,
                "rationale": item.rationale,
                "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
            }
            for item in decisions
        ],
        "next": _next(execution, awaiting),
        **_provenance_sections(services, execution_id, digest),
    }
    if agent_results:
        brief["riskFactors"] = agent_results
    # Since #55: certification, preflight, deferred items, checklist, contract, interruptions.
    from governed_harness.application.ladder import brief_sections, not_verified_lines

    ladder = brief_sections(services, execution)
    if ladder:
        brief.update(ladder)
        brief["notVerified"] = [*brief["notVerified"], *not_verified_lines(ladder)]
    return brief


def _assumptions_section(task: Task) -> dict[str, Any]:
    """``assumptions`` of what was asked (#79): the points the agent review left open after its
    last round; absent when the task records none, so other briefs keep their form."""
    from governed_harness.orchestration.intent_convergence import assumptions_of

    assumptions = assumptions_of(task)
    return {"assumptions": assumptions} if assumptions else {}


def _assumption_notes(task: Task) -> list[str]:
    from governed_harness.orchestration.intent_convergence import assumptions_of

    return [
        f"assumption {item.get('assumptionId')} was not answered by a person: "
        f"{item.get('question')}"
        for item in assumptions_of(task)
    ]


def _agent_results_section(
    services: EngineServices, execution: Execution, findings: list[Finding]
) -> dict[str, Any] | None:
    """Risk factors of the ChangeSet (``verification.riskFactors``, #52): what blocks, what the
    decider must acknowledge (``--acknowledge-risk``) and what only informs."""
    signals = [item for item in findings if item.category == "risk"]
    raw = services.state.get_flag(f"riskack:{execution.execution_id}:{execution.change_set_digest}")
    required = json.loads(raw) if raw else []
    if not signals and not required:
        return None
    return {
        "acknowledgementRequired": required,
        "signals": [
            {
                "factor": item.rule_id.removeprefix("risk."),
                "severity": item.severity.value,
                "message": item.message,
                "location": item.location.path if item.location else None,
                "line": item.location.start_line if item.location else None,
            }
            for item in signals
        ],
    }


def _provenance_sections(
    services: EngineServices, execution_id: str, digest: str | None
) -> dict[str, Any]:
    """``provenance`` and ``selfReports`` (``provenance`` settings, since 1.1); absent when the
    run recorded neither, so the brief of other runs keeps its form."""
    sections: dict[str, Any] = {}
    records = [
        item
        for item in services.state.list(
            "component_provenance", ComponentProvenance, execution_id=execution_id
        )
        if item.change_set_digest == digest
    ]
    if records:
        latest = records[-1]
        out_of_band = [item.path for item in latest.files if item.source == "OUT_OF_BAND"]
        sections["provenance"] = {
            "changeSetDigest": latest.change_set_digest,
            "agentFiles": len(latest.files) - len(out_of_band),
            "outOfBandFiles": len(out_of_band),
            "outOfBandPaths": out_of_band,
            "files": [item.model_dump(mode="json", by_alias=True) for item in latest.files],
        }
    reports = services.state.list("agent_self_report", AgentSelfReport, execution_id=execution_id)
    if reports:
        sections["selfReports"] = [
            {
                "invocationId": item.invocation_id,
                "quality": item.quality.value,
                "assumptions": list(item.assumptions),
                "alternativesDiscarded": list(item.alternatives_discarded),
                "lowConfidenceAreas": [
                    entry.model_dump(mode="json", by_alias=True)
                    for entry in item.low_confidence_areas
                ],
                "unrequestedChanges": [
                    entry.model_dump(mode="json", by_alias=True)
                    for entry in item.unrequested_changes
                ],
                "problems": list(item.problems),
                "contrast": item.contrast.model_dump(mode="json", by_alias=True)
                if item.contrast
                else None,
            }
            for item in reports[-3:]
        ]
    return sections
