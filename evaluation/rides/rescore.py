#!/usr/bin/env python3
"""Score the kept workspaces of the rides runs with the final hidden suite.

The runs are made without the hidden suite (run-rides.sh leaves it out of the copy the agents can reach).
This script reads every record of the given JSON-lines files, runs run_rides.oracle on the workspace the
record names, and writes the records with ``final.hidden`` filled to ``<file>.scored.jsonl``; the
original files are not changed.

Usage: python rescore.py results/rides-*.jsonl
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv, FILES = sys.argv[:1], [Path(a) for a in sys.argv[1:]]

import run_rides  # noqa: E402


def main() -> int:
    if not FILES:
        print(__doc__, file=sys.stderr)
        return 2
    for path in FILES:
        scored = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            with tempfile.TemporaryDirectory() as scratch:
                hidden = run_rides.oracle(Path(record["runDir"]) / "ws", Path(scratch))
            if hidden.get("pending"):
                print("hidden suite or hidden_cases.json missing", file=sys.stderr)
                return 1
            record["final"]["hidden"] = hidden
            scored.append(json.dumps(record, sort_keys=True))
            print(f"{record['model']} {record['level']} {record['condition']} r{record['rep']}: "
                  f"{hidden['passed']}/{hidden['total']} cases, "
                  f"{hidden['requirementsMet']}/{hidden['requirementsTotal']} requirements")
        path.with_suffix(".scored.jsonl").write_text("\n".join(scored) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
