"""``harness verify``: check a run's record against its event chain without aborting.

The events are the audit authority; the ``records`` table is a projection that the CLI, the API
and the gate read. Before 2.0 nobody compared the two: a forged decider written into the
projection was shown by ``status`` while the chain still said otherwise, and a chain whose last
events had been deleted was reported as valid. The verifier reports, for one run:

* the event chain (sequence, links, digests) and, when ``governance.chainAnchor`` is set, whether
  the chain still contains the head recorded outside ``.harness`` (truncation);
* every record that has an event of its own (decisions, gates, ChangeSets, validations,
  findings, evidence, tool and agent invocations, clarifications), rebuilt from the event and
  compared with the stored projection, plus the execution's pointers and the phase results;
* every artifact the run references: present and with the digest its record states.

Nothing is repaired: the report says what differs, and the events say what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from governed_harness.domain.errors import NotFoundError
from governed_harness.domain.models import (
    AgentInvocation,
    Artifact,
    ChangeSet,
    ClarificationRecord,
    ClarificationRequest,
    Evidence,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    ToolInvocation,
    ValidationResult,
)
from governed_harness.events import AnchorStore, StoredEvent

if TYPE_CHECKING:
    from governed_harness.orchestration.engine_types import EngineServices

PROJECTED_EVENTS: dict[str, tuple[str, type[BaseModel], str]] = {
    "human.decision.recorded": ("decision", HumanDecision, "decision_id"),
    "gate.evaluated": ("gate", GateEvaluation, "gate_evaluation_id"),
    "changeset.created": ("change_set", ChangeSet, "change_set_id"),
    "validation.completed": ("validation", ValidationResult, "validation_result_id"),
    "finding.recorded": ("finding", Finding, "finding_id"),
    "evidence.recorded": ("evidence", Evidence, "evidence_id"),
    "tool.invocation.completed": ("tool_invocation", ToolInvocation, "invocation_id"),
    "agent.invocation.completed": ("agent_invocation", AgentInvocation, "invocation_id"),
    "intent.clarified": ("clarification", ClarificationRecord, "clarification_id"),
    "intent.clarification.requested": (
        "clarification_request",
        ClarificationRequest,
        "request_id",
    ),
}
"""Event type -> (record type, model, id field) of the records projected one-to-one from events."""

PRUNED_EVENT = "retention.artifacts.pruned"


@dataclass
class RunVerification:
    execution_id: str
    chain: dict[str, Any]
    anchor: dict[str, Any]
    records_checked: int = 0
    record_problems: list[dict[str, Any]] = field(default_factory=list)
    artifacts_checked: int = 0
    artifact_problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return (
            bool(self.chain["valid"])
            and self.anchor["status"] in {"matched", "absent", "off"}
            and not self.record_problems
            and not self.artifact_problems
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "valid": self.valid,
            "eventChain": self.chain,
            "anchor": self.anchor,
            "records": {"checked": self.records_checked, "problems": self.record_problems},
            "artifacts": {"checked": self.artifacts_checked, "problems": self.artifact_problems},
        }

    def summary(self) -> str:
        reasons = []
        if not self.chain["valid"]:
            reasons.append(f"event chain: {self.chain['error']}")
        if self.anchor["status"] not in {"matched", "absent", "off"}:
            reasons.append(f"anchor: {self.anchor['status']}")
        if self.record_problems:
            reasons.append(f"{len(self.record_problems)} record(s) differ from the events")
        if self.artifact_problems:
            reasons.append(f"{len(self.artifact_problems)} artifact problem(s)")
        return "; ".join(reasons) or "valid"


class RunVerifier:
    def __init__(self, services: EngineServices) -> None:
        self.s = services

    def anchor_store(self) -> AnchorStore:
        mode = self.s.resolved.project.governance_settings.chain_anchor or "off"
        return AnchorStore(mode, self.s.paths.workspace, self.s.resolved.project.project_id)

    def verify(self, execution_id: str) -> RunVerification:
        events = self._events(execution_id)
        has_record = self._has_execution_record(execution_id)
        if not events and not has_record:
            raise NotFoundError(f"run not found: {execution_id}")
        check = self.s.events.check_chain(execution_id)
        report = RunVerification(
            execution_id=execution_id,
            chain=check.as_dict(),
            anchor=self._anchor(execution_id, events),
        )
        self._check_records(report, events)
        self._check_artifacts(report, events)
        return report

    def execution_ids(self) -> list[str]:
        ids = dict.fromkeys(self.s.events.execution_ids())
        for item in self.s.state.list_dicts(
            "execution", project_id=self.s.resolved.project.project_id
        ):
            ids.setdefault(str(item.get("executionId")), None)
        return list(ids)

    # ----- parts ------------------------------------------------------------------
    def _events(self, execution_id: str) -> list[StoredEvent]:
        try:
            return self.s.events.list(execution_id)
        except (ValueError, TypeError):
            return []

    def _has_execution_record(self, execution_id: str) -> bool:
        try:
            self.s.state.get_dict("execution", execution_id)
        except NotFoundError:
            return False
        return True

    def _anchor(self, execution_id: str, events: list[StoredEvent]) -> dict[str, Any]:
        store = self.anchor_store()
        if store.mode == "off":
            return {"mode": "off", "status": "off"}
        anchor = store.read(execution_id)
        base: dict[str, Any] = {"mode": store.mode, "location": store.location}
        if anchor is None:
            return {**base, "status": "absent"}
        base.update(anchor.as_dict())
        at = next((event for event in events if event.sequence == anchor.sequence), None)
        if at is None:
            return {**base, "status": "truncated", "eventCount": len(events)}
        if at.event_digest != anchor.event_digest:
            return {**base, "status": "rewritten"}
        later = sum(1 for event in events if event.sequence > anchor.sequence)
        return {**base, "status": "matched", "eventsAfterAnchor": later}

    def _check_records(self, report: RunVerification, events: list[StoredEvent]) -> None:
        execution_id = report.execution_id
        from_events: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            projected = PROJECTED_EVENTS.get(event.event_type)
            if projected is None:
                continue
            record_type, model, id_field = projected
            try:
                item = model.model_validate(event.payload)
            except ValidationError as error:
                report.record_problems.append(
                    self._problem(record_type, None, "unreadable-event", str(error)[:300])
                )
                continue
            key = (record_type, str(getattr(item, id_field)))
            from_events[key] = item.model_dump(mode="json")
        stored: dict[tuple[str, str], dict[str, Any]] = {}
        for record_type, model, id_field in {
            (value[0], value[1], value[2]) for value in PROJECTED_EVENTS.values()
        }:
            for raw in self.s.state.list_dicts(record_type, execution_id=execution_id):
                try:
                    item = model.model_validate(raw)
                except ValidationError as error:
                    report.record_problems.append(
                        self._problem(record_type, None, "unreadable-record", str(error)[:300])
                    )
                    continue
                stored[(record_type, str(getattr(item, id_field)))] = item.model_dump(mode="json")
        report.records_checked = len(set(from_events) | set(stored))
        for key in sorted(set(from_events) | set(stored)):
            record_type, record_id = key
            if key not in stored:
                report.record_problems.append(self._problem(record_type, record_id, "missing"))
            elif key not in from_events:
                report.record_problems.append(
                    self._problem(record_type, record_id, "no-event", "the record has no event")
                )
            elif stored[key] != from_events[key]:
                fields = sorted(
                    name
                    for name in set(stored[key]) | set(from_events[key])
                    if stored[key].get(name) != from_events[key].get(name)
                )
                report.record_problems.append(
                    self._problem(record_type, record_id, "differs", ", ".join(fields))
                )
        self._check_execution(report, events, from_events)
        self._check_phases(report, events)

    def _check_execution(
        self,
        report: RunVerification,
        events: list[StoredEvent],
        from_events: dict[tuple[str, str], dict[str, Any]],
    ) -> None:
        try:
            execution = self.s.state.get("execution", report.execution_id, Execution)
        except NotFoundError:
            report.record_problems.append(
                self._problem("execution", report.execution_id, "missing")
            )
            return
        except ValidationError as error:
            report.record_problems.append(
                self._problem("execution", report.execution_id, "unreadable-record", str(error))
            )
            return
        report.records_checked += 1
        created = next((event for event in events if event.event_type == "run.created"), None)
        mismatches: list[str] = []
        if created is not None:
            expected = {
                "taskId": execution.task_id,
                "configurationDigest": execution.configuration_digest,
                "workflowDigest": execution.workflow_digest,
                "policyDigest": execution.policy_digest,
            }
            mismatches.extend(
                name for name, value in expected.items() if created.payload.get(name) != value
            )
        if (
            execution.human_decision_id
            and ("decision", execution.human_decision_id) not in from_events
        ):
            mismatches.append("humanDecisionId")
        if (
            execution.gate_evaluation_id
            and ("gate", execution.gate_evaluation_id) not in from_events
        ):
            mismatches.append("gateEvaluationId")
        digests = {
            str(value.get("digest"))
            for (record_type, _), value in from_events.items()
            if record_type == "change_set"
        }
        if execution.change_set_digest and execution.change_set_digest not in digests:
            mismatches.append("changeSetDigest")
        if mismatches:
            report.record_problems.append(
                self._problem("execution", report.execution_id, "differs", ", ".join(mismatches))
            )

    def _check_phases(self, report: RunVerification, events: list[StoredEvent]) -> None:
        completed = {
            event.phase_execution_id: event.payload
            for event in events
            if event.event_type == "phase.completed" and event.phase_execution_id
        }
        started = {
            event.phase_execution_id
            for event in events
            if event.event_type == "phase.started" and event.phase_execution_id
        }
        for raw in self.s.state.list_dicts("phase", execution_id=report.execution_id):
            try:
                phase = PhaseExecution.model_validate(raw)
            except ValidationError as error:
                report.record_problems.append(
                    self._problem("phase", None, "unreadable-record", str(error)[:300])
                )
                continue
            report.records_checked += 1
            payload = completed.get(phase.phase_execution_id)
            if payload is None:
                if phase.phase_execution_id not in started:
                    report.record_problems.append(
                        self._problem("phase", phase.phase_execution_id, "no-event")
                    )
                continue
            if payload.get("status") != phase.status.value or payload.get("phaseId") != (
                phase.phase_id.value
            ):
                report.record_problems.append(
                    self._problem("phase", phase.phase_execution_id, "differs", "status, phaseId")
                )

    def _check_artifacts(self, report: RunVerification, events: list[StoredEvent]) -> None:
        pruned = {
            str(uri)
            for event in events
            if event.event_type == PRUNED_EVENT
            for uri in event.payload.get("artifactRefs", [])
        }
        seen: set[tuple[str, str]] = set()
        for raw in self.s.state.list_dicts("artifact", execution_id=report.execution_id):
            try:
                artifact = Artifact.model_validate(raw)
            except ValidationError as error:
                report.artifact_problems.append(
                    {"uri": None, "problem": "unreadable-record", "detail": str(error)[:300]}
                )
                continue
            self._check_artifact(report, artifact.uri, artifact.digest, pruned, seen)
        for raw in self.s.state.list_dicts("evidence", execution_id=report.execution_id):
            try:
                evidence = Evidence.model_validate(raw)
            except ValidationError:
                continue  # reported by the record check
            self._check_artifact(report, evidence.artifact_ref, evidence.digest, pruned, seen)

    def _check_artifact(
        self,
        report: RunVerification,
        uri: str,
        digest: str,
        pruned: set[str],
        seen: set[tuple[str, str]],
    ) -> None:
        if (uri, digest) in seen:
            return
        seen.add((uri, digest))
        if not uri.startswith("artifact://"):
            return
        report.artifacts_checked += 1
        expected = "sha256:" + uri.removeprefix("artifact://sha256/")
        if digest != expected:
            report.artifact_problems.append(
                {"uri": uri, "problem": "digest-differs", "detail": f"record states {digest}"}
            )
            return
        if uri in pruned:
            return
        try:
            self.s.artifacts.get(uri)
        except FileNotFoundError:
            report.artifact_problems.append({"uri": uri, "problem": "missing"})
        except (OSError, ValueError) as error:
            report.artifact_problems.append(
                {"uri": uri, "problem": "content-differs", "detail": str(error)}
            )

    @staticmethod
    def _problem(
        record_type: str, record_id: str | None, problem: str, detail: str | None = None
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "recordType": record_type,
            "recordId": record_id,
            "problem": problem,
        }
        if detail:
            value["detail"] = detail
        return value
