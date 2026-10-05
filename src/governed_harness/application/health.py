"""Rule health across runs and outcomes recorded after a run.

Rule health reads every run of the project and says, for each rule and validator, how often it
fired, how often it blocked a gate, how often a person granted an exception over it, how often
it fired on a ChangeSet that was later corrected, how often it fired in a rejected run and how
often a run where it fired was later linked to an incident or revert. A rule that blocks often
and is often excepted is a candidate false positive; one that is quiet in runs that later had
incidents is a candidate gap. Nothing is changed: a person decides. No figure is per person."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from governed_harness import __version__
from governed_harness.domain.enums import ActorType, DecisionKind, ResultStatus
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    ChangeSet,
    ExceptionRecord,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
    ValidationResult,
    utc_now,
)
from governed_harness.gates.exceptions import exception_ids
from governed_harness.orchestration.engine import NON_HUMAN_ACTOR_PREFIXES, EngineServices

OUTCOME_KINDS: tuple[OutcomeKind, ...] = ("INCIDENT", "REVERT", "HOTFIX", "REGRESSION", "OTHER")


def record_outcome(
    services: EngineServices,
    *,
    execution_id: str,
    kind: str,
    summary: str,
    reference: str | None,
    observed_at: str | None,
    actor_id: str,
) -> OutcomeRecord:
    if actor_id.startswith(NON_HUMAN_ACTOR_PREFIXES):
        raise PolicyViolationError("only a person can link an outcome to a run")
    upper = kind.upper()
    if upper not in OUTCOME_KINDS:
        raise ConfigurationError(f"outcome kind must be one of {', '.join(OUTCOME_KINDS)}: {kind}")
    if not summary.strip():
        raise ConfigurationError("an outcome needs a summary")
    when = utc_now()
    if observed_at:
        try:
            when = datetime.fromisoformat(observed_at)
        except ValueError as error:
            raise ConfigurationError(
                f"--observed-at is not an ISO 8601 instant: {observed_at}"
            ) from error
        when = when if when.tzinfo else when.replace(tzinfo=utc_now().tzinfo)
    execution = services.state.get("execution", execution_id, Execution)
    actor = Actor(actor_type=ActorType.HUMAN, actor_id=actor_id)
    record = OutcomeRecord(
        outcome_id=new_id("outcome"),
        project_id=execution.project_id,
        execution_id=execution.execution_id,
        kind=upper,
        summary=summary.strip(),
        reference=(reference or "").strip() or None,
        change_set_digest=execution.change_set_digest,
        observed_at=when,
        actor=actor,
        provenance=Provenance(
            actor=actor,
            core_version=__version__,
            configuration_digest=execution.configuration_digest,
            policy_digest=execution.policy_digest,
            source_refs=(f"record://execution/{execution.execution_id}",),
        ),
    )
    services.state.put(
        "outcome",
        record.outcome_id,
        record,
        execution_id=execution.execution_id,
        project_id=execution.project_id,
    )
    services.artifacts.put_json(
        record.model_dump(mode="json", by_alias=True),
        metadata={"kind": "outcome", "executionId": execution.execution_id},
    )
    return record


def list_outcomes(services: EngineServices, execution_id: str | None = None) -> list[OutcomeRecord]:
    records = services.state.list(
        "outcome",
        OutcomeRecord,
        execution_id=execution_id,
        project_id=services.resolved.project.project_id,
    )
    return sorted(records, key=lambda item: item.observed_at)


def _blank() -> dict[str, Any]:
    return {
        "fired": 0,
        "runs": set(),
        "blockedGates": 0,
        "excepted": 0,
        "exceptionApplications": 0,
        "correctedRuns": set(),
        "rejectedRuns": set(),
        "outcomeRuns": set(),
    }


def rule_health(services: EngineServices, since_days: int | None = None) -> dict[str, Any]:
    project_id = services.resolved.project.project_id
    cutoff = utc_now() - timedelta(days=since_days) if since_days is not None else None
    runs = [
        item
        for item in services.state.list("execution", Execution, project_id=project_id)
        if cutoff is None or item.created_at >= cutoff
    ]
    run_ids = {item.execution_id for item in runs}
    rules: dict[str, dict[str, Any]] = defaultdict(_blank)
    validators: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "mandatory": False, "notPassed": 0, "results": 0}
    )
    outcomes = [
        item
        for item in services.state.list("outcome", OutcomeRecord, project_id=project_id)
        if item.execution_id in run_ids
    ]
    outcome_runs = {item.execution_id for item in outcomes}
    corrected_runs: set[str] = set()
    applications: dict[str, int] = defaultdict(int)
    for execution in runs:
        run_id = execution.execution_id
        events = services.events.list(run_id)
        corrected = any(event.event_type == "correction.authorized" for event in events)
        if corrected:
            corrected_runs.add(run_id)
        decisions = services.state.list("decision", HumanDecision, execution_id=run_id)
        rejected = any(item.decision is DecisionKind.REJECT for item in decisions)
        final = execution.change_set_digest
        findings = {
            item.finding_id: item
            for item in services.state.list("finding", Finding, execution_id=run_id)
        }
        digest_of: dict[str, str] = {}
        seen_validators: set[str] = set()
        for validation in services.state.list("validation", ValidationResult, execution_id=run_id):
            for finding_id in validation.finding_ids:
                digest_of[finding_id] = validation.change_set_digest
            entry = validators[validation.validator_id]
            entry["results"] += 1
            entry["mandatory"] = entry["mandatory"] or validation.mandatory
            if validation.status is not ResultStatus.PASSED:
                entry["notPassed"] += 1
            seen_validators.add(validation.validator_id)
        for validator_id in seen_validators:
            validators[validator_id]["runs"] += 1
        for finding in findings.values():
            entry = rules[finding.rule_id]
            entry["fired"] += 1
            entry["runs"].add(run_id)
            if corrected and digest_of.get(finding.finding_id) not in {None, final}:
                entry["correctedRuns"].add(run_id)
            if rejected and digest_of.get(finding.finding_id) == final:
                entry["rejectedRuns"].add(run_id)
            if run_id in outcome_runs:
                entry["outcomeRuns"].add(run_id)
        for gate in services.state.list("gate", GateEvaluation, execution_id=run_id):
            for code in gate.reason_codes:
                if code.startswith("BLOCKING_FINDING_"):
                    blocked = findings.get(code.removeprefix("BLOCKING_FINDING_"))
                    if blocked is not None:
                        rules[blocked.rule_id]["blockedGates"] += 1
            for exception_id in exception_ids(gate.reason_codes):
                applications[exception_id] += 1
    for record in services.state.list("exception", ExceptionRecord, project_id=project_id):
        if record.execution_id not in run_ids:
            continue
        for rule_id in {scope.rule_id for scope in record.scope}:
            rules[rule_id]["excepted"] += 1
            rules[rule_id]["exceptionApplications"] += applications[record.exception_id]
    rows = []
    for rule_id, entry in rules.items():
        rows.append(
            {
                "ruleId": rule_id,
                "fired": entry["fired"],
                "runs": len(entry["runs"]),
                "blockedGates": entry["blockedGates"],
                "excepted": entry["excepted"],
                "exceptionApplications": entry["exceptionApplications"],
                "correctedRuns": len(entry["correctedRuns"]),
                "rejectedRuns": len(entry["rejectedRuns"]),
                "runsWithLaterOutcomes": len(entry["outcomeRuns"]),
                "signal": _signal(entry),
            }
        )
    rows.sort(key=lambda item: (-item["fired"], item["ruleId"]))
    return {
        "projectId": project_id,
        "since": cutoff.isoformat() if cutoff else None,
        "runs": len(runs),
        "correctedRuns": len(corrected_runs),
        "outcomes": {
            kind: sum(1 for item in outcomes if item.kind == kind) for kind in OUTCOME_KINDS
        },
        "rules": rows,
        "validators": [
            {
                "validatorId": validator_id,
                "mandatory": entry["mandatory"],
                "runs": entry["runs"],
                "results": entry["results"],
                "notPassed": entry["notPassed"],
            }
            for validator_id, entry in sorted(validators.items())
        ],
        "changeSets": sum(
            len(services.state.list("change_set", ChangeSet, execution_id=item.execution_id))
            for item in runs
        ),
    }


def _signal(entry: dict[str, Any]) -> str | None:
    """A fixed-rule hint, not a verdict: what a person may want to look at."""
    if entry["blockedGates"] and entry["excepted"] >= max(1, entry["blockedGates"] // 2):
        return "often excepted when it blocks: check its precision"
    if entry["outcomeRuns"]:
        return "fired in runs later linked to an outcome"
    if entry["correctedRuns"]:
        return "led to corrections"
    return None
