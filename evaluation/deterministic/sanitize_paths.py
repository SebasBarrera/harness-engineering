#!/usr/bin/env python3
"""Replace the local absolute paths in result files by placeholders, as the earlier results did
("local paths and run identifiers are not recorded", evaluation/README.md).

``<repo>`` is the repository checkout, ``<work>`` the work directory of the suites (``--work``),
``<tmp>`` the system temporary directories and ``~`` the home directory. Only text files
(``.json``, ``.jsonl``, ``.txt``, ``.xml``) under the given directory are rewritten; the script
prints each file it changes.

Usage: python evaluation/deterministic/sanitize_paths.py DIR --work WORK [--work WORK ...]
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--work", action="append", default=[], type=Path)
    args = parser.parse_args()
    pairs = [(str(REPO), "<repo>")]
    pairs += [(str(work.resolve()), "<work>") for work in args.work]
    pairs += [(str(work.resolve()).replace("/private/", "/", 1), "<work>") for work in args.work]
    home = os.path.expanduser("~")
    temp = [
        (re.compile(r"/private/var/folders/[^\s\"'/]+/[^\s\"'/]+/T"), "<tmp>"),
        (re.compile(r"/var/folders/[^\s\"'/]+/[^\s\"'/]+/T"), "<tmp>"),
    ]
    for path in sorted(args.directory.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl", ".txt", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for old, placeholder in sorted(pairs, key=lambda item: -len(item[0])):
            new = new.replace(old, placeholder)
        for pattern, placeholder in temp:
            new = pattern.sub(placeholder, new)
        new = new.replace(home, "~")
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(f"sanitized {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
