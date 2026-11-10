#!/usr/bin/env python3
"""N04: validity of the harness's own metrics against an external measurement. No model.

For each run directory, ``harness metrics --format json`` (computed by the harness from its run
records) is compared with what was measured outside the harness:

* the agent calls as the agent side recorded them: ``agent-calls/call-*.json`` (the normalized
  records of ``evaluation/agentlib.py``: ``inputTokens``, ``outputTokens``, ``cacheReadTokens``,
  ``cacheCreationTokens``, ``costUsd``, ``wallSeconds``) or ``agent-calls.jsonl`` (one line per
  call of a fixture provider: ``kind``, ``inputTokens``, ``outputTokens``, ``wallSeconds``);
* the runner's clock: ``runner-clock.json`` (``totalSeconds`` of the commands the runner ran).

Per metric the record holds the harness value, the external value, the absolute and relative
difference and whether they are equal. Input tokens are compared as the harness defines them
(input plus cache tokens when the provider reports both; ``agentlib`` keeps them apart). A
metric without an external value is counted as not comparable (coverage). Two properties are
checked on every metrics document: costs from a price table are labelled estimated
(``estimatedUsd`` apart from ``reportedUsd``), and there is no per-person indicator (no key
grouping by actor and no human actor id of the run in the document, RD-16).

Layout: ``RUN_DIR/ws`` (else ``RUN_DIR``) is the project; the run registry is
``RUN_DIR/harness-state/state`` when present (the anchors next to it), else ``--state-dir``.
``--demo-workdir`` adds every project of a ``scripts/demo_flows.py all --workdir`` directory
(external side: none; their metrics are checked for internal consistency and RD-16 only).

Usage:
  python evaluation/deterministic/metrics_validity.py --out DIR [--harness PATH]
      [RUN_DIR ...] [--runs-glob GLOB] [--demo-workdir DIR --demo-state DIR]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import GIT_ENV, add_common_arguments, environment, write_json, write_jsonl  # noqa: E402

PERSON_KEYS = re.compile(r"by(actor|person|user|reviewer|decider)s?$", re.IGNORECASE)


def harness_metrics(harness: str, workspace: Path, state: Path | None) -> dict[str, Any]:
    env = {**os.environ, **GIT_ENV}
    if state is not None:
        env["HARNESS_STATE_DIR"] = str(state)
        env["HARNESS_ANCHOR_DIR"] = str(state.parent / "anchors")
    proc = subprocess.run(
        [harness, "metrics", "--path", str(workspace), "--format", "json"],
        capture_output=True,
        text=True,
        env=env,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stderr or proc.stdout)[-500:], "exitCode": proc.returncode}


def external(run_dir: Path) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    source = None
    folder = run_dir / "agent-calls"
    if folder.is_dir():
        for path in sorted(folder.glob("call-*.json")):
            record = json.loads(path.read_text())
            calls.append(
                {
                    "input": int(record.get("inputTokens") or 0)
                    + int(record.get("cacheReadTokens") or 0)
                    + int(record.get("cacheCreationTokens") or 0),
                    "output": int(record.get("outputTokens") or 0),
                    "cache": int(record.get("cacheReadTokens") or 0)
                    + int(record.get("cacheCreationTokens") or 0),
                    "costUsd": record.get("costUsd"),
                    "wallSeconds": record.get("wallSeconds"),
                }
            )
        source = "agentlib agent-calls/call-*.json"
    elif (run_dir / "agent-calls.jsonl").exists():
        for line in (run_dir / "agent-calls.jsonl").read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                calls.append(
                    {
                        "input": int(record.get("inputTokens") or 0),
                        "output": int(record.get("outputTokens") or 0),
                        "cache": int(record.get("cacheTokens") or 0),
                        "costUsd": record.get("costUsd"),
                        "wallSeconds": record.get("wallSeconds"),
                        "kind": record.get("kind"),
                    }
                )
        source = "fixture provider agent-calls.jsonl"
    clock = (
        json.loads((run_dir / "runner-clock.json").read_text())
        if (run_dir / "runner-clock.json").exists()
        else None
    )
    costs = [c["costUsd"] for c in calls if c.get("costUsd") is not None]
    walls = [c["wallSeconds"] for c in calls if c.get("wallSeconds") is not None]
    return {
        "source": source,
        "agentCalls": len(calls) if source else None,
        "inputTokens": sum(c["input"] for c in calls) if source else None,
        "outputTokens": sum(c["output"] for c in calls) if source else None,
        "cacheTokens": sum(c["cache"] for c in calls) if source else None,
        "costUsd": round(sum(costs), 6) if costs else None,
        "agentCallSeconds": round(sum(walls), 3) if walls else None,
        "runnerSeconds": clock.get("totalSeconds") if isinstance(clock, dict) else None,
        "callsByKind": _by_kind(calls),
    }


def _by_kind(calls: list[dict[str, Any]]) -> dict[str, int] | None:
    kinds = [c.get("kind") for c in calls if c.get("kind")]
    if not kinds:
        return None
    out: dict[str, int] = {}
    for kind in kinds:
        out[kind] = out.get(kind, 0) + 1
    return dict(sorted(out.items()))


def person_check(metrics: dict[str, Any], actors: set[str]) -> dict[str, Any]:
    keys: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if PERSON_KEYS.search(str(key)):
                    keys.append(f"{path}.{key}")
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for item in value:
                walk(item, path + "[]")

    walk(metrics, "$")
    text = json.dumps(metrics)
    found = sorted(actor for actor in actors if actor and actor in text)
    return {"perPersonKeys": keys, "humanActorIdsInDocument": found, "ok": not keys and not found}


def human_actors(workspace: Path, state: Path | None) -> set[str]:
    """The human actor ids recorded for the project's runs (to look for them in the metrics)."""
    import sqlite3

    databases = list(state.rglob("state.db")) if state is not None and state.exists() else []
    databases += list((workspace / ".harness").glob("state.db"))
    actors: set[str] = set()
    for database in databases:
        connection = sqlite3.connect(database)
        try:
            for (actor,) in connection.execute("select actor_json from events"):
                value = json.loads(actor)
                if str(value.get("actor_type", value.get("actorType", ""))).upper() == "HUMAN":
                    actors.add(str(value.get("actor_id") or value.get("actorId")))
        finally:
            connection.close()
    return actors


def compare(name: str, harness_value: Any, external_value: Any) -> dict[str, Any]:
    if harness_value is None or external_value is None:
        return {
            "metric": name,
            "harness": harness_value,
            "external": external_value,
            "comparable": False,
        }
    difference = float(harness_value) - float(external_value)
    relative = (
        abs(difference) / abs(float(external_value))
        if float(external_value)
        else (0.0 if difference == 0 else None)
    )
    return {
        "metric": name,
        "harness": harness_value,
        "external": external_value,
        "comparable": True,
        "absDiff": round(difference, 6),
        "relDiff": round(relative, 6) if relative is not None else None,
        "equal": difference == 0,
    }


def assess_run(
    run_dir: Path, args: argparse.Namespace, state: Path | None, kind: str
) -> dict[str, Any]:
    workspace = run_dir / "ws" if (run_dir / "ws").is_dir() else run_dir
    metrics = harness_metrics(args.harness, workspace, state)
    if "error" in metrics:
        return {"runDir": str(run_dir), "kind": kind, "error": metrics["error"]}
    totals = metrics.get("totals") or {}
    tokens = totals.get("tokens") or {}
    runs = metrics.get("runs") or []
    ext = external(run_dir) if kind == "measured" else {"source": None}
    record: dict[str, Any] = {
        "runDir": run_dir.name,
        "kind": kind,
        "harnessRuns": len(runs),
        "externalSource": ext.get("source"),
        "comparisons": [
            compare("agentCalls", totals.get("agentCalls"), ext.get("agentCalls")),
            compare("inputTokens", tokens.get("input"), ext.get("inputTokens")),
            compare("outputTokens", tokens.get("output"), ext.get("outputTokens")),
            compare("cacheTokens", tokens.get("cache"), ext.get("cacheTokens")),
            compare(
                "costUsd",
                (totals.get("cost") or {}).get("reportedUsd")
                if ext.get("costUsd") is not None
                else None,
                ext.get("costUsd"),
            ),
            compare(
                "agentCallSeconds",
                ((metrics.get("time") or {}).get("agentCalls") or {}).get("totalMs", 0) / 1000
                if metrics.get("time")
                else None,
                ext.get("agentCallSeconds"),
            ),
            compare(
                "runWallSeconds",
                sum(int(r.get("wallMs") or 0) for r in runs) / 1000 if runs else None,
                ext.get("runnerSeconds"),
            ),
        ],
        "callsByKindHarness": {
            item["key"]: item["calls"]
            for item in (metrics.get("tokens") or {}).get("byCallKind", [])
        },
        "callsByKindExternal": ext.get("callsByKind"),
    }
    by_kind = record["callsByKindHarness"]
    record["internal"] = {
        "callsByKindSumEqualsTotal": sum(by_kind.values()) == totals.get("agentCalls"),
        "perRunCallsSumEqualsTotal": sum(int(r.get("agentCalls") or 0) for r in runs)
        == totals.get("agentCalls"),
        "perRunTokensSumEqualsTotal": sum(
            int(r.get("inputTokens") or 0) + int(r.get("outputTokens") or 0) for r in runs
        )
        == int(tokens.get("input") or 0) + int(tokens.get("output") or 0),
    }
    cost = totals.get("cost") or {}
    record["costLabels"] = {
        "reportedUsd": cost.get("reportedUsd"),
        "estimatedUsd": cost.get("estimatedUsd"),
        "unpricedCalls": cost.get("unpricedCalls"),
        "estimatedKeptApart": "estimatedUsd" in cost and "reportedUsd" in cost,
    }
    record["perPerson"] = person_check(metrics, human_actors(workspace, state))
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, dict[str, Any]] = {}
    for record in records:
        for item in record.get("comparisons", []):
            bucket = metrics.setdefault(
                item["metric"],
                {"runs": 0, "comparable": 0, "equal": 0, "maxAbsDiff": 0.0, "relDiffs": []},
            )
            bucket["runs"] += 1
            if item["comparable"]:
                bucket["comparable"] += 1
                bucket["equal"] += int(item["equal"])
                bucket["maxAbsDiff"] = max(bucket["maxAbsDiff"], abs(item["absDiff"]))
                if item["relDiff"] is not None:
                    bucket["relDiffs"].append(item["relDiff"])
    for bucket in metrics.values():
        values = sorted(bucket.pop("relDiffs"))
        bucket["medianRelDiff"] = values[len(values) // 2] if values else None
        bucket["maxRelDiff"] = values[-1] if values else None
    return {
        "runDirs": len(records),
        "measured": sum(1 for r in records if r.get("kind") == "measured"),
        "demo": sum(1 for r in records if r.get("kind") == "demo"),
        "errors": [r for r in records if "error" in r],
        "byMetric": metrics,
        "internalConsistency": {
            key: sum(1 for r in records if (r.get("internal") or {}).get(key))
            for key in (
                "callsByKindSumEqualsTotal",
                "perRunCallsSumEqualsTotal",
                "perRunTokensSumEqualsTotal",
            )
        },
        "callsByKindMatches": sum(
            1
            for r in records
            if r.get("callsByKindExternal") and r["callsByKindExternal"] == r["callsByKindHarness"]
        ),
        "costEstimatedKeptApart": sum(
            1 for r in records if (r.get("costLabels") or {}).get("estimatedKeptApart")
        ),
        "noPerPersonIndicator": sum(1 for r in records if (r.get("perPerson") or {}).get("ok")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("run_dirs", nargs="*", type=Path)
    parser.add_argument("--runs-glob", default="")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--demo-workdir", type=Path, default=None)
    parser.add_argument("--demo-state", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run_dirs = (
        list(args.run_dirs) + [Path(p) for p in sorted(glob.glob(args.runs_glob))]
        if args.runs_glob
        else list(args.run_dirs)
    )
    records = []
    for run_dir in run_dirs:
        state = (
            run_dir / "harness-state" / "state"
            if (run_dir / "harness-state" / "state").is_dir()
            else args.state_dir
        )
        records.append(assess_run(run_dir, args, state, "measured"))
    if args.demo_workdir:
        for project in sorted(p for p in args.demo_workdir.iterdir() if (p / ".harness").is_dir()):
            records.append(assess_run(project, args, args.demo_state, "demo"))
    write_json(
        args.out / "environment.json",
        environment(
            args.harness,
            args.wheel,
            {
                "suite": "N04 metrics validity",
                "runDirs": len(run_dirs),
                "demoWorkdir": str(args.demo_workdir) if args.demo_workdir else None,
            },
        ),
    )
    write_jsonl(args.out / "metrics-validity.jsonl", records)
    write_json(args.out / "metrics-validity-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
