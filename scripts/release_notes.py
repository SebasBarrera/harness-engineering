#!/usr/bin/env python3
"""Generate release notes from Conventional Commits between two refs.

The repository integrates work branches with merge commits and no pull requests, so notes are
built from the commits themselves: grouped by type, with the issues each commit closes.

Usage::

    python scripts/release_notes.py v0.8.0 v0.8.1 > notes.md
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict

SECTIONS = [
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("security", "Security"),
    ("refactor", "Refactoring (no behavior change)"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build and packaging"),
    ("ci", "CI/CD"),
    ("style", "Style"),
    ("perf", "Performance"),
    ("chore", "Maintenance"),
]
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?: (?P<text>.+)$")
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


def commits(base: str, head: str) -> list[tuple[str, str, str]]:
    log = subprocess.run(
        [
            "git",
            "log",
            "--no-merges",
            "--reverse",
            "--format=%h%x1f%s%x1f%b%x1e",
            f"{base}..{head}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    entries = []
    for record in log.split("\x1e"):
        if record.strip():
            short, subject, body = (record.strip("\n").split("\x1f") + ["", ""])[:3]
            entries.append((short, subject, body))
    return entries


def render(base: str, head: str) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    other: list[str] = []
    closed: set[int] = set()
    for short, subject, body in commits(base, head):
        issues = sorted({int(n) for n in CLOSES.findall(body)})
        closed.update(issues)
        suffix = f" ({', '.join(f'#{n}' for n in issues)})" if issues else ""
        match = SUBJECT.match(subject)
        if match and match["type"] in dict(SECTIONS):
            scope = f"**{match['scope']}:** " if match["scope"] else ""
            breaking = " **(breaking)**" if match["bang"] else ""
            grouped[match["type"]].append(f"- {scope}{match['text']}{breaking} `{short}`{suffix}")
        else:
            other.append(f"- {subject} `{short}`{suffix}")
    lines = [f"## Changes since {base}", ""]
    for key, title in SECTIONS:
        if grouped[key]:
            lines += [f"### {title}", "", *grouped[key], ""]
    if other:
        lines += ["### Other", "", *other, ""]
    if closed:
        lines += ["### Issues closed", "", ", ".join(f"#{n}" for n in sorted(closed)), ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(render(argv[1], argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
