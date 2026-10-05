"""Fingerprints of what the ChangeSet leaves out (``governance.protectExcludedPaths``).

The ChangeSet is computed over the workspace minus ``DEFAULT_EXCLUDES`` and symbolic links, so a
write to ``.git/hooks``, an installed dependency in ``venv`` or a file in ``dist`` was invisible
to the gate: an agent could leave a Git hook that runs when the person commits, or patch an
installed package so the tests pass. The guard fingerprints those places before and after each
agent invocation in IMPLEMENTATION; any difference is a ``CRITICAL`` finding.

A fingerprint is the size, the modification time in nanoseconds and the SHA-256 of the first
``HEAD_BYTES`` of each file (the target for a symbolic link). It is cheap enough for a virtual
environment or ``node_modules`` and detects every write that changes size, time or the start of a
file; a write that restores all three is not detected.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

GUARDED_DIRECTORIES = frozenset(
    {".git", ".harness", ".venv", "venv", "node_modules", "dist", "build"}
)
"""Directories the ChangeSet excludes that the guard watches (caches such as ``__pycache__`` are
rewritten by every test run and are not watched)."""

IGNORED_PATTERNS: tuple[str, ...] = (
    # The harness's own state while the agent runs.
    ".harness/state.db",
    ".harness/state.db-*",
    ".harness/artifacts/*",
    ".harness/lease.json",
    ".harness/lease.json.*",
    # The digest cache of workspace.snapshotCache: the harness rewrites it around every agent
    # invocation, and a copy that does not match its seal in the state database is ignored.
    ".harness/cache/*",
    # Git rewrites its index on a read (git status) and keeps transient locks; objects only
    # matter once a ref points to them, and refs are watched. The chain-anchor notes are the
    # harness's own (governance.chainAnchor: git-note).
    ".git/index",
    "*.lock",
    ".git/objects/*",
    ".git/refs/notes/governed-harness",
    ".git/logs/refs/notes/*",
)

PRUNED_DIRECTORIES = frozenset({".harness/artifacts", ".git/objects"})
"""Ignored directories that are not walked at all (they can hold many files)."""

HEAD_BYTES = 8192


@dataclass(frozen=True)
class GuardChange:
    path: str
    status: str  # ADDED, MODIFIED or DELETED


def _ignored(relative: str) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in IGNORED_PATTERNS)


def _file_fingerprint(path: Path) -> str:
    stat = path.lstat()
    if path.is_symlink():
        return f"link:{os.readlink(path)}"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(HEAD_BYTES))
    except OSError:
        return f"unreadable:{stat.st_size}:{stat.st_mtime_ns}"
    return f"{stat.st_size}:{stat.st_mtime_ns}:{digest.hexdigest()}"


class ExcludedPathGuard:
    def __init__(
        self,
        root: Path,
        guarded: Iterable[str] = GUARDED_DIRECTORIES,
        *,
        include_ignored: bool = False,
    ) -> None:
        self.root = root.resolve(strict=True)
        self.guarded = frozenset(guarded)
        self.include_ignored = include_ignored
        """``workspace.snapshot: git`` (since 1.1): files ``.gitignore`` excludes leave the
        ChangeSet, so the guard also fingerprints the ignored files outside the directories it
        already watches and outside the caches every test run rewrites."""

    def fingerprint(self) -> dict[str, str]:
        """Fingerprints of every file below a guarded directory (at any depth) and of every
        symbolic link in the workspace, by workspace-relative path."""
        prints: dict[str, str] = {}
        for directory, dirnames, filenames in os.walk(self.root, followlinks=False):
            current = Path(directory)
            inside = self._inside_guarded(current)
            for name in list(dirnames):
                path = current / name
                if path.is_symlink():
                    self._add(prints, path)
                    dirnames.remove(name)
                elif path.relative_to(self.root).as_posix() in PRUNED_DIRECTORIES:
                    dirnames.remove(name)
            for name in filenames:
                path = current / name
                if inside or path.is_symlink():
                    self._add(prints, path)
        if self.include_ignored:
            from governed_harness.runtime.snapshots import ignored_outside_excludes

            for relative in ignored_outside_excludes(self.root):
                path = self.root / relative
                if relative not in prints and not self._inside_guarded(path.parent):
                    self._add(prints, path)
        return dict(sorted(prints.items()))

    @staticmethod
    def compare(before: dict[str, str], after: dict[str, str]) -> tuple[GuardChange, ...]:
        changes: list[GuardChange] = []
        for path in sorted(set(before) | set(after)):
            if path not in before:
                changes.append(GuardChange(path, "ADDED"))
            elif path not in after:
                changes.append(GuardChange(path, "DELETED"))
            elif before[path] != after[path]:
                changes.append(GuardChange(path, "MODIFIED"))
        return tuple(changes)

    def _inside_guarded(self, directory: Path) -> bool:
        relative = directory.relative_to(self.root).parts
        return any(part in self.guarded for part in relative)

    def _add(self, prints: dict[str, str], path: Path) -> None:
        relative = path.relative_to(self.root).as_posix()
        if _ignored(relative):
            return
        try:
            prints[relative] = _file_fingerprint(path)
        except OSError:
            return
