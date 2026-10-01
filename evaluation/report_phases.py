#!/usr/bin/env python3
"""Extract phase durations and retrospective recommendations of the governed runs.

For every run of the harness condition, reads the state the harness left in the workspace
(`.harness/state.db`) and records the time spent in each phase, the human decisions, the
validations that did not pass and the recommendations of the retrospective. The summary covers the
runs approved without a correction cycle (phase durations) and every retrospective (recommendations
by category, with the non-passed validations of the run that produced them).

Descriptive values only: medians, ranges and counts.

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

NAME = re.compile(r"^(greenfield|brownfield|security)-harness-(.+)-r(\d+)-\d{8}T\d{6}Z$")
PHASES = ["INTENT", "DISCOVERY", "SPECIFICATION", "PLANNING", "IMPLEMENTATION", "VERIFICATION",
          "INDEPENDENT_REVIEW", "DECISION", "CLOSURE"]
OPTIONAL_VALIDATORS = {"python.ruff", "python.mypy"}


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def payloads(db: sqlite3.Connection, record_type: str) -> list[dict[str, Any]]:
    rows = db.execute("select payload_json from records where record_type=? order by rowid", (record_type,))
    return [json.loads(payload) for (payload,) in rows]


def extract(run_dir: Path) -> dict[str, Any] | None:
    match = NAME.match(run_dir.name)
    state = run_dir / "ws" / ".harness" / "state.db"
    if not match or not state.exists():
        return None
    scenario, model, rep = match.groups()
    db = sqlite3.connect(state)
    seconds: dict[str, float] = defaultdict(float)
    attempts: Counter[str] = Counter()
    for phase in payloads(db, "phase"):
        if phase.get("startedAt") and phase.get("finishedAt"):
            elapsed = instant(phase["finishedAt"]) - instant(phase["startedAt"])
            seconds[phase["phaseId"]] += elapsed.total_seconds()
            attempts[phase["phaseId"]] += 1
    non_passed = sorted({(v["validatorId"], v["status"]) for v in payloads(db, "validation") if v["status"] != "PASSED"})
    retrospectives = payloads(db, "retrospective")
    record = {
        "scenario": scenario,
        "model": model,
        "rep": int(rep),
        "decisions": [d["decision"] for d in payloads(db, "decision")],
        "phaseSeconds": {p: round(seconds[p], 3) for p in PHASES if p in seconds},
        "phaseAttempts": {p: attempts[p] for p in PHASES if p in attempts},
        "nonPassedValidations": [{"validator": v, "status": s} for v, s in non_passed],
        "retrospectives": len(retrospectives),
        "recommendations": [
            {"category": r["category"], "statement": r["statement"], "appliedAutomatically": x["appliedAutomatically"]}
            for x in retrospectives
            for r in x["recommendations"]
        ],
    }
    db.close()
    return record


def spread(values: list[float]) -> dict[str, float]:
    return {"median": round(statistics.median(values), 3), "min": round(min(values), 3), "max": round(max(values), 3)}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    straight = [r for r in records if r["decisions"] == ["APPROVE"]]
    totals = [sum(r["phaseSeconds"].values()) for r in straight]
    implementation = [r["phaseSeconds"]["IMPLEMENTATION"] for r in straight]
    control = [
        sum(s for p, s in r["phaseSeconds"].items() if p not in ("IMPLEMENTATION", "VERIFICATION")) for r in straight
    ]
    produced = [(r, rec) for r in records for rec in r["recommendations"]]
    by_category = Counter(rec["category"] for _, rec in produced)
    validation = [r for r, rec in produced if rec["category"] == "validation"]
    return {
        "runs": len(records),
        "approvedWithoutCorrection": {
            "runs": len(straight),
            "phaseSeconds": {p: spread([r["phaseSeconds"].get(p, 0.0) for r in straight]) for p in PHASES},
            "totalPhaseSeconds": spread(totals),
            "implementationShare": spread([i / t for i, t in zip(implementation, totals, strict=True)]),
            "phasesOtherThanImplementationAndVerificationSeconds": spread(control),
        },
        "retrospective": {
            "runsWithRetrospective": sum(1 for r in records if r["retrospectives"]),
            "runsWithoutRetrospective": [
                {"scenario": r["scenario"], "model": r["model"], "rep": r["rep"], "decisions": r["decisions"]}
                for r in records
                if not r["retrospectives"]
            ],
            "records": sum(r["retrospectives"] for r in records),
            "recommendations": sum(by_category.values()),
            "appliedAutomatically": sum(1 for _, rec in produced if rec["appliedAutomatically"]),
            "byCategory": dict(sorted(by_category.items())),
            "byCategoryAndScenario": {
                category: dict(sorted(Counter(r["scenario"] for r, rec in produced if rec["category"] == category).items()))
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


def main() -> int:
    runs, out, summary = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    records = [r for r in (extract(p) for p in sorted(runs.glob("*/*")) if p.is_dir()) if r]
    records.sort(key=lambda r: (r["scenario"], r["model"], r["rep"]))
    out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    data = summarize(records)
    summary.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
