from __future__ import annotations

import subprocess  # nosec B404 - git with a fixed argv, no shell
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
        lines = tuple(
            line for line in status_result.stdout.decode("utf-8", "replace").splitlines() if line
        )
        return GitState(
            True,
            head_result.stdout.decode().strip() if head_result.returncode == 0 else None,
            branch_result.stdout.decode().strip() or None,
            bool(lines),
            lines,
        )

    def user_identity(self) -> tuple[str | None, str | None]:
        """The ``user.name`` and ``user.email`` Git resolves for the workspace (repository,
        global and system configuration), ``None`` for each one that is not set."""
        values: list[str | None] = []
        for key in ("user.name", "user.email"):
            try:
                result = self._run("config", "--get", key)
            except (OSError, subprocess.SubprocessError):
                values.append(None)
                continue
            text = result.stdout.decode("utf-8", "replace").strip()
            values.append(text if result.returncode == 0 and text else None)
        return values[0], values[1]

    def _run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # nosec B603 B607 - git from PATH, argv built here, no shell
            ["git", *args],
            cwd=self.workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
