#!/usr/bin/env python3
"""Write hidden_cases.json: every case of the hidden suite (``module::name[param]``) and its requirement id.

The requirement id is the prefix of the test name (``test_<ID>_...``). The suite is collected against the
reference implementation, which only has to import; no test is run.

Usage: python make_hidden_cases.py  (from evaluation/rides, with the reference on the path)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"^test_([a-z]\d+)_", re.IGNORECASE)


def main() -> int:
    env = {"PYTHONPATH": str(HERE / "reference"), "PATH": "/usr/bin:/bin"}
    if sys.platform == "win32":
        # Python on Windows needs SYSTEMROOT to start, and there is no /usr/bin: keep the caller's PATH.
        env |= {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")}
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                           "-o", "addopts=", str(HERE / "hidden")], cwd=HERE, capture_output=True, text=True,
                          env=env)
    cases: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "::" not in line:
            continue
        path, name = line.split("::", 1)
        match = NAME.match(name)
        if not match:
            print(f"no requirement id: {line}", file=sys.stderr)
            return 1
        key = f"{Path(path).stem}::{name}"
        if key in cases:
            print(f"duplicate case: {key}", file=sys.stderr)
            return 1
        cases[key] = match.group(1).upper()
    if proc.returncode != 0 or not cases:
        print(proc.stdout[-3000:], proc.stderr[-3000:], file=sys.stderr)
        return 1
    (HERE / "hidden_cases.json").write_text(json.dumps(cases, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                                         newline="\n")
    print(f"hidden_cases.json: {len(cases)} cases, {len(set(cases.values()))} requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
