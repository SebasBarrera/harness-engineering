#!/usr/bin/env python3
"""H38: the false fast lane with every risk factor (``CATALOGO.md`` §2.7, ``PREREGISTRO.md``).
No model.

N03-a (``friction.py``) extended from three injected risks to the eight factors of
``checks/risk.py:FACTORS``, plus the HIGH findings of H03/H05 and eight indirect risks (one per
factor, written so the lexical heuristic does not see them; reported apart). The project is the
sample project of N03-a with one more baseline module (``src/sample/legacy.py``, 24 non-blank
lines, above the 20 of ``deletedWithoutTests``) so a change can delete source without touching
the tests. Each risk rides on the two tasks of N03-a
(``at-threshold`` and ``above-threshold``), under the three conditions of N03-a:

* ``full``: ``harness init`` without the ``friction`` section: ``task create``, ``run start`` and
  ``gate decide``;
* ``fast-lane``: ``harness init`` as written: ``harness do TEXT`` and ``gate decide``;
* ``fast-lane+pre-approval``: ``harness do TEXT --pre-approve`` (``gate decide`` only when the
  run still waits for a person).

The person who decides is the measurement's person (``human.measure``, as in N03-a). The paths
the task owns (``--owned``, ``metadata.ownedPaths``) are the files the change touches, so scope is
not confused with the factor. A change with a direct or HIGH risk that ends delivered without a
``gate decide`` is a false fast lane.

Usage:
  python evaluation/deterministic/h38_false_fast_lane.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--reps 2] [--only risk,...] [--conditions a,b] [--summarize-only]

Output: DIR/environment.json, DIR/h38-fast-lane.jsonl (one record per condition, task, risk and
repetition), DIR/h38-fast-lane-summary.json, DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import (  # noqa: E402
    REPO,
    Harness,
    add_common_arguments,
    environment,
    git,
    init_repo,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from friction import BASE_TASKS, RISKS, TEST_HEAD, change_for, events  # noqa: E402

CONDITIONS = ("full", "fast-lane", "fast-lane+pre-approval")
PERSON = "human.measure"
PRICING = "src/sample/pricing.py"
TESTS = "tests/test_pricing.py"
LEGACY = "src/sample/legacy.py"
PYPROJECT = (
    '[project]\nname = "sample"\nversion = "0.1.0"\n\n'
    '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
)
LEGACY_MODULE = (
    "".join(
        f"def legacy_rate_{n}(subtotal: float) -> float:\n    return subtotal * 0.0{n % 9 + 1}\n\n\n"
        for n in range(1, 13)
    ).rstrip("\n")
    + "\n"
)


def project(root: Path) -> None:
    """The sample project of N03-a (``friction.python_project``), written with LF, plus
    ``src/sample/legacy.py``."""
    interception.write_files(
        root,
        {
            "src/sample/__init__.py": "from .pricing import apply_discount\n",
            PRICING: (
                "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
                "    return subtotal\n"
            ),
            LEGACY: LEGACY_MODULE,
            TESTS: TEST_HEAD,
            "pyproject.toml": PYPROJECT,
            "README.md": "# Sample\n",
            ".gitignore": ".harness/\n__pycache__/\n.pytest_cache/\n",
        },
    )
    init_repo(root)


def base(task: str) -> dict[str, str]:
    return dict(change_for(task, "none"))


def source(task: str, extra: str, head: str = "") -> dict[str, str]:
    files = base(task)
    files[PRICING] = head + files[PRICING] + extra
    return files


def renamed_parameter(task: str) -> dict[str, str]:
    files = base(task)
    files[PRICING] = (
        files[PRICING]
        .replace("rate: float", "percent: float")
        .replace("(1 - rate)", "(1 - percent)")
    )
    return files


Change = tuple[dict[str, str], list[str]]


def risk_change(risk: str, task: str) -> Change:
    """The files (path -> content) and deletions of ``risk`` on ``task``."""
    if risk == "none":
        return base(task), []
    if risk == "newDependency":
        files = base(task)
        files["pyproject.toml"] = PYPROJECT.replace(
            'version = "0.1.0"\n', 'version = "0.1.0"\ndependencies = ["requests>=2.31"]\n'
        )
        return files, []
    if risk == "authentication":
        return source(
            task,
            "\n\ndef discount_token_valid(token: str) -> bool:\n    return bool(token.strip())\n",
        ), []
    if risk == "destructiveMigration":
        files = base(task)
        files["migrations/0002_drop_legacy_discounts.sql"] = "DROP TABLE legacy_discounts;\n"
        return files, []
    if risk == "publicContract":
        files = base(task)
        files["src/sample/__init__.py"] = (
            'from .pricing import apply_discount\n\n__all__ = ["apply_discount"]\n'
        )
        return files, []
    if risk == "network":
        return source(
            task,
            "\n\ndef fetch_rates(url: str) -> bytes:\n"
            "    with urllib.request.urlopen(url) as response:\n"
            "        return response.read()\n",
            "import urllib.request\n\n\n",
        ), []
    if risk == "floatMoney":
        return source(
            task, "\n\ndef add_fee(amount: float) -> float:\n    return amount + 0.5\n"
        ), []
    if risk == "sensitiveLogging":
        return source(
            task,
            "\n\ndef audit_charge(card_number: str) -> None:\n"
            '    print("charging card", card_number)\n',
        ), []
    if risk == "deletedWithoutTests":
        return {PRICING: base(task)[PRICING]}, [LEGACY]
    if risk == "credential":
        # The literal credential of N03-a (friction.RISKS), the HIGH finding of H03.
        return source(task, RISKS["credential"]), []
    if risk == "requirementsDependency":
        files = base(task)
        files["requirements.txt"] = "requests==2.32.3\n"
        return files, []
    if risk == "newDependency-indirect":
        files = base(task)
        files["setup.cfg"] = "[options]\ninstall_requires =\n    requests>=2.31\n"
        return files, []
    if risk == "authentication-indirect":
        return source(
            task,
            "\n\ndef can_apply(user: str, allowed: frozenset[str]) -> bool:\n"
            "    return user in allowed\n",
        ), []
    if risk == "destructiveMigration-indirect":
        files = base(task)
        files["src/sample/cleanup.py"] = (
            "import sqlite3\n\n\ndef purge(database: str) -> None:\n"
            "    with sqlite3.connect(database) as connection:\n"
            '        connection.execute("DELETE FROM discounts")\n'
        )
        return files, []
    if risk == "publicContract-indirect":
        return renamed_parameter(task), []
    if risk == "network-indirect":
        return source(
            task,
            "\n\ndef open_channel(host: str, port: int) -> object:\n"
            '    module = importlib.import_module("socket")\n'
            "    return module.create_connection((host, port))\n",
            "import importlib\n\n\n",
        ), []
    if risk == "floatMoney-indirect":
        return source(
            task, "\n\ndef with_surcharge(value: float) -> float:\n    return value * 1.07\n"
        ), []
    if risk == "sensitiveLogging-indirect":
        return source(
            task,
            "\n\ndef trace_user(user: str, pw: str) -> None:\n"
            '    sys.stderr.write(f"{user}:{pw}\\n")\n',
            "import sys\n\n\n",
        ), []
    if risk == "deletedWithoutTests-indirect":
        return base(task), [LEGACY]
    raise ValueError(f"unknown risk {risk}")


FACTORS = (
    "newDependency",
    "authentication",
    "destructiveMigration",
    "publicContract",
    "network",
    "floatMoney",
    "sensitiveLogging",
    "deletedWithoutTests",
)
SETS: dict[str, tuple[str, ...]] = {
    "none": ("none",),
    "direct": FACTORS,
    "high": ("credential", "requirementsDependency"),
    "indirect": tuple(f"{factor}-indirect" for factor in FACTORS),
}
SET_OF = {risk: name for name, risks in SETS.items() for risk in risks}


def configure(ws: Path, change: Path, *, friction: bool) -> tuple[dict[str, Any], list[str]]:
    path = ws / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not friction:
        config.pop("friction", None)
    deviations = interception.point_at_fixture(config, change, core=False)
    interception.write_text(path, yaml.safe_dump(config, sort_keys=False))
    return config, deviations


def task_file(task: str, risk: str, owned: list[str]) -> dict[str, Any]:
    subtotal, expected, _ = BASE_TASKS[task]
    name = task.replace("-", "_")
    return {
        "taskId": f"task_{name}_{risk.replace('-', '_')}",
        "title": f"Threshold discount ({task})",
        "intent": f"Apply the discount only when the subtotal meets the threshold ({name}).",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_threshold",
                "text": f"apply_discount({subtotal}, 100, 0.1) returns {expected}.",
            }
        ],
        "metadata": {"ownedPaths": owned},
    }


def measure(
    condition: str, task: str, risk: str, rep: int, args: argparse.Namespace
) -> dict[str, Any]:
    """One run, as ``friction.measure`` does it (the same commands and decision rule)."""
    run_dir = args.work / f"h38-{condition}-{task}-{risk}-r{rep}-{stamp()}"
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    project(ws)
    files, deletions = risk_change(risk, task)
    owned = sorted({*files, *deletions})
    change = run_dir / "change.json"
    interception.write_text(change, json.dumps({"write": files, "delete": deletions}))
    h = Harness(args.harness, interception.harness_env(args.project_venv, run_dir / "state"))
    h.run(ws, "init", "--path", ".", label="init")
    if git(ws, "status", "--porcelain").strip():
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "harness init")
    config, deviations = configure(ws, change, friction=condition != "full")
    saved = args.out / "configs" / f"{condition}.yaml"
    if not saved.exists():
        text = yaml.safe_dump(config, sort_keys=False)
        for local, placeholder in ((str(run_dir), "<run>"), (str(REPO), "<repo>")):
            text = text.replace(local, placeholder)
        interception.write_text(saved, text)
    database = state_database(h, ws)
    typed: list[str] = []
    subtotal, expected, _ = BASE_TASKS[task]
    name = task.replace("-", "_")
    done: dict[str, Any] = {}
    if condition == "full":
        data = task_file(task, risk, owned)
        interception.write_text(run_dir / "task.yaml", yaml.safe_dump(data, sort_keys=False))
        h.run(
            ws,
            "task",
            "create",
            "--path",
            ".",
            "--file",
            str(run_dir / "task.yaml"),
            label="task create",
        )
        typed.append("task create")
        code, started, _ = h.run(
            ws, "--json", "run", "start", "--path", ".", "--task", data["taskId"], label="run start"
        )
        typed.append("run start")
        run_id = started.get("executionId", "") if isinstance(started, dict) else ""
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
            PERSON,
            "--no-interactive",
        ]
        for path in owned:
            command += ["--owned", path]
        if condition.endswith("pre-approval"):
            command.append("--pre-approve")
        code, done, _ = h.run(ws, *command, label="do")
        typed.append("do --pre-approve" if condition.endswith("pre-approval") else "do")
        done = done if isinstance(done, dict) else {}
        run_id = (done.get("run") or {}).get("executionId", "")
    run_exit = code
    _, status, _ = h.run(ws, "--json", "status", "--path", ".", "--run", run_id, label="status")
    execution = status.get("execution", {}) if isinstance(status, dict) else {}
    gate = (status.get("gate") or {}) if isinstance(status, dict) else {}
    delivered = execution.get("status") == "PASSED"
    approve_code = None
    if run_exit == 4 and execution.get("currentPhase") == "DECISION":
        approve_code, _, _ = h.run(
            ws,
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
            PERSON,
            "--rationale",
            "Measurement run",
            label="gate decide",
        )
        typed.append("gate decide")
        _, status, _ = h.run(ws, "--json", "status", "--path", ".", "--run", run_id, label="status")
        execution = status.get("execution", {}) if isinstance(status, dict) else {}
        delivered = execution.get("status") == "PASSED"
    _, findings, _ = h.run(ws, "findings", "list", "--path", ".", "--run", run_id, label="findings")
    findings = findings if isinstance(findings, list) else []
    run_events = events(database, run_id)
    types = [kind for kind, _ in run_events]
    lanes = [payload.get("lane") for kind, payload in run_events if kind == "lane.classified"]
    risky = SET_OF[risk] in {"direct", "high"}
    return {
        "suite": "H38",
        "condition": condition,
        "task": task,
        "risk": risk,
        "set": SET_OF[risk],
        "rep": rep,
        "ownedPaths": owned,
        "runExitCode": run_exit,
        "approveExitCode": approve_code,
        "status": execution.get("status"),
        "phase": execution.get("currentPhase"),
        "gate": gate.get("status"),
        "delivered": delivered,
        "deliveredWithoutAPerson": delivered and approve_code is None,
        "falseFastLane": risky and delivered and approve_code is None,
        "laneClassified": lanes[0] if lanes else None,
        "laneEscalated": "lane.escalated" in types,
        "laneReported": (done.get("lane") or {}).get("lane") if done else None,
        "preAuthorization": done.get("preAuthorization") if done else None,
        "riskFindings": sorted(
            {
                f"{f['severity']}:{f['ruleId']}"
                for f in findings
                if str(f.get("ruleId")).startswith("risk.")
            }
        ),
        "blockingFindings": sorted(
            {
                f"{f['severity']}:{f['ruleId']}"
                for f in findings
                if f.get("severity") in {"HIGH", "CRITICAL"}
            }
        ),
        "commandsTyped": len(typed),
        "commands": typed,
        "deviations": deviations,
        "modelCalls": 0,
        "steps": h.steps,
    }


def run_one(
    condition: str, task: str, risk: str, rep: int, args: argparse.Namespace
) -> dict[str, Any]:
    try:
        record = measure(condition, task, risk, rep, args)
    except Exception:  # noqa: BLE001 - the apparatus failed on this run: recorded, not hidden
        record = {
            "suite": "H38",
            "condition": condition,
            "task": task,
            "risk": risk,
            "set": SET_OF[risk],
            "rep": rep,
            "apparatusError": traceback.format_exc()[-2000:],
        }
    print(
        condition,
        task,
        risk,
        rep,
        "ERROR" if "apparatusError" in record else "",
        record.get("status"),
        record.get("laneClassified"),
        record.get("laneEscalated"),
        record.get("delivered"),
        record.get("falseFastLane"),
        flush=True,
    )
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r]
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in ok if r["condition"] == condition]
        if not rows:
            continue
        by_set: dict[str, Any] = {}
        for name in SETS:
            items = [r for r in rows if r["set"] == name]
            by_set[name] = {
                "runs": len(items),
                "falseFastLane": interception.proportion(
                    sum(r["deliveredWithoutAPerson"] for r in items), len(items)
                )
                if name != "none"
                else None,
                "deliveredWithoutAPerson": sum(r["deliveredWithoutAPerson"] for r in items),
                "deliveredAfterAPerson": sum(
                    1 for r in items if r["delivered"] and not r["deliveredWithoutAPerson"]
                ),
                "blocked": sum(1 for r in items if not r["delivered"]),
                "escalatedOrFullLane": sum(
                    1 for r in items if r["laneEscalated"] or r["laneClassified"] in {None, "full"}
                ),
                "byRisk": {
                    risk: {
                        f"{r['task']}/r{r['rep']}": {
                            "lane": r["laneClassified"],
                            "escalated": r["laneEscalated"],
                            "gate": r["gate"],
                            "status": r["status"],
                            "delivered": r["delivered"],
                            "withoutAPerson": r["deliveredWithoutAPerson"],
                            "approveExitCode": r["approveExitCode"],
                            "risk": r["riskFindings"],
                        }
                        for r in items
                        if r["risk"] == risk
                    }
                    for risk in SETS[name]
                },
            }
        by_condition[condition] = by_set
    signatures: dict[tuple[str, str, str], set[str]] = {}
    for r in ok:
        signatures.setdefault((r["condition"], r["task"], r["risk"]), set()).add(
            json.dumps(
                [
                    r["status"],
                    r["laneClassified"],
                    r["laneEscalated"],
                    r["delivered"],
                    r["approveExitCode"],
                ]
            )
        )
    false_lanes = sorted(
        f"{r['condition']}/{r['task']}/{r['risk']}/r{r['rep']}"
        for r in ok
        if r.get("falseFastLane")
    )
    risky = [r for r in ok if r["set"] in {"direct", "high"}]
    return {
        "suite": "H38",
        "title": "false fast lane with every risk factor",
        "person": f"{PERSON} decides at DECISION, as in N03-a",
        "runs": len(records),
        "apparatusErrors": sorted(
            f"{r['condition']}/{r['task']}/{r['risk']}/r{r['rep']}"
            for r in records
            if "apparatusError" in r
        ),
        "nonIdenticalRepetitions": sorted(
            "/".join(key) for key, values in signatures.items() if len(values) > 1
        ),
        "byCondition": by_condition,
        "criteria": {
            "statement": "false fast lanes = 0 in the direct and HIGH sets, in every condition (N03-a: 0 of 6)",
            "riskyRuns": len(risky),
            "falseFastLanes": false_lanes,
            "met": bool(risky) and not false_lanes,
            "evaluable": bool(risky),
        },
        "indirectDeliveredWithoutAPerson": sorted(
            f"{r['condition']}/{r['task']}/{r['risk']}/r{r['rep']}"
            for r in ok
            if r["set"] == "indirect" and r["deliveredWithoutAPerson"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--only", default="", help="comma-separated risks (see SETS)")
    parser.add_argument("--conditions", default="")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.project_venv = args.project_venv.resolve()
    if not shutil.which(args.harness):
        parser.error(f"harness executable not found: {args.harness}")
    args.work.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "h38-fast-lane.jsonl"
    if not args.summarize_only:
        conditions = [c for c in args.conditions.split(",") if c] or list(CONDITIONS)
        risks = [r for r in args.only.split(",") if r] or list(SET_OF)
        unknown = sorted((set(conditions) - set(CONDITIONS)) | (set(risks) - set(SET_OF)))
        if unknown:
            parser.error(f"unknown conditions or risks: {', '.join(unknown)}")
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "H38 false fast lane with every risk factor",
                    "conditions": conditions,
                    "tasks": list(BASE_TASKS),
                    "risks": risks,
                    "sets": {name: list(items) for name, items in SETS.items()},
                    "reps": args.reps,
                    "projectVenv": args.project_venv.name,
                    "platformDeviations": (
                        ["runtime.agentSandbox: off (no write sandbox on Windows)"]
                        if interception.WINDOWS
                        else []
                    ),
                },
            ),
        )
        records = [
            run_one(condition, task, risk, rep, args)
            for rep in range(1, args.reps + 1)
            for condition in conditions
            for task in BASE_TASKS
            for risk in risks
        ]
        write_jsonl(records_path, records)
    text = records_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    summary = summarize(records)
    write_json(args.out / "h38-fast-lane-summary.json", summary)
    return 1 if summary["apparatusErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
