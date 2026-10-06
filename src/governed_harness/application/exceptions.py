"""Exceptions with expiry, scope, alternative evidence and follow-up (``review.exceptions``).

``APPROVE_EXCEPTION`` keeps its 1.0.0 meaning without the key. With it, the decision carries an
expiry and an exception record is stored with its provenance on the run's event chain; the gate
of a later run does not block on the findings the exception covers while it is in force, and
blocks on them again once it expires (``gates.exceptions``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from governed_harness import __version__
from governed_harness.domain.enums import EvidenceKind, FindingSeverity, PhaseId
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    ExceptionRecord,
    ExceptionScope,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    Provenance,
    utc_now,
)
from governed_harness.gates.exceptions import exception_ids
from governed_harness.orchestration.engine import EngineServices, RunEngine
from governed_harness.reporting.fingerprint import finding_fingerprint

MAX_EXCEPTION_DAYS = 365


@dataclass(frozen=True)
class ExceptionOptions:
    """What a person declares with APPROVE_EXCEPTION under ``review.exceptions``."""

    expires_in: str | None = None
    expires_at: str | None = None
    scope: tuple[str, ...] = ()
    alternative_evidence: str | None = None
    follow_up: str | None = None

    @property
    def given(self) -> bool:
        return bool(
            self.expires_in
            or self.expires_at
            or self.scope
            or self.alternative_evidence
            or self.follow_up
        )


_DURATION = re.compile(r"^\s*(\d+)\s*([dhw]?)\s*$", re.IGNORECASE)


def parse_expiry(
    *, expires_in: str | None, expires_at: str | None, default_days: int, now: datetime
) -> datetime:
    """``expires_in`` is ``N``/``Nd`` days, ``Nh`` hours or ``Nw`` weeks; ``expires_at`` an ISO
    8601 instant. Neither: ``default_days``. The expiry must be in the future and at most a
    year away."""
    if expires_in and expires_at:
        raise ConfigurationError("use either --expires-in or --expires-at, not both")
    if expires_at:
        try:
            value = datetime.fromisoformat(expires_at)
        except ValueError as error:
            raise ConfigurationError(
                f"--expires-at is not an ISO 8601 instant: {expires_at}"
            ) from error
        value = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
    elif expires_in:
        match = _DURATION.match(expires_in)
        if not match:
            raise ConfigurationError(f"--expires-in must look like 14d, 36h or 2w: {expires_in}")
        amount, unit = int(match.group(1)), (match.group(2) or "d").lower()
        delta = {
            "d": timedelta(days=amount),
            "h": timedelta(hours=amount),
            "w": timedelta(weeks=amount),
        }
        value = now + delta[unit]
    else:
        value = now + timedelta(days=default_days)
    if value <= now:
        raise ConfigurationError("an exception must expire in the future")
    if value - now > timedelta(days=MAX_EXCEPTION_DAYS):
        raise ConfigurationError(f"an exception may last at most {MAX_EXCEPTION_DAYS} days")
    return value


def parse_scope(items: tuple[str, ...]) -> list[ExceptionScope]:
    """``rule`` or ``rule:path`` entries from ``--scope``."""
    scopes: list[ExceptionScope] = []
    for item in items:
        rule, _, path = item.strip().partition(":")
        if not rule:
            raise ConfigurationError(f"--scope needs a rule id, optionally rule:path: {item!r}")
        scopes.append(ExceptionScope(rule_id=rule, path=path or None))
    return scopes


def default_scope(services: EngineServices, gate: GateEvaluation) -> list[ExceptionScope]:
    """The blocking findings the gate evaluated, each by rule, path and fingerprint."""
    names = services.resolved.effective_policies.get("findingBlockSeverities", ["HIGH", "CRITICAL"])
    blocking = {FindingSeverity(str(name)) for name in names}
    ids = {
        ref.removeprefix("record://finding/")
        for ref in gate.input_refs
        if ref.startswith("record://finding/")
    }
    scopes: dict[str, ExceptionScope] = {}
    for finding in services.state.list("finding", Finding, execution_id=gate.execution_id):
        if finding.finding_id in ids and finding.severity in blocking:
            fingerprint = finding_fingerprint(finding)
            scopes[fingerprint] = ExceptionScope(
                rule_id=finding.rule_id,
                path=finding.location.path if finding.location else None,
                fingerprint=fingerprint,
            )
    return list(scopes.values())


def record_exception(
    services: EngineServices,
    *,
    decision: HumanDecision,
    scope: list[ExceptionScope],
    alternative_evidence: str | None,
    follow_up: str | None,
) -> ExceptionRecord:
    if decision.expires_at is None:  # the decision of an exception always carries its expiry
        raise ConfigurationError("an exception needs an expiry (--expires-in or --expires-at)")
    engine = RunEngine(services)
    execution = engine.get_execution(decision.execution_id)
    gate = services.state.get("gate", decision.gate_evaluation_id, GateEvaluation)
    record = ExceptionRecord(
        exception_id=new_id("exception"),
        project_id=execution.project_id,
        execution_id=execution.execution_id,
        decision_id=decision.decision_id,
        gate_evaluation_id=decision.gate_evaluation_id,
        actor=decision.actor,
        rationale=decision.rationale,
        scope=tuple(scope or default_scope(services, gate)),
        change_set_digest=decision.change_set_digest,
        granted_at=decision.decided_at,
        expires_at=decision.expires_at,
        alternative_evidence=(alternative_evidence or "").strip() or None,
        follow_up=(follow_up or "").strip() or None,
        provenance=Provenance(
            actor=decision.actor,
            core_version=__version__,
            configuration_digest=execution.configuration_digest,
            policy_digest=execution.policy_digest,
            source_refs=(f"record://decision/{decision.decision_id}",),
        ),
    )
    services.state.put(
        "exception",
        record.exception_id,
        record,
        execution_id=execution.execution_id,
        project_id=execution.project_id,
    )
    reference = services.artifacts.put_json(
        record.model_dump(mode="json", by_alias=True),
        metadata={"kind": "exception", "executionId": execution.execution_id},
    )
    engine._record_evidence(
        execution,
        PhaseId.DECISION,
        EvidenceKind.HUMAN_DECISION,
        reference,
        f"Exception covering {len(record.scope)} finding scope(s) until "
        f"{record.expires_at.isoformat()}",
        supports=(decision.decision_id,),
    )
    services.events.append(
        execution.execution_id,
        "exception.granted",
        record.model_dump(mode="json", by_alias=True),
        actor=decision.actor,
    )
    return record


def exception_entry(
    record: ExceptionRecord, now: datetime, applied_in: list[str] | None = None
) -> dict[str, Any]:
    remaining = record.expires_at - now
    return {
        "exceptionId": record.exception_id,
        "status": "ACTIVE" if record.active_at(now) else "EXPIRED",
        "executionId": record.execution_id,
        "decisionId": record.decision_id,
        "actorId": record.actor.actor_id,
        "rationale": record.rationale,
        "grantedAt": record.granted_at.isoformat(),
        "expiresAt": record.expires_at.isoformat(),
        "daysLeft": max(0, remaining.days) if remaining.total_seconds() > 0 else 0,
        "scope": [item.model_dump(mode="json", by_alias=True) for item in record.scope],
        "alternativeEvidence": record.alternative_evidence,
        "followUp": record.follow_up,
        "changeSetDigest": record.change_set_digest,
        "appliedIn": applied_in or [],
    }


def project_exceptions(services: EngineServices) -> list[ExceptionRecord]:
    return sorted(
        services.state.list(
            "exception", ExceptionRecord, project_id=services.resolved.project.project_id
        ),
        key=lambda item: item.granted_at,
    )


def applications(services: EngineServices) -> dict[str, list[str]]:
    """For each exception id, the runs whose gate relied on it."""
    used: dict[str, set[str]] = {}
    for gate in services.state.list(
        "gate", GateEvaluation, project_id=services.resolved.project.project_id
    ):
        for exception_id in exception_ids(gate.reason_codes):
            used.setdefault(exception_id, set()).add(gate.execution_id)
    return {key: sorted(value) for key, value in used.items()}


def list_exceptions(
    services: EngineServices, *, status: str = "all", expiring_within: int | None = None
) -> list[dict[str, Any]]:
    now = utc_now()
    used = applications(services)
    entries = []
    for record in project_exceptions(services):
        entry = exception_entry(record, now, used.get(record.exception_id))
        if status != "all" and entry["status"] != status.upper():
            continue
        if expiring_within is not None and (
            entry["status"] != "ACTIVE" or record.expires_at - now > timedelta(days=expiring_within)
        ):
            continue
        entries.append(entry)
    return entries


def brief_exceptions(services: EngineServices, execution: Execution) -> list[dict[str, Any]]:
    """Exceptions granted in this run or relied on by its current gate."""
    now = utc_now()
    relied: set[str] = set()
    if execution.gate_evaluation_id:
        gate = services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
        relied = set(exception_ids(gate.reason_codes))
    entries = []
    for record in project_exceptions(services):
        if record.execution_id == execution.execution_id or record.exception_id in relied:
            entry = exception_entry(record, now)
            entry["reliedOnByGate"] = record.exception_id in relied
            entries.append(entry)
    return entries
