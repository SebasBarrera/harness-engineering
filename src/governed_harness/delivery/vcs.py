"""Git plumbing for delivery: read trees and blobs, recompute a ChangeSet digest between two
revisions, and write a commit without touching the working tree.

Every command runs without a shell, with ``GIT_TERMINAL_PROMPT=0`` and the repository's own
configuration (including commit signing)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from governed_harness.domain.errors import HarnessError
from governed_harness.evidence.hashing import sha256_bytes
from governed_harness.runtime.workspace import (
    FileState,
    WorkspaceDiff,
    WorkspaceSnapshot,
    WorkspaceSnapshotter,
    decode_text,
)

FALLBACK_IDENTITY = ("Governed Agent Harness", "harness@localhost.invalid")
"""Author and committer of a harness commit in a repository without a Git identity."""

_TIMEOUT_SECONDS = 120
_REGULAR_MODES = frozenset({"100644", "100755"})


class VcsError(HarnessError):
    """A Git operation of delivery failed."""


@dataclass(frozen=True)
class RevisionDiff:
    """The ChangeSet a range of commits amounts to, computed like the harness computes it."""

    base: str
    head: str
    diff: WorkspaceDiff
    special: tuple[str, ...] = ()
    """Entries a ChangeSet cannot hold: mode-only changes, symbolic links, submodules."""

    @property
    def digest(self) -> str:
        return self.diff.digest

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.diff.changes)


@dataclass
class Git:
    root: Path
    env: dict[str, str] = field(default_factory=dict)

    def run(
        self,
        *args: str,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **self.env, **(env or {})}
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.root,
                input=stdin,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_SECONDS,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise VcsError(f"git {args[0]} could not run: {error}") from error
        if check and result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip() or "no message"
            raise VcsError(f"git {args[0]} failed ({result.returncode}): {message}")
        return result

    def text(
        self, *args: str, stdin: bytes | None = None, env: Mapping[str, str] | None = None
    ) -> str:
        return self.run(*args, stdin=stdin, env=env).stdout.decode("utf-8", "replace").strip()

    def is_repository(self) -> bool:
        return self.run("rev-parse", "--is-inside-work-tree", check=False).returncode == 0

    def resolve(self, revision: str) -> str | None:
        result = self.run("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}", check=False)
        value = result.stdout.decode("utf-8", "replace").strip()
        return value if result.returncode == 0 and value else None

    def blob(self, revision: str, path: str) -> bytes | None:
        result = self.run("cat-file", "blob", f"{revision}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def identity_env(self) -> dict[str, str]:
        """Author and committer variables when the repository resolves no ``user.name`` or
        ``user.email``; empty otherwise (Git's own identity is used)."""
        name = self.run("config", "--get", "user.name", check=False).stdout.strip()
        email = self.run("config", "--get", "user.email", check=False).stdout.strip()
        if name and email:
            return {}
        fallback_name, fallback_email = FALLBACK_IDENTITY
        return {
            "GIT_AUTHOR_NAME": fallback_name,
            "GIT_AUTHOR_EMAIL": fallback_email,
            "GIT_COMMITTER_NAME": fallback_name,
            "GIT_COMMITTER_EMAIL": fallback_email,
        }

    def trailers(self, base: str, head: str) -> list[dict[str, str]]:
        """The ``Harness-*`` trailers of every commit in ``base..head``."""
        output = self.run(
            "log",
            "--format=%H%x00%(trailers:only,unfold)%x1e",
            f"{base}..{head}",
        ).stdout.decode("utf-8", "replace")
        found: list[dict[str, str]] = []
        for record in output.split("\x1e"):
            record = record.strip("\n")
            if not record:
                continue
            commit, _, block = record.partition("\x00")
            values = {"commit": commit.strip()}
            for line in block.splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().startswith("Harness-"):
                    values[key.strip()] = value.strip()
            if len(values) > 1:
                found.append(values)
        return found


def _entries(
    git: Git, base: str, head: str, paths: Sequence[str]
) -> list[tuple[str, str, str, str, str, str]]:
    """``git diff --raw`` entries: (src mode, dst mode, src blob, dst blob, status, path)."""
    args = ["diff", "--raw", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", base, head]
    if paths:
        args += ["--", *paths]
    data = git.run(*args).stdout.decode("utf-8", "surrogateescape")
    parts = data.split("\x00")
    entries = []
    index = 0
    while index < len(parts) - 1:
        header = parts[index]
        if not header.startswith(":"):
            index += 1
            continue
        src_mode, dst_mode, src_blob, dst_blob, status = header[1:].split(" ")[:5]
        entries.append((src_mode, dst_mode, src_blob, dst_blob, status[:1], parts[index + 1]))
        index += 2
    return entries


def revision_diff(
    root: Path,
    base: str,
    head: str,
    *,
    paths: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> RevisionDiff:
    """The ChangeSet diff (and digest) from ``base`` to ``head``: the same computation the
    harness applies to the workspace, fed with the blobs of the two revisions."""
    git = Git(root, dict(env or {}))
    base_commit = git.resolve(base)
    head_commit = git.resolve(head)
    if base_commit is None:
        raise VcsError(f"unknown revision: {base}")
    if head_commit is None:
        raise VcsError(f"unknown revision: {head}")
    before: dict[str, FileState] = {}
    after: dict[str, FileState] = {}
    special: list[str] = []
    for src_mode, dst_mode, _src, _dst, status, path in _entries(
        git, base_commit, head_commit, paths
    ):
        modes = {mode for mode in (src_mode, dst_mode) if mode != "000000"}
        if not modes <= _REGULAR_MODES:
            special.append(f"{path} (mode {src_mode} -> {dst_mode})")
            continue
        if status == "M" and src_mode != dst_mode:
            special.append(f"{path} (mode {src_mode} -> {dst_mode})")
        if src_mode != "000000":
            data = git.blob(base_commit, path) or b""
            before[path] = FileState(path, sha256_bytes(data), len(data), decode_text(data))
        if dst_mode != "000000":
            data = git.blob(head_commit, path) or b""
            after[path] = FileState(path, sha256_bytes(data), len(data), decode_text(data))
    snapshotter = WorkspaceSnapshotter(root)
    diff = snapshotter.diff(WorkspaceSnapshot(before, ""), WorkspaceSnapshot(after, ""))
    return RevisionDiff(base_commit, head_commit, diff, tuple(special))
