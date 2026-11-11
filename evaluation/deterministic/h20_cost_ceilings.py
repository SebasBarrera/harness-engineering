#!/usr/bin/env python3
"""H20: cumulative USD cost ceilings (``budget.perCall/perTask/perRun``, ``warnAt`` and
``harness budget raise``), ``CATALOGO.md`` §2.3 and ``PREREGISTRO.md``. No model is called.

The fixture provider ``budget_agent.py`` reports a USD cost for every call, fixed by the scenario
by call number within the run (0.01 after the listed ones). Twelve scenarios over three
conditions: ``H-default`` (the ceilings ``harness init`` writes: per call 25, per task 200, per
run 100, ``warnAt`` 0.8), ``H-reduced`` (only the cost ceilings change: per call 2, per run 3,
per task 5) and ``H-reduced-raise`` (H-reduced, then the simulated person raises or tries to
raise the limit).

Every scenario is judged against an **oracle of the documented rules** applied to the costs the
fixture reported: after each call, per scope (call, run, task = all runs of the task), used >=
limit is ``budget.exceeded`` and otherwise used >= ``warnAt`` x limit is ``budget.warning``; after
an exceeded limit no further call is made until a person raises it. A scenario whose intended
crossing does not happen (the run made fewer calls) is "not evaluated", never conforming.

Usage:
  python evaluation/deterministic/h20_cost_ceilings.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only C01,...]
      [--conditions H-default,H-reduced,H-reduced-raise] [--reps 1] [--summarize-only]

Writes ``DIR/h20-cost-ceilings.jsonl``, ``DIR/h20-cost-ceilings-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import SIMULATED_PERSON  # noqa: E402
from resource_support import (  # noqa: E402
    cleanup,
    correct_spec,
    discount_task,
    finding_items,
    governed,
    main,
    proportion,
    read_log,
    run_state,
    run_task,
    saved_config,
    stamp_dir,
)
from suite_support import events  # noqa: E402

DEFAULT_COST = 0.01
WARN_AT = 0.8
CEILINGS = {
    "H-default": {"call": 25.0, "task": 200.0, "run": 100.0},
    "H-reduced": {"call": 2.0, "task": 5.0, "run": 3.0},
    "H-reduced-raise": {"call": 2.0, "task": 5.0, "run": 3.0},
}
RAISED_RUN_LIMIT = 10.0
SCOPES = ("call", "run", "task")


def scenario(
    case_id: str,
    condition: str,
    runs: list[list[float]],
    intended: tuple[str, str] | None,
    description: str,
    *,
    default: float = DEFAULT_COST,
    raise_mode: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "condition": condition,
        "runs": runs,
        "default": default,
        "intended": intended,
        "raise": raise_mode,
        "description": description,
    }


CASES: list[dict[str, Any]] = [
    scenario("C01", "H-default", [[]], None, "every call 0.01: nothing"),
    scenario("C02", "H-default", [[20.0]], ("warning", "call"), "call 1 at 80 % of per call"),
    scenario("C03", "H-default", [[25.0]], ("exceeded", "call"), "call 1 at the per-call limit"),
    scenario(
        "C04", "H-default", [[24.0] * 5], ("exceeded", "run"), "five calls of 24: run reaches 120"
    ),
    scenario("C05", "H-reduced", [[]], None, "every call 0.1: nothing", default=0.1),
    scenario("C06", "H-reduced", [[1.6]], ("warning", "call"), "call 1 at 80 % of per call"),
    scenario("C07", "H-reduced", [[2.5]], ("exceeded", "call"), "call 1 above the per-call limit"),
    scenario("C08", "H-reduced", [[1.2, 1.2]], ("warning", "run"), "run reaches 2.4 (80 %)"),
    scenario("C09", "H-reduced", [[1.5, 1.5]], ("exceeded", "run"), "run reaches 3.0"),
    scenario(
        "C10",
        "H-reduced",
        [[1.6, 0.9], [1.6, 1.0]],
        ("exceeded", "task"),
        "two runs of one task; the first cancelled at DECISION; the task reaches 5",
    ),
    scenario(
        "C11",
        "H-reduced-raise",
        [[1.5, 1.5]],
        ("exceeded", "run"),
        "as C09, then budget raise to 10 by a person and run continue",
        raise_mode="accepted",
    ),
    scenario(
        "C12",
        "H-reduced-raise",
        [[1.5, 1.5]],
        ("exceeded", "run"),
        "as C09, then three refused raises (empty rationale, not higher, agent actor)",
        raise_mode="refused",
    ),
]


def conditions_of(case: dict[str, Any]) -> list[str]:
    return [case["condition"]]


def reduced(config: dict[str, Any]) -> None:
    budget = config.setdefault("budget", {})
    for key, scope in (("perCall", "call"), ("perTask", "task"), ("perRun", "run")):
        budget.setdefault(key, {})["costUsd"] = CEILINGS["H-reduced"][scope]


# ----- the oracle ------------------------------------------------------------------------------
def oracle(
    costs_by_run: list[list[float]],
    ceilings: dict[str, float],
    raises: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """The documented outcome of each run for the costs the fixture reported. ``raises`` maps a
    run index to the run limit a person raised it to after its calls stopped."""
    task_total = 0.0
    out = []
    for index, costs in enumerate(costs_by_run):
        run_total = 0.0
        warnings: set[str] = set()
        exceeded: set[str] = set()
        first_exceeded: int | None = None
        run_limit = ceilings["run"]
        for number, cost in enumerate(costs, start=1):
            run_total += cost
            task_total += cost
            if first_exceeded is not None and raises and index in raises:
                run_limit = max(run_limit, raises[index])
            used = {"call": cost, "run": run_total, "task": task_total}
            limits = {"call": ceilings["call"], "run": run_limit, "task": ceilings["task"]}
            crossed = False
            for scope in SCOPES:
                if used[scope] >= limits[scope] - 1e-9:
                    exceeded.add(scope)
                    crossed = True
                elif used[scope] >= WARN_AT * limits[scope] - 1e-9:
                    warnings.add(scope)
            if crossed and first_exceeded is None:
                first_exceeded = number
        out.append(
            {
                "calls": len(costs),
                "runCost": round(run_total, 6),
                "warnings": sorted(warnings),
                "exceeded": sorted(exceeded),
                "firstExceededAtCall": first_exceeded,
            }
        )
    return out


# ----- one scenario ----------------------------------------------------------------------------
def budget_events(database: Path, run_id: str) -> dict[str, Any]:
    items = events(database, run_id)
    return {
        "warnings": sorted(
            {str(e["payload"].get("scope")) for e in items if e["type"] == "budget.warning"}
        ),
        "exceeded": sorted(
            {str(e["payload"].get("scope")) for e in items if e["type"] == "budget.exceeded"}
        ),
        "raised": [
            {
                "scope": e["payload"].get("scope"),
                "metric": e["payload"].get("metric"),
                "limit": e["payload"].get("limit"),
                "previousLimit": e["payload"].get("previousLimit"),
                "rationale": e["payload"].get("rationale"),
            }
            for e in items
            if e["type"] == "budget.raised"
        ],
        "eventTypes": sorted({e["type"] for e in items if e["type"].startswith("budget.")}),
    }


def budget_show(workspace: Any, run_id: str) -> dict[str, Any]:
    code, shown, _ = workspace.h.run(
        workspace.ws, "budget", "show", "--path", ".", "--run", run_id, label="budget show"
    )
    run_cost = None
    if isinstance(shown, dict):
        usage = shown.get("usage") or shown.get("used") or {}
        run_usage = usage.get("run") if isinstance(usage, dict) else None
        if isinstance(run_usage, dict):
            run_cost = run_usage.get("costUsd")
    return {"budgetShowExitCode": code, "budgetShowRunCost": run_cost, "budgetShow": shown}


def raise_budget(
    workspace: Any, run_id: str, *, to: float, rationale: str, actor: str, label: str
) -> int:
    code, _, _ = workspace.h.run(
        workspace.ws,
        "budget",
        "raise",
        "--path",
        ".",
        "--run",
        run_id,
        "--scope",
        "run",
        "--metric",
        "costUsd",
        "--to",
        f"{to:g}",
        "--rationale",
        rationale,
        "--actor",
        actor,
        label=label,
    )
    return code


def cancel(workspace: Any, run_id: str) -> int:
    code, _, _ = workspace.h.run(
        workspace.ws, "run", "cancel", "--path", ".", "--run", run_id, label="run cancel"
    )
    return code


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h20", case["id"], condition)
    spec = {**correct_spec(), "costs": case["runs"][0], "defaultCost": case["default"]}
    workspace = governed(run_dir, args, spec, mutate=reduced if condition != "H-default" else None)
    workspace.write_spec(spec, "calls-run1.jsonl")
    saved_config(args, condition, workspace)
    task = discount_task(f"task_h20_{case['id'].lower()}")
    runs: list[dict[str, Any]] = []
    raise_codes: dict[str, int] = {}
    for index, costs in enumerate(case["runs"]):
        if index:
            workspace.write_spec(
                {**correct_spec(), "costs": costs, "defaultCost": case["default"]},
                f"calls-run{index + 1}.jsonl",
            )
            started = workspace.h.run(
                workspace.ws,
                "run",
                "start",
                "--path",
                ".",
                "--task",
                task["taskId"],
                label="run start",
            )
            code, payload, _ = started
            run_id = payload.get("executionId", "") if isinstance(payload, dict) else ""
            result = {"runId": run_id, "runExitCode": code, "waits": []}
        else:
            result = run_task(workspace, task)
        run_id = result["runId"]
        log_before = len(read_log(workspace.log))
        entry: dict[str, Any] = {
            "runId": run_id,
            "runExitCode": result["runExitCode"],
            "waits": result["waits"],
            "log": workspace.log.name,
            "callsBeforeAction": log_before,
        }
        if case["raise"] == "accepted":
            raise_codes["accepted"] = raise_budget(
                workspace,
                run_id,
                to=RAISED_RUN_LIMIT,
                rationale="evaluation: raise the run ceiling to continue",
                actor=SIMULATED_PERSON,
                label="budget raise",
            )
        elif case["raise"] == "refused":
            raise_codes["emptyRationale"] = raise_budget(
                workspace,
                run_id,
                to=RAISED_RUN_LIMIT,
                rationale=" ",
                actor=SIMULATED_PERSON,
                label="budget raise (empty rationale)",
            )
            raise_codes["notHigher"] = raise_budget(
                workspace,
                run_id,
                to=CEILINGS[condition]["run"],
                rationale="evaluation: a limit that is not higher",
                actor=SIMULATED_PERSON,
                label="budget raise (not higher)",
            )
            raise_codes["agentActor"] = raise_budget(
                workspace,
                run_id,
                to=RAISED_RUN_LIMIT,
                rationale="evaluation: raised by an agent",
                actor="agent.fixture",
                label="budget raise (agent actor)",
            )
        if case["raise"]:
            code, _, _ = workspace.h.run(
                workspace.ws,
                "run",
                "continue",
                "--path",
                ".",
                "--run",
                run_id,
                label="run continue",
            )
            entry["continueExitCode"] = code
        if len(case["runs"]) > 1 and index < len(case["runs"]) - 1:
            entry["cancelExitCode"] = cancel(workspace, run_id)
        logged = read_log(workspace.log)
        entry["costs"] = [float((c.get("usage") or {}).get("costUsd") or 0.0) for c in logged]
        entry["calls"] = len(logged)
        entry["callsAfterAction"] = len(logged) - log_before
        entry["callKinds"] = [c.get("kind") for c in logged]
        entry.update(run_state(workspace, run_id))
        entry.update(budget_events(workspace.database, run_id))
        entry.update(budget_show(workspace, run_id))
        entry["findings"] = sorted(
            {
                f"{f['severity']}:{f['rule']}"
                for f in finding_items(workspace.h, workspace.ws, run_id)
                if str(f["rule"]).startswith("budget.")
            }
        )
        runs.append(entry)
    record = judge(case, condition, runs, raise_codes)
    record.update(
        {
            "description": case["description"],
            "intended": case["intended"],
            "runs": runs,
            "raiseExitCodes": raise_codes,
            "deviations": workspace.deviations,
            "budgetConfig": workspace.config.get("budget"),
            "steps": list(workspace.h.steps),
        }
    )
    cleanup(run_dir, args.keep_work)
    return record


def judge(
    case: dict[str, Any], condition: str, runs: list[dict[str, Any]], raise_codes: dict[str, int]
) -> dict[str, Any]:
    """Compare what happened with the oracle (``PREREGISTRO.md``, H20, «Conformidad»)."""
    ceilings = CEILINGS[condition]
    # The oracle sees the calls made before the person acted; after an accepted raise the run
    # limit is the raised one.
    before = [r["costs"][: r["callsBeforeAction"]] for r in runs]
    expected = oracle(before, ceilings)
    checks: dict[str, bool] = {}
    problems: list[str] = []
    for index, (run, want) in enumerate(zip(runs, expected, strict=True)):
        tag = f"run{index + 1}"
        checks[f"{tag}.warnings"] = run["warnings"] == want["warnings"] or (
            # A scope exceeded later in the run may also have been warned earlier.
            set(run["warnings"]) == set(want["warnings"])
        )
        checks[f"{tag}.exceeded"] = run["exceeded"] == want["exceeded"]
        if want["firstExceededAtCall"] is not None:
            checks[f"{tag}.noCallAfterExceeded"] = (
                run["callsBeforeAction"] == want["firstExceededAtCall"]
            )
            checks[f"{tag}.blocked"] = run.get("runStatus") == "BLOCKED" and any(
                "budget.exceeded" in item for item in run.get("blockedPhases") or []
            )
            checks[f"{tag}.exitCode6"] = run["runExitCode"] == 6
        checks[f"{tag}.findingsMatchEvents"] = (
            ("LOW:budget.warning" in run["findings"]) == bool(run["warnings"])
        ) and (("HIGH:budget.exceeded" in run["findings"]) == bool(run["exceeded"]))
        shown = run.get("budgetShowRunCost")
        checks[f"{tag}.budgetShowCost"] = (
            shown is not None and abs(float(shown) - round(sum(run["costs"]), 6)) < 1e-6
        )
    if case["raise"] == "accepted":
        run = runs[0]
        checks["raise.exit0"] = raise_codes.get("accepted") == 0
        checks["raise.recorded"] = any(
            r.get("limit") == RAISED_RUN_LIMIT and r.get("rationale") for r in run["raised"]
        )
        checks["raise.newCallAfterContinue"] = run["callsAfterAction"] >= 1
    if case["raise"] == "refused":
        run = runs[0]
        for name in ("emptyRationale", "notHigher", "agentActor"):
            checks[f"raise.{name}.exit5"] = raise_codes.get(name) == 5
        checks["raise.nothingRecorded"] = not run["raised"]
        checks["raise.noCallAfterContinue"] = run["callsAfterAction"] == 0
    intended = case["intended"]
    reached = True
    if intended is not None:
        kind, scope = intended
        reached = any(scope in want[kind + ("s" if kind == "warning" else "")] for want in expected)
    if not reached:
        problems.append(
            f"the intended crossing ({intended[0]} of {intended[1]}) did not happen: the run "
            f"made {[r['callsBeforeAction'] for r in runs]} calls"
        )
    failed = sorted(name for name, ok in checks.items() if not ok)
    conforming = None if not reached else not failed
    return {
        "oracle": expected,
        "checks": checks,
        "failedChecks": failed,
        "intendedCrossingReached": reached,
        "conforming": conforming,
        "notEvaluatedBecause": problems or None,
        "runStatus": runs[-1].get("runStatus"),
        "phase": runs[-1].get("phase"),
        "brief": "not evaluated" if conforming is None else f"conforming={conforming}",
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    evaluated = [r for r in ok if r.get("conforming") is not None]
    conforming = sum(1 for r in evaluated if r["conforming"])
    summary = {
        "suite": "H20",
        "title": "cumulative USD cost ceilings",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION and "
        "raises the limit in C11 (a declared simulation)",
        "scenarios": {
            r["case"]: {
                "condition": r["condition"],
                "description": r.get("description"),
                "conforming": r.get("conforming"),
                "failedChecks": r.get("failedChecks"),
                "notEvaluatedBecause": r.get("notEvaluatedBecause"),
                "oracle": r.get("oracle"),
                "observed": [
                    {
                        k: run.get(k)
                        for k in (
                            "calls",
                            "callsBeforeAction",
                            "callsAfterAction",
                            "costs",
                            "warnings",
                            "exceeded",
                            "findings",
                            "runStatus",
                            "phase",
                            "runExitCode",
                            "continueExitCode",
                            "budgetShowRunCost",
                        )
                    }
                    for run in r.get("runs") or []
                ],
                "raiseExitCodes": r.get("raiseExitCodes"),
            }
            for r in ok
        },
        "conforming": proportion(conforming, len(evaluated)),
        "notEvaluated": sorted(r["case"] for r in ok if r.get("conforming") is None),
        "nonIdenticalRepetitions": sorted(
            {
                r["case"]
                for r in records
                if "apparatusError" not in r
                and any(
                    o["case"] == r["case"]
                    and "apparatusError" not in o
                    and o.get("conforming") != r.get("conforming")
                    for o in records
                )
            }
        ),
    }
    summary["criteria"] = {
        "conforming12of12": {
            "value": f"{conforming}/{len(CASES)} conforming, {len(evaluated)} evaluated",
            "met": conforming == len(CASES) and len(ok) == len(CASES),
        },
        "note": "Pre-registered in PREREGISTRO.md (H20); evaluated as is. A scenario not evaluated "
        "counts against the criterion.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H20",
            title="cumulative USD cost ceilings",
            stem="h20-cost-ceilings",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["H-default", "H-reduced", "H-reduced-raise"],
            runner=runner,
            summarize=summarize,
        )
    )
