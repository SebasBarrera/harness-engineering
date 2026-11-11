#!/usr/bin/env python3
"""Fixture command provider of the resource and block suites H19, H20 and H44. No model.

Provider protocol 1.1, the protocol of the fixture agent of ``suite_support.py``: the request
arrives as JSON on stdin; every read-only kind gets an empty, valid result; ``implement`` writes
the change of the specification (whole files, and deletions) and, when asked, one test per
requirement and criterion id of the task (``traceTests``). What makes it differ is what it
*reports* and how long it takes, both fixed by the specification:

* ``tokens``: ``{"factor": f}`` reports ``inputTokens + outputTokens`` = f x the ``tokenBudget``
  the call received in its ``agentContract`` (no cache fields); ``{"input": n}`` reports n;
* ``cache``: ``{"inputFactor": a, "cacheFactor": b, "cacheRead": bool}`` reports a x budget as
  input, of which b x budget is cache (``cacheTokens``) and, with ``cacheRead``, also read from
  the cache (``cacheReadTokens``, the field of PR #145; sent only when the suite found it in the
  installed harness's response schema);
* ``sleep``: ``{"factor": f}`` sleeps f x the ``timeLimitSeconds`` of the call;
* ``costs``: a list of USD costs by call number (1-based, in this log), ``defaultCost`` after it;
* ``inject``: ``{"kind": k, "result": {...}}`` answers calls of kind k with that result.

Each call is appended to the log **before** the sleep, so a call stopped at its time limit is in
the log too. Usage: python budget_agent.py --spec SPEC.json
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

RESULTS: dict[str, Any] = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "acceptance": {"tests": []},
    "locate": {"locations": [], "questions": []},
}
ARCHITECTURE = {
    "style": "custom",
    "summary": "No layering.",
    "layers": [],
    "allow": {},
    # architecture.documentation: project (reverse engineering), as interception_agent.py.
    "organization": "None.",
    "components": [{"name": "app", "path": ".", "responsibility": "The project."}],
    "conventions": [],
    "guidance": ["Follow the structure."],
}


def ids(items: Any, *names: str) -> list[str]:
    found: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            for name in names:
                if item.get(name):
                    found.append(str(item[name]))
                    break
    return found


def result_for(kind: str, request: dict[str, Any], task: dict[str, Any]) -> Any:
    if kind == "review" and "outputContract" in request:
        return {"verdict": "PASS", "findings": [], "summary": "No finding."}
    if kind == "architecture":
        return {"options": []} if request.get("mode") == "advise" else ARCHITECTURE
    if kind == "plan":
        return {
            "subtasks": [
                {
                    "title": str(task.get("title") or "The whole task"),
                    "requirements": ids(
                        task.get("requirements"), "requirementId", "requirement_id"
                    ),
                    "criteria": ids(
                        task.get("acceptanceCriteria") or task.get("acceptance_criteria"),
                        "criterionId",
                        "criterion_id",
                    ),
                    "constraints": [],
                }
            ]
        }
    return RESULTS.get(kind, {})


def write_change(spec: dict[str, Any], task: dict[str, Any]) -> None:
    for path, content in (spec.get("change") or {}).items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    for path in spec.get("delete") or []:
        Path(path).unlink(missing_ok=True)
    trace = spec.get("traceTests")
    if trace:
        names = ids(task.get("requirements"), "requirementId", "requirement_id") + ids(
            task.get("acceptanceCriteria") or task.get("acceptance_criteria"),
            "criterionId",
            "criterion_id",
        )
        lines = [trace["header"]]
        for name in dict.fromkeys(names):
            lines.append(f"\n\ndef test_{name}() -> None:\n    assert {trace['assert']}\n")
        target = Path(trace["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes("".join(lines).encode("utf-8"))


def usage_for(spec: dict[str, Any], contract: dict[str, Any], raw: str, number: int) -> dict:
    budget = int(contract.get("tokenBudget") or spec.get("fallbackBudget") or 50_000)
    output = 10
    usage: dict[str, Any] = {}
    tokens = spec.get("tokens") or {}
    cache = spec.get("cache") or {}
    if cache:
        usage["inputTokens"] = math.ceil(cache["inputFactor"] * budget)
        usage["cacheTokens"] = math.ceil(cache["cacheFactor"] * budget)
        if cache.get("cacheRead"):
            usage["cacheReadTokens"] = usage["cacheTokens"]
    elif "factor" in tokens:
        usage["inputTokens"] = max(0, round(tokens["factor"] * budget) - output)
    elif "input" in tokens:
        usage["inputTokens"] = int(tokens["input"])
    else:
        usage["inputTokens"] = math.ceil(len(raw) / 4)
    usage["outputTokens"] = output
    costs = spec.get("costs")
    if costs is not None or "defaultCost" in spec:
        listed = list(costs or [])
        usage["costUsd"] = (
            listed[number - 1] if number <= len(listed) else spec.get("defaultCost", 0.01)
        )
    return usage


def main() -> int:
    spec = json.loads(Path(sys.argv[sys.argv.index("--spec") + 1]).read_text(encoding="utf-8"))
    raw = sys.stdin.buffer.read().decode("utf-8")
    request = json.loads(raw)
    kind = str(request.get("kind", "implement"))
    panel = kind == "review" and "outputContract" in request
    task = request.get("task") if isinstance(request.get("task"), dict) else {}
    contract = (
        request.get("agentContract") if isinstance(request.get("agentContract"), dict) else {}
    )
    log = Path(spec["log"])
    earlier = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    number = 1 + sum(1 for line in earlier if line.strip())
    usage = usage_for(spec, contract, raw, number)
    sleep = (spec.get("sleep") or {}).get("factor")
    limit = contract.get("timeLimitSeconds")
    seconds = float(sleep) * float(limit) if sleep is not None and limit else 0.0
    record = {
        "number": number,
        "kind": kind,
        "panel": panel,
        "contract": bool(contract),
        "callKind": contract.get("callKind"),
        "iteration": (contract.get("iteration") or {}).get("number"),
        "sizeClass": contract.get("sizeClass"),
        "tokenBudget": contract.get("tokenBudget"),
        "timeLimitSeconds": limit,
        "usage": usage,
        "sleepSeconds": seconds,
        "requestChars": len(raw),
        "startedAt": time.time(),
    }
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    if seconds:
        time.sleep(seconds)
    inject = spec.get("inject") or {}
    if inject.get("kind") == kind and not panel:
        answer: dict[str, Any] = {"status": "PASSED", "summary": kind, "result": inject["result"]}
    elif kind == "implement":
        write_change(spec, task)
        answer = {"status": "PASSED", "summary": "Change written"}
    else:
        answer = {"status": "PASSED", "summary": kind, "result": result_for(kind, request, task)}
    answer["usage"] = usage
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
