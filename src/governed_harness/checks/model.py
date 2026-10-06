"""What a deterministic check returns, and the ChangeSet diff it reads.

The checks of this package are pure: they read source text and a unified diff and return
``Issue`` values. They never run a command, never write a record and never read the network; the
validators in ``governed_harness.validators.agent_results`` turn issues into findings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from governed_harness.domain.enums import FindingSeverity


@dataclass(frozen=True)
class Issue:
    """One problem a check found. ``rule_id`` is stable (``<check>.<rule>``)."""

    rule_id: str
    severity: FindingSeverity
    message: str
    path: str | None = None
    line: int | None = None
    category: str = "verification"
    recommendation: str | None = None


@dataclass(frozen=True)
class DiffLine:
    number: int
    """Line number in the new file for an added line, in the old file for a removed one."""
    text: str


@dataclass
class DiffFile:
    """One file of a unified diff: its paths and its added and removed lines."""

    old_path: str | None
    new_path: str | None
    added: list[DiffLine] = field(default_factory=list)
    removed: list[DiffLine] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_added(self) -> bool:
        return self.old_path is None

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _side(value: str, prefix: str) -> str | None:
    value = value.split("\t", 1)[0].strip()
    if value == "/dev/null":
        return None
    return value[len(prefix) :] if value.startswith(prefix) else value


def parse_unified_diff(text: str) -> list[DiffFile]:
    """The files of a unified diff (the ChangeSet diff), with numbered added and removed
    lines. Binary entries and unknown lines are skipped."""
    files: list[DiffFile] = []
    current: DiffFile | None = None
    old_line = new_line = 0
    pending_old: str | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            pending_old = _side(line[4:], "a/")
            current = DiffFile(old_path=pending_old, new_path=_side(lines[index + 1][4:], "b/"))
            files.append(current)
            index += 2
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            old_line, new_line = int(match.group(1)), int(match.group(2))
        elif current is not None and line.startswith("+"):
            current.added.append(DiffLine(new_line, line[1:]))
            new_line += 1
        elif current is not None and line.startswith("-"):
            current.removed.append(DiffLine(old_line, line[1:]))
            old_line += 1
        elif current is not None and line.startswith(" "):
            old_line += 1
            new_line += 1
        index += 1
    return files


TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|(^|/)conftest\.py$"
)


def is_test_path(path: str) -> bool:
    """Whether a workspace path is a test file or lives in a test directory."""
    return bool(TEST_PATH.search(path.replace("\\", "/")))
