from __future__ import annotations

import difflib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

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


NO_NEWLINE_MARKER = "\\ No newline at end of file\n"


def unified_file_diff(
    path: str, old_text: str, new_text: str, *, added: bool = False, deleted: bool = False
) -> list[str]:
    """The unified diff of one text file as lines that each end with a newline, in the form
    ``git apply`` and ``patch`` read: an added file is diffed from ``/dev/null`` and a deleted
    one to it, and a last line without a newline is followed by the ``\\ No newline at end of
    file`` marker.

    Before 1.1 the lines were joined with an extra newline (every line was followed by an empty
    line), so ChangeSet diffs recorded by earlier versions are not valid patches; their digests
    stay as recorded."""
    lines: list[str] = []
    for line in difflib.unified_diff(
        _text_lines(old_text),
        _text_lines(new_text),
        fromfile="/dev/null" if added else f"a/{path}",
        tofile="/dev/null" if deleted else f"b/{path}",
    ):
        if line.endswith("\n"):
            lines.append(line)
        else:
            lines.extend((line + "\n", NO_NEWLINE_MARKER))
    return lines


def _text_lines(text: str) -> list[str]:
    # Split on newlines only, as Git does: str.splitlines also breaks on form feeds, vertical
    # tabs and other separators, which would make the hunks disagree with the file.
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


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
            # A missing side diffs as empty text; a present binary side (text None) is not diffed.
            old_text = "" if old is None else old.text
            new_text = "" if new is None else new.text
            if old_text is not None and new_text is not None:
                chunk = unified_file_diff(
                    path, old_text, new_text, added=old is None, deleted=new is None
                )
                for line in chunk:
                    if line.startswith("+") and not line.startswith("+++"):
                        additions += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        deletions += 1
                if chunk:
                    diff_chunks.append("".join(chunk))
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
        return decode_text(path.read_bytes(), self.max_text_bytes)


def decode_text(data: bytes, max_text_bytes: int = 2_000_000) -> str | None:
    """The text of a file's bytes as the ChangeSet diff reads it: ``None`` for a file larger
    than ``max_text_bytes``, with a NUL byte or that is not UTF-8 (diffed as binary)."""
    if len(data) > max_text_bytes or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
