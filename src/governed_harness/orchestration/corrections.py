"""Reproduce-first corrections and empty corrections (#52, N5; extends #36).

In the evaluation half of the correction rounds a small model concluded that the code already
complied and changed nothing, and a reported defect was never fixed because the agent's own tests
passed. Under ``runtime.reproduceFirst``:

* a correction attempt (an IMPLEMENTATION after a failed VERIFICATION or a REQUEST_CHANGES)
  that changes nothing gets an ``agent.empty-correction`` finding, with the agent's summary, so
  the reviewer sees the claim instead of a silent stop;
* a correction after REQUEST_CHANGES must add or change a test that fails on the code as it was
  before the correction and passes after it (Python projects, pytest). A correction without such
  a test gets a HIGH ``correction.not-reproduced`` finding, which blocks the gate.

The findings belong to the ChangeSet the correction produced: VERIFICATION records them in the
``harness.reproduce-first`` validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from governed_harness.agents import AgentExecutionResult
from governed_harness.capabilities import grants_from_rules
from governed_harness.checks import is_test_path
from governed_harness.domain.enums import (
    ActorType,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.models import Actor, ChangeSet, Execution, Finding, PhaseExecution
from governed_harness.orchestration.workspace_ops import changes_since, materialized
from governed_harness.runtime import CancellationToken, SafeProcessRunner, WorkspaceSnapshotter
from governed_harness.runtime.process_runner import CommandSpec
from governed_harness.validators import ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.orchestration.agent_results import AgentResults

REPRODUCE_ID = "harness.reproduce-first"
EMPTY_RULE = "agent.empty-correction"
NOT_REPRODUCED_RULE = "correction.not-reproduced"


class Corrections:
    def __init__(self, results: AgentResults) -> None:
        self.results = results

    @property
    def enabled(self) -> bool:
        return bool(self.results.project.runtime.reproduce_first)

    def _trigger(self, execution: Execution) -> str | None:
        """The trigger of the correction this IMPLEMENTATION attempt answers, if any."""
        events = self.results.s.events.list(execution.execution_id)
        for event in reversed(events):
            if event.event_type == "phase.started" and event.payload.get("phaseId") == (
                PhaseId.IMPLEMENTATION
            ):
                # Skip the start of the current attempt, recorded just before this call.
                continue
            if event.event_type == "correction.authorized":
                return str(event.payload.get("trigger") or "CHANGES_REQUESTED")
            if event.event_type == "phase.completed" and event.payload.get("phaseId") == (
                PhaseId.IMPLEMENTATION
            ):
                return None
        return None

    def start(self, execution: Execution, phase: PhaseExecution) -> None:
        if not self.enabled or phase.attempt < 2:
            return
        trigger = self._trigger(execution)
        if trigger is None:
            return
        snapshot = WorkspaceSnapshotter(self.results.s.paths.workspace).snapshot()
        ref = self.results.s.artifacts.put_json(
            self.results.engine._snapshot_to_dict(snapshot),
            metadata={"kind": "correction-start", "executionId": execution.execution_id},
        )
        self.results.set_flag_json(
            f"correctionstart:{execution.execution_id}",
            {
                "phaseExecutionId": phase.phase_execution_id,
                "snapshotRef": ref.uri,
                "trigger": trigger,
            },
        )
        self.results.s.state.set_flag(f"reproduce:{execution.execution_id}", "")

    def end(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None:
        """Compare the workspace with the start of the correction attempt."""
        if not self.enabled:
            return
        start = self.results.flag_json(f"correctionstart:{execution.execution_id}")
        if not isinstance(start, dict) or start.get("phaseExecutionId") != phase.phase_execution_id:
            return
        engine = self.results.engine
        before = engine._snapshot_from_dict(
            json.loads(self.results.s.artifacts.get(start["snapshotRef"]))
        )
        workspace = self.results.s.paths.workspace
        diff = changes_since(workspace, before)
        findings: list[Finding] = []
        if not diff.changes:
            findings.append(
                self.results.record_finding(
                    execution,
                    validator_id=REPRODUCE_ID,
                    rule_id=EMPTY_RULE,
                    category="agent-claim",
                    severity=FindingSeverity.MEDIUM,
                    message=(
                        f"The correction after {start['trigger']} changed nothing; the agent "
                        f"said: {result.summary[:500]!r}"
                    ),
                    evidence_refs=((result.output_ref,) if result.output_ref else ()),
                    recommendation=(
                        "Read the agent's explanation: decide again, or request the change "
                        "with a verifiable condition (review.structuredChanges)."
                    ),
                    introduced=True,
                )
            )
        elif start["trigger"] == "CHANGES_REQUESTED":
            findings.extend(self._reproduced(execution, before, diff.changes))
        self.results.set_flag_json(
            f"reproduce:{execution.execution_id}",
            {
                "phaseExecutionId": phase.phase_execution_id,
                "trigger": start["trigger"],
                "changedPaths": [item.path for item in diff.changes],
                "findingIds": [item.finding_id for item in findings],
            },
        )

    def _reproduced(self, execution: Execution, before: Any, changes: Any) -> list[Finding]:
        results = self.results
        tests = [
            item.path for item in changes if is_test_path(item.path) and item.status != "DELETED"
        ]
        sources = [item.path for item in changes if not is_test_path(item.path)]
        python = any(item.technology == "python" for item in results.s.resolved.profiles)
        if not python:
            return [
                results.record_finding(
                    execution,
                    validator_id=REPRODUCE_ID,
                    rule_id="correction.reproduce-unavailable",
                    category="corrections",
                    severity=FindingSeverity.INFO,
                    message="The reproduce-first check runs pytest; this project is not Python",
                )
            ]
        python_tests = [path for path in tests if path.endswith(".py")]
        if not python_tests or not sources:
            return [
                results.record_finding(
                    execution,
                    validator_id=REPRODUCE_ID,
                    rule_id=NOT_REPRODUCED_RULE,
                    category="corrections",
                    severity=FindingSeverity.HIGH,
                    message=(
                        "The correction after REQUEST_CHANGES adds no test that reproduces the "
                        "reported problem"
                        if not python_tests
                        else "The correction changes only tests; nothing was corrected"
                    ),
                    recommendation=(
                        "Add a test that fails on the code before the correction and passes "
                        "after it, then correct the code."
                    ),
                    introduced=True,
                )
            ]
        engine = results.engine
        workspace = results.s.paths.workspace
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{REPRODUCE_ID}", version="1")
        grants = grants_from_rules(
            execution.execution_id, actor, results.s.resolved.effective_capabilities
        )
        argv = ("python", "-m", "pytest", "-q", *python_tests)
        record: dict[str, Any] = {"tests": python_tests, "revertedSources": sources}
        with materialized(workspace, results.s.paths.harness_dir / "tmp", before, sources) as copy:
            if copy is None:
                return []
            outcome = SafeProcessRunner(copy).run(
                CommandSpec(
                    argv=argv,
                    cwd=copy,
                    timeout_seconds=float(engine._bounded_timeout(900)),
                    max_output_bytes=results.project.runtime.max_output_bytes,
                ),
                actor=actor,
                grants=grants,
                cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
            )
            record["before"] = {
                "status": outcome.status.value,
                "exitCode": outcome.exit_code,
                "stdoutRef": results.s.artifacts.put(outcome.stdout, media_type="text/plain").uri,
            }
        ref = results.record_json(
            execution,
            PhaseId.IMPLEMENTATION,
            record,
            kind="reproduce-first",
            summary=(
                f"Reproduce-first: the correction's tests {outcome.status.value} on the code "
                "before the correction"
            ),
        )
        if outcome.status is ResultStatus.FAILED:
            return []
        return [
            results.record_finding(
                execution,
                validator_id=REPRODUCE_ID,
                rule_id=NOT_REPRODUCED_RULE,
                category="corrections",
                severity=FindingSeverity.HIGH,
                message=(
                    "The tests the correction added or changed "
                    f"({', '.join(python_tests[:5])}) do not fail on the code before the "
                    f"correction ({outcome.status.value}): they do not reproduce the problem"
                ),
                evidence_refs=(ref,),
                recommendation="Write a test that fails without the correction.",
                introduced=True,
            )
        ]

    def validation(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput | None:
        """The findings of the latest correction attempt as a validation of the ChangeSet."""
        if not self.enabled:
            return None
        value = self.results.flag_json(f"reproduce:{execution.execution_id}")
        if not isinstance(value, dict):
            return None
        findings = tuple(
            self.results.s.state.get("finding", item, Finding) for item in value["findingIds"]
        )
        blocking = [
            item
            for item in findings
            if item.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
        ]
        result = self.results.record_validation(
            execution,
            validator_id=REPRODUCE_ID,
            digest=change_set.digest,
            status=ResultStatus.FAILED if blocking else ResultStatus.PASSED,
            kind=ValidationKind.VALIDATION_FAILURE if blocking else ValidationKind.SUCCESS,
            mandatory=True,
            summary=(
                f"Correction after {value['trigger']}: {len(findings)} finding(s)"
                if findings
                else f"Correction after {value['trigger']} reproduced and changed the code"
            ),
            findings=findings,
            evidence_refs=(change_set.diff_ref,),
            started_at=datetime.now(UTC),
        )
        return ValidatorOutput(result, ())
