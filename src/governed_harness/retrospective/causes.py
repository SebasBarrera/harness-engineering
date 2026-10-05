"""Causes of a run's stops and corrections, by reason code (``retrospective.causal``).

The 1.0.0 retrospective counted every non-passed validation, including optional validators that
had no effect on the gate, and so attributed cycles to the wrong cause. Here a cause is only
what stopped VERIFICATION (a mandatory validator that did not pass), what failed a delivery gate
(a blocking finding), a decision that sent the run back or ended it, an exception, a transient
provider failure or an outcome recorded after the run. Subjects are validators, rules and
providers, never people (no per-person metric)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from governed_harness.domain.enums import DecisionKind, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import (
    ExceptionRecord,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    OutcomeRecord,
    RetrospectiveCause,
    ValidationResult,
)
from governed_harness.events import StoredEvent
from governed_harness.storage import SQLiteStateStore

STOPPING = {
    ResultStatus.FAILED,
    ResultStatus.BLOCKED,
    ResultStatus.ERROR,
    ResultStatus.TIMED_OUT,
    ResultStatus.INCONCLUSIVE,
}


@dataclass
class CauseAnalysis:
    causes: list[RetrospectiveCause] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def analyze(
    state: SQLiteStateStore,
    events: list[StoredEvent],
    execution: Execution,
    blocking: set[FindingSeverity],
) -> CauseAnalysis:
    run_id = execution.execution_id
    analysis = CauseAnalysis()
    validations = sorted(
        state.list("validation", ValidationResult, execution_id=run_id),
        key=lambda item: item.finished_at,
    )
    # VERIFICATION attempts: each new ChangeSet digest verified is one attempt.
    attempt_of: dict[str, int] = {}
    for item in validations:
        attempt_of.setdefault(item.change_set_digest, len(attempt_of) + 1)

    mandatory: dict[tuple[str, str], list[int]] = defaultdict(list)
    refs: dict[tuple[str, str], list[str]] = defaultdict(list)
    optional_failures: set[str] = set()
    for item in validations:
        if item.status in STOPPING:
            if item.mandatory:
                key = (item.validator_id, item.status.value)
                attempt = attempt_of[item.change_set_digest]
                if attempt not in mandatory[key]:
                    mandatory[key].append(attempt)
                refs[key].append(f"record://validation/{item.validation_result_id}")
            else:
                optional_failures.add(item.validator_id)
    corrected = any(event.event_type == "correction.authorized" for event in events)
    for (validator, status), attempts in sorted(mandatory.items()):
        last = max(attempt_of.values()) if attempt_of else 0
        effect = (
            "stopped VERIFICATION and was corrected in a later attempt"
            if corrected and max(attempts) < last
            else "stopped VERIFICATION"
        )
        analysis.causes.append(
            RetrospectiveCause(
                reason_code=f"MANDATORY_VALIDATOR_{status}",
                subject=validator,
                phase=PhaseId.VERIFICATION,
                effect=effect,
                occurrences=len(attempts),
                attempts=tuple(sorted(attempts)),
                evidence_refs=tuple(refs[(validator, status)][:10]),
            )
        )
    gate_optional = optional_failures - {validator for validator, _ in mandatory}
    if gate_optional:
        analysis.notes.append(
            "Optional validators that did not pass had no effect on the gate and are not counted "
            "as causes: " + ", ".join(sorted(gate_optional)) + "."
        )

    findings = {
        item.finding_id: item for item in state.list("finding", Finding, execution_id=run_id)
    }
    gates = sorted(
        state.list("gate", GateEvaluation, execution_id=run_id), key=lambda item: item.evaluated_at
    )
    rule_gates: dict[str, list[int]] = defaultdict(list)
    rule_refs: dict[str, list[str]] = defaultdict(list)
    for index, gate in enumerate(gates, start=1):
        for code in gate.reason_codes:
            if not code.startswith("BLOCKING_FINDING_"):
                continue
            finding = findings.get(code.removeprefix("BLOCKING_FINDING_"))
            if finding is None or finding.severity not in blocking:
                continue
            if index not in rule_gates[finding.rule_id]:
                rule_gates[finding.rule_id].append(index)
            rule_refs[finding.rule_id].append(f"record://finding/{finding.finding_id}")
    for rule, indexes in sorted(rule_gates.items()):
        analysis.causes.append(
            RetrospectiveCause(
                reason_code="BLOCKING_FINDING",
                subject=rule,
                phase=PhaseId.DECISION,
                effect="failed the delivery gate",
                occurrences=len(indexes),
                attempts=tuple(indexes),
                evidence_refs=tuple(rule_refs[rule][:10]),
            )
        )

    decisions = sorted(
        state.list("decision", HumanDecision, execution_id=run_id), key=lambda item: item.decided_at
    )
    by_kind: dict[DecisionKind, list[int]] = defaultdict(list)
    for index, decision in enumerate(decisions, start=1):
        by_kind[decision.decision].append(index)
    effects = {
        DecisionKind.REQUEST_CHANGES: ("CHANGES_REQUESTED", "sent the run back to IMPLEMENTATION"),
        DecisionKind.REJECT: ("REJECTED", "ended the run"),
        DecisionKind.APPROVE_EXCEPTION: ("EXCEPTION_APPROVED", "closed the run over a failed gate"),
    }
    for kind, (code, effect) in effects.items():
        if by_kind.get(kind):
            analysis.causes.append(
                RetrospectiveCause(
                    reason_code=code,
                    subject="human-decision",
                    phase=PhaseId.DECISION,
                    effect=effect,
                    occurrences=len(by_kind[kind]),
                    attempts=tuple(by_kind[kind]),
                    evidence_refs=tuple(
                        f"record://decision/{item.decision_id}"
                        for item in decisions
                        if item.decision is kind
                    ),
                )
            )
    exceptions = state.list("exception", ExceptionRecord, execution_id=run_id)
    for record in exceptions:
        for scope in record.scope:
            analysis.causes.append(
                RetrospectiveCause(
                    reason_code="EXCEPTION_GRANTED",
                    subject=scope.rule_id,
                    phase=PhaseId.DECISION,
                    effect=f"excepted until {record.expires_at.isoformat()}",
                    occurrences=1,
                    evidence_refs=(f"record://exception/{record.exception_id}",),
                )
            )
    retries = [event for event in events if event.event_type == "agent.invocation.retried"]
    if retries:
        provider = state.get_flag(f"provider:{run_id}") or "provider"
        analysis.causes.append(
            RetrospectiveCause(
                reason_code="PROVIDER_TRANSIENT_FAILURE",
                subject=provider,
                phase=PhaseId.IMPLEMENTATION,
                effect="repeated the agent call",
                occurrences=len(retries),
            )
        )
    if execution.status is ResultStatus.CANCELLED:
        analysis.causes.append(
            RetrospectiveCause(
                reason_code="RUN_CANCELLED",
                subject=execution.current_phase.value,
                phase=execution.current_phase,
                effect="ended the run before closure",
                occurrences=1,
            )
        )
    for outcome in state.list("outcome", OutcomeRecord, execution_id=run_id):
        analysis.causes.append(
            RetrospectiveCause(
                reason_code=f"POST_RUN_{outcome.kind}",
                subject=outcome.reference or outcome.kind.lower(),
                phase=PhaseId.CLOSURE,
                effect="recorded after the run",
                occurrences=1,
                evidence_refs=(f"record://outcome/{outcome.outcome_id}",),
            )
        )
    return analysis
