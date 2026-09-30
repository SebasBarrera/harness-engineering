from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitState:
    is_repository: bool
    head: str | None
    branch: str | None
    dirty: bool
    status: tuple[str, ...]


class GitAdapter:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve(strict=True)

    def state(self) -> GitState:
        if self._run("rev-parse", "--is-inside-work-tree").returncode != 0:
            return GitState(False, None, None, False, ())
        head_result = self._run("rev-parse", "HEAD")
        branch_result = self._run("branch", "--show-current")
        status_result = self._run("status", "--porcelain=v1", "--untracked-files=all")
        lines = tuple(line for line in status_result.stdout.decode("utf-8", "replace").splitlines() if line)
        return GitState(
            True,
            head_result.stdout.decode().strip() if head_result.returncode == 0 else None,
            branch_result.stdout.decode().strip() or None,
            bool(lines),
            lines,
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
