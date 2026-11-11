#!/usr/bin/env python3
"""Fixture command provider of the robustness suites H47, H48 and H50. No model.

Provider protocol 1.1. It answers as ``budget_agent.py`` (whose ``result_for``, ``write_change``
and ``usage_for`` it reuses): every read-only kind gets an empty, valid result and ``implement``
writes the change of the specification. What the specification adds:

* ``block``: ``{"kind": k, "marker": PATH, "partial": {path: content}, "maxSeconds": s}``: the
  first call of kind k writes ``partial`` (when given), then the marker (its pid, kind and
  time), then waits up to s seconds (default 600) and exits 1 without answering. The marker
  makes it block once: a later call of the same kind answers normally (H47);
* ``sleepFirst``: seconds the first call of the log sleeps before answering (H48);
* ``changeByTask``: ``{taskId: {path: content}}``, the change ``implement`` writes for each task
  (H48), instead of ``change``; ``{cwd}`` in a content is replaced by the name of the directory
  the call runs in;
* ``faults``: a list of ``{"kind": k, "mode": m, "times": n}`` (``times`` -1: always) for the
  calls of kind k made through this provider (``name``): ``transient`` (stderr names a transient
  cause, ``message``, exit 1), ``garbage`` (text that is not JSON, exit 0), ``empty`` (no output,
  exit 0), ``sleep`` (sleeps ``seconds`` and then answers normally) (H50).

Every call is appended to the log before it acts. Usage: python robust_agent.py --spec SPEC.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from budget_agent import result_for, usage_for, write_change  # noqa: E402

GARBAGE = "The model is thinking... <<partial output>> {not json"


def fault_for(spec: dict[str, Any], kind: str, earlier: list[dict[str, Any]]) -> dict | None:
    name = spec.get("name", "primary")
    done = sum(1 for c in earlier if c.get("kind") == kind and c.get("provider") == name)
    for fault in spec.get("faults") or []:
        if fault.get("kind") != kind:
            continue
        times = int(fault.get("times", 1))
        if times < 0 or done < times:
            return dict(fault)
    return None


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
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    earlier = [json.loads(line) for line in lines if line.strip()]
    number = 1 + len(earlier)
    block = spec.get("block") or {}
    blocking = block.get("kind") == kind and not Path(block["marker"]).exists()
    fault = None if blocking else fault_for(spec, kind, earlier)
    record = {
        "number": number,
        "provider": spec.get("name", "primary"),
        "kind": kind,
        "panel": panel,
        "taskId": task.get("taskId") or task.get("task_id"),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "block": blocking,
        "fault": (fault or {}).get("mode"),
        "contractIteration": (contract.get("iteration") or {}).get("number"),
        "startedAt": time.time(),
    }
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    if blocking:
        for path, content in (block.get("partial") or {}).items():
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        marker = Path(block["marker"])
        temporary = marker.with_name(marker.name + ".tmp")
        temporary.write_text(
            json.dumps({"pid": os.getpid(), "kind": kind, "at": time.time()}), encoding="utf-8"
        )
        os.replace(temporary, marker)
        time.sleep(float(block.get("maxSeconds", 600)))
        return 1
    if number == 1 and spec.get("sleepFirst"):
        time.sleep(float(spec["sleepFirst"]))
    if fault is not None:
        mode = fault["mode"]
        if mode == "transient":
            sys.stderr.write(str(fault.get("message", "Error: 429 rate limit")) + "\n")
            return 1
        if mode == "garbage":
            sys.stdout.write(GARBAGE + "\n")
            return 0
        if mode == "empty":
            return 0
        if mode == "sleep":
            time.sleep(float(fault.get("seconds", 60)))
    usage = usage_for(spec, contract, raw, number)
    if kind == "implement":
        by_task = spec.get("changeByTask") or {}
        task_id = str(record["taskId"] or "")
        if task_id in by_task:
            # "{cwd}" names the directory the call ran in (H48: which clone or worktree wrote it).
            here = Path.cwd().name
            files = {path: text.replace("{cwd}", here) for path, text in by_task[task_id].items()}
            change = {**spec, "change": files}
        else:
            change = spec
        write_change(change, task)
        answer: dict[str, Any] = {"status": "PASSED", "summary": "Change written"}
    else:
        answer = {"status": "PASSED", "summary": kind, "result": result_for(kind, request, task)}
    answer["usage"] = usage
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
