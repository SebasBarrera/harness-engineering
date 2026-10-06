"""The unified diff a review reads: files, reportable locations and slices (#57).

A reviewer may only report on lines the change touched. The reportable locations come from the
unified diff itself:

* ``new``: an added line, with its line number in the new file;
* ``old``: a removed line, with its line number in the old file;
* context lines only advance the counters and are never reportable;
* a deleted file is reportable on the ``old`` side for every removed line (a whole-file deletion
  is reviewable), and on its line 1 when the diff shows no line of it (an empty or binary file).

The parser counts the lines of every hunk, so a removed line that reads ``-- x`` followed by an
added ``++ y`` is never taken for a new file header. It reads Git diffs (``diff --git`` headers,
``Binary files ... differ``) and the ChangeSet diff the harness records (no ``diff --git`` line,
``Binary files differ: PATH``)."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from governed_harness.checks.model import DiffFile, DiffLine
from governed_harness.evidence.hashing import sha256_bytes

Side = Literal["new", "old"]
SIDES: tuple[Side, ...] = ("new", "old")

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GIT_HEADER = re.compile(r"^diff --git (?:\"?a/)?(.+?)\"? (?:\"?b/)?(.+?)\"?$")
_GIT_BINARY = re.compile(r"^Binary files (?:a/)?(.+?) and (?:b/)?(.+?) differ$")
_HARNESS_BINARY = re.compile(r"^Binary files differ: (.+)$")


@dataclass
class FileChange:
    """One file of a unified diff, with its numbered added and removed lines and the text of
    its own section (for a slice)."""

    old_path: str | None
    new_path: str | None
    added: list[DiffLine] = field(default_factory=list)
    removed: list[DiffLine] = field(default_factory=list)
    binary: bool = False
    lines: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None

    @property
    def is_added(self) -> bool:
        return self.old_path is None

    @property
    def text(self) -> str:
        return "".join(line if line.endswith("\n") else line + "\n" for line in self.lines)

    @property
    def changed_lines(self) -> int:
        return len(self.added) + len(self.removed)

    def as_diff_file(self) -> DiffFile:
        """The form the deterministic checks of ``governed_harness.checks`` read."""
        return DiffFile(self.old_path, self.new_path, list(self.added), list(self.removed))


def _side_path(value: str, prefix: str) -> str | None:
    value = value.split("\t", 1)[0].strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value == "/dev/null":
        return None
    return value[len(prefix) :] if value.startswith(prefix) else value


def parse_diff(text: str) -> list[FileChange]:
    """The files of a unified diff, in diff order."""
    return _DiffParser(text.splitlines()).parse()


class _DiffParser:
    """A single pass over the lines of a unified diff. Inside a hunk the counters of its header
    decide what a line is; outside, each header kind is tried in turn."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.files: list[FileChange] = []
        self.current: FileChange | None = None
        self.old_left = self.new_left = 0
        self.old_line = self.new_line = 0
        self.open_header = False

    def parse(self) -> list[FileChange]:
        index = 0
        while index < len(self.lines):
            index += self._step(index)
        return self.files

    def _step(self, index: int) -> int:
        """Read the line at ``index``; the number of lines it consumed."""
        line = self.lines[index]
        if self.current is not None and (self.old_left > 0 or self.new_left > 0):
            self._hunk_line(self.current, line)
            return 1
        for header in (self._git_header, self._file_header, self._mode, self._binary, self._hunk):
            used = header(index)
            if used:
                return used
        if self.current is not None:
            self.current.lines.append(line)
        return 1

    def _hunk_line(self, current: FileChange, line: str) -> None:
        current.lines.append(line)
        if line.startswith("+"):
            current.added.append(DiffLine(self.new_line, line[1:]))
            self.new_line += 1
            self.new_left -= 1
        elif line.startswith("-"):
            current.removed.append(DiffLine(self.old_line, line[1:]))
            self.old_line += 1
            self.old_left -= 1
        elif not line.startswith("\\"):
            self.old_line += 1
            self.new_line += 1
            self.old_left -= 1
            self.new_left -= 1

    def _git_header(self, index: int) -> int:
        line = self.lines[index]
        git_header = _GIT_HEADER.match(line)
        if not git_header:
            return 0
        self.current = FileChange(git_header.group(1), git_header.group(2), lines=[line])
        self.files.append(self.current)
        self.open_header = True
        return 1

    def _file_header(self, index: int) -> int:
        """A ``---``/``+++`` pair: the paths of the file the ``diff --git`` line opened, or a
        new file of a diff without such lines."""
        line = self.lines[index]
        following = self.lines[index + 1] if index + 1 < len(self.lines) else ""
        if not (line.startswith("--- ") and following.startswith("+++ ")):
            return 0
        old_path = _side_path(line[4:], "a/")
        new_path = _side_path(following[4:], "b/")
        if self.current is not None and self.open_header:
            self.current.old_path, self.current.new_path = old_path, new_path
        else:
            self.current = FileChange(old_path, new_path)
            self.files.append(self.current)
        self.open_header = False
        self.current.lines.extend((line, following))
        return 2

    def _mode(self, index: int) -> int:
        line = self.lines[index]
        current = self.current
        if current is None or not line.startswith(("new file mode", "deleted file mode")):
            return 0
        current.lines.append(line)
        if line.startswith("new file mode"):
            current.old_path = None
        else:
            current.new_path = None
        return 1

    def _binary(self, index: int) -> int:
        line = self.lines[index]
        harness = _HARNESS_BINARY.match(line)
        binary = _GIT_BINARY.match(line) or harness
        if not binary:
            return 0
        self.open_header = False
        if harness:
            self.current = FileChange(binary.group(1), binary.group(1), binary=True, lines=[line])
            self.files.append(self.current)
        elif self.current is not None:
            self.current.binary = True
            self.current.lines.append(line)
            if binary.group(1) == "/dev/null":
                self.current.old_path = None
            if binary.group(2) == "/dev/null":
                self.current.new_path = None
        return 1

    def _hunk(self, index: int) -> int:
        line = self.lines[index]
        hunk = _HUNK.match(line)
        if not hunk or self.current is None:
            return 0
        self.open_header = False
        self.old_line, self.new_line = int(hunk.group(1)), int(hunk.group(3))
        self.old_left = int(hunk.group(2)) if hunk.group(2) is not None else 1
        self.new_left = int(hunk.group(4)) if hunk.group(4) is not None else 1
        self.current.lines.append(line)
        return 1


# ----- reportable locations ---------------------------------------------------------------------
@dataclass(frozen=True)
class Locations:
    """The lines a reviewer may report on: ``(path, side) -> line numbers``."""

    lines: dict[tuple[str, Side], frozenset[int]]

    def allows(self, path: str, side: str, line: int) -> bool:
        if side == "new":
            return line in self.lines.get((path, "new"), frozenset())
        if side == "old":
            return line in self.lines.get((path, "old"), frozenset())
        return False

    def paths(self) -> list[str]:
        return sorted({path for path, _side in self.lines})

    def as_ranges(self) -> dict[str, dict[str, list[list[int]]]]:
        """``{path: {side: [[first, last], ...]}}``: what the request shows the reviewer."""
        value: dict[str, dict[str, list[list[int]]]] = {}
        for (path, side), numbers in sorted(self.lines.items()):
            if numbers:
                value.setdefault(path, {})[side] = _ranges(sorted(numbers))
        return value

    def count(self) -> int:
        return sum(len(numbers) for numbers in self.lines.values())


def _ranges(numbers: Sequence[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for number in numbers:
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def reportable_locations(files: Iterable[FileChange]) -> Locations:
    lines: dict[tuple[str, Side], set[int]] = {}
    for item in files:
        if item.added and item.new_path:
            lines.setdefault((item.new_path, "new"), set()).update(
                line.number for line in item.added
            )
        if item.removed and item.old_path:
            lines.setdefault((item.old_path, "old"), set()).update(
                line.number for line in item.removed
            )
        if item.is_deleted and item.old_path and not item.removed:
            lines.setdefault((item.old_path, "old"), set()).add(1)
    return Locations({key: frozenset(value) for key, value in lines.items()})


# ----- slices -----------------------------------------------------------------------------------
def glob_match(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
        return True
    return "/" not in pattern and fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def render(files: Iterable[FileChange]) -> str:
    """The diff text of some files (a slice), section by section as parsed."""
    return "".join(item.text for item in files)


def diff_hash(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


__all__ = [
    "SIDES",
    "FileChange",
    "Locations",
    "Side",
    "diff_hash",
    "glob_match",
    "parse_diff",
    "render",
    "reportable_locations",
]
