"""New or existing project, decided deterministically (#56).

A project is **new** when its Git repository has no commit yet, or when it has no source file
beyond scaffolding. Scaffolding is what a generator or a person writes before any behaviour:
test files, configuration and documentation, source files that define nothing (only imports,
an empty ``__init__.py``) and an entry point (``main``, ``index``, ``app``, ``Program``,
``__main__``) of at most ``SCAFFOLD_LINES`` non-blank lines. Otherwise it is **existing**. The
decision drives which questions INTENT asks (``intake.projectSetup``) and whether the
architecture call advises options (new) or surveys what is there (existing)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governed_harness.checks.model import is_test_path
from governed_harness.checks.principles import is_source
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

SCAFFOLD_LINES = 15
"""An entry point with at most this many non-blank lines counts as scaffolding."""
_MAX_FILES = 20_000


@dataclass(frozen=True)
class ProjectKind:
    kind: str
    """``new`` or ``existing``."""
    reason: str
    commits: int | None
    """Commits on ``HEAD`` (``None`` when the workspace is not a Git repository)."""
    source_files: int
    """Source files beyond scaffolding."""
    scaffolding: tuple[str, ...] = field(default_factory=tuple)

    @property
    def new(self) -> bool:
        return self.kind == "new"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "commits": self.commits,
            "sourceFiles": self.source_files,
            "scaffolding": list(self.scaffolding[:20]),
        }


def _commits(workspace: Path) -> int | None:
    if not (workspace / ".git").exists():
        return None
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed git argv, no shell
            ["git", "rev-list", "--count", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or 0)
    except ValueError:
        return 0


_DEFINITION = re.compile(
    r"\b(?:def|class|function|func|fn|fun|struct|interface|enum|trait|impl|record|module)\b"
    r"|=>"
)
_ENTRY_POINTS = frozenset({"main", "index", "app", "program", "__main__", "application"})


def _is_scaffolding(path: Path) -> bool:
    try:
        text = path.read_bytes()[:200_000].decode("utf-8", "replace")
    except OSError:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if not _DEFINITION.search(text):
        return True
    return path.stem.lower() in _ENTRY_POINTS and len(lines) <= SCAFFOLD_LINES


def detect_project_kind(workspace: Path) -> ProjectKind:
    root = workspace.resolve()
    commits = _commits(root)
    substantive = 0
    scaffolding: list[str] = []
    stack = [root]
    seen = 0
    while stack and seen < _MAX_FILES:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name in DEFAULT_EXCLUDES or entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
                continue
            seen += 1
            relative = entry.relative_to(root).as_posix()
            if not is_source(relative) or is_test_path(relative):
                continue
            if _is_scaffolding(entry):
                scaffolding.append(relative)
            else:
                substantive += 1
    if commits == 0:
        return ProjectKind(
            "new", "the Git repository has no commit", commits, substantive, tuple(scaffolding)
        )
    if substantive == 0:
        return ProjectKind(
            "new", "no source file beyond scaffolding", commits, substantive, tuple(scaffolding)
        )
    return ProjectKind(
        "existing",
        f"{substantive} source file(s) beyond scaffolding"
        + (f" and {commits} commit(s)" if commits else ""),
        commits,
        substantive,
        tuple(scaffolding),
    )


__all__ = ["SCAFFOLD_LINES", "ProjectKind", "detect_project_kind"]
