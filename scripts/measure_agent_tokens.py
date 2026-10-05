#!/usr/bin/env python3
"""Measure the agent tokens of a governed run before and after the wave 6 settings (#56).

Token cost is an acceptance criterion of issue #56: tools verify, the agent receives only the
standards cards that apply to its files, the architecture survey runs once per project and the
principles checklist rides on the existing review call. This script measures it with the
installed ``harness`` CLI and a fixture command provider that calls no model:

* the provider answers every request (implement: it applies the task's patches; the read-only
  kinds: empty answers) and reports as usage an **estimate** of the tokens it received, the
  request's JSON length divided by four (a common rule of thumb for English text and code;
  not a tokenizer), and of the tokens it returned;
* the harness records that usage like any provider's (``harness budget show``), so the per-run
  totals below are the harness's own records of the reported usage;
* the provider also logs each call's kind and size, for the breakdown by call kind.

Two configurations of the same project (the quickstart's Python project) run the same two
tasks one after the other:

* ``before``: ``harness init`` without the wave 6 keys (standards, principles, testing,
  architecture, project setup), the agent-results defaults kept;
* ``after``: ``harness init`` as written, wave 6 included.

The JSON report goes to standard output (or ``--output``); ``--markdown`` prints a table.
Exit code 0 when every run reached DECISION (exit 4) as expected.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_flows  # noqa: E402 - the demonstration helpers, imported from this directory
import yaml  # noqa: E402 - a dependency of the package

AGENT = """\
import json, math, sys
from pathlib import Path

request = json.load(sys.stdin)
text = json.dumps(request)
kind = request.get("kind", "implement")
results = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "plan": {"subtasks": []},
    "acceptance": {"tests": []},
    "architecture": (
        {"options": []}
        if request.get("mode") == "advise"
        else {"style": "custom", "summary": "No layering.", "layers": [], "allow": {}}
    ),
}
if kind == "implement":
    for patch in request["task"]["implementation"]["patches"]:
        path = Path(patch["path"])
        if patch["operation"] == "replace":
            path.write_text(patch["content"])
        else:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(patch.get("content") or "")
    answer = {"status": "PASSED", "summary": "Patches applied"}
else:
    answer = {"status": "PASSED", "summary": kind, "result": results[kind]}
answer["usage"] = {
    "inputTokens": math.ceil(len(text) / 4),
    "outputTokens": math.ceil(len(json.dumps(answer)) / 4),
}
log = Path(LOG)
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "kind": kind,
        "inputChars": len(text),
        "inputTokens": answer["usage"]["inputTokens"],
        "standardsCards": len((request.get("standards") or {}).get("cards") or []),
        "checklist": sorted((request.get("checklist") or {}).keys()),
    }) + "\\n")
print(json.dumps(answer))
"""

TASKS = (
    ("task_first", 100, 90, "at_threshold"),
    ("task_second", 150, 135, "above_threshold"),
)
TASK = """\
taskId: {task_id}
title: Threshold discount ({task_id})
intent: Apply a percentage discount only when the subtotal meets the configured threshold.
requirements:
  - requirementId: req_discount
    text: Apply a percentage discount only when the subtotal meets the configured threshold.
acceptanceCriteria:
  - criterionId: ac_threshold
    text: apply_discount({subtotal}, 100, 0.1) returns {expected}.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |

        def test_req_discount_{test}() -> None:
            assert apply_discount({subtotal}, 100, 0.1) == {expected}
"""

WAVE6 = {
    "intake": ("projectSetup",),
    "verification": ("principles",),
}
WAVE6_SECTIONS = ("standards", "testing", "architecture")


def harness(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [demo_flows.shutil.which("harness") or "harness", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=demo_flows.isolated_env(),
    )


def configure(root: Path, log: Path, *, wave6: bool) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text())
    if not wave6:
        for section, keys in WAVE6.items():
            for key in keys:
                config.get(section, {}).pop(key, None)
        for section in WAVE6_SECTIONS:
            config.pop(section, None)
    (root / "agent.py").write_text(AGENT.replace("LOG", repr(str(log))))
    config["agentProvider"] = "measure"
    config["agentProviders"] = {"measure": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"].update({"agentSandbox": "off", "providerRetryDelaySeconds": 0})
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def measure(workdir: Path, name: str, *, wave6: bool) -> dict[str, Any]:
    root = workdir / name
    demo_flows.python_project(root)
    (root / ".gitignore").write_text(".harness/\n__pycache__/\n.pytest_cache/\nagent.py\n")
    log = workdir / f"{name}-calls.jsonl"
    log.unlink(missing_ok=True)
    if harness(root, "init", "--path", ".").returncode != 0:
        raise SystemExit(f"{name}: harness init failed")
    configure(root, log, wave6=wave6)
    runs: list[dict[str, Any]] = []
    for task_id, subtotal, expected, test in TASKS:
        (root / f"{task_id}.yaml").write_text(
            TASK.format(task_id=task_id, subtotal=subtotal, expected=expected, test=test)
        )
        harness(root, "task", "create", "--path", ".", "--file", f"{task_id}.yaml")
        before = log.read_text().splitlines() if log.exists() else []
        started = harness(root, "--json", "run", "start", "--path", ".", "--task", task_id)
        run = json.loads(started.stdout)
        calls = [json.loads(line) for line in log.read_text().splitlines()[len(before) :]]
        budget = json.loads(
            harness(
                root, "--json", "budget", "show", "--path", ".", "--run", run["executionId"]
            ).stdout
        )
        by_kind: dict[str, dict[str, int]] = {}
        for call in calls:
            entry = by_kind.setdefault(call["kind"], {"calls": 0, "inputTokens": 0})
            entry["calls"] += 1
            entry["inputTokens"] += call["inputTokens"]
        runs.append(
            {
                "task": task_id,
                "exitCode": started.returncode,
                "phase": run["currentPhase"],
                "recordedTokens": budget["usage"]["run"]["tokens"],
                "recordedCalls": budget["usage"]["run"]["calls"],
                "byKind": by_kind,
                "standardsCards": [
                    call["standardsCards"] for call in calls if call["kind"] == "implement"
                ],
                "reviewChecklist": [
                    call["checklist"] for call in calls if call["kind"] == "review"
                ],
            }
        )
        harness(
            root,
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run["executionId"],
            "--decision",
            "REJECT",
            "--change-set-digest",
            run.get("changeSetDigest") or "x",
            "--actor",
            "human.measure",
            "--rationale",
            "Measurement run",
        )
    return {"configuration": name, "wave6": wave6, "runs": runs}


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "| Configuration | Run | Exit | Agent calls | Recorded tokens | Calls by kind (estimated input tokens) |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in report["measurements"]:
        for run in item["runs"]:
            kinds = ", ".join(
                f"{kind} {value['calls']}x {value['inputTokens']}"
                for kind, value in sorted(run["byKind"].items())
            )
            lines.append(
                f"| {item['configuration']} | {run['task']} | {run['exitCode']} | "
                f"{run['recordedCalls']} | {run['recordedTokens']} | {kinds} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    temp = None
    if args.workdir is None:
        temp = tempfile.mkdtemp(prefix="harness-tokens-")
        workdir = Path(temp)
    else:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
    # The chain anchors and the run registry (runtime.stateDir: auto, #55) of the measurement
    # live in temporary directories of their own; demo_flows.isolated_env() passes them on.
    anchors = tempfile.mkdtemp(prefix="harness-anchors-")
    os.environ.setdefault("HARNESS_ANCHOR_DIR", anchors)
    state = tempfile.mkdtemp(prefix="harness-state-")
    os.environ.setdefault("HARNESS_STATE_DIR", state)
    try:
        measurements = [
            measure(workdir, "before", wave6=False),
            measure(workdir, "after", wave6=True),
        ]
    finally:
        shutil.rmtree(anchors, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    report: dict[str, Any] = {
        "estimate": "tokens = ceil(characters of the request JSON / 4), reported by the fixture "
        "provider and recorded by the harness",
        "measurements": measurements,
    }
    totals = {
        item["configuration"]: [run["recordedTokens"] for run in item["runs"]]
        for item in measurements
    }
    report["deltaPerRun"] = [
        after - before for before, after in zip(totals["before"], totals["after"], strict=True)
    ]
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(markdown(report) if args.markdown else text)
    ok = all(run["exitCode"] == 4 for item in measurements for run in item["runs"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
