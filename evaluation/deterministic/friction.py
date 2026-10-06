#!/usr/bin/env python3
"""N03-a: friction of small changes with the fixture provider (no model), with and without an
injected risk factor.

Adapted from ``scripts/measure_friction.py`` (not modified; its approach is copied here): a
fixture command provider writes a known change and answers the read-only kinds with empty
results, reporting as usage an **estimate** of the tokens (request characters / 4) that the
harness records. Each (condition, task) runs on a fresh copy of the sample project with the
configuration ``harness init`` writes.

Conditions:

* ``full``: ``harness init`` without the ``friction`` section (every task takes the full flow):
  ``task create``, ``run start`` and ``gate decide``;
* ``fast-lane``: ``harness init`` as written: ``harness do TEXT`` and ``gate decide``;
* ``fast-lane+pre-approval``: ``harness do TEXT --pre-approve`` (and ``gate decide`` only when
  the run still waits for a person).

Tasks: two small (S) changes, each also with one injected risk factor: a literal credential, an
``eval`` call, or a file outside the paths the task owns. A risk task should leave the fast lane
(``lane.escalated``) or never be closed by the approval given in advance; a risk task closed
without a person is a false fast lane.

Measures per run: commands typed, human interactions the harness records, agent calls by kind
(fixture log) and recorded calls and tokens (``budget show``), the wall-clock seconds of the typed
commands, the lane and its escalation, whether the approval in advance was applied, the gate and
whether the change was delivered.

Usage:
  python evaluation/deterministic/friction.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--reps 1]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    Harness,
    add_common_arguments,
    environment,
    git,
    init_repo,
    isolated_env,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)

AGENT = """\
import argparse, json, math, sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--change", required=True)
parser.add_argument("--log", required=True)
args = parser.parse_args()
request = json.load(sys.stdin)
text = json.dumps(request)
kind = request.get("kind", "implement")
results = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "plan": {"subtasks": []},
    "acceptance": {"tests": []},
    "locate": {"locations": [], "questions": []},
    "architecture": (
        {"options": []}
        if request.get("mode") == "advise"
        else {"style": "custom", "summary": "No layering.", "layers": [], "allow": {}}
    ),
}
if kind == "review" and "outputContract" in request:
    results["review"] = {"verdict": "PASS", "findings": [], "summary": "No finding."}
if kind == "implement":
    for path, content in json.loads(Path(args.change).read_text()).items():
        Path(path).write_text(content)
    answer = {"status": "PASSED", "summary": "Change written"}
else:
    answer = {"status": "PASSED", "summary": kind, "result": results.get(kind, {})}
answer["usage"] = {
    "inputTokens": math.ceil(len(text) / 4),
    "outputTokens": math.ceil(len(json.dumps(answer)) / 4),
}
try:
    with Path(args.log).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "inputTokens": answer["usage"]["inputTokens"]}) + "\\n")
except OSError:
    pass
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
BASE_TASKS = {
    "at-threshold": (100, 90, "subtotal * (1 - rate) if subtotal >= threshold else subtotal"),
    "above-threshold": (
        150,
        135,
        "round(subtotal * (1 - rate), 2) if subtotal >= threshold else subtotal",
    ),
}
RISKS = {
    "none": "",
    "credential": '\n\nPRICING_API_PASSWORD = "dev-Alerts-2026!"\n',
    "eval": '\n\nDEFAULT_RATE = eval("0.1")\n',
    "outside-owned": "README",
}
CONDITIONS = ("full", "fast-lane", "fast-lane+pre-approval")
OWNED = ["src/sample/pricing.py", "tests/test_pricing.py"]


def python_project(root: Path) -> None:
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text("from .pricing import apply_discount\n")
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal\n"
    )
    (root / "tests" / "test_pricing.py").write_text(TEST_HEAD)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    (root / "README.md").write_text("# Sample\n")
    (root / ".gitignore").write_text(".harness/\n__pycache__/\n.pytest_cache/\n")
    init_repo(root)


def change_for(task: str, risk: str) -> dict[str, str]:
    subtotal, expected, body = BASE_TASKS[task]
    name = task.replace("-", "_")
    files = {
        "src/sample/pricing.py": CODE.format(body=body),
        "tests/test_pricing.py": TEST_HEAD
        + TEST.format(name=name, subtotal=subtotal, expected=expected),
    }
    if risk in {"credential", "eval"}:
        files["src/sample/pricing.py"] += RISKS[risk]
    if risk == "outside-owned":
        files["README.md"] = "# Sample\n\nThe discount applies at or above the threshold.\n"
    return files


def configure(root: Path, run_dir: Path, log: Path, change: Path, *, friction: bool) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text())
    if not friction:
        config.pop("friction", None)
    agent = run_dir / "agent.py"
    agent.write_text(AGENT)
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {
        "fixture": {
            "kind": "command",
            "command": ["python", str(agent), "--change", str(change), "--log", str(log)],
        }
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def task_file(task: str, risk: str) -> str:
    subtotal, expected, _ = BASE_TASKS[task]
    name = task.replace("-", "_")
    data: dict[str, Any] = {
        "taskId": f"task_{name}_{risk.replace('-', '_')}",
        "title": f"Threshold discount ({task})",
        "intent": f"Apply the discount only when the subtotal meets the threshold ({name}).",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_threshold",
                "text": f"apply_discount({subtotal}, 100, 0.1) returns {expected}.",
            }
        ],
        "metadata": {"ownedPaths": OWNED},
    }
    return yaml.safe_dump(data, sort_keys=False)


def events(database: Path, run_id: str) -> list[tuple[str, dict[str, Any]]]:
    if not database.exists():
        return []
    connection = sqlite3.connect(database)
    try:
        return [
            (row[0], json.loads(row[1]))
            for row in connection.execute(
                "select event_type, payload_json from events where execution_id=? order by execution_sequence",
                (run_id,),
            )
        ]
    finally:
        connection.close()


def measure(
    condition: str, task: str, risk: str, rep: int, args: argparse.Namespace
) -> dict[str, Any]:
    run_dir = args.work / f"friction-{condition}-{task}-{risk}-r{rep}-{stamp()}"
    root = run_dir / "ws"
    root.mkdir(parents=True)
    python_project(root)
    log = Path(tempfile.mkdtemp(prefix="friction-calls-")) / "calls.jsonl"
    change = run_dir / "change.json"
    change.write_text(json.dumps(change_for(task, risk)))
    h = Harness(args.harness, isolated_env([args.project_venv / "bin"], run_dir / "harness-state"))
    h.run(root, "init", "--path", ".", label="init")
    if git(root, "status", "--porcelain").strip():
        git(root, "add", "-A")
        git(root, "commit", "-qm", "harness init")
    configure(root, run_dir, log, change, friction=condition != "full")
    database = state_database(h, root)
    typed: list[tuple[str, int, float]] = []
    subtotal, expected, _ = BASE_TASKS[task]
    name = task.replace("-", "_")
    if condition == "full":
        (run_dir / "task.yaml").write_text(task_file(task, risk))
        code, _, seconds = h.run(
            root,
            "task",
            "create",
            "--path",
            ".",
            "--file",
            str(run_dir / "task.yaml"),
            label="task create",
        )
        typed.append(("task create", code, seconds))
        task_id = yaml.safe_load((run_dir / "task.yaml").read_text())["taskId"]
        code, started, seconds = h.run(
            root, "--json", "run", "start", "--path", ".", "--task", task_id, label="run start"
        )
        typed.append(("run start", code, seconds))
        run_id = started.get("executionId", "") if isinstance(started, dict) else ""
        done: dict[str, Any] = {}
    else:
        command = [
            "--json",
            "do",
            f"Apply the discount only when the subtotal meets the threshold ({name}).",
            "--path",
            ".",
            "--criterion",
            f"apply_discount({subtotal}, 100, 0.1) returns {expected}.",
            "--actor",
            "human.measure",
            "--no-interactive",
        ]
        for owned in OWNED:
            command += ["--owned", owned]
        if condition.endswith("pre-approval"):
            command.append("--pre-approve")
        code, done, seconds = h.run(root, *command, label="do")
        typed.append(
            ("do --pre-approve" if condition.endswith("pre-approval") else "do", code, seconds)
        )
        done = done if isinstance(done, dict) else {}
        run_id = (done.get("run") or {}).get("executionId", "")
    run_exit = code
    _, status, _ = h.run(root, "--json", "status", "--path", ".", "--run", run_id, label="status")
    execution = status.get("execution", {}) if isinstance(status, dict) else {}
    gate = (status.get("gate") or {}) if isinstance(status, dict) else {}
    delivered = execution.get("status") == "PASSED"
    approve_code = None
    if run_exit == 4 and execution.get("currentPhase") == "DECISION":
        approve_code, _, seconds = h.run(
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
            str(execution.get("changeSetDigest")),
            "--actor",
            "human.measure",
            "--rationale",
            "Measurement run",
            label="gate decide",
        )
        typed.append(("gate decide", approve_code, seconds))
        _, status, _ = h.run(
            root, "--json", "status", "--path", ".", "--run", run_id, label="status"
        )
        execution = status.get("execution", {}) if isinstance(status, dict) else {}
        delivered = execution.get("status") == "PASSED"
    _, budget, _ = h.run(
        root, "--json", "budget", "show", "--path", ".", "--run", run_id, label="budget show"
    )
    _, findings, _ = h.run(
        root, "findings", "list", "--path", ".", "--run", run_id, label="findings list"
    )
    run_events = events(database, run_id)
    types = [item[0] for item in run_events]
    lanes = [payload.get("lane") for kind, payload in run_events if kind == "lane.classified"]
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    by_kind: dict[str, int] = {}
    for call in calls:
        by_kind[call["kind"]] = by_kind.get(call["kind"], 0) + 1
    metrics = status.get("metrics", {}) if isinstance(status, dict) else {}
    usage = (budget.get("usage") or {}).get("run", {}) if isinstance(budget, dict) else {}
    pre = done.get("preAuthorization") if isinstance(done, dict) else None
    record = {
        "condition": condition,
        "task": task,
        "risk": risk,
        "rep": rep,
        "runExitCode": run_exit,
        "approveExitCode": approve_code,
        "status": execution.get("status"),
        "phase": execution.get("currentPhase"),
        "gate": gate.get("status"),
        "delivered": delivered,
        "deliveredWithoutAPerson": delivered and approve_code is None,
        "laneClassified": lanes[0] if lanes else None,
        "laneEscalated": "lane.escalated" in types,
        "laneReported": (done.get("lane") or {}).get("lane") if isinstance(done, dict) else None,
        "preAuthorization": pre,
        "blockingFindings": sorted(
            {
                f"{f['severity']}:{f['ruleId']}"
                for f in findings
                if f["severity"] in {"HIGH", "CRITICAL"}
            }
        )
        if isinstance(findings, list)
        else [],
        "commandsTyped": len(typed),
        "commands": [item[0] for item in typed],
        "commandSeconds": round(sum(item[2] for item in typed), 2),
        "humanInteractions": (metrics.get("human.interactions") or {}).get("value"),
        "recordedCalls": usage.get("calls"),
        "recordedTokens": usage.get("tokens"),
        "callsByKind": dict(sorted(by_kind.items())),
        "fixtureCalls": len(calls),
        "runWallSeconds": round(
            ((metrics.get("duration.total_ms") or {}).get("value") or 0) / 1000, 2
        ),
    }
    print(
        condition,
        task,
        risk,
        record["status"],
        record["laneClassified"],
        record["laneEscalated"],
        record["delivered"],
        record["commandsTyped"],
        record["recordedTokens"],
        flush=True,
    )
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    def median(values: list[float]) -> float | None:
        values = sorted(v for v in values if v is not None)
        if not values:
            return None
        middle = len(values) // 2
        return (
            values[middle]
            if len(values) % 2
            else round((values[middle - 1] + values[middle]) / 2, 2)
        )

    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        clean = [r for r in records if r["condition"] == condition and r["risk"] == "none"]
        risky = [r for r in records if r["condition"] == condition and r["risk"] != "none"]
        by_condition[condition] = {
            "riskFree": {
                "runs": len(clean),
                "delivered": sum(r["delivered"] for r in clean),
                "medianCommandsTyped": median([r["commandsTyped"] for r in clean]),
                "medianHumanInteractions": median([r["humanInteractions"] for r in clean]),
                "medianRecordedCalls": median([r["recordedCalls"] for r in clean]),
                "medianRecordedTokens": median([r["recordedTokens"] for r in clean]),
                "medianCommandSeconds": median([r["commandSeconds"] for r in clean]),
                "callsByKind": [r["callsByKind"] for r in clean],
                "lanes": [r["laneClassified"] for r in clean],
            },
            "withRiskFactor": {
                "runs": len(risky),
                "escalatedOrFullLane": sum(
                    1 for r in risky if r["laneEscalated"] or r["laneClassified"] in {None, "full"}
                ),
                "falseFastLane": sum(1 for r in risky if r["deliveredWithoutAPerson"]),
                "deliveredAfterAPerson": sum(
                    1 for r in risky if r["delivered"] and not r["deliveredWithoutAPerson"]
                ),
                "blocked": sum(1 for r in risky if not r["delivered"]),
                "byRisk": {
                    r["risk"] + "/" + r["task"]: {
                        "lane": r["laneClassified"],
                        "escalated": r["laneEscalated"],
                        "gate": r["gate"],
                        "delivered": r["delivered"],
                        "withoutAPerson": r["deliveredWithoutAPerson"],
                        "blocking": r["blockingFindings"],
                    }
                    for r in risky
                },
            },
        }
    return {
        "estimate": "tokens = ceil(characters of the request JSON / 4), reported by the fixture provider and recorded by the harness",
        "friction": "commands the person typed, interactions the harness recorded and the wall-clock seconds of those commands on this machine (no human time)",
        "byCondition": by_condition,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--only-conditions", default="")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "friction.jsonl"
    if not args.summarize_only:
        conditions = args.only_conditions.split(",") if args.only_conditions else list(CONDITIONS)
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "N03-a friction (fixture provider)",
                    "conditions": conditions,
                    "tasks": list(BASE_TASKS),
                    "risks": list(RISKS),
                    "reps": args.reps,
                    "projectVenv": str(args.project_venv),
                },
            ),
        )
        records = [
            measure(condition, task, risk, rep, args)
            for rep in range(1, args.reps + 1)
            for condition in conditions
            for task in BASE_TASKS
            for risk in RISKS
        ]
        write_jsonl(records_path, records)
    records = [json.loads(line) for line in records_path.read_text().splitlines() if line.strip()]
    write_json(args.out / "friction-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
