#!/usr/bin/env python3
"""Fixture command provider of the interception suites (H01-H07, H09, H10, H12, H15, H38). No model.

The same protocol as the fixture agent of ``friction.py``: the request arrives as JSON on stdin;
the read-only kinds get empty results; ``implement`` writes the seeded change exactly as the case
declares it (whole files, and deletions) and reports success. The usage it reports is an
**estimate** (request characters / 4), as in ``friction.py``.

Usage (as a provider command): python interception_agent.py --change CHANGE.json
where CHANGE.json is ``{"write": {path: content}, "delete": [path, ...]}``, optionally with
``"append": {path: text}``, ``"symlink": {path: target}`` and ``"tolerateDenied": true`` (H04),
``"results": {kind: result}`` (the result of a read-only call instead of the empty one; under
``"acceptanceRevision"`` the answer to an acceptance request that carries a ``revision``; H06,
H12), ``"summary"`` and ``"selfReport"`` (what ``implement`` answers with; H07, H12). Without
these keys it answers as before.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESULTS = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "plan": {"subtasks": []},
    "acceptance": {"tests": []},
    "locate": {"locations": [], "questions": []},
}


def write_change(change: dict[str, Any]) -> list[str]:
    """Write the change: whole files (bytes, so LF on every platform), deletions, text appended
    to a file (``"append"``, H04) and symbolic links (``"symlink": {path: target}``, H04). With ``tolerateDenied`` (H04) a write the sandbox
    or the file system refuses is skipped and returned, as an agent that gets "permission denied"
    goes on; without it the error ends the call, as before."""
    tolerate = bool(change.get("tolerateDenied"))
    denied: list[str] = []

    def attempt(path: str, action: Callable[[], None]) -> None:
        try:
            action()
        except OSError:
            if not tolerate:
                raise
            denied.append(path)

    def writer(target: Path, content: str) -> Callable[[], None]:
        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))

        return write

    def appender(target: Path, text: str) -> Callable[[], None]:
        def append() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("ab") as handle:
                handle.write(text.encode("utf-8"))

        return append

    def linker(link: Path, target: str) -> Callable[[], None]:
        def make() -> None:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)

        return make

    for path, content in change.get("write", {}).items():
        attempt(path, writer(Path(path), content))
    for path in change.get("delete", []):
        attempt(path, lambda path=path: Path(path).unlink(missing_ok=True))
    for path, text in (change.get("append") or {}).items():
        attempt(path, appender(Path(path), text))
    for path, target in (change.get("symlink") or {}).items():
        attempt(path, linker(Path(path), target))
    if denied:
        print("denied: " + ", ".join(denied), file=sys.stderr)
    return denied


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
    change = json.loads(Path(args.change).read_text(encoding="utf-8"))
    overrides = change.get("results") or {}
    if kind in overrides and not (kind == "architecture" and request.get("mode") == "advise"):
        results[kind] = overrides[kind]
    if kind == "acceptance" and request.get("revision") and "acceptanceRevision" in overrides:
        results[kind] = overrides["acceptanceRevision"]
    if kind == "implement":
        denied = write_change(change)
        answer = {"status": "PASSED", "summary": str(change.get("summary") or "Change written")}
        if "selfReport" in change:
            answer["selfReport"] = change["selfReport"]
        if denied:
            answer["summary"] += "; denied: " + ", ".join(denied)
        if change.get("tolerateDenied"):
            # Next to the change file, for the suite's record; where the sandbox does not allow
            # it, nothing is written and the suite records that it could not read it.
            with contextlib.suppress(OSError):
                Path(args.change).with_name("denied.json").write_text(json.dumps(denied))
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
