#!/usr/bin/env python3
"""Fixture reviewer for the review-panel corpus (N02-a): a command provider that calls no model.

Two modes:

* ``silent``: every reviewer answers PASS with no finding, so the report holds only what the
  harness finds by itself (rules a tool verifies, its own checks, consistency checks).
* ``oracle``: every reviewer answers the seeded findings of its domain from the case's truth
  file (``--truth``), located on the new side of its diff slice, plus two decoys per reviewer
  whose slice has an added line: one finding at line 9999 of a file of the slice (outside the
  diff; the harness must drop it) and one on a rule outside the catalog without evidence (the
  harness must downgrade it to a suggestion). The decoys sent are reported in the summary as
  JSON, so the runner compares them with what the harness dropped and downgraded.

It answers a non-panel ``review`` request with ``{"findings": []}`` and every other read-only
kind with an empty result. Estimated usage (characters / 4) is reported, as in
``scripts/measure_friction.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(diff: str) -> dict[str, list[int]]:
    """New-side line numbers of the added lines of a unified diff, by file."""
    lines: dict[str, list[int]] = {}
    current: str | None = None
    number = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else None
            continue
        if line.startswith("--- "):
            continue
        match = HUNK.match(line)
        if match:
            number = int(match.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            lines.setdefault(current, []).append(number)
            number += 1
        elif line.startswith("-"):
            continue
        else:
            number += 1
    return lines


def oracle_answer(request: dict[str, Any], truth: list[dict[str, Any]]) -> dict[str, Any]:
    reviewer = request.get("reviewer") or {}
    domain = reviewer.get("domain") or reviewer.get("id")
    diff = (request.get("slice") or {}).get("diff", "")
    added = added_lines(diff)
    findings = []
    for item in truth:
        if item["domain"] != domain or item["path"] not in added:
            continue
        findings.append(
            {
                "file": item["path"],
                "side": "new",
                "line": item["line"],
                "rule": item["rule"],
                "severity": "error",
                "issue": f"Seeded defect ({item['rule']}).",
                "evidence": item["text"],
            }
        )
    decoys = {"outside": 0, "outOfCatalog": 0}
    if added:
        path = sorted(added)[0]
        findings.append(
            {
                "file": path,
                "side": "new",
                "line": 9999,
                "rule": f"{domain}.decoy-outside-the-diff",
                "severity": "error",
                "issue": "Decoy: a location that is not in the diff.",
                "evidence": "",
            }
        )
        decoys["outside"] += 1
        findings.append(
            {
                "file": path,
                "side": "new",
                "line": added[path][0],
                "rule": "fixture.out-of-catalog",
                "severity": "error",
                "issue": "Decoy: a rule outside the catalog, without evidence.",
                "evidence": "",
            }
        )
        decoys["outOfCatalog"] += 1
    verdict = "FAIL" if findings else "PASS"
    return {"verdict": verdict, "findings": findings, "summary": json.dumps({"decoys": decoys})}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["silent", "oracle"], required=True)
    parser.add_argument("--truth", default="")
    parser.add_argument(
        "--log", default="", help="append one line per call (sandbox-writable path)"
    )
    args = parser.parse_args()
    request = json.load(sys.stdin)
    kind = request.get("kind", "implement")
    if args.log:
        try:
            with Path(args.log).open("a", encoding="utf-8") as handle:
                reviewer = (request.get("reviewer") or {}).get("id")
                handle.write(
                    json.dumps({"kind": kind, "reviewer": reviewer, "mode": args.mode}) + "\n"
                )
        except OSError:
            pass
    if kind == "review" and "outputContract" in request:
        if args.mode == "oracle":
            truth = json.loads(Path(args.truth).read_text()) if args.truth else []
            result: dict[str, Any] = oracle_answer(request, truth)
        else:
            result = {"verdict": "PASS", "findings": [], "summary": "silent fixture"}
    elif kind == "review":
        result = {"findings": []}
    elif kind == "implement":
        print(
            json.dumps({"status": "PASSED", "summary": "the fixture reviewer implements nothing"})
        )
        return 0
    else:
        result = {}
    answer: dict[str, Any] = {"status": "PASSED", "summary": "reviewed", "result": result}
    answer["usage"] = {
        "inputTokens": -(-len(json.dumps(request)) // 4),
        "outputTokens": -(-len(json.dumps(result)) // 4),
    }
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
