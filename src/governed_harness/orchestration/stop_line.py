"""Stop the line (#52, N2): what happens to the changes of a run that stops without approval.

Before 1.1 a rejected, failed, timed-out or cancelled run left its changes in the workspace: the
next step of a plan started on top of code the harness never approved, and the evaluated
deliverable contained seven such steps. Under ``governance.stopTheLine``:

* ``restore``: the changes since DISCOVERY are kept as a quarantined patch (an artifact on the
  run's chain, with the list of paths and the content of every restored path) and the workspace
  is restored to the baseline; a file whose baseline content was not recorded as text cannot be
  restored and the line is blocked instead. A run that stopped after IMPLEMENTATION and is
  continued gets its quarantined changes back before it is evaluated again, unless a new
  attempt was written over a restored path (the quarantine is then superseded): the recorded
  content must match its digest, otherwise nothing is written and the run is BLOCKED;
* ``block``: the changes stay, and no new run starts in the workspace until a person
  quarantines them with ``harness run quarantine`` (which restores the baseline) or the run is
  approved.

Under either mode a task that declares ``ownedPaths`` gets a HIGH finding in VERIFICATION for
every changed path outside them, so an out-of-scope write is no longer silent."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities.authorizer import contained_path
from governed_harness.domain.enums import (
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.domain.models import ChangeSet, Execution, utc_now
from governed_harness.evidence.hashing import sha256_bytes, sha256_file
from governed_harness.orchestration.workspace_ops import restore_changes
from governed_harness.runtime.guard import IGNORED_PATTERNS
from governed_harness.runtime.workspace import WorkspaceDiff, WorkspaceSnapshot
from governed_harness.validators import ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

OWNED_PATHS_ID = "harness.owned-paths"
STOPPED_STATUSES = frozenset({ResultStatus.FAILED, ResultStatus.TIMED_OUT, ResultStatus.ERROR})
_STOPPED_PHASES = frozenset({PhaseId.IMPLEMENTATION, PhaseId.VERIFICATION})
REAPPLY_PHASES = frozenset(
    {PhaseId.VERIFICATION, PhaseId.INDEPENDENT_REVIEW, PhaseId.DECISION, PhaseId.CLOSURE}
)
"""The phases that evaluate the run's ChangeSet: a run quarantined in one of them and continued
gets its quarantined changes back. A run quarantined in IMPLEMENTATION implements again from the
baseline."""


def _write_checked(path: str, target: Path, data: bytes | None) -> None:
    """Write ``data`` to ``target`` (``None`` deletes it) and check it holds it afterwards."""
    if data is None:
        target.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    expected = sha256_bytes(data) if data is not None else None
    if (sha256_file(target) if target.is_file() else None) != expected:
        raise OSError(f"{path} does not hold the quarantined content after writing")


def _undo_writes(written: list[tuple[Path, bytes | None]]) -> None:
    """Put back the previous content of each written target, the last written first."""
    for target, previous in reversed(written):
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)


class StopLine:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def mode(self) -> str:
        return self.results.project.governance_settings.stop_the_line or "off"

    def _flag(self, project_id: str) -> str:
        return f"stopline:{project_id}"

    # ----- new runs --------------------------------------------------------------------------
    def check_new_run(self, project_id: str) -> None:
        """Refuse a new run while a stopped run's changes are in the workspace (``block``)."""
        if self.mode == "off":
            return
        stopped = self.results.flag_json(self._flag(project_id))
        if isinstance(stopped, dict) and stopped.get("executionId"):
            raise PolicyViolationError(
                f"run {stopped['executionId']} stopped without approval ({stopped.get('reason')}) "
                f"and its changes are still in the workspace: quarantine them with harness run "
                f"quarantine --run {stopped['executionId']} (governance.stopTheLine)"
            )

    # ----- stops ----------------------------------------------------------------------------
    def after_continue(self, execution: Execution) -> Execution:
        """A run that ends a ``run continue`` failed in IMPLEMENTATION or VERIFICATION, with no
        correction left, has stopped: its changes are quarantined or the line is blocked."""
        if self.mode == "off":
            return execution
        if execution.status in STOPPED_STATUSES and execution.current_phase in _STOPPED_PHASES:
            self.stop(execution, f"{execution.current_phase} {execution.status}")
        if execution.status is ResultStatus.PASSED:
            self.release(execution)
        return self.results.engine.get_execution(execution.execution_id)

    def stop(self, execution: Execution, reason: str) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        if self.mode == "restore":
            return self.quarantine(execution, reason)
        record = self._record(execution, reason, restore=False)
        if record is not None:
            self.results.set_flag_json(
                self._flag(execution.project_id),
                {"executionId": execution.execution_id, "reason": reason},
            )
        return record

    def quarantine(self, execution: Execution, reason: str) -> dict[str, Any] | None:
        """Keep the run's changes as a patch and restore the baseline (also the command
        ``harness run quarantine``)."""
        record = self._record(execution, reason, restore=True)
        stopped = self.results.flag_json(self._flag(execution.project_id))
        unrestorable = bool(record and record["unrestorablePaths"])
        if unrestorable:
            self.results.set_flag_json(
                self._flag(execution.project_id),
                {"executionId": execution.execution_id, "reason": reason + " (unrestorable)"},
            )
        elif isinstance(stopped, dict) and stopped.get("executionId") == execution.execution_id:
            self.results.s.state.set_flag(self._flag(execution.project_id), "")
        return record

    def release(self, execution: Execution) -> None:
        stopped = self.results.flag_json(self._flag(execution.project_id))
        if isinstance(stopped, dict) and stopped.get("executionId") == execution.execution_id:
            self.results.s.state.set_flag(self._flag(execution.project_id), "")

    def _kept(self, execution: Execution) -> set[str]:
        """The frozen acceptance tests of a run that is still open stay in the workspace when
        its changes are restored (#81): a person approved them and the harness wrote them, and
        the next VERIFICATION of the run checks that they are unchanged. A closed, cancelled or
        rejected run keeps nothing."""
        from governed_harness.orchestration.engine import run_is_open

        latest = self.results.engine.get_execution(execution.execution_id)
        if not run_is_open(latest):
            return set()
        return self.results.acceptance.untouched(latest)

    def _record(self, execution: Execution, reason: str, *, restore: bool) -> dict[str, Any] | None:
        results = self.results
        diff = results.baseline_changes(execution)
        contents = results.baseline_contents(execution)
        if diff is None or contents is None:
            return None
        workspace = results.s.paths.workspace
        if not diff.changes:
            return None
        patch = results.s.artifacts.put(
            diff.unified_diff,
            media_type="text/x-diff",
            metadata={"kind": "quarantined-patch", "executionId": execution.execution_id},
        )
        restored: list[str] = []
        unrestorable: list[str] = []
        kept = self._kept(execution)
        captured: dict[str, dict[str, Any]] = {}
        if restore:
            captured = self._capture(execution, diff, kept)
            restored, unrestorable = restore_changes(
                workspace,
                contents,
                WorkspaceDiff(
                    changes=tuple(item for item in diff.changes if item.path not in kept),
                    unified_diff=diff.unified_diff,
                    digest=diff.digest,
                ),
            )
        record: dict[str, Any] = {
            "executionId": execution.execution_id,
            "reason": reason,
            "mode": "restore" if restore else "block",
            "paths": [{"path": item.path, "status": item.status} for item in diff.changes],
            "patchRef": patch.uri,
            "restoredPaths": restored,
            "unrestorablePaths": unrestorable,
            "phase": execution.current_phase.value,
        }
        if restore:
            record["quarantinedContents"] = [
                captured[path] for path in restored if path in captured
            ]
        if kept:
            record["keptPaths"] = sorted(kept)
        ref = results.record_json(
            execution,
            execution.current_phase,
            record,
            kind="workspace-quarantine",
            summary=(
                f"Stop the line ({reason}): {len(diff.changes)} changed path(s) "
                + (f"quarantined, {len(restored)} restored" if restore else "kept; line blocked")
            ),
        )
        results.s.events.append(
            execution.execution_id,
            "workspace.quarantined" if restore else "workspace.line-stopped",
            {**record, "evidenceRef": ref},
        )
        return record

    def _capture(
        self, execution: Execution, diff: WorkspaceDiff, kept: set[str]
    ) -> dict[str, dict[str, Any]]:
        """The content of every changed path about to be restored, stored as an artifact with
        the digest it had, so that ``run continue`` can put it back intact."""
        workspace = self.results.s.paths.workspace
        captured: dict[str, dict[str, Any]] = {}
        for item in diff.changes:
            if item.path in kept:
                continue
            entry: dict[str, Any] = {"path": item.path, "digest": item.after_digest}
            if item.after_digest is not None:
                try:
                    data: bytes | None = contained_path(workspace, Path(item.path)).read_bytes()
                except OSError:
                    data = None
                if data is not None and sha256_bytes(data) == item.after_digest:
                    # Stored as written (F4): run continue puts back exactly these bytes, which
                    # a redaction would change; the workspace held them already.
                    entry["contentRef"] = self.results.s.artifacts.put(
                        data,
                        metadata={
                            "kind": "quarantined-content",
                            "executionId": execution.execution_id,
                            "path": item.path,
                            "unredacted": True,
                        },
                        redact=False,
                    ).uri
            captured[item.path] = entry
        return captured

    # ----- run continue after a quarantine ---------------------------------------------------
    def pending(self, execution: Execution) -> dict[str, Any] | None:
        """The latest quarantine of the run that was not put back yet."""
        pending: dict[str, Any] | None = None
        for event in self.results.s.events.list(execution.execution_id):
            if event.event_type == "workspace.quarantined":
                pending = dict(event.payload)
            elif event.event_type in {
                "workspace.quarantine.reapplied",
                "workspace.quarantine.superseded",
            }:
                pending = None
        return pending

    def _rewritten(self, execution: Execution, record: dict[str, Any]) -> list[str]:
        """The restored paths that now hold neither their baseline nor their quarantined
        content: a new attempt was written over the restored baseline (a person or an
        embedded session correcting the run)."""
        workspace = self.results.s.paths.workspace
        baseline = self.results.baseline_snapshot(execution)
        if baseline is None:
            return []
        digests = {
            str(item["path"]): item.get("digest")
            for item in record.get("quarantinedContents") or []
        }
        rewritten: list[str] = []
        for path in record["restoredPaths"]:
            target = contained_path(workspace, Path(path))
            current = sha256_file(target) if target.is_file() else None
            state = baseline.files.get(path)
            if current != (state.digest if state else None) and (
                path not in digests or current != digests[path]
            ):
                rewritten.append(path)
        return rewritten

    def reapply(self, execution: Execution) -> Execution | None:
        """Put the quarantined changes of a run back into the workspace before ``run continue``
        evaluates it again; the run BLOCKED when they cannot be put back intact, ``None``
        otherwise. Nothing is left written unless every path is put back, and nothing is ever
        written over a new attempt: when a restored path holds new content the quarantine is
        superseded and the run is evaluated on the workspace as it is."""
        execution = self.results.engine.get_execution(execution.execution_id)
        if execution.current_phase not in REAPPLY_PHASES:
            return None
        record = self.pending(execution)
        if record is None or not record.get("restoredPaths"):
            return None
        phase = record.get("phase")
        if phase is None or PhaseId(phase) not in REAPPLY_PHASES:
            return None
        events = self.results.s.events
        rewritten = self._rewritten(execution, record)
        if rewritten:
            events.append(
                execution.execution_id,
                "workspace.quarantine.superseded",
                {"evidenceRef": record.get("evidenceRef"), "rewrittenPaths": rewritten},
            )
            return None
        planned, problems = self._plan_reapply(execution, record)
        if not problems:
            problems = self._write(planned)
        if problems:
            events.append(
                execution.execution_id,
                "workspace.quarantine.reapply-refused",
                {"evidenceRef": record.get("evidenceRef"), "problems": problems},
            )
            blocked = execution.model_copy(
                update={
                    "status": ResultStatus.BLOCKED,
                    "terminal_reason": (
                        "The quarantined changes of the run cannot be put back intact "
                        "(governance.stopTheLine): "
                        + "; ".join(problems[:10])
                        + ". Write the change again (the quarantined patch is on the run's "
                        "chain) and run continue, or cancel the run."
                    ),
                    "updated_at": utc_now(),
                }
            )
            self.results.engine._save_execution(blocked)
            return blocked
        events.append(
            execution.execution_id,
            "workspace.quarantine.reapplied",
            {
                "evidenceRef": record.get("evidenceRef"),
                "patchRef": record.get("patchRef"),
                "paths": [path for path, _target, _data in planned],
            },
        )
        return None

    def _plan_reapply(
        self, execution: Execution, record: dict[str, Any]
    ) -> tuple[list[tuple[str, Path, bytes | None]], list[str]]:
        """What to write for each restored path (``None`` deletes it) and why it cannot be."""
        workspace = self.results.s.paths.workspace
        baseline = self.results.baseline_snapshot(execution)
        entries = {str(item["path"]): item for item in record.get("quarantinedContents") or []}
        planned: list[tuple[str, Path, bytes | None]] = []
        problems: list[str] = []
        for path in record["restoredPaths"]:
            item, problem = self._plan_path(workspace, baseline, entries.get(path), path)
            if problem is not None:
                problems.append(problem)
            elif item is not None:
                planned.append(item)
        return planned, problems

    def _plan_path(
        self,
        workspace: Path,
        baseline: WorkspaceSnapshot | None,
        entry: dict[str, Any] | None,
        path: str,
    ) -> tuple[tuple[str, Path, bytes | None] | None, str | None]:
        """What to write for one restored path (nothing when it already holds the quarantined
        content), or why it cannot be written."""
        if entry is None or baseline is None:
            return None, f"{path}: no recorded content"
        target = contained_path(workspace, Path(path))
        digest = entry.get("digest")
        current = sha256_file(target) if target.is_file() else None
        state = baseline.files.get(path)
        if current == digest:
            return None, None  # already holds the quarantined content
        if current != (state.digest if state else None):
            return None, f"{path}: changed since the quarantine"
        if digest is None:
            return (path, target, None), None
        data = self._quarantined_content(entry)
        if data is None or sha256_bytes(data) != digest:
            return None, f"{path}: quarantined content missing or not matching its digest"
        return (path, target, data), None

    def _quarantined_content(self, entry: dict[str, Any]) -> bytes | None:
        """The stored quarantined content of a path, ``None`` when it cannot be read."""
        if not entry.get("contentRef"):
            return None
        try:
            return self.results.s.artifacts.get(str(entry["contentRef"]))
        except (OSError, ValueError):
            return None

    def _write(self, planned: list[tuple[str, Path, bytes | None]]) -> list[str]:
        """Write the planned contents; on a failure undo what was written and report it."""
        written: list[tuple[Path, bytes | None]] = []
        try:
            for path, target, data in planned:
                written.append((target, target.read_bytes() if target.is_file() else None))
                _write_checked(path, target, data)
        except OSError as error:
            _undo_writes(written)
            return [f"writing the quarantined content failed: {error}"]
        return []

    # ----- out-of-scope writes ---------------------------------------------------------------
    def owned_paths_output(
        self, execution: Execution, change_set: ChangeSet
    ) -> ValidatorOutput | None:
        if self.mode == "off":
            return None
        task = self.results.engine.run_task(execution)
        owned = task.metadata.get("ownedPaths")
        if not owned or task.implementation.mode == "patch":
            return None
        owned_set = {str(item) for item in owned}
        # The frozen acceptance tests (or BDD feature files) were written by the harness after
        # a person approved them, not by the agent: they are not outside the task's scope.
        acceptance = self.results.acceptance.state(execution) or {}
        owned_set |= set(acceptance.get("frozen") or {})
        since = self.results.baseline_changes(execution)
        if since is None:
            return None
        outside = [
            item.path
            for item in since.changes
            if item.path not in owned_set
            and not any(fnmatch.fnmatchcase(item.path, pattern) for pattern in IGNORED_PATTERNS)
        ]
        findings = tuple(
            self.results.record_finding(
                execution,
                validator_id=OWNED_PATHS_ID,
                rule_id="workspace.outside-owned-paths",
                category="workspace-integrity",
                severity=FindingSeverity.HIGH,
                message=f"{path} changed but is not one of the task's ownedPaths",
                path=path,
                recommendation=(
                    "Keep the change inside the declared paths, or declare the path in the task."
                ),
                introduced=True,
            )
            for path in outside
        )
        result = self.results.record_validation(
            execution,
            validator_id=OWNED_PATHS_ID,
            digest=change_set.digest,
            status=ResultStatus.FAILED if findings else ResultStatus.PASSED,
            kind=ValidationKind.POLICY_VIOLATION if findings else ValidationKind.SUCCESS,
            mandatory=True,
            summary=(
                f"{len(findings)} changed path(s) outside the task's ownedPaths"
                if findings
                else "Every changed path is one of the task's ownedPaths"
            ),
            findings=findings,
            evidence_refs=(change_set.diff_ref,),
        )
        return ValidatorOutput(result, ())
