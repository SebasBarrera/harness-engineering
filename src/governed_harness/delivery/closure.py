"""The closure commit (``delivery.closureCommit``, since 1.1).

A decision binds the digest of the workspace ChangeSet, but what a team merges is a commit. At
CLOSURE the harness writes the approved ChangeSet as one commit whose parent is ``HEAD``:

1. every ChangeSet file must still have the approved content (its ``afterDigest``) and every
   file it changes must have its baseline content at ``HEAD`` (its ``beforeDigest``);
2. the tree is built in a temporary index from ``HEAD`` plus the ChangeSet files, so the
   working tree, the index and the current branch are not touched;
3. the commit carries the trailers ``Harness-Run``, ``Harness-Task``, ``Harness-ChangeSet``,
   ``Harness-Decision`` and, for ``APPROVE_EXCEPTION``, ``Harness-Exception``;
4. its diff against ``HEAD`` is recomputed (``delivery.vcs.revision_diff``) and must equal the
   approved digest before any ref points to it;
5. ``branch`` creates the branch (``harness/{runId}`` by default); ``head`` moves the current
   branch to the commit and refreshes the index entries of the ChangeSet paths.

A workspace that is not a Git repository, has no commit, or whose ``HEAD`` does not hold the
baseline of a ChangeSet file (the run started on uncommitted changes, for example after an
earlier run whose change is not committed yet) gets no commit: the closure records why
(``delivery.commit.skipped``), because a commit on ``HEAD`` would carry more than what was
approved. Any other failure (the workspace no longer holds the approved content, a branch of the
same name that is not this run's, Git refusing an operation) stops CLOSURE: the run stays open
with the reason, and ``run continue`` tries again (a branch already holding this run's commit is
reused)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from governed_harness.configuration.models import DeliveryConfig
from governed_harness.delivery.vcs import Git, VcsError, revision_diff
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.models import ChangedFile, ChangeSet, HumanDecision, Task
from governed_harness.evidence.hashing import sha256_bytes

TRAILER_RUN = "Harness-Run"
TRAILER_TASK = "Harness-Task"
TRAILER_CHANGESET = "Harness-ChangeSet"
TRAILER_DECISION = "Harness-Decision"
TRAILER_EXCEPTION = "Harness-Exception"
_SUBJECT_CHARS = 72
_RATIONALE_CHARS = 200


class ClosureCommitError(VcsError):
    """The closure commit cannot be created; CLOSURE does not pass."""


@dataclass(frozen=True)
class ClosureCommit:
    mode: str
    status: str  # CREATED, REUSED or SKIPPED
    reason: str | None = None
    commit: str | None = None
    parent: str | None = None
    branch: str | None = None
    trailers: dict[str, str] = field(default_factory=dict)
    author: str | None = None
    subject: str | None = None

    def as_dict(self) -> dict[str, Any]:
        extra = {"author": self.author, "subject": self.subject} if self.author else {}
        return extra | {
            "mode": self.mode,
            "status": self.status,
            "reason": self.reason,
            "commit": self.commit,
            "parent": self.parent,
            "branch": self.branch,
            "trailers": self.trailers,
        }


def _one_line(text: str, limit: int) -> str:
    value = " ".join(text.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def closure_trailers(
    execution_id: str, task: Task, change_set: ChangeSet, decision: HumanDecision
) -> dict[str, str]:
    trailers = {
        TRAILER_RUN: execution_id,
        TRAILER_TASK: task.task_id,
        TRAILER_CHANGESET: change_set.digest,
        TRAILER_DECISION: f"{decision.decision_id} {decision.decision.value}",
    }
    if decision.decision is DecisionKind.APPROVE_EXCEPTION:
        trailers[TRAILER_EXCEPTION] = _one_line(decision.rationale, _RATIONALE_CHARS)
    return trailers


def commit_message(
    execution_id: str,
    task: Task,
    change_set: ChangeSet,
    decision: HumanDecision,
    trailers: dict[str, str],
) -> str:
    body = (
        f"Approved ChangeSet of harness run {execution_id} (task {task.task_id}): "
        f"{len(change_set.files)} file(s).\n"
        f"{decision.decision.value} by {decision.actor.actor_id} at "
        f"{decision.decided_at.isoformat()}: {_one_line(decision.rationale, _RATIONALE_CHARS)}"
    )
    footer = "\n".join(f"{key}: {value}" for key, value in trailers.items())
    return f"{_one_line(task.title, _SUBJECT_CHARS)}\n\n{body}\n\n{footer}\n"


def commit_author(git: Git, commit: str) -> tuple[str, str]:
    """Author (``Name <email>``) and subject of ``commit``, as Git shows them."""
    shown = git.text("show", "-s", "--format=%an <%ae>%x1f%s", commit)
    author, _, subject = shown.partition("")
    return author, subject


def branch_name(config: DeliveryConfig, execution_id: str, task_id: str) -> str:
    return config.branch_template.replace("{runId}", execution_id).replace("{taskId}", task_id)


def _mode(git: Git, head: str, relative: str, path: Path) -> str:
    """The mode the file has at ``HEAD`` (a ChangeSet does not record modes), or, for a new
    file, executable when the workspace file is."""
    listed = git.text("ls-tree", head, "--", relative)
    mode = listed.split(" ", 1)[0] if listed else ""
    if mode in {"100644", "100755"}:
        return mode
    return "100755" if os.name != "nt" and os.access(path, os.X_OK) else "100644"


def _reused_branch(
    git: Git,
    mode: str,
    branch: str,
    execution_id: str,
    change_set: ChangeSet,
    trailers: dict[str, str],
) -> ClosureCommit | None:
    """``REUSED`` when ``branch`` already holds this run's commit, ``None`` when it does not
    exist; any other existing branch is an error."""
    existing = git.resolve(f"refs/heads/{branch}")
    if existing is None:
        return None
    parent = git.resolve(f"{existing}~1")
    found = (
        {key: value for item in git.trailers(parent, existing) for key, value in item.items()}
        if parent
        else {}
    )
    if found.get(TRAILER_RUN) == execution_id and found.get(TRAILER_CHANGESET) == change_set.digest:
        return ClosureCommit(mode, "REUSED", None, existing, parent, branch, trailers)
    raise ClosureCommitError(
        f"branch {branch} already exists and does not hold this run's commit; "
        "set delivery.branch or delete the branch, then run continue"
    )


def _work_is_stale(workspace: Path, item: ChangedFile) -> bool:
    """The workspace no longer holds the approved content of ``item``."""
    target = workspace / item.path
    if item.after_digest is None:
        return target.exists()
    return not target.is_file() or sha256_bytes(target.read_bytes()) != item.after_digest


def _stale_paths(
    git: Git, workspace: Path, head: str, change_set: ChangeSet
) -> tuple[list[str], list[str]]:
    """The paths whose baseline ``HEAD`` does not hold, and those the workspace changed since."""
    stale_base = []
    stale_work = []
    for item in change_set.files:
        current = git.blob(head, item.path)
        if (sha256_bytes(current) if current is not None else None) != item.before_digest:
            stale_base.append(item.path)
        if _work_is_stale(workspace, item):
            stale_work.append(item.path)
    return stale_base, stale_work


def _write_tree(git: Git, workspace: Path, head: str, change_set: ChangeSet) -> str:
    """The tree of ``HEAD`` with the approved ChangeSet applied, built in a private index."""
    with tempfile.TemporaryDirectory(prefix="harness-index-") as directory:
        index = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        git.run("read-tree", head, env=index)
        for item in change_set.files:
            if item.after_digest is None:
                git.run("update-index", "--force-remove", "--", item.path, env=index)
                continue
            path = workspace / item.path
            blob = git.text("hash-object", "-w", "--no-filters", "--stdin", stdin=path.read_bytes())
            git.run(
                "update-index",
                "--add",
                "--cacheinfo",
                f"{_mode(git, head, item.path, path)},{blob},{item.path}",
                env=index,
            )
        return git.text("write-tree", env=index)


def create_closure_commit(
    workspace: Path,
    config: DeliveryConfig,
    *,
    execution_id: str,
    task: Task,
    change_set: ChangeSet,
    decision: HumanDecision,
) -> ClosureCommit:
    result = _create_closure_commit(workspace, config, execution_id, task, change_set, decision)
    if config.record_commit_author and result.commit:
        author, subject = commit_author(Git(workspace), result.commit)
        result = replace(result, author=author, subject=subject)
    return result


def _create_closure_commit(
    workspace: Path,
    config: DeliveryConfig,
    execution_id: str,
    task: Task,
    change_set: ChangeSet,
    decision: HumanDecision,
) -> ClosureCommit:
    mode = config.mode
    git = Git(workspace)
    if not git.is_repository():
        return ClosureCommit(mode, "SKIPPED", "the workspace is not a Git repository")
    head = git.resolve("HEAD")
    if head is None:
        return ClosureCommit(mode, "SKIPPED", "the repository has no commit to build on")
    trailers = closure_trailers(execution_id, task, change_set, decision)
    branch = branch_name(config, execution_id, task.task_id) if mode == "branch" else None
    if branch is not None:
        reused = _reused_branch(git, mode, branch, execution_id, change_set, trailers)
        if reused is not None:
            return reused
    stale_base, stale_work = _stale_paths(git, workspace, head, change_set)
    if stale_work:
        raise ClosureCommitError(
            f"the workspace no longer holds the approved content of {', '.join(sorted(stale_work))}"
        )
    if stale_base:
        # A commit on HEAD would also carry changes nobody approved in this run; the approval
        # stays valid, so CLOSURE records why there is no commit instead of creating one.
        return ClosureCommit(
            mode,
            "SKIPPED",
            f"HEAD ({head[:12]}) does not hold the baseline of {', '.join(sorted(stale_base))} "
            "(uncommitted changes when the run started, or HEAD moved): a commit on HEAD would "
            "carry more than the approved ChangeSet",
            parent=head,
            branch=branch,
            trailers=trailers,
        )
    tree = _write_tree(git, workspace, head, change_set)
    message = commit_message(execution_id, task, change_set, decision, trailers)
    commit = git.text(
        "commit-tree",
        tree,
        "-p",
        head,
        "-F",
        "-",
        stdin=message.encode("utf-8"),
        env=git.identity_env(),
    )
    recomputed = revision_diff(workspace, head, commit)
    if recomputed.digest != change_set.digest or recomputed.special:
        raise ClosureCommitError(
            f"the commit's diff recomputes to {recomputed.digest}, not the approved "
            f"{change_set.digest}; no ref was updated"
        )
    _update_ref(git, branch, execution_id, commit, head, change_set)
    return ClosureCommit(mode, "CREATED", None, commit, head, branch, trailers)


def _update_ref(
    git: Git,
    branch: str | None,
    execution_id: str,
    commit: str,
    head: str,
    change_set: ChangeSet,
) -> None:
    """Create ``branch`` at ``commit``, or move ``HEAD`` to it and refresh the index."""
    if branch is not None:
        git.run(
            "update-ref",
            "-m",
            f"harness closure {execution_id}",
            f"refs/heads/{branch}",
            commit,
            "",
        )
        return
    git.run("update-ref", "-m", f"harness closure {execution_id}", "HEAD", commit, head)
    paths = [item.path for item in change_set.files]
    if paths:
        git.run("reset", "-q", "--", *paths)
