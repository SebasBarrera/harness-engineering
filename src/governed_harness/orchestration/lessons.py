"""Lessons from recurring findings across steps (#43).

In a multi-step plan the agent repeated mistakes an earlier step had already corrected (literal
secrets in tests after a correction), paying again for the same correction. Under
``memory.learnFromFindings: auto``:

* when a run closes, the findings that caused a correction (rule, validator, the paths and a
  short lesson) are proposed as project memory records (``lesson:<validator>:<rule>``) with
  provenance (the run, the findings, the correction events). A lesson seen in
  ``memory.recurrenceRuns`` runs or more (default 2) is approved automatically only under
  ``memory.autoApproveRecurring``; otherwise it waits for a person (``harness memory
  approve``);
* before IMPLEMENTATION, the active (approved, unexpired) lessons relevant to the task (a rule
  of a validator or check the project runs, or a path the task touches) are added to the
  provider request as ``lessons`` and listed in the context manifest; each use is an
  event."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from governed_harness.domain.enums import ActorType, FindingSeverity, MemoryLevel, ResultStatus
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    Execution,
    Finding,
    MemoryRecord,
    PhaseExecution,
    Task,
    ValidationResult,
    utc_now,
)
from governed_harness.memory import MemoryStore
from governed_harness.memory.store import is_effective

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

LESSON_PREFIX = "lesson:"
DEFAULT_RECURRENCE_RUNS = 2
DEFAULT_MAX_LESSONS = 10
_SEVERE = {FindingSeverity.MEDIUM, FindingSeverity.HIGH, FindingSeverity.CRITICAL}
_BLOCKING = {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
LESSON_ACTOR = Actor(actor_type=ActorType.HARNESS, actor_id="harness.lessons", version="1")


def lesson_text(finding: Finding) -> str:
    """A short, reusable statement of what to avoid, from the finding's own words."""
    advice = f" {finding.recommendation}" if finding.recommendation else ""
    return f"Avoid {finding.rule_id}: {finding.message[:240]}.{advice[:240]}".strip()


class Lessons:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def config(self) -> Any:
        return self.results.project.memory

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.config.enabled)

    # ----- proposals -----------------------------------------------------------------------
    def corrected_findings(self, execution: Execution) -> list[Finding]:
        """Findings of the ChangeSets a correction was authorized for (MEDIUM or worse)."""
        state = self.results.s.state
        digests: set[str] = set()
        for event in self.results.s.events.list(execution.execution_id):
            if event.event_type != "correction.authorized":
                continue
            digest = event.payload.get("changeSetDigest") or event.payload.get(
                "approvedChangeSetDigest"
            )
            if isinstance(digest, str):
                digests.add(digest)
        if not digests:
            return []
        validations = [
            item
            for item in state.list(
                "validation", ValidationResult, execution_id=execution.execution_id
            )
            if item.change_set_digest in digests
        ]
        # What caused the correction: the findings of mandatory validations that did not pass,
        # and blocking findings of any validation. Optional validators' findings did not.
        failing = {
            finding_id
            for item in validations
            if item.mandatory and item.status is not ResultStatus.PASSED
            for finding_id in item.finding_ids
        }
        ids = {finding_id for item in validations for finding_id in item.finding_ids}
        return [
            item
            for item in state.list("finding", Finding, execution_id=execution.execution_id)
            if item.finding_id in ids
            and item.introduced is not False
            and (
                (item.finding_id in failing and item.severity in _SEVERE)
                or item.severity in _BLOCKING
            )
        ]

    def propose(self, execution: Execution) -> list[MemoryRecord]:
        """Propose (or recur) one lesson per (validator, rule) that caused a correction."""
        if not self.enabled:
            return []
        findings = self.corrected_findings(execution)
        groups: dict[tuple[str, str], list[Finding]] = {}
        for item in findings:
            groups.setdefault((item.validator_id, item.rule_id), []).append(item)
        store = MemoryStore(self.results.s.state)
        existing = {
            record.key: record
            for record in sorted(
                store.list_project(execution.project_id), key=lambda item: item.created_at
            )
            if record.key.startswith(LESSON_PREFIX)
        }
        recurrence = self.config.recurrence_runs or DEFAULT_RECURRENCE_RUNS
        proposed: list[MemoryRecord] = []
        for (validator_id, rule_id), items in sorted(groups.items()):
            key = f"{LESSON_PREFIX}{validator_id}:{rule_id}"
            previous = existing.get(key)
            runs = list(previous.value.get("runs", [])) if previous else []
            if execution.execution_id in runs:
                continue
            runs.append(execution.execution_id)
            paths = sorted(
                {
                    *(previous.value.get("paths", []) if previous else []),
                    *(item.location.path for item in items if item.location and item.location.path),
                }
            )[:20]
            approved = bool(
                (previous is not None and previous.approved)
                or (self.config.auto_approve_recurring and len(runs) >= recurrence)
            )
            record = MemoryRecord(
                memory_id=new_id("mem"),
                project_id=execution.project_id,
                level=MemoryLevel.PROJECT,
                key=key,
                value={
                    "kind": "lesson",
                    "validatorId": validator_id,
                    "ruleId": rule_id,
                    "lesson": lesson_text(items[0]),
                    "paths": paths,
                    "runs": runs,
                    "occurrences": int(previous.value.get("occurrences", 0) if previous else 0)
                    + len(items),
                    "recurring": len(runs) >= recurrence,
                },
                provenance=self.results.engine._provenance(execution).model_copy(
                    update={
                        "actor": LESSON_ACTOR,
                        "source_refs": tuple(
                            f"record://finding/{item.finding_id}" for item in items[:10]
                        ),
                    }
                ),
                execution_id=execution.execution_id,
                supersedes=previous.memory_id if previous else None,
                approved=approved,
            )
            store.put(record)
            proposed.append(record)
            self.results.s.events.append(
                execution.execution_id,
                "lesson.proposed",
                {
                    "memoryId": record.memory_id,
                    "key": key,
                    "runs": len(runs),
                    "approved": approved,
                    "autoApproved": approved and not (previous and previous.approved),
                    "findingIds": [item.finding_id for item in items[:10]],
                },
                actor=LESSON_ACTOR,
            )
        return proposed

    # ----- use -------------------------------------------------------------------------------
    def active(self, execution: Execution, task: Task, paths: list[str]) -> list[MemoryRecord]:
        """Approved, unexpired lessons relevant to the task: a rule of a validator the project
        runs, or a path the task touches."""
        if not self.enabled:
            return []
        store = MemoryStore(self.results.s.state)
        now = utc_now()
        records = [
            record
            for record in store.list_project(execution.project_id)
            if record.key.startswith(LESSON_PREFIX)
            and is_effective(record)
            and not (record.valid_until and record.valid_until <= now)
        ]
        superseded = {record.supersedes for record in records if record.supersedes}
        records = [record for record in records if record.memory_id not in superseded]
        validators = {item.validator_id for item in self.results.s.resolved.effective_validators}
        relevant: list[MemoryRecord] = []
        for record in sorted(records, key=lambda item: item.key):
            validator = str(record.value.get("validatorId", ""))
            lesson_paths = [str(item) for item in record.value.get("paths", [])]
            touches = any(
                fnmatch.fnmatchcase(path, pattern) or path == pattern
                for path in paths
                for pattern in lesson_paths
            )
            if validator in validators or validator.startswith(("harness.", "review.")) or touches:
                relevant.append(record)
        return relevant[: self.config.max_lessons or DEFAULT_MAX_LESSONS]

    def for_request(
        self, execution: Execution, phase: PhaseExecution, task: Task, paths: list[str]
    ) -> list[dict[str, Any]]:
        lessons = self.active(execution, task, paths)
        if not lessons:
            return []
        self.results.s.events.append(
            execution.execution_id,
            "lesson.applied",
            {
                "memoryIds": [item.memory_id for item in lessons],
                "keys": [item.key for item in lessons],
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return [
            {
                "memoryId": item.memory_id,
                "rule": item.value.get("ruleId"),
                "validator": item.value.get("validatorId"),
                "lesson": item.value.get("lesson"),
                "paths": item.value.get("paths", []),
            }
            for item in lessons
        ]
