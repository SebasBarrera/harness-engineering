"""Restore and materialize workspace states for the agent-results settings.

* ``restore_changes`` undoes the changes between a recorded state and the workspace (a
  read-only agent call that wrote, a quarantined attempt under ``governance.stopTheLine``).
* ``materialized`` builds a temporary copy of the workspace in which the changed paths have the
  content of a recorded state, so a validator can run on the baseline (differential
  verification) or on the state before a correction (reproduce-first) without touching the
  workspace itself. Large dependency directories are linked, not copied.

The recorded content comes from a ``Contents`` callback, so the same code serves a snapshot with
the text of every file (1.0.0 baselines, in-memory snapshots) and a manifest baseline whose
content Git gives back (``workspace.baseline: manifest``). A file whose recorded content cannot be
given back is reported, never guessed."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Contents:
    """The recorded state of a path: whether it existed and its bytes (``None`` when they
    cannot be given back)."""

    existed: Callable[[str], bool]
    content: Callable[[str], bytes | None]


def snapshot_contents(snapshot: WorkspaceSnapshot) -> Contents:
    """The contents of a snapshot that holds the text of its files."""

    def content(path: str) -> bytes | None:
        state = snapshot.files.get(path)
        return state.text.encode("utf-8") if state and state.text is not None else None

    return Contents(lambda path: path in snapshot.files, content)


def restore_changes(
    workspace: Path, recorded: Contents, diff: WorkspaceDiff
) -> tuple[list[str], list[str]]:
    """Write back the recorded content of every path in ``diff`` (delete what was added).
    Returns the restored paths and the ones that could not be restored."""
    restored: list[str] = []
    unrestorable: list[str] = []
    for change in diff.changes:
        target = contained_path(workspace, Path(change.path))
        if not recorded.existed(change.path):
            target.unlink(missing_ok=True)
            restored.append(change.path)
            continue
        data = recorded.content(change.path)
        if data is None:
            unrestorable.append(change.path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        restored.append(change.path)
    return restored, unrestorable


def changes_since(workspace: Path, before: WorkspaceSnapshot) -> WorkspaceDiff:
    """Changes between an in-memory snapshot (with texts) and the workspace."""
    snapshotter = WorkspaceSnapshotter(workspace)
    return snapshotter.diff(before, snapshotter.snapshot())


def _ignore(root: Path) -> Callable[[str, list[str]], set[str]]:
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name in SKIPPED_DIRECTORIES}
        if Path(directory).resolve() == root:
            skipped |= {name for name in names if name in LINKED_DIRECTORIES}
        return skipped

    return ignore


@contextlib.contextmanager
def materialized(
    workspace: Path, scratch: Path, recorded: Contents, paths: list[str]
) -> Iterator[Path | None]:
    """A temporary copy of ``workspace`` where each of ``paths`` has its recorded content (or
    is absent when it did not exist). Yields ``None`` when a path cannot be reverted. The copy
    is removed on exit."""
    root = workspace.resolve(strict=True)
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_root = Path(tempfile.mkdtemp(prefix="state-", dir=scratch))
    try:
        copy = target_root / "ws"
        shutil.copytree(root, copy, symlinks=True, ignore=_ignore(root))
        for name in sorted(LINKED_DIRECTORIES):
            source = root / name
            if source.is_dir():
                (copy / name).symlink_to(source, target_is_directory=True)
        for path in paths:
            target = contained_path(copy, Path(path))
            if not recorded.existed(path):
                target.unlink(missing_ok=True)
                continue
            data = recorded.content(path)
            if data is None:
                yield None
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        yield copy
    finally:
        shutil.rmtree(target_root, ignore_errors=True)
