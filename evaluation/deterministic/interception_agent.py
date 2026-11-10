#!/usr/bin/env python3
"""Fixture command provider of the interception suites (H01, H03, H05, H38). No model.

The same protocol as the fixture agent of ``friction.py``: the request arrives as JSON on stdin;
the read-only kinds get empty results; ``implement`` writes the seeded change exactly as the case
declares it (whole files, and deletions) and reports success. The usage it reports is an
**estimate** (request characters / 4), as in ``friction.py``.

Usage (as a provider command): python interception_agent.py --change CHANGE.json
where CHANGE.json is ``{"write": {path: content}, "delete": [path, ...]}``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

RESULTS = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "plan": {"subtasks": []},
    "acceptance": {"tests": []},
    "locate": {"locations": [], "questions": []},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--change", required=True)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    text = json.dumps(request)
    kind = request.get("kind", "implement")
    results = dict(RESULTS)
    # The survey answer carries the reverse engineering fields that `harness init` asks for
    # (architecture.documentation: project); without them DISCOVERY blocks as malformed.
    results["architecture"] = (
        {"options": []}
        if request.get("mode") == "advise"
        else {
            "style": "custom",
            "summary": "No layering.",
            "layers": [],
            "allow": {},
            "organization": "None.",
            "components": [{"name": "app", "path": ".", "responsibility": "The project."}],
            "conventions": [],
            "guidance": ["Follow the structure."],
        }
    )
    if kind == "review" and "outputContract" in request:
        results["review"] = {"verdict": "PASS", "findings": [], "summary": "No finding."}
    if kind == "implement":
        change = json.loads(Path(args.change).read_text(encoding="utf-8"))
        for path, content in change.get("write", {}).items():
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, so the change is written with LF on every platform.
            target.write_bytes(content.encode("utf-8"))
        for path in change.get("delete", []):
            Path(path).unlink(missing_ok=True)
        answer = {"status": "PASSED", "summary": "Change written"}
    else:
        answer = {"status": "PASSED", "summary": kind, "result": results.get(kind, {})}
    answer["usage"] = {
        "inputTokens": math.ceil(len(text) / 4),
        "outputTokens": math.ceil(len(json.dumps(answer)) / 4),
    }
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
