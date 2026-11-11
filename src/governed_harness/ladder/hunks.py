"""Hunks of a change and their reversal, for light mutation (#55, item 5).

A hunk is a maximal run of changed lines between the baseline text of a file and its current
text (``difflib`` opcodes other than ``equal``, without context lines). Reverting one hunk puts
its baseline lines back and keeps every other change, so a test run on the result tells whether
some test notices that this hunk is gone. The order is deterministic: by path, then by position
in the file."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class Hunk:
    path: str
    index: int
    """Position of the hunk in the file (1-based)."""
    before_start: int
    before_end: int
    after_start: int
    after_end: int
    """Line ranges (0-based, end exclusive) in the baseline and in the current text."""

    @property
    def label(self) -> str:
        return f"{self.path}#{self.index} (lines {self.after_start + 1}-{max(self.after_end, self.after_start + 1)})"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "index": self.index,
            "beforeLines": [self.before_start + 1, self.before_end],
            "afterLines": [self.after_start + 1, self.after_end],
        }


def _blocks(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """``start..end`` split after each run of blank lines that is followed by more lines, so
    that a new function added next to a changed line is a hunk of its own."""
    blocks: list[tuple[int, int]] = []
    begin = start
    index = start
    while index < end:
        if not lines[index].strip():
            following = index
            while following < end and not lines[following].strip():
                following += 1
            if following < end and any(lines[item].strip() for item in range(begin, index)):
                blocks.append((begin, following))
                begin = following
            index = following
            continue
        index += 1
    blocks.append((begin, end))
    return blocks


def hunks(path: str, before: str, after: str) -> list[Hunk]:
    """The hunks that turn ``before`` into ``after``. A changed region that adds several blocks
    of lines (separated by blank lines) gives one hunk per block: the first replaces the
    removed lines, the others are additions."""
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    found: list[Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        blocks = _blocks(new, j1, j2) if j2 > j1 else [(j1, j2)]
        for position, (start, end) in enumerate(blocks):
            first = position == 0
            found.append(Hunk(path, len(found) + 1, i1 if first else i2, i2, start, end))
    return found


_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--", ";")


def cosmetic(hunk: Hunk, before: str, after: str) -> bool:
    """Whether every line the hunk removes or adds is blank or a comment: reverting it cannot
    change behaviour, so it is not mutated."""
    old = before.splitlines()[hunk.before_start : hunk.before_end]
    new = after.splitlines()[hunk.after_start : hunk.after_end]
    return all(
        not line.strip() or line.strip().startswith(_COMMENT_PREFIXES) for line in (*old, *new)
    )


def revert(hunk: Hunk, before: str, after: str) -> str:
    """``after`` with the lines of ``hunk`` put back to their baseline content."""
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    return "".join(
        new[: hunk.after_start] + old[hunk.before_start : hunk.before_end] + new[hunk.after_end :]
    )


__all__ = ["Hunk", "cosmetic", "hunks", "revert"]
