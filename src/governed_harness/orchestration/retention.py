"""``harness gc``: apply ``retention.artifactDays`` and ``retention.eventDays``.

Both settings were written by ``harness init`` and read by nothing. Their semantics here, for
runs that ended (closed, cancelled or rejected; an open run is never touched) longer ago than
the setting, counted from the run's last update:

* ``artifactDays``: the run's artifacts (configuration snapshot, baseline, diffs, validator
  output, evidence, trace) are deleted from ``.harness/artifacts`` unless a run that is kept
  references the same content, and a ``retention.artifacts.pruned`` event on the run's chain
  lists them, so ``harness verify`` reports them as pruned rather than missing. The events and
  records stay.
* ``eventDays``: the run is removed entirely: its events, its records (memory records stay),
  its flags and the artifacts no kept run references.

Without ``--apply`` nothing is deleted and the report says what would be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import Execution

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices

PRUNED_EVENT = "retention.artifacts.pruned"
_ARTIFACT_PREFIX = "artifact://sha256/"
KEPT_RECORD_TYPES = frozenset({"memory", "task"})


@dataclass
class RetentionPlan:
    artifact_days: int | None
    event_days: int | None
    removed_runs: list[str] = field(default_factory=list)
    pruned_runs: dict[str, list[str]] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

    def as_dict(self, applied: bool) -> dict[str, Any]:
        return {
            "applied": applied,
            "artifactDays": self.artifact_days,
            "eventDays": self.event_days,
            "removedRuns": self.removed_runs,
            "prunedRuns": sorted(self.pruned_runs),
            "artifactsRemoved": len(self.artifacts),
            "artifacts": self.artifacts,
        }


def _days(retention: dict[str, Any], key: str) -> int | None:
    value = retention.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"retention.{key} must be a whole number of days: {value!r}")
    days: int = value
    return days


def _artifact_refs(value: Any) -> set[str]:
    """Every artifact URI inside a JSON value."""
    found: set[str] = set()
    if isinstance(value, str):
        if value.startswith(_ARTIFACT_PREFIX):
            found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            found |= _artifact_refs(item)
    elif isinstance(value, list):
        for item in value:
            found |= _artifact_refs(item)
    return found


class RetentionCollector:
    def __init__(self, services: EngineServices) -> None:
        self.s = services

    def plan(self, now: datetime | None = None) -> RetentionPlan:
        from governed_harness.orchestration.engine import run_is_open

        retention = dict(self.s.resolved.project.retention)
        plan = RetentionPlan(_days(retention, "artifactDays"), _days(retention, "eventDays"))
        moment = now or datetime.now(UTC)
        runs = self.s.state.list(
            "execution", Execution, project_id=self.s.resolved.project.project_id
        )
        kept: set[str] = set()
        candidates: set[str] = set()
        for run in runs:
            age = moment - run.updated_at
            ended = not run_is_open(run)
            refs = self._references(run)
            if ended and plan.event_days is not None and age >= timedelta(days=plan.event_days):
                plan.removed_runs.append(run.execution_id)
                candidates |= refs
            elif (
                ended
                and plan.artifact_days is not None
                and age >= timedelta(days=plan.artifact_days)
            ):
                pruned = refs - self._pruned(run.execution_id)
                if pruned:
                    plan.pruned_runs[run.execution_id] = sorted(pruned)
                    candidates |= pruned
            else:
                kept |= refs
        plan.artifacts = sorted(candidates - kept)
        removable = set(plan.artifacts)
        plan.pruned_runs = {
            run_id: [uri for uri in uris if uri in removable]
            for run_id, uris in plan.pruned_runs.items()
            if any(uri in removable for uri in uris)
        }
        return plan

    def apply(self, plan: RetentionPlan) -> None:
        from governed_harness.orchestration.engine import RunEngine

        for uri in plan.artifacts:
            self.s.artifacts.delete(uri)
        engine = RunEngine(self.s)
        for run_id, uris in plan.pruned_runs.items():
            self.s.events.append(
                run_id,
                PRUNED_EVENT,
                {"artifactRefs": uris, "artifactDays": plan.artifact_days},
            )
            engine.anchor_chain(run_id)
        for run_id in plan.removed_runs:
            self.s.events.delete_execution(run_id)
            self.s.state.delete_execution(run_id, keep=KEPT_RECORD_TYPES)

    def _references(self, run: Execution) -> set[str]:
        refs = {run.configuration_snapshot_ref, run.workflow_ref}
        for record_type in self.s.state.record_types(run.execution_id):
            if record_type in KEPT_RECORD_TYPES:
                continue
            for item in self.s.state.list_dicts(record_type, execution_id=run.execution_id):
                refs |= _artifact_refs(item)
        refs |= _artifact_refs(self.s.state.flags_for(run.execution_id))
        return {ref for ref in refs if ref.startswith(_ARTIFACT_PREFIX)}

    def _pruned(self, execution_id: str) -> set[str]:
        return {
            str(uri)
            for event in self.s.events.list(execution_id)
            if event.event_type == PRUNED_EVENT
            for uri in event.payload.get("artifactRefs", [])
        }
