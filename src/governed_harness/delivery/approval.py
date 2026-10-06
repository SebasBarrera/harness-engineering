"""``harness verify-approval``: is what gets merged what a person approved?

The command recomputes the ChangeSet digest of a range of commits (``--base`` to ``--head``, for
example the base and head of a pull request) with the computation the harness applies to the
workspace, and looks for a human decision ``APPROVE`` or ``APPROVE_EXCEPTION`` bound to exactly
that digest:

* in portable evidence bundles (``--bundle``, verified first: entries, event chain, decision
  records), and
* in the workspace's own ``.harness/state.db`` when it exists (a local or self-hosted run), with
  the event chain of each run verified.

The range passes only when an approval that has not expired matches the recomputed digest and
the range holds nothing a ChangeSet cannot (a mode-only change, a symbolic link, a submodule).
Any other change in the range (a commit after the closure commit, a rebase that changed the
context of a hunk, a file the ChangeSet excluded) changes the digest and the range fails: the
approval holds for what was approved, nothing else. The ``Harness-*`` trailers of the commits
in the range are reported to tell which run a commit claims; a trailer alone never approves."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from governed_harness.delivery.bundle import decision_entries, verify_bundle
from governed_harness.delivery.vcs import Git, revision_diff
from governed_harness.domain.models import utc_now
from governed_harness.events.sqlite_store import SQLiteEventStore


def local_database(root: Path) -> Path:
    """The workspace's state database: ``.harness/state.db``, or the registry
    ``runtime.stateDir`` names (#55) when the workspace has a project configuration."""
    config = root / ".harness" / "project.yaml"
    if config.is_file():
        # An unreadable configuration keeps the 1.0.0 path.
        with contextlib.suppress(Exception):
            from governed_harness.configuration.loader import load_project_config
            from governed_harness.runtime.state_location import resolve_state_location

            project = load_project_config(config)
            return resolve_state_location(
                root, project.project_id, project.runtime.state_dir, create=False
            ).database
    return root / ".harness" / "state.db"


def _local_approvals(root: Path, now: datetime) -> list[dict[str, Any]]:
    database = local_database(root)
    if not database.is_file():
        return []
    found: list[dict[str, Any]] = []
    with SQLiteEventStore(database) as store:
        events = store.list_all()
        runs = sorted({event.execution_id for event in events})
        for run in runs:
            try:
                store.verify_chain(run)
                valid, problems = True, []
            except ValueError as error:
                valid, problems = False, [str(error)]
            payloads = [event.as_dict() for event in events if event.execution_id == run]
            for decision in decision_entries(payloads, now):
                found.append(
                    {
                        **decision,
                        "source": "workspace",
                        "executionId": run,
                        "evidenceValid": valid,
                        "problems": problems,
                    }
                )
    return found


def verify_approval(
    root: Path,
    base: str,
    head: str = "HEAD",
    *,
    bundles: Sequence[Path] = (),
    use_workspace: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or utc_now()
    diff = revision_diff(root, base, head)
    approvals: list[dict[str, Any]] = []
    for bundle in bundles:
        report = verify_bundle(bundle, moment)
        for decision in report["decisions"]:
            approvals.append(
                {
                    **decision,
                    "source": f"bundle:{bundle}",
                    "executionId": report["executionId"],
                    "evidenceValid": report["valid"],
                    "problems": report["problems"],
                }
            )
        if not report["decisions"]:
            approvals.append(
                {
                    "source": f"bundle:{bundle}",
                    "executionId": report["executionId"],
                    "decision": None,
                    "approval": False,
                    "changeSetDigest": None,
                    "expired": False,
                    "evidenceValid": report["valid"],
                    "problems": report["problems"],
                }
            )
    if use_workspace:
        approvals.extend(_local_approvals(root, moment))
    for item in approvals:
        item["matches"] = bool(
            item.get("approval")
            and item.get("changeSetDigest") == diff.digest
            and not item.get("expired")
            and item.get("evidenceValid")
        )
    matched = [item for item in approvals if item["matches"]]
    trailers = Git(root).trailers(diff.base, diff.head)
    reasons: list[str] = []
    if not diff.paths:
        reasons.append("the range changes no file: there is nothing an approval could cover")
    if diff.special:
        reasons.append(
            "the range holds changes a ChangeSet cannot hold: " + ", ".join(diff.special)
        )
    if not matched:
        reasons.append(
            f"no valid, unexpired APPROVE or APPROVE_EXCEPTION is bound to {diff.digest}"
        )
    for item in trailers:
        claimed = item.get("Harness-ChangeSet")
        if claimed and claimed != diff.digest:
            reasons.append(
                f"commit {item['commit'][:12]} claims ChangeSet {claimed}; the range recomputes "
                f"to {diff.digest} (informational)"
            )
    passed = bool(matched) and not diff.special and bool(diff.paths)
    return {
        "status": "PASSED" if passed else "FAILED",
        "base": diff.base,
        "head": diff.head,
        "computedDigest": diff.digest,
        "files": [
            {
                "path": change.path,
                "status": change.status,
                "additions": change.additions,
                "deletions": change.deletions,
            }
            for change in diff.diff.changes
        ],
        "special": list(diff.special),
        "trailers": trailers,
        "approvals": approvals,
        "matchedApproval": matched[0] if matched else None,
        "reasons": reasons,
    }
