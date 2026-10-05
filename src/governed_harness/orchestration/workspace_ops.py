"""Restore and materialize workspace states for the agent-results settings.

* ``restore_changes`` undoes the changes between a recorded snapshot and the workspace (a
  read-only agent call that wrote, a quarantined attempt under ``governance.stopTheLine``).
* ``materialized`` builds a temporary copy of the workspace in which the changed paths have the
  content of a recorded snapshot, so a validator can run on the baseline (differential
  verification) or on the state before a correction (reproduce-first) without touching the
  workspace itself. Large dependency directories are linked, not copied.

Both need the text of the recorded snapshot; a binary or oversized file (no text recorded) cannot
be restored and is reported, never guessed."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from governed_harness.capabilities.authorizer import contained_path
from governed_harness.runtime.workspace import (
    WorkspaceDiff,
    WorkspaceSnapshot,
    WorkspaceSnapshotter,
)

LINKED_DIRECTORIES = frozenset({".venv", "venv", "node_modules"})
"""Top-level directories linked into a materialized copy instead of copied (dependencies)."""
SKIPPED_DIRECTORIES = frozenset(
    {".harness", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)


def restore_changes(
    workspace: Path, before: WorkspaceSnapshot, diff: WorkspaceDiff
) -> tuple[list[str], list[str]]:
    """Write back the recorded content of every path in ``diff`` (delete what was added).
    Returns the restored paths and the ones that could not be restored."""
    restored: list[str] = []
    unrestorable: list[str] = []
    for change in diff.changes:
        target = contained_path(workspace, Path(change.path))
        previous = before.files.get(change.path)
        if previous is None:
            target.unlink(missing_ok=True)
            restored.append(change.path)
        elif previous.text is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(previous.text.encode("utf-8"))
            restored.append(change.path)
        else:
            unrestorable.append(change.path)
    return restored, unrestorable


def changes_since(workspace: Path, before: WorkspaceSnapshot) -> WorkspaceDiff:
    snapshotter = WorkspaceSnapshotter(workspace)
    return snapshotter.diff(before, snapshotter.snapshot())


def _ignore(root: Path) -> object:
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name in SKIPPED_DIRECTORIES}
        if Path(directory).resolve() == root:
            skipped |= {name for name in names if name in LINKED_DIRECTORIES}
        return skipped

    return ignore


@contextlib.contextmanager
def materialized(
    workspace: Path, scratch: Path, revert_to: WorkspaceSnapshot, paths: list[str]
) -> Iterator[Path | None]:
    """A temporary copy of ``workspace`` where each of ``paths`` has its content in
    ``revert_to`` (or is absent when it is not in it). Yields ``None`` when a path cannot be
    reverted (its recorded content is not text). The copy is removed on exit."""
    root = workspace.resolve(strict=True)
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_root = Path(tempfile.mkdtemp(prefix="state-", dir=scratch))
    try:
        copy = target_root / "ws"
        shutil.copytree(root, copy, symlinks=True, ignore=_ignore(root))  # type: ignore[arg-type]
        for name in sorted(LINKED_DIRECTORIES):
            source = root / name
            if source.is_dir():
                (copy / name).symlink_to(source, target_is_directory=True)
        for path in paths:
            target = contained_path(copy, Path(path))
            previous = revert_to.files.get(path)
            if previous is None:
                target.unlink(missing_ok=True)
                continue
            if previous.text is None:
                yield None
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(previous.text.encode("utf-8"))
        yield copy
    finally:
        shutil.rmtree(target_root, ignore_errors=True)
