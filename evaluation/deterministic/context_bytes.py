#!/usr/bin/env python3
"""H22: bytes and estimated tokens each agent call kind sends, with the minimal-context contract
of wave 11 (``agentContract: enforce``), with it off and in the direct prompt. No model is called.

Tasks (``CATALOGO.md`` H22: «3 escenarios + increments»):

* ``greenfield``: ``evaluation/tasks/greenfield-shipping.yaml`` on its fixture;
* ``security``: ``evaluation/tasks/greenfield-alerts.yaml`` on its fixture;
* ``brownfield``: ``evaluation/tasks/brownfield-itsdangerous.yaml`` on itsdangerous 2.2.0 (the
  archive in ``--cache``, checked by ``run_eval.prepare``; without it the task is not evaluated);
* ``increments``: the five increments of ``evaluation/longitudinal/increments.yaml`` merged into
  one task (their requirements, criteria and constraints, in order) on the longitudinal fixture.
  Interpretation declared here: it is the only one of the four tasks with at least 5
  requirements (9), the population of the catalogue's criterion.

Conditions:

* ``enforce``: ``harness init`` as written (``agentContract.mode: enforce``);
* ``off``: the same with ``agentContract.mode: off``;
* ``D*``: the direct prompt of ``agentlib.build_prompt`` for the task (one prompt; the direct
  agent makes no read-only calls). **A model of the direct agent's input, not an agent.**
* extension, declared here and not in the catalogue: ``enforce-plan`` and ``off-plan`` set
  ``planning.threshold: 1`` so that the ``plan`` call is made (with the default threshold of 12
  requirements none of the four tasks is decomposed, so ``plan`` is otherwise not measured).

In each governed run the fixture provider (``suite_support.FIXTURE_AGENT``) writes the reference
solution of the task plus a test per requirement and criterion id the implement request names,
answers each read-only call with an empty valid result, and logs every request it receives: its
size as the harness measures it (``agents/contract.py:request_bytes``), characters / 4 as
estimated tokens, its top-level keys and the task fields it carries. The waits before DECISION
are answered by the declared simulated person (``common.simulated_person``). Under ``enforce``
the bytes the harness itself recorded (``agent.call.recorded``, ``sentBytes``; what
``harness agent-contract calls`` shows) are kept next to the fixture's measurement.

Metrics: bytes and estimated tokens by task, condition and kind (first call and every call), the
ratio enforce/off by kind, the ratio of ``implement`` to the direct prompt, and the task fields
sent against ``TASK_FIELDS`` of the harness under test.

Usage:
  python evaluation/deterministic/context_bytes.py --out DIR [--work DIR] [--cache DIR]
      [--project-venv DIR] [--harness PATH] [--only-tasks a,b] [--only-conditions a,b]
      [--wheel WHEEL]

Writes ``DIR/context-bytes.jsonl`` (one record per task and condition, with every call),
``DIR/context-bytes-summary.json`` and ``DIR/environment.json``. Exits 0 when the suite ran,
whatever it found.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    Harness,
    add_common_arguments,
    environment,
    git,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from suite_support import (  # noqa: E402
    bin_dir,
    commit_init,
    configure,
    direct_prompt,
    events,
    import_agentlib,
    portable_env,
    read_log,
    start_run,
    status_of,
    write_fixture,
)

ARCHIVE = "itsdangerous-2.2.0.tar.gz"
GOVERNED = ("enforce", "off")
EXTENSIONS = ("enforce-plan", "off-plan")
CONDITIONS = (*GOVERNED, "D*", *EXTENSIONS)
KINDS = ("clarify", "plan", "acceptance", "locate", "architecture", "review", "implement")
READ_ONLY = tuple(k for k in KINDS if k != "implement")
TRACE_PATH = "tests/test_trace_ids.py"
TASKS: dict[str, dict[str, Any]] = {
    "greenfield": {
        "file": EVALUATION / "tasks" / "greenfield-shipping.yaml",
        "fixture": EVALUATION / "fixtures" / "greenfield-shipping",
        "change": {
            "src/shipping/__init__.py": EVALUATION
            / "reference"
            / "greenfield-shipping"
            / "shipping"
            / "__init__.py"
        },
        "header": "from shipping import shipping_cost\n",
        "assert": "callable(shipping_cost)",
    },
    "security": {
        "file": EVALUATION / "tasks" / "greenfield-alerts.yaml",
        "fixture": EVALUATION / "fixtures" / "greenfield-alerts",
        "change": {
            "src/alerts/__init__.py": EVALUATION
            / "reference"
            / "greenfield-alerts"
            / "alerts"
            / "__init__.py"
        },
        "header": "from alerts import build_alert_request\n",
        "assert": "callable(build_alert_request)",
    },
    "brownfield": {
        "file": EVALUATION / "tasks" / "brownfield-itsdangerous.yaml",
        "archive": ARCHIVE,
        "change": {
            "src/itsdangerous/encoding.py": EVALUATION
            / "reference"
            / "brownfield-itsdangerous-encoding.py"
        },
        "header": "from itsdangerous.encoding import base64_decode\n",
        "assert": "base64_decode('YWJj') == b'abc'",
    },
    "increments": {
        "fixture": EVALUATION / "longitudinal" / "fixture",
        "change": {
            "src/inventory/__init__.py": EVALUATION
            / "longitudinal"
            / "reference"
            / "inventory"
            / "__init__.py"
        },
        "header": "import inventory\n",
        "assert": "hasattr(inventory, 'Inventory')",
    },
}


def task_of(name: str) -> dict[str, Any]:
    spec = TASKS[name]
    if "file" in spec:
        return yaml.safe_load(Path(spec["file"]).read_text(encoding="utf-8"))
    increments = yaml.safe_load(
        (EVALUATION / "longitudinal" / "increments.yaml").read_text(encoding="utf-8")
    )
    merged: dict[str, Any] = {
        "taskId": "task_eval_increments_merged",
        "title": "Inventory library (the five increments in one task)",
        "intent": " ".join(str(item["task"]["intent"]).strip() for item in increments),
        "requirements": [],
        "acceptanceCriteria": [],
        "constraints": [],
    }
    for item in increments:
        task = item["task"]
        merged["requirements"] += list(task.get("requirements") or [])
        merged["acceptanceCriteria"] += list(task.get("acceptanceCriteria") or [])
        for constraint in task.get("constraints") or []:
            if constraint not in merged["constraints"]:
                merged["constraints"].append(constraint)
    return merged


def prepare_workspace(name: str, ws: Path, args: argparse.Namespace) -> str | None:
    """The task's workspace, committed; the reason when it cannot be prepared."""
    spec = TASKS[name]
    if "archive" in spec:
        if not args.cache or not (args.cache / spec["archive"]).exists():
            return f"{spec['archive']} is not in --cache"
        import_agentlib()  # run_eval imports agentlib
        from run_eval import prepare

        site = next((args.project_venv / "lib").glob("python3*/site-packages"), None)
        if site is None:
            return "the project venv has no lib/python3*/site-packages (brownfield needs it)"
        prepare("brownfield", ws, args.cache, site)
        return None
    ws.mkdir(parents=True)
    for item in Path(spec["fixture"]).iterdir():
        target = ".gitignore" if item.name == "gitignore.template" else item.name
        shutil.copy2(item, ws / target)
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "baseline")
    return None


def allowed_fields() -> dict[str, list[str]] | None:
    try:
        from governed_harness.agents.contract import TASK_FIELDS
    except ImportError:
        return None
    return {kind: list(fields) for kind, fields in TASK_FIELDS.items()}


def governed(name: str, condition: str, args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.work / f"h22-{name}-{condition}-{stamp()}"
    run_dir.mkdir(parents=True)
    ws = run_dir / "ws"
    reason = prepare_workspace(name, ws, args)
    if reason:
        return {"notEvaluated": reason}
    state_root = run_dir / "harness-state"
    h = Harness(args.harness, portable_env([bin_dir(args.project_venv)], state_root))
    h.run(ws, "init", "--path", ".", label="init")
    commit_init(ws)
    spec = TASKS[name]
    log = Path(tempfile.mkdtemp(prefix=f"h22-{name}-")) / "calls.jsonl"
    command = write_fixture(
        run_dir,
        {
            "log": str(log),
            "change": {
                path: Path(source).read_text(encoding="utf-8")
                for path, source in spec["change"].items()
            },
            "traceTests": {"path": TRACE_PATH, "header": spec["header"], "assert": spec["assert"]},
        },
    )

    def mutate(config: dict[str, Any]) -> None:
        if condition.startswith("off"):
            config.setdefault("agentContract", {})["mode"] = "off"
        if condition.endswith("-plan"):
            config.setdefault("planning", {})["threshold"] = 1

    config, deviations = configure(ws, command, mutate=mutate)
    database = state_database(h, ws)
    task = task_of(name)
    started = start_run(h, ws, run_dir, task)
    run_id = started["runId"]
    status = status_of(h, ws, run_id)
    execution = status.get("execution") or {}
    recorded = [
        {
            "kind": e["payload"].get("callKind"),
            "iteration": e["payload"].get("iteration"),
            "sentBytes": e["payload"].get("sentBytes"),
            "receivedBytes": e["payload"].get("receivedBytes"),
            "finalStatus": e["payload"].get("finalStatus"),
        }
        for e in events(database, run_id)
        if e["type"] == "agent.call.recorded"
    ]
    calls = read_log(log)
    return {
        "deviations": deviations,
        "agentContractMode": (config.get("agentContract") or {}).get("mode"),
        "planningThreshold": (config.get("planning") or {}).get("threshold"),
        "requirements": len(task.get("requirements") or []),
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        "finalStatus": execution.get("status"),
        "finalPhase": execution.get("currentPhase"),
        "calls": [
            {
                k: c.get(k)
                for k in (
                    "kind",
                    "panel",
                    "number",
                    "requestBytes",
                    "requestChars",
                    "estimatedInputTokens",
                    "topLevelKeys",
                    "taskFields",
                    "contract",
                )
            }
            for c in calls
        ],
        "harnessRecorded": recorded,
        "steps": list(h.steps),
    }


def direct(name: str) -> dict[str, Any]:
    task = task_of(name)
    prompt = direct_prompt(task)
    return {
        "requirements": len(task.get("requirements") or []),
        "calls": [
            {
                "kind": "implement",
                "panel": False,
                "number": 1,
                "requestBytes": len(prompt.encode("utf-8")),
                "requestChars": len(prompt),
                "estimatedInputTokens": math.ceil(len(prompt) / 4),
                "source": "agentlib.build_prompt (the direct agent's whole input)",
            }
        ],
    }


def kind_key(call: dict[str, Any]) -> str:
    return "review-panel" if call.get("panel") else str(call["kind"])


def by_kind(record: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for call in record.get("calls") or []:
        grouped.setdefault(kind_key(call), []).append(call)
    out: dict[str, Any] = {}
    for kind, calls in grouped.items():
        sizes = [int(c["requestBytes"]) for c in calls]
        out[kind] = {
            "calls": len(calls),
            "firstBytes": sizes[0],
            "meanBytes": round(sum(sizes) / len(sizes), 1),
            "totalBytes": sum(sizes),
            "firstEstimatedTokens": calls[0].get("estimatedInputTokens"),
            "totalEstimatedTokens": sum(int(c.get("estimatedInputTokens") or 0) for c in calls),
            "taskFields": calls[0].get("taskFields"),
        }
    return out


def ratio(a: float | None, b: float | None) -> float | None:
    return round(a / b, 4) if a is not None and b else None


def summarize(records: list[dict[str, Any]], fields: dict[str, list[str]] | None) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for name in TASKS:
        rows = {r["condition"]: r for r in records if r["task"] == name}
        if not rows:
            continue
        kinds = {c: by_kind(r) for c, r in rows.items() if "notEvaluated" not in r}
        item: dict[str, Any] = {
            "requirements": next(
                (r.get("requirements") for r in rows.values() if r.get("requirements") is not None),
                None,
            ),
            "notEvaluated": {c: r["notEvaluated"] for c, r in rows.items() if "notEvaluated" in r},
            "finalStatus": {
                c: (r.get("finalStatus"), r.get("finalPhase")) for c, r in rows.items() if c != "D*"
            },
            "byCondition": kinds,
        }
        item["criterionApplies"] = (item["requirements"] or 0) >= 5
        for on, off in (("enforce", "off"), ("enforce-plan", "off-plan")):
            if on in kinds and off in kinds:
                item[f"ratio:{on}/{off}"] = {
                    kind: {
                        "first": ratio(
                            kinds[on][kind]["firstBytes"], kinds[off][kind]["firstBytes"]
                        ),
                        "mean": ratio(kinds[on][kind]["meanBytes"], kinds[off][kind]["meanBytes"]),
                    }
                    for kind in sorted(set(kinds[on]) & set(kinds[off]))
                }
                item[f"notCalled:{on}"] = sorted(set(KINDS) - set(kinds[on]))
        if "D*" in kinds:
            direct_bytes = kinds["D*"]["implement"]["firstBytes"]
            item["implementVsDirect"] = {
                c: ratio(k["implement"]["firstBytes"], direct_bytes)
                for c, k in kinds.items()
                if c != "D*" and "implement" in k
            }
        if fields is not None:
            item["taskFieldsVsTaskFields"] = {
                c: {
                    kind: {
                        "sent": data["taskFields"],
                        "extra": sorted(
                            set(data["taskFields"] or [])
                            - set(fields.get(kind.split("-")[0], data["taskFields"] or []))
                        ),
                        "allowedNotSent": sorted(
                            set(fields.get(kind.split("-")[0], [])) - set(data["taskFields"] or [])
                        ),
                    }
                    for kind, data in k.items()
                    if kind.split("-")[0] in fields and kind != "review-panel"
                }
                for c, k in kinds.items()
                if c != "D*"
            }
        recorded = (rows.get("enforce") or {}).get("harnessRecorded") or []
        if recorded and "enforce" in kinds:
            item["harnessRecordedVsFixture"] = {
                kind: {
                    "harnessFirstSentBytes": next(
                        (r["sentBytes"] for r in recorded if r["kind"] == kind), None
                    ),
                    "fixtureFirstBytes": kinds["enforce"].get(kind, {}).get("firstBytes"),
                }
                for kind in sorted({r["kind"] for r in recorded})
            }
        tasks[name] = item
    return {
        "measure": "requestBytes = len(json.dumps(request, sort_keys=True).encode('utf-8')) of the "
        "request the fixture provider received (agents/contract.py:request_bytes); estimated "
        "tokens = ceil(characters / 4); D* = bytes of agentlib.build_prompt(task)",
        "review-panel": "the reviewers of the review panel (requests with an outputContract) are "
        "counted apart from the review call of review.agentReview",
        "taskFields": fields,
        "byTask": tasks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None, help=f"directory with {ARCHIVE}")
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--only-tasks", default="")
    parser.add_argument("--only-conditions", default="")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    args.cache = args.cache.resolve() if args.cache else None
    records_path = args.out / "context-bytes.jsonl"
    fields = allowed_fields()
    if not args.summarize_only:
        tasks = args.only_tasks.split(",") if args.only_tasks else list(TASKS)
        conditions = args.only_conditions.split(",") if args.only_conditions else list(CONDITIONS)
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "H22 context bytes (fixture provider)",
                    "tasks": tasks,
                    "conditions": conditions,
                    "extensions": [c for c in conditions if c in EXTENSIONS],
                    "projectVenv": str(args.project_venv),
                    "taskFieldsOfHarnessUnderTest": fields,
                },
            ),
        )
        records = []
        for name in tasks:
            for condition in conditions:
                result = direct(name) if condition == "D*" else governed(name, condition, args)
                record = {"task": name, "condition": condition, **result}
                records.append(record)
                print(
                    name,
                    condition,
                    record.get("finalStatus"),
                    record.get("finalPhase"),
                    {k: v["firstBytes"] for k, v in by_kind(record).items()},
                    record.get("notEvaluated") or "",
                    flush=True,
                )
        write_jsonl(records_path, records)
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_json(args.out / "context-bytes-summary.json", summarize(records, fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
