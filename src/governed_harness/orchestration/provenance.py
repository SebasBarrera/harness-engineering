"""Provenance per component and the agent's self-report (``provenance``, since 1.1).

``provenance.agentSnapshots``: before and after every agent invocation the harness records the
state of the ChangeSet scope as a manifest of digests (only the files that differ from the
baseline, so the record stays small in a large repository). A file is attributed to the
invocation that changed it; a difference that appears between two invocations, or after the last
one, was produced by no agent invocation and is recorded, per file, as an out-of-band edit
(``provenance.out-of-band-edit`` events). When the run reaches DECISION the provenance of every
ChangeSet file (``AGENT`` with the invocation, or ``OUT_OF_BAND``) is recorded as evidence. This
is attribution, not a gate condition: an edit by the person who reviews is legitimate, and the
record says that it happened.

``provenance.selfReport``: the agent's structured self-report is stored as data of quality
``REPORTED`` and contrasted with the ChangeSet recorded after the same invocation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from governed_harness.agents.base import AgentExecutionResult
from governed_harness.agents.self_report import parse_self_report
from governed_harness.domain.enums import EvidenceKind, PhaseId
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    AgentSelfReport,
    ChangeSet,
    ComponentProvenance,
    Execution,
    FileProvenance,
    OutOfBandEdit,
    PhaseExecution,
    SelfReportContrast,
)
from governed_harness.evidence import sha256_json

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import EngineHost

Manifest = dict[str, str | None]
"""Path -> digest of every file of the ChangeSet scope that differs from the baseline
(``None``: deleted)."""


def _status(before: str | None, after: str | None) -> Literal["ADDED", "MODIFIED", "DELETED"]:
    if before is None:
        return "ADDED"
    if after is None:
        return "DELETED"
    return "MODIFIED"


class ProvenanceRecorder:
    def __init__(self, engine: EngineHost) -> None:
        self.engine = engine
        self.s = engine.s
        self._baselines: dict[str, dict[str, str]] = {}

    def _baseline_digest(self, execution: Execution, path: str) -> str | None:
        if execution.execution_id not in self._baselines:
            self._baselines[execution.execution_id] = self.engine.baseline_digests(execution)
        return self._baselines[execution.execution_id].get(path)

    # ----- settings ----------------------------------------------------------------------
    @property
    def snapshots_enabled(self) -> bool:
        return bool(self.s.resolved.project.provenance_settings.agent_snapshots)

    @property
    def self_report_enabled(self) -> bool:
        return bool(self.s.resolved.project.provenance_settings.self_report)

    # ----- snapshots around agent invocations ----------------------------------------------
    def current_manifest(self, execution: Execution) -> Manifest:
        diff = self.engine._compute_owned_diff(execution)
        return {change.path: change.after_digest for change in diff.changes}

    def _snapshots(self, execution_id: str) -> list[dict[str, Any]]:
        raw = self.s.state.get_flag(f"agentsnapshots:{execution_id}")
        if not raw:
            return []
        refs: list[str] = json.loads(raw)
        return [json.loads(self.s.artifacts.get(ref)) for ref in refs]

    def before_invocation(self, execution: Execution) -> None:
        """Record what changed since the last invocation before the agent runs again."""
        if not self.snapshots_enabled:
            return
        self.s.state.set_flag(
            f"agentpre:{execution.execution_id}",
            json.dumps(self.current_manifest(execution), sort_keys=True),
        )

    def after_invocation(self, execution: Execution, result: AgentExecutionResult) -> None:
        if not self.snapshots_enabled:
            return
        pre_raw = self.s.state.get_flag(f"agentpre:{execution.execution_id}")
        before: Manifest = json.loads(pre_raw) if pre_raw else {}
        after = self.current_manifest(execution)
        previous = self._snapshots(execution.execution_id)
        if previous:
            self._out_of_band(
                execution,
                previous[-1]["after"],
                before,
                previous[-1]["invocationId"],
                PhaseId.IMPLEMENTATION,
            )
        changed = sorted(
            path for path in set(before) | set(after) if before.get(path) != after.get(path)
        )
        record = {
            "invocationId": result.invocation.invocation_id,
            "provider": result.invocation.provider,
            "before": before,
            "after": after,
            "changed": changed,
            "digest": sha256_json(after),
        }
        ref = self.s.artifacts.put_json(
            record,
            metadata={"kind": "agent-snapshot", "executionId": execution.execution_id},
        )
        refs = [
            *json.loads(self.s.state.get_flag(f"agentsnapshots:{execution.execution_id}") or "[]"),
            ref.uri,
        ]
        self.s.state.set_flag(f"agentsnapshots:{execution.execution_id}", json.dumps(refs))
        self.s.events.append(
            execution.execution_id,
            "provenance.agent.snapshot",
            {
                "invocationId": result.invocation.invocation_id,
                "snapshotRef": ref.uri,
                "changedPaths": changed,
                "scopeDigest": record["digest"],
            },
        )

    def _out_of_band(
        self,
        execution: Execution,
        agent_state: Manifest,
        current: Manifest,
        after_invocation_id: str | None,
        detected_in: PhaseId,
    ) -> list[OutOfBandEdit]:
        edits: list[OutOfBandEdit] = []
        recorded = {
            (item["path"], item.get("currentDigest"))
            for item in json.loads(
                self.s.state.get_flag(f"outofband:{execution.execution_id}") or "[]"
            )
        }
        for path in sorted(set(agent_state) | set(current)):
            if (path in agent_state) == (path in current) and agent_state.get(path) == current.get(
                path
            ):
                continue
            # A path missing from a manifest has its baseline content; None is a deletion.
            agent_digest = agent_state.get(path, self._baseline_digest(execution, path))
            current_digest = current.get(path, self._baseline_digest(execution, path))
            if agent_digest == current_digest:
                continue
            status = _status(agent_digest, current_digest)
            edit = OutOfBandEdit(
                path=path,
                status=status,
                agent_digest=agent_digest,
                current_digest=current_digest,
                after_invocation_id=after_invocation_id,
                detected_in=detected_in,
            )
            edits.append(edit)
            if (path, current_digest) in recorded:
                continue
            recorded.add((path, current_digest))
            self.s.events.append(
                execution.execution_id,
                "provenance.out-of-band-edit",
                edit.model_dump(mode="json", by_alias=True),
            )
        self.s.state.set_flag(
            f"outofband:{execution.execution_id}",
            json.dumps(
                [
                    {"path": path, "currentDigest": digest}
                    for path, digest in sorted(recorded, key=str)
                ]
            ),
        )
        return edits

    def attribute(self, execution: Execution, change_set: ChangeSet, phase_id: PhaseId) -> None:
        """Record who produced each file of the ChangeSet (once per ChangeSet digest)."""
        if not self.snapshots_enabled:
            return
        snapshots = self._snapshots(execution.execution_id)
        if not snapshots:
            return
        key = f"provenance:{execution.execution_id}:{change_set.digest}"
        if self.s.state.get_flag(key):
            return
        writer: dict[str, tuple[str | None, str]] = {}
        for snapshot in snapshots:
            for path in snapshot["changed"]:
                writer[path] = (snapshot["after"].get(path), snapshot["invocationId"])
        last = snapshots[-1]
        current = {item.path: item.after_digest for item in change_set.files}
        edits = self._out_of_band(execution, last["after"], current, last["invocationId"], phase_id)
        edited = {item.path for item in edits}
        files = []
        for item in change_set.files:
            written = writer.get(item.path)
            agent = (
                written is not None and written[0] == item.after_digest and item.path not in edited
            )
            files.append(
                FileProvenance(
                    path=item.path,
                    status=item.status,
                    digest=item.after_digest,
                    source="AGENT" if agent else "OUT_OF_BAND",
                    invocation_id=written[1] if agent and written else None,
                )
            )
        record = ComponentProvenance(
            provenance_id=new_id("provenance"),
            execution_id=execution.execution_id,
            change_set_digest=change_set.digest,
            detected_in=phase_id,
            files=tuple(files),
            out_of_band_edits=tuple(edits),
            agent_snapshot_refs=tuple(
                json.loads(
                    self.s.state.get_flag(f"agentsnapshots:{execution.execution_id}") or "[]"
                )
            ),
        )
        self.s.state.put(
            "component_provenance",
            record.provenance_id,
            record,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        ref = self.s.artifacts.put_json(
            record.model_dump(mode="json", by_alias=True),
            metadata={"kind": "component-provenance", "executionId": execution.execution_id},
        )
        out_of_band = sum(1 for item in files if item.source == "OUT_OF_BAND")
        self.engine._record_evidence(
            execution,
            phase_id,
            EvidenceKind.OTHER,
            ref,
            f"Provenance of {len(files)} ChangeSet file(s): {len(files) - out_of_band} by an agent "
            f"invocation, {out_of_band} out of band",
            supports=(change_set.change_set_id,),
        )
        self.s.state.set_flag(key, record.provenance_id)

    # ----- self-report ---------------------------------------------------------------------
    def record_self_report(
        self,
        execution: Execution,
        phase: PhaseExecution,
        result: AgentExecutionResult,
        change_set: ChangeSet | None,
    ) -> None:
        if not self.self_report_enabled or not self.engine._external_provider(
            execution.execution_id
        ):
            return
        parsed = parse_self_report(result.self_report)
        problems = parsed.problems if parsed else ("the agent's answer carried no self-report",)
        contrast = None
        if parsed is not None and change_set is not None:
            changed = {item.path for item in change_set.files}
            contrast = SelfReportContrast(
                change_set_digest=change_set.digest,
                declared_paths_not_in_change_set=tuple(
                    path for path in parsed.paths() if path not in changed
                ),
                unrequested_changes_in_change_set=tuple(
                    dict.fromkeys(
                        item.path
                        for item in parsed.unrequested_changes
                        if item.path and item.path in changed
                    )
                ),
            )
        report = AgentSelfReport(
            report_id=new_id("selfreport"),
            execution_id=execution.execution_id,
            invocation_id=result.invocation.invocation_id,
            provider=result.invocation.provider,
            assumptions=parsed.assumptions if parsed else (),
            alternatives_discarded=parsed.alternatives_discarded if parsed else (),
            low_confidence_areas=parsed.low_confidence_areas if parsed else (),
            unrequested_changes=parsed.unrequested_changes if parsed else (),
            problems=problems,
            contrast=contrast,
        )
        self.s.state.put(
            "agent_self_report",
            report.report_id,
            report,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        ref = self.s.artifacts.put_json(
            report.model_dump(mode="json", by_alias=True),
            metadata={"kind": "agent-self-report", "executionId": execution.execution_id},
        )
        self.engine._record_evidence(
            execution,
            phase.phase_id,
            EvidenceKind.OTHER,
            ref,
            f"Agent self-report (REPORTED, not verified): {len(report.assumptions)} assumption(s), "
            f"{len(report.low_confidence_areas)} low-confidence area(s), "
            f"{len(report.unrequested_changes)} unrequested change(s)",
            supports=(result.invocation.invocation_id,),
        )
        self.s.events.append(
            execution.execution_id,
            "agent.self_report.recorded",
            {
                "reportId": report.report_id,
                "invocationId": report.invocation_id,
                "quality": report.quality.value,
                "artifactRef": ref.uri,
                "problems": list(report.problems),
            },
        )
