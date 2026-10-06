#!/usr/bin/env python3
"""Measure the agent tokens and the friction of a small change before and after the fast lane
(#58).

Token cost and friction are acceptance criteria of issue #58: a small, risk-free task should
cost fewer agent tokens in the fast lane, and fewer interactions and less wall time for the
person. This script measures both with the installed ``harness`` CLI and a fixture command
provider that calls no model:

* the provider answers every request (implement: it writes the same small change to
  ``src/sample/pricing.py`` and ``tests/test_pricing.py``; the read-only kinds: empty answers)
  and reports as usage an **estimate** of the tokens it received, the request's JSON length
  divided by four (a rule of thumb, not a tokenizer), and of the tokens it returned; the harness
  records that usage like any provider's;
* the provider logs each call's kind, for the breakdown by call kind;
* friction is counted from what the person has to do: the commands typed, the interactions the
  harness records (``human.interactions``) and the wall-clock seconds of those commands on this
  machine (the person's own reading and thinking time is not measured).

Three flows on the same project and the same two tasks, one after the other:

* ``before``: ``harness init`` without the ``friction`` section (the full flow for every task):
  ``task create``, ``run start`` (exit 4), ``gate decide`` APPROVE;
* ``fast-lane``: ``harness init`` as written; ``harness do TEXT`` (exit 4), ``gate decide``;
* ``fast-lane+pre-approval``: ``harness do TEXT --pre-approve`` (exit 0, one command).

The JSON report goes to standard output (or ``--output``); ``--markdown`` prints tables.
Exit code 0 when every command ended as expected.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
    "locate": {"locations": []},
    "architecture": (
        {"options": []}
        if request.get("mode") == "advise"
        else {"style": "custom", "summary": "No layering.", "layers": [], "allow": {}}
    ),
}
if kind == "implement":
    for path, content in json.loads(Path("change.json").read_text()).items():
        Path(path).write_text(content)
    answer = {"status": "PASSED", "summary": "Change written"}
else:
    answer = {"status": "PASSED", "summary": kind, "result": results.get(kind, {})}
answer["usage"] = {
    "inputTokens": math.ceil(len(text) / 4),
    "outputTokens": math.ceil(len(json.dumps(answer)) / 4),
}
with Path(LOG).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"kind": kind, "inputTokens": answer["usage"]["inputTokens"]}) + "\\n")
print(json.dumps(answer))
"""

CODE = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return {body}\n"
)
TEST_HEAD = (
    "from sample import apply_discount\n\n\n"
    "def test_below_threshold() -> None:\n"
    "    assert apply_discount(99, 100, 0.1) == 99\n"
)
TEST = (
    "\n\ndef test_req_discount_{name}() -> None:\n"
    "    assert apply_discount({subtotal}, 100, 0.1) == {expected}\n"
)
TASKS = (
    (
        "task_first",
        100,
        90,
        "at_threshold",
        "subtotal * (1 - rate) if subtotal >= threshold else subtotal",
    ),
    (
        "task_second",
        150,
        135,
        "above_threshold",
        "round(subtotal * (1 - rate), 2) if subtotal >= threshold else subtotal",
    ),
)
TEXT = "Apply the discount only when the subtotal meets the threshold ({name})."
CRITERION = "apply_discount({subtotal}, 100, 0.1) returns {expected}."
TASK = """\
taskId: {task_id}
title: Threshold discount ({task_id})
intent: Apply the discount only when the subtotal meets the threshold ({name}).
acceptanceCriteria:
  - criterionId: ac_threshold
    text: apply_discount({subtotal}, 100, 0.1) returns {expected}.
"""
FLOWS = ("before", "fast-lane", "fast-lane+pre-approval")


def harness(root: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    result = subprocess.run(
        [shutil.which("harness") or "harness", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=demo_flows.isolated_env(),
    )
    return result, time.monotonic() - started


def configure(root: Path, log: Path, *, friction: bool) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text())
    if not friction:
        config.pop("friction", None)
    (root / "agent.py").write_text(AGENT.replace("LOG", repr(str(log))))
    config["agentProvider"] = "measure"
    config["agentProviders"] = {"measure": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"].update({"agentSandbox": "off", "providerRetryDelaySeconds": 0})
    path.write_text(yaml.safe_dump(config, sort_keys=False))


class Failed(Exception):
    pass


def expect(result: subprocess.CompletedProcess[str], code: int, what: str) -> Any:
    if result.returncode != code:
        raise Failed(
            f"{what}: exit {result.returncode}, expected {code}: "
            f"{(result.stderr or result.stdout)[-1500:]}"
        )
    return json.loads(result.stdout) if result.stdout.strip().startswith(("{", "[")) else None


def measure(workdir: Path, flow: str) -> dict[str, Any]:
    root = workdir / flow
    demo_flows.python_project(root)
    (root / ".gitignore").write_text(
        ".harness/\n__pycache__/\n.pytest_cache/\nagent.py\nchange.json\n*.yaml\n"
    )
    log = workdir / f"{flow}-calls.jsonl"
    log.unlink(missing_ok=True)
    expect(harness(root, "init", "--path", ".")[0], 0, "init")
    configure(root, log, friction=flow != "before")
    runs: list[dict[str, Any]] = []
    tests = TEST_HEAD
    for task_id, subtotal, expected, name, body in TASKS:
        # Each task adds its test to the earlier ones: nothing is removed.
        tests += TEST.format(name=name, subtotal=subtotal, expected=expected)
        (root / "change.json").write_text(
            json.dumps(
                {"src/sample/pricing.py": CODE.format(body=body), "tests/test_pricing.py": tests}
            )
        )
        before = log.read_text().splitlines() if log.exists() else []
        commands: list[str] = []
        seconds = 0.0
        if flow == "before":
            (root / f"{task_id}.yaml").write_text(
                TASK.format(task_id=task_id, name=name, subtotal=subtotal, expected=expected)
            )
            result, spent = harness(
                root, "task", "create", "--path", ".", "--file", f"{task_id}.yaml"
            )
            expect(result, 0, "task create")
            commands.append("task create")
            seconds += spent
            result, spent = harness(
                root, "--json", "run", "start", "--path", ".", "--task", task_id
            )
            run = expect(result, 4, "run start")
            commands.append("run start")
            seconds += spent
            run_id = run["executionId"]
        else:
            args = [
                "--json",
                "do",
                TEXT.format(name=name),
                "--path",
                ".",
                "--criterion",
                CRITERION.format(subtotal=subtotal, expected=expected),
                "--actor",
                "human.measure",
                "--no-interactive",
            ]
            pre = flow.endswith("pre-approval")
            if pre:
                args.append("--pre-approve")
            result, spent = harness(root, *args)
            done = expect(result, 0 if pre else 4, "do")
            commands.append("do --pre-approve" if pre else "do")
            seconds += spent
            run_id = done["run"]["executionId"]
        status = expect(
            harness(root, "--json", "status", "--path", ".", "--run", run_id)[0], 0, "status"
        )
        if status["execution"]["status"] != "PASSED":
            result, spent = harness(
                root,
                "--json",
                "gate",
                "decide",
                "--path",
                ".",
                "--run",
                run_id,
                "--decision",
                "APPROVE",
                "--change-set-digest",
                status["execution"]["changeSetDigest"],
                "--actor",
                "human.measure",
                "--rationale",
                "Measurement run",
            )
            expect(result, 0, "gate decide")
            commands.append("gate decide")
            seconds += spent
            status = expect(
                harness(root, "--json", "status", "--path", ".", "--run", run_id)[0], 0, "status"
            )
        budget = expect(
            harness(root, "--json", "budget", "show", "--path", ".", "--run", run_id)[0],
            0,
            "budget show",
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()[len(before) :]]
        by_kind: dict[str, int] = {}
        for call in calls:
            by_kind[call["kind"]] = by_kind.get(call["kind"], 0) + 1
        metrics = status["metrics"]
        runs.append(
            {
                "task": task_id,
                "status": status["execution"]["status"],
                "recordedTokens": budget["usage"]["run"]["tokens"],
                "recordedCalls": budget["usage"]["run"]["calls"],
                "callsByKind": dict(sorted(by_kind.items())),
                "humanInteractions": metrics["human.interactions"]["value"],
                "humanDecisions": metrics["human.decisions"]["value"],
                "commandsTyped": len(commands),
                "commands": commands,
                "commandSeconds": round(seconds, 2),
                "runWallSeconds": round(metrics["duration.total_ms"]["value"] / 1000, 2),
            }
        )
        demo_flows.git(root, "add", "src", "tests")
        demo_flows.git(root, "commit", "-qm", f"{task_id} done")
    return {"flow": flow, "runs": runs}


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "| Flow | Task | Agent calls | Recorded tokens | Calls by kind | Interactions | "
        "Commands | Command seconds |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for item in report["measurements"]:
        for run in item["runs"]:
            kinds = ", ".join(f"{kind} {count}" for kind, count in run["callsByKind"].items())
            lines.append(
                f"| {item['flow']} | {run['task']} | {run['recordedCalls']} | "
                f"{run['recordedTokens']} | {kinds} | {run['humanInteractions']} | "
                f"{run['commandsTyped']} | {run['commandSeconds']} |"
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
        temp = tempfile.mkdtemp(prefix="harness-friction-")
        workdir = Path(temp)
    else:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
    anchors = tempfile.mkdtemp(prefix="harness-anchors-")
    os.environ.setdefault("HARNESS_ANCHOR_DIR", anchors)
    state = tempfile.mkdtemp(prefix="harness-state-")
    os.environ.setdefault("HARNESS_STATE_DIR", state)
    ok = True
    measurements: list[dict[str, Any]] = []
    try:
        for flow in FLOWS:
            try:
                measurements.append(measure(workdir, flow))
            except Failed as error:
                ok = False
                measurements.append({"flow": flow, "error": str(error), "runs": []})
    finally:
        shutil.rmtree(anchors, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    report: dict[str, Any] = {
        "estimate": "tokens = ceil(characters of the request JSON / 4), reported by the fixture "
        "provider and recorded by the harness",
        "friction": "commands the person typed, interactions the harness recorded and the "
        "wall-clock seconds of those commands on this machine (no human time)",
        "measurements": measurements,
    }
    tokens = {
        item["flow"]: [run["recordedTokens"] for run in item["runs"]] for item in measurements
    }
    if all(len(values) == len(TASKS) for values in tokens.values()):
        report["tokenDeltaPerRun"] = {
            flow: [after - before for before, after in zip(tokens["before"], values, strict=True)]
            for flow, values in tokens.items()
            if flow != "before"
        }
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(markdown(report) if args.markdown else text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
