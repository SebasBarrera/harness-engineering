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
    files: list[FileChange] = []
    current: FileChange | None = None
    old_left = new_left = 0
    old_line = new_line = 0
    open_header = False
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        in_hunk = current is not None and (old_left > 0 or new_left > 0)
        if in_hunk and current is not None:
            current.lines.append(line)
            if line.startswith("+"):
                current.added.append(DiffLine(new_line, line[1:]))
                new_line += 1
                new_left -= 1
            elif line.startswith("-"):
                current.removed.append(DiffLine(old_line, line[1:]))
                old_line += 1
                old_left -= 1
            elif line.startswith("\\"):
                pass
            else:
                old_line += 1
                new_line += 1
                old_left -= 1
                new_left -= 1
            index += 1
            continue
        git_header = _GIT_HEADER.match(line)
        if git_header:
            current = FileChange(git_header.group(1), git_header.group(2), lines=[line])
            files.append(current)
            open_header = True
            index += 1
            continue
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            old_path = _side_path(line[4:], "a/")
            new_path = _side_path(lines[index + 1][4:], "b/")
            if current is not None and open_header:
                current.old_path, current.new_path = old_path, new_path
            else:
                current = FileChange(old_path, new_path)
                files.append(current)
            open_header = False
            current.lines.extend((line, lines[index + 1]))
            index += 2
            continue
        if current is not None and line.startswith(("new file mode", "deleted file mode")):
            current.lines.append(line)
            if line.startswith("new file mode"):
                current.old_path = None
            else:
                current.new_path = None
            index += 1
            continue
        binary = _GIT_BINARY.match(line) or _HARNESS_BINARY.match(line)
        if binary:
            open_header = False
            if _HARNESS_BINARY.match(line):
                current = FileChange(binary.group(1), binary.group(1), binary=True, lines=[line])
                files.append(current)
            elif current is not None:
                current.binary = True
                current.lines.append(line)
                if binary.group(1) == "/dev/null":
                    current.old_path = None
                if binary.group(2) == "/dev/null":
                    current.new_path = None
            index += 1
            continue
        hunk = _HUNK.match(line)
        if hunk and current is not None:
            open_header = False
            old_line, new_line = int(hunk.group(1)), int(hunk.group(3))
            old_left = int(hunk.group(2)) if hunk.group(2) is not None else 1
            new_left = int(hunk.group(4)) if hunk.group(4) is not None else 1
            current.lines.append(line)
            index += 1
            continue
        if current is not None:
            current.lines.append(line)
        index += 1
    return files


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
