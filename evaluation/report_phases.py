#!/usr/bin/env python3
"""Extract phase durations and retrospective recommendations of the governed runs.

For every run of the harness condition, reads the state the harness left in the workspace
(`.harness/state.db`) and records the time spent in each phase, the human decisions, the
validations that did not pass and the recommendations of the retrospective. The summary covers the
runs approved without a correction cycle (phase durations) and every retrospective (recommendations
by category, with the non-passed validations of the run that produced them).

Descriptive values only: medians, ranges and counts.

Since the 2.0.0 evaluation the runs of every governed condition are read (harness-core, harness,
harness-tiered and the 0.9.0 harness runs), from the workspace's state database or from the run
registry kept in the run directory, and each phase's seconds are split into the time of the agent
calls made in it (``modelSecondsByPhase``, from the agent invocation records) and the harness's own
process time (``processSecondsByPhase``), P15. Summaries are given per condition.

Usage: python report_phases.py <runs-dir> <out.jsonl> <summary.json>
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_state import state_db  # noqa: E402

NAME = re.compile(
    r"^(greenfield|brownfield|security)(?:-(poor|casual))?-(harness-core|harness-tiered|harness|clarify)-(.+)"
    r"-r(\d+)-\d{8}T\d{6}Z$"
)
PHASES = [
    "INTENT",
    "DISCOVERY",
    "SPECIFICATION",
    "PLANNING",
    "IMPLEMENTATION",
    "VERIFICATION",
    "INDEPENDENT_REVIEW",
    "DECISION",
    "CLOSURE",
]
OPTIONAL_VALIDATORS = {"python.ruff", "python.mypy"}


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def payloads(db: sqlite3.Connection, record_type: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "select payload_json from records where record_type=? order by rowid", (record_type,)
    )
    return [json.loads(payload) for (payload,) in rows]


def extract(run_dir: Path) -> dict[str, Any] | None:
    match = NAME.match(run_dir.name)
    state = state_db(run_dir / "ws", run_dir) if match else None
    if not match or state is None:
        return None
    scenario, prompt, condition, model, rep = match.groups()
    db = sqlite3.connect(state)
    seconds: dict[str, float] = defaultdict(float)
    attempts: Counter[str] = Counter()
    for phase in payloads(db, "phase"):
        if phase.get("startedAt") and phase.get("finishedAt"):
            elapsed = instant(phase["finishedAt"]) - instant(phase["startedAt"])
            seconds[phase["phaseId"]] += elapsed.total_seconds()
            attempts[phase["phaseId"]] += 1
    model_seconds: dict[str, float] = defaultdict(float)
    for invocation in payloads(db, "agent_invocation"):
        if invocation.get("startedAt") and invocation.get("finishedAt"):
            elapsed = instant(invocation["finishedAt"]) - instant(invocation["startedAt"])
            model_seconds[str(invocation.get("phaseId"))] += elapsed.total_seconds()
    non_passed = sorted(
        {
            (v["validatorId"], v["status"])
            for v in payloads(db, "validation")
            if v["status"] != "PASSED"
        }
    )
    retrospectives = payloads(db, "retrospective")
    record = {
        "scenario": scenario,
        "prompt": prompt or "full",
        "condition": condition,
        "model": model,
        "rep": int(rep),
        "decisions": [d["decision"] for d in payloads(db, "decision")],
        "phaseSeconds": {p: round(seconds[p], 3) for p in PHASES if p in seconds},
        "phaseAttempts": {p: attempts[p] for p in PHASES if p in attempts},
        "modelSecondsByPhase": {
            p: round(model_seconds[p], 3) for p in PHASES if p in model_seconds
        },
        "processSecondsByPhase": {
            p: round(max(0.0, seconds[p] - model_seconds.get(p, 0.0)), 3)
            for p in PHASES
            if p in seconds
        },
        "nonPassedValidations": [{"validator": v, "status": s} for v, s in non_passed],
        "retrospectives": len(retrospectives),
        "recommendations": [
            {
                "category": r.get("category"),
                "statement": r.get("statement"),
                "appliedAutomatically": x.get("appliedAutomatically"),
            }
            for x in retrospectives
            for r in x.get("recommendations") or []
        ],
    }
    db.close()
    return record


def spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "min": None, "max": None}
    return {
        "median": round(statistics.median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    straight = [r for r in records if r["decisions"] == ["APPROVE"]]
    totals = [sum(r["phaseSeconds"].values()) for r in straight]
    implementation = [r["phaseSeconds"]["IMPLEMENTATION"] for r in straight]
    control = [
        sum(s for p, s in r["phaseSeconds"].items() if p not in ("IMPLEMENTATION", "VERIFICATION"))
        for r in straight
    ]
    produced = [(r, rec) for r in records for rec in r["recommendations"]]
    by_category = Counter(rec["category"] for _, rec in produced)
    validation = [r for r, rec in produced if rec["category"] == "validation"]
    return {
        "runs": len(records),
        "approvedWithoutCorrection": {
            "runs": len(straight),
            "phaseSeconds": {
                p: spread([r["phaseSeconds"].get(p, 0.0) for r in straight]) for p in PHASES
            },
            "totalPhaseSeconds": spread(totals),
            "implementationShare": spread(
                [i / t for i, t in zip(implementation, totals, strict=True)]
            ),
            "phasesOtherThanImplementationAndVerificationSeconds": spread(control),
        },
        "retrospective": {
            "runsWithRetrospective": sum(1 for r in records if r["retrospectives"]),
            "runsWithoutRetrospective": [
                {
                    "scenario": r["scenario"],
                    "model": r["model"],
                    "rep": r["rep"],
                    "decisions": r["decisions"],
                }
                for r in records
                if not r["retrospectives"]
            ],
            "records": sum(r["retrospectives"] for r in records),
            "recommendations": sum(by_category.values()),
            "appliedAutomatically": sum(1 for _, rec in produced if rec["appliedAutomatically"]),
            "byCategory": dict(sorted(by_category.items())),
            "byCategoryAndScenario": {
                category: dict(
                    sorted(
                        Counter(
                            r["scenario"] for r, rec in produced if rec["category"] == category
                        ).items()
                    )
                )
                for category in sorted(by_category)
            },
            "validationRecommendations": {
                "total": len(validation),
                "onlyOptionalValidatorsFailed": sum(
                    1
                    for r in validation
                    if all(v["validator"] in OPTIONAL_VALIDATORS for v in r["nonPassedValidations"])
                ),
            },
        },
    }


def summarize_v2(records: list[dict[str, Any]]) -> dict[str, Any]:
    """P15 for one condition: phase seconds split into agent calls and the harness's own work, over
    the runs approved without a correction; every run counted, none dropped."""
    straight = [r for r in records if r["decisions"] == ["APPROVE"]]
    other = [r for r in records if r["decisions"] != ["APPROVE"]]
    total = [sum(r["phaseSeconds"].values()) for r in straight]
    model = [sum(r["modelSecondsByPhase"].values()) for r in straight]
    return {
        "runs": len(records),
        "approvedWithoutCorrection": len(straight),
        "otherRuns": [
            {k: r[k] for k in ("prompt", "scenario", "model", "rep", "decisions")} for r in other
        ],
        "phaseSeconds": {
            p: spread([r["phaseSeconds"].get(p, 0.0) for r in straight]) for p in PHASES
        },
        "modelSecondsByPhase": {
            p: spread([r["modelSecondsByPhase"].get(p, 0.0) for r in straight]) for p in PHASES
        },
        "processSecondsByPhase": {
            p: spread([r["processSecondsByPhase"].get(p, 0.0) for r in straight]) for p in PHASES
        },
        "totalPhaseSeconds": spread(total),
        "modelSeconds": spread(model),
        "processSeconds": spread([t - m for t, m in zip(total, model, strict=True)]),
        "recommendations": dict(
            sorted(
                Counter(rec["category"] for r in records for rec in r["recommendations"]).items()
            )
        ),
        "runsWithRetrospective": sum(1 for r in records if r["retrospectives"]),
    }


def main() -> int:
    runs, out, summary = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    candidates = sorted({*runs.glob("*"), *runs.glob("*/*")})  # <runs>/<run> or <runs>/<model>/<run>
    records = [r for r in (extract(p) for p in candidates if p.is_dir()) if r]
    records.sort(key=lambda r: (r["condition"], r["prompt"], r["scenario"], r["model"], r["rep"]))
    out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_condition[record["condition"]].append(record)
    data = summarize(records) if len(by_condition) == 1 and "harness" in by_condition else {}
    data["byCondition"] = {
        condition: summarize_v2(rows) for condition, rows in sorted(by_condition.items())
    }
    summary.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
