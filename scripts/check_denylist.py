#!/usr/bin/env python3
"""Fail if any tracked file matches the privacy denylist (.github/privacy-denylist.txt)."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST = ROOT / ".github" / "privacy-denylist.txt"


def patterns() -> list[str]:
    lines = DENYLIST.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def tracked_files() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8")
    return [path for path in output.split("\0") if path]


def main() -> int:
    rules = patterns()
    violations = [
        (path, rule)
        for path in tracked_files()
        for rule in rules
        if fnmatch.fnmatchcase(path, rule) or fnmatch.fnmatchcase(Path(path).name, rule)
    ]
    for path, rule in violations:
        print(f"DENYLISTED {path} (rule {rule!r})")
    print(f"checked {len(tracked_files())} tracked files against {len(rules)} rules: "
          f"{len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
