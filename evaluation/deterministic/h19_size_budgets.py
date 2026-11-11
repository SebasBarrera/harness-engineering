#!/usr/bin/env python3
"""H19: token and time budgets of one agent call by task size S/M/L (``CATALOGO.md`` §2.3,
``PREREGISTRO.md``). No model is called.

Nine synthetic tasks on the sample project, three per size, each crossing one routing threshold
(``agents/routing.py:classify_size``: requirements 5/15, owned files 5/20, their lines
2000/10000). The fixture provider ``budget_agent.py`` reports, on every call of the run, a usage
fixed relative to the budget the call received in its ``agentContract`` (profiles
``tokens-0.5``, ``tokens-1.0``, ``tokens-2.0``), or sleeps relative to its time limit
(``time-0.5``, ``time-2.0``), or reports cached input (``cache-probe``).

The per-call token accounting is **read from the installed harness**, not fixed here: when the
response schema (``harness agent-contract schema response``) declares ``cacheReadTokens`` in its
``Usage``, prompt-cache reads do not count (the rule of PR #145) and the probe reports them;
otherwise every reported input token counts and the probe reports only ``cacheTokens``. The rule
the run used is recorded as ``tokenAccounting``.

Conditions: ``D*`` (one call with the direct prompt, no budget: a model of the direct agent, not
an agent), ``H`` (``harness init``), ``H-reduced`` (``agentContract.budgets`` S 2000/6 s,
M 8000/8 s, L 32000/10 s).

Usage:
  python evaluation/deterministic/h19_size_budgets.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only S1-tokens-0.5,...]
      [--conditions D*,H,H-reduced] [--reps 1] [--summarize-only]

Writes ``DIR/h19-size-budgets.jsonl``, ``DIR/h19-size-budgets-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from resource_support import (  # noqa: E402
    AGENT,
    OWNED,
    baseline,
    cleanup,
    contract_calls,
    correct_spec,
    discount_task,
    environment_python,
    generated_module,
    governed,
    invocation_seconds,
    main,
    proportion,
    read_log,
    run_state,
    run_task,
    saved_config,
    stamp_dir,
)
from suite_support import bin_dir, direct_prompt, portable_env  # noqa: E402

DEFAULT_BUDGETS = {"S": (50_000, 600), "M": (200_000, 1_800), "L": (800_000, 5_400)}
REDUCED_BUDGETS = {"S": (2_000, 6), "M": (8_000, 8), "L": (32_000, 10)}
CACHE_READS_EXCLUDED = "fresh input + cache creation + output + reasoning (cache reads excluded)"
ALL_INPUT = "input + output + reasoning (every reported input token, cache included)"


def small_modules(prefix: str, count: int) -> dict[str, str]:
    return {f"src/sample/{prefix}_{i:02d}.py": f"VALUE = {i}\n" for i in range(1, count + 1)}


def _task(size: str, requirements: int, extra: dict[str, str], rule: str) -> dict[str, Any]:
    return {
        "size": size,
        "requirements": requirements,
        "files": extra,
        "owned": OWNED + sorted(extra),
        "threshold": rule,
    }


TASKS: dict[str, dict[str, Any]] = {
    "S1": _task("S", 1, {}, "all signals within S"),
    "S2": _task("S", 5, {}, "requirements=5 (upper bound of S)"),
    "S3": _task(
        "S",
        1,
        {f"src/sample/s3_{n}.py": generated_module(630) for n in ("a", "b", "c")},
        "files=5 (upper bound of S), about 1 900 lines",
    ),
    "M1": _task("M", 6, {}, "requirements=6>5"),
    "M2": _task("M", 1, small_modules("m2", 4), "files=6>5"),
    "M3": _task("M", 1, {"src/sample/m3_table.py": generated_module(2_100)}, "loc>2000"),
    "L1": _task("L", 16, {}, "requirements=16>15"),
    "L2": _task("L", 1, small_modules("l2", 19), "files=21>20"),
    "L3": _task("L", 1, {"src/sample/l3_table.py": generated_module(10_100)}, "loc>10000"),
}
TOKEN_PROFILES = ("tokens-0.5", "tokens-1.0", "tokens-2.0")
TIME_PROFILES = ("time-0.5", "time-2.0")
PROBE_TASKS = ("S1", "M1", "L1")


def cases() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task_id in TASKS:
        items.append({"id": f"{task_id}-direct", "task": task_id, "profile": "direct"})
        for profile in TOKEN_PROFILES + TIME_PROFILES:
            items.append({"id": f"{task_id}-{profile}", "task": task_id, "profile": profile})
        if task_id in PROBE_TASKS:
            items.append(
                {"id": f"{task_id}-cache-probe", "task": task_id, "profile": "cache-probe"}
            )
    return items


CASES = cases()


def conditions_of(case: dict[str, Any]) -> list[str]:
    profile = case["profile"]
    if profile == "direct":
        return ["D*"]
    if profile in TIME_PROFILES:
        return ["H-reduced"]
    if profile == "cache-probe":
        return ["H"]
    return ["H", "H-reduced"]


# ----- the accounting rule of the installed harness --------------------------------------------
def token_accounting(args: argparse.Namespace) -> dict[str, Any]:
    """Which per-call token accounting the installed harness declares (its response schema)."""
    env = portable_env([bin_dir(args.project_venv)], args.work / "schema-state")
    proc = subprocess.run(
        [args.harness, "agent-contract", "schema", "response"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    try:
        schema = json.loads(proc.stdout)
        fields = sorted(((schema.get("$defs") or {}).get("Usage") or {}).get("properties") or {})
    except (json.JSONDecodeError, AttributeError):
        fields = []
    excluded = "cacheReadTokens" in fields
    return {
        "tokenAccounting": {
            "source": "harness agent-contract schema response ($defs.Usage)",
            "schemaExitCode": proc.returncode,
            "usageFields": fields,
            "cacheReadsExcluded": excluded,
            "rule": CACHE_READS_EXCLUDED if excluded else ALL_INPUT,
        }
    }


def counted(usage: dict[str, Any], excluded: bool) -> int:
    inputs = int(usage.get("inputTokens") or 0)
    reads = min(int(usage.get("cacheReadTokens") or 0), inputs) if excluded else 0
    return (
        inputs
        - reads
        + int(usage.get("outputTokens") or 0)
        + int(usage.get("reasoningTokens") or 0)
    )


# ----- one case --------------------------------------------------------------------------------
def spec_for(profile: str, excluded: bool) -> dict[str, Any]:
    spec = correct_spec()
    if profile.startswith("tokens-"):
        spec["tokens"] = {"factor": float(profile.removeprefix("tokens-"))}
    elif profile.startswith("time-"):
        spec["sleep"] = {"factor": float(profile.removeprefix("time-"))}
    elif profile == "cache-probe":
        spec["cache"] = {"inputFactor": 1.5, "cacheFactor": 1.0, "cacheRead": excluded}
    return spec


def reduced(config: dict[str, Any]) -> None:
    config.setdefault("agentContract", {})["budgets"] = {
        size: {"tokens": tokens, "wallSeconds": seconds}
        for size, (tokens, seconds) in REDUCED_BUDGETS.items()
    }


def direct(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """D*: one call with the direct prompt reporting 2x the default budget; no budget applies."""
    info = TASKS[case["task"]]
    run_dir = stamp_dir(args, "h19", case["id"], "direct")
    ws = run_dir / "ws"
    ws.mkdir()
    baseline(ws, info["files"])
    task = discount_task(f"task_h19_{case['task'].lower()}", info["requirements"], info["owned"])
    budget = DEFAULT_BUDGETS[info["size"]][0]
    log = run_dir / "calls.jsonl"
    spec = {**correct_spec(), "tokens": {"input": 2 * budget - 10}, "log": str(log)}
    spec_path = run_dir / "fixture-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    python = environment_python(args)
    request = {"kind": "implement", "prompt": direct_prompt(task), "task": task}
    answer = subprocess.run(
        [python, str(AGENT), "--spec", str(spec_path)],
        cwd=ws,
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
    )
    tests = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=ws,
        capture_output=True,
        text=True,
        check=False,
    )
    usage = (json.loads(answer.stdout or "{}").get("usage")) or {}
    cleanup(run_dir, args.keep_work)
    return {
        "task": case["task"],
        "profile": "direct",
        "expectedSize": info["size"],
        "reportedTokens": int(usage.get("inputTokens") or 0) + int(usage.get("outputTokens") or 0),
        "defaultBudgetOfSize": budget,
        "cut": False,
        "stoppedBy": "nothing (no budget exists in D*)",
        "delivered": tests.returncode == 0,
        "testsExitCode": tests.returncode,
        "brief": f"delivered={tests.returncode == 0}",
    }


def governed_case(case: dict[str, Any], condition: str, args: argparse.Namespace) -> dict[str, Any]:
    info = TASKS[case["task"]]
    accounting = args.context["tokenAccounting"]
    excluded = bool(accounting["cacheReadsExcluded"])
    run_dir = stamp_dir(args, "h19", case["id"], condition)
    workspace = governed(
        run_dir,
        args,
        spec_for(case["profile"], excluded),
        extra_files=info["files"],
        mutate=reduced if condition == "H-reduced" else None,
    )
    saved_config(args, condition, workspace)
    task = discount_task(f"task_h19_{case['task'].lower()}", info["requirements"], info["owned"])
    started = run_task(workspace, task)
    run_id = started["runId"]
    state = run_state(workspace, run_id)
    recorded = contract_calls(workspace.database, run_id)
    durations = invocation_seconds(workspace.database, run_id)
    logged = read_log(workspace.log)
    by_key: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for entry in logged:
        if entry.get("contract"):
            by_key.setdefault((entry.get("callKind"), entry.get("iteration")), []).append(entry)
    calls = []
    for event in recorded:
        key = (event.get("callKind"), event.get("iteration"))
        matches = by_key.get(key) or []
        entry = matches.pop(0) if matches else None
        usage = (entry or {}).get("usage") or {}
        budget = (entry or {}).get("tokenBudget")
        limit = (entry or {}).get("timeLimitSeconds")
        tokens = counted(usage, excluded)
        slept = float((entry or {}).get("sleepSeconds") or 0.0)
        exceeds_tokens = budget is not None and tokens > int(budget)
        exceeds_time = limit is not None and slept > float(limit)
        seconds = durations.get(str(event.get("invocationId")))
        calls.append(
            {
                "callKind": event.get("callKind"),
                "iteration": event.get("iteration"),
                "size": event.get("size"),
                "finalStatus": event.get("finalStatus"),
                "limitReached": event.get("limitReached"),
                "cut": event.get("finalStatus") == "LIMIT_REACHED",
                "matched": entry is not None,
                "tokenBudget": budget,
                "timeLimitSeconds": limit,
                "reportedUsage": usage,
                "countedTokens": tokens,
                "tokenRatio": round(tokens / int(budget), 4) if budget else None,
                "sleptSeconds": slept,
                "invocationSeconds": seconds,
                "timeRatio": round(seconds / float(limit), 4) if seconds and limit else None,
                "exceeds": exceeds_tokens or exceeds_time,
                "exceedsBy": "tokens" if exceeds_tokens else ("time" if exceeds_time else None),
                "summary": str(event.get("summary") or "")[:200],
            }
        )
    unmatched_log = [
        {k: e.get(k) for k in ("number", "kind", "callKind", "iteration", "contract")}
        for entries in by_key.values()
        for e in entries
    ] + [
        {k: e.get(k) for k in ("number", "kind", "panel", "contract")}
        for e in logged
        if not e.get("contract")
    ]
    sizes = [c["size"] for c in calls]
    record = {
        "task": case["task"],
        "profile": case["profile"],
        "expectedSize": info["size"],
        "threshold": info["threshold"],
        "tokenAccounting": accounting["rule"],
        "cacheReadsExcluded": excluded,
        "assignedSize": sizes[0] if sizes else None,
        "sizesByCall": sizes,
        "calls": calls,
        "fixtureCallsWithoutContractRecord": unmatched_log,
        "fixtureCalls": len(logged),
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        **state,
        "deviations": workspace.deviations,
        "agentContract": workspace.config.get("agentContract"),
        "steps": list(workspace.h.steps),
        "brief": f"size={sizes[0] if sizes else None} cuts={sum(c['cut'] for c in calls)}",
    }
    cleanup(run_dir, args.keep_work)
    return record


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    if condition == "D*":
        return direct(case, args)
    return governed_case(case, condition, args)


# ----- summary ---------------------------------------------------------------------------------
def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r]
    governed_rows = [r for r in ok if r["condition"] != "D*"]
    sizes: dict[str, Any] = {}
    for task_id, info in TASKS.items():
        rows = [r for r in governed_rows if r["task"] == task_id]
        assigned = sorted({str(r.get("assignedSize")) for r in rows})
        later = sorted({s for r in rows for s in r.get("sizesByCall") or []})
        sizes[task_id] = {
            "expected": info["size"],
            "threshold": info["threshold"],
            "assigned": assigned,
            "sizesSeenInLaterCalls": later,
            "correct": bool(rows) and assigned == [info["size"]],
            "runs": len(rows),
        }
    calls = [
        {**c, "condition": r["condition"], "profile": r["profile"], "task": r["task"]}
        for r in governed_rows
        for c in r.get("calls") or []
    ]
    exceeding = [c for c in calls if c["exceeds"]]
    below = [c for c in calls if c["profile"] in {"tokens-0.5", "time-0.5"}]
    at_limit = [c for c in calls if c["profile"] == "tokens-1.0"]
    probe = [c for c in calls if c["profile"] == "cache-probe"]
    excluded = {r.get("cacheReadsExcluded") for r in governed_rows}
    token_cuts = [c for c in calls if c["cut"] and c["limitReached"] == "tokens"]
    time_cuts = [c for c in calls if c["cut"] and c["limitReached"] == "time"]

    def by(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row[key]), []).append(row)
        return {
            name: proportion(sum(c["cut"] for c in items), len(items))
            for name, items in sorted(groups.items())
        }

    correct_sizes = sum(1 for item in sizes.values() if item["correct"])
    cut_exceeding = sum(1 for c in exceeding if c["cut"] and c["limitReached"] == c["exceedsBy"])
    cuts_below = sum(1 for c in below if c["cut"])
    large_legit = [c for c in below if TASKS[c["task"]]["size"] == "L"]
    summary = {
        "suite": "H19",
        "title": "token and time budgets per task size",
        "tokenAccounting": sorted(
            {str(r.get("tokenAccounting")) for r in governed_rows if r.get("tokenAccounting")}
        ),
        "cacheReadsExcluded": sorted(str(v) for v in excluded),
        "directEquivalent": "D* is one call with the direct prompt and no budget: a model of a "
        "direct agent, not an agent (CATALOGO.md §4.4)",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a declared "
        "simulation)",
        "sizes": sizes,
        "cutsOfExceedingCalls": proportion(cut_exceeding, len(exceeding)),
        "cutsBelowTheLimit": proportion(cuts_below, len(below)),
        "cutsAtExactlyTheBudget": proportion(sum(c["cut"] for c in at_limit), len(at_limit)),
        "cacheProbe": {
            "calls": len(probe),
            "cut": sum(c["cut"] for c in probe),
            "expectedCut": sum(c["exceeds"] for c in probe),
            "matchesDeclaredRule": all(c["cut"] == c["exceeds"] for c in probe) if probe else None,
        },
        "falseCutsInLargeTasks": proportion(sum(c["cut"] for c in large_legit), len(large_legit)),
        "maxOvershoot": {
            "tokens": max((c["tokenRatio"] or 0 for c in token_cuts), default=None),
            "time": max((c["timeRatio"] or 0 for c in time_cuts), default=None),
        },
        "cutsByProfile": by(calls, "profile"),
        "cutsByCallKind": by(calls, "callKind"),
        "unmatchedContractEvents": sum(1 for c in calls if not c["matched"]),
        "missedCuts": [
            f"{c['condition']}/{c['task']}/{c['profile']}/{c['callKind']}#{c['iteration']}"
            for c in exceeding
            if not (c["cut"] and c["limitReached"] == c["exceedsBy"])
        ],
        "falseCuts": [
            f"{c['condition']}/{c['task']}/{c['profile']}/{c['callKind']}#{c['iteration']}"
            for c in below
            if c["cut"]
        ],
        "finalStates": {
            f"{r['condition']}/{r['case']}": f"{r.get('runStatus')}:{r.get('phase')}"
            for r in governed_rows
        },
        "direct": {
            r["case"]: {
                "reportedTokens": r.get("reportedTokens"),
                "defaultBudgetOfSize": r.get("defaultBudgetOfSize"),
                "cut": r.get("cut"),
                "delivered": r.get("delivered"),
            }
            for r in ok
            if r["condition"] == "D*"
        },
    }
    summary["criteria"] = {
        "sizeCorrect9of9": {
            "value": f"{correct_sizes}/{len(TASKS)}",
            "met": correct_sizes == len(TASKS) and all(i["runs"] for i in sizes.values()),
        },
        "cutAllExceedingCalls": {
            "value": summary["cutsOfExceedingCalls"],
            "met": bool(exceeding) and cut_exceeding == len(exceeding),
        },
        "noCutBelowTheLimit": {
            "value": summary["cutsBelowTheLimit"],
            "met": bool(below) and cuts_below == 0,
        },
        "note": "Pre-registered in PREREGISTRO.md (H19); evaluated as is. A criterion without "
        "observations is not met (not evaluated).",
    }
    return summary


def extra_environment(args: argparse.Namespace) -> dict[str, Any]:
    return {
        **token_accounting(args),
        "budgets": {"H": DEFAULT_BUDGETS, "H-reduced": REDUCED_BUDGETS},
    }


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H19",
            title="token and time budgets per task size",
            stem="h19-size-budgets",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["D*", "H", "H-reduced"],
            runner=runner,
            summarize=summarize,
            extra_environment=extra_environment,
        )
    )
