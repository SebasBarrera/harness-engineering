"""Stop the line (#52, N2): what happens to the changes of a run that stops without approval.

Before 2.0 a rejected, failed, timed-out or cancelled run left its changes in the workspace: the
next step of a plan started on top of code the harness never approved, and the evaluated
deliverable contained seven such steps. Under ``governance.stopTheLine``:

* ``restore``: the changes since DISCOVERY are kept as a quarantined patch (an artifact on the
  run's chain, with the list of paths) and the workspace is restored to the baseline; a file
  whose baseline content was not recorded as text cannot be restored and the line is blocked
  instead;
* ``block``: the changes stay, and no new run starts in the workspace until a person
  quarantines them with ``harness run quarantine`` (which restores the baseline) or the run is
  approved.

Under either mode a task that declares ``ownedPaths`` gets a HIGH finding in VERIFICATION for
every changed path outside them, so an out-of-scope write is no longer silent."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from governed_harness.domain.enums import (
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.domain.models import ChangeSet, Execution
from governed_harness.orchestration.workspace_ops import restore_changes
from governed_harness.runtime.guard import IGNORED_PATTERNS
from governed_harness.runtime.workspace import WorkspaceDiff
from governed_harness.validators import ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

OWNED_PATHS_ID = "harness.owned-paths"
STOPPED_STATUSES = frozenset({ResultStatus.FAILED, ResultStatus.TIMED_OUT, ResultStatus.ERROR})
_STOPPED_PHASES = frozenset({PhaseId.IMPLEMENTATION, PhaseId.VERIFICATION})


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
        if restore:
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
        }
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
