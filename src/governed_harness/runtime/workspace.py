from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from governed_harness.capabilities.authorizer import contained_path
from governed_harness.evidence.hashing import sha256_bytes, sha256_file, sha256_json


DEFAULT_EXCLUDES = {
    ".git",
    ".harness",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


@dataclass(frozen=True)
class FileState:
    path: str
    digest: str
    size_bytes: int
    text: str | None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: dict[str, FileState]
    digest: str


@dataclass(frozen=True)
class WorkspaceChange:
    path: str
    status: str
    before_digest: str | None
    after_digest: str | None
    additions: int
    deletions: int


@dataclass(frozen=True)
class WorkspaceDiff:
    changes: tuple[WorkspaceChange, ...]
    unified_diff: bytes
    digest: str


class WorkspaceSnapshotter:
    def __init__(
        self,
        root: Path,
        *,
        excludes: Iterable[str] = DEFAULT_EXCLUDES,
        max_text_bytes: int = 2_000_000,
    ) -> None:
        self.root = root.resolve(strict=True)
        self.excludes = set(excludes)
        self.max_text_bytes = max_text_bytes

    def snapshot(self) -> WorkspaceSnapshot:
        states: dict[str, FileState] = {}
        for directory, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name not in self.excludes and not (Path(directory) / name).is_symlink()
            ]
            for filename in sorted(filenames):
                path = Path(directory) / filename
                if path.is_symlink():
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    size = path.stat().st_size
                    digest = sha256_file(path)
                    text = self._read_text(path, size)
                except OSError:
                    continue
                states[relative] = FileState(relative, digest, size, text)
        digest = sha256_json({path: state.digest for path, state in sorted(states.items())})
        return WorkspaceSnapshot(files=states, digest=digest)

    def diff(self, before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> WorkspaceDiff:
        paths = sorted(set(before.files) | set(after.files))
        changes: list[WorkspaceChange] = []
        diff_chunks: list[str] = []
        for path in paths:
            old = before.files.get(path)
            new = after.files.get(path)
            if old and new and old.digest == new.digest:
                continue
            if old is None:
                status = "ADDED"
            elif new is None:
                status = "DELETED"
            else:
                status = "MODIFIED"
            additions = deletions = 0
            if (old is None or old.text is not None) and (new is None or new.text is not None):
                old_lines = [] if old is None else old.text.splitlines(keepends=True)  # type: ignore[union-attr]
                new_lines = [] if new is None else new.text.splitlines(keepends=True)  # type: ignore[union-attr]
                chunk = list(
                    difflib.unified_diff(
                        old_lines,
                        new_lines,
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                        lineterm="",
                    )
                )
                for line in chunk:
                    if line.startswith("+") and not line.startswith("+++"):
                        additions += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        deletions += 1
                if chunk:
                    diff_chunks.append("\n".join(chunk) + "\n")
            else:
                diff_chunks.append(f"Binary files differ: {path}\n")
            changes.append(
                WorkspaceChange(
                    path=path,
                    status=status,
                    before_digest=old.digest if old else None,
                    after_digest=new.digest if new else None,
                    additions=additions,
                    deletions=deletions,
                )
            )
        unified = "".join(diff_chunks).encode("utf-8")
        digest = sha256_json(
            {
                "changes": [change.__dict__ for change in changes],
                "diffDigest": sha256_bytes(unified),
            }
        )
        return WorkspaceDiff(tuple(changes), unified, digest)

    def _read_text(self, path: Path, size: int) -> str | None:
        if size > self.max_text_bytes:
            return None
        data = path.read_bytes()
        if b"\x00" in data:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None
