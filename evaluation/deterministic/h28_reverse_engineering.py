#!/usr/bin/env python3
"""H28: reuse of the reverse engineering with 0 calls (``CATALOGO.md`` §2.5,
``PREREGISTRO.md``). No model is called.

One governed workspace per condition (and repetition) on the billing project of the review
corpus (``evaluation/corpus/review/baseline``), and sixteen runs in a row, each a new task with
the same small change, cancelled at its stop and followed by ``git reset --hard`` and ``git clean
-fd`` (``.harness/`` is ignored and stays):

* s01-s10: ten runs, the layout unchanged;
* s11: after committing three new source directories (a material change of the layout);
* s12: unchanged; s13: after deleting ``.harness/reverse-engineering/``; s14: unchanged;
* s15: after ``harness architecture refresh``; s16: unchanged.

Conditions: ``harness`` (``harness init``: ``architecture.documentation: project``,
``refresh: manual``), ``harness-auto`` (``refresh: auto``) and ``harness-nodoc`` (without
``architecture.documentation``). Per run: the ``architecture`` calls (the fixture's log and the
``agent.call.recorded`` events), the ``architecture.survey.completed`` and
``architecture.survey.reused`` events with their payload, whether the implement request carries
the ``reverseEngineering`` pointer, and the documents present.

The steps are the cases: ``--only`` runs a subset, but the sequence is only meaningful whole.

Usage:
  python evaluation/deterministic/h28_reverse_engineering.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only s01,s02]
      [--conditions harness,harness-auto,harness-nodoc] [--reps 1] [--summarize-only]
      [--keep-work]

Writes ``DIR/h28-reverse-engineering.jsonl``, ``DIR/h28-reverse-engineering-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import EVALUATION, git  # noqa: E402
from practice_support import cancel, commit, workspace, write_files  # noqa: E402
from resource_support import (  # noqa: E402
    cleanup,
    contract_calls,
    main,
    proportion,
    read_log,
    run_state,
    saved_config,
    stamp_dir,
)
from suite_support import events, start_run  # noqa: E402

REVIEW = EVALUATION / "corpus" / "review"
CONDITIONS = ("harness", "harness-auto", "harness-nodoc")
DOCUMENTS = ".harness/reverse-engineering"
SERVICE = "src/billing/service.py"
NEW_DIRECTORIES = {
    "src/billing/reports/__init__.py": "def report_name() -> str:\n    return 'reports'\n",
    "src/billing/exports/csv_export.py": "def export_name() -> str:\n    return 'exports'\n",
    "src/billing/notify/mailer.py": "def notify_name() -> str:\n    return 'notify'\n",
}
ACTIONS = {"s11": "layout", "s13": "delete-documents", "s15": "refresh"}
CASES = [
    {"id": f"s{number:02d}", "action": ACTIONS.get(f"s{number:02d}")} for number in range(1, 17)
]
LAST = CASES[-1]["id"]


def baseline(root: Path) -> None:
    """The billing project of the review corpus (``h45_clean_changes.review_baseline``)."""
    shutil.copytree(REVIEW / "baseline", root, dirs_exist_ok=True)
    (root / "gitignore.template").rename(root / ".gitignore")
    (root / "dot-github").rename(root / ".github")


def mutate_for(condition: str) -> Any:
    def mutate(config: dict[str, Any]) -> None:
        architecture = config.setdefault("architecture", {})
        if condition == "harness-auto":
            architecture["refresh"] = "auto"
        elif condition == "harness-nodoc":
            architecture.pop("documentation", None)

    return mutate


def service_change() -> dict[str, str]:
    text = (REVIEW / "baseline" / SERVICE).read_text(encoding="utf-8")
    return {SERVICE: text + "\n\ndef invoice_currency() -> str:\n    return 'EUR'\n"}


def sequence(args: argparse.Namespace, condition: str, rep: int) -> Any:
    """The workspace of a (condition, repetition), created at its first step."""
    store: dict[tuple[str, int], Any] = args.__dict__.setdefault("h28", {})
    key = (condition, rep)
    if key not in store:
        run_dir = stamp_dir(args, "h28", condition, f"r{rep}")
        item = workspace(run_dir, args, baseline, {}, mutate=mutate_for(condition))
        saved_config(args, condition, item)
        store[key] = item
    return store[key]


def show(item: Any) -> dict[str, Any]:
    _, shown, _ = item.h.run(item.ws, "architecture", "show", "--path", ".", "--json")
    return shown if isinstance(shown, dict) else {}


def similarity(left: list[str], right: list[str]) -> float:
    """The Jaccard similarity of two layouts (``orchestration/architecture.py:similarity``)."""
    a, b = set(left), set(right)
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def act(item: Any, action: str | None) -> dict[str, Any]:
    if action == "layout":
        write_files(item.ws, NEW_DIRECTORIES)
        commit(item.ws, "three new source directories")
        return {"actionDone": "committed " + ", ".join(sorted(NEW_DIRECTORIES))}
    if action == "delete-documents":
        target = item.ws / DOCUMENTS
        existed = target.is_dir()
        shutil.rmtree(target, ignore_errors=True)
        return {"actionDone": f"deleted {DOCUMENTS}" if existed else f"{DOCUMENTS} absent"}
    if action == "refresh":
        code, shown, _ = item.h.run(
            item.ws, "architecture", "refresh", "--path", ".", label="refresh"
        )
        return {"actionDone": "harness architecture refresh", "refreshExitCode": code}
    return {}


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    item = sequence(args, condition, rep)
    step = case["id"]
    record: dict[str, Any] = {"step": step, "action": case["action"]}
    record.update(act(item, case["action"]))
    before = show(item)
    recorded_layout = (before.get("state") or {}).get("layout")
    current_layout = before.get("layout") or []
    record["layoutSimilarityBefore"] = (
        round(similarity(recorded_layout, current_layout), 3)
        if isinstance(recorded_layout, list)
        else None
    )
    record["layoutBefore"] = current_layout
    item.write_spec({"write": service_change()}, f"calls-{step}.jsonl")
    task = {
        "taskId": f"task_h28_{step}",
        "title": f"Invoice currency ({step})",
        "intent": "Expose the invoice currency.",
        "acceptanceCriteria": [{"criterionId": "ac_tests", "text": "python -m pytest -q passes."}],
        "metadata": {"ownedPaths": [SERVICE]},
    }
    started = start_run(item.h, item.ws, item.run_dir, task)
    run_id = started["runId"]
    state = run_state(item, run_id)
    calls = read_log(item.log)
    recorded = contract_calls(item.database, run_id)
    survey_events = [
        {"type": e["type"], "payload": e["payload"]}
        for e in events(item.database, run_id)
        if e["type"].startswith("architecture.survey.")
    ]
    after = show(item)
    documents = item.ws / DOCUMENTS
    record.update(
        {
            "runId": run_id,
            "runExitCode": started["runExitCode"],
            "waits": started["waits"],
            **state,
            "fixtureArchitectureCalls": sum(1 for c in calls if c["kind"] == "architecture"),
            "fixtureCalls": [f"{c['kind']}:{c.get('mode') or ''}" for c in calls],
            "recordedArchitectureCalls": sum(
                1 for c in recorded if c.get("callKind") == "architecture"
            ),
            "surveyEvents": survey_events,
            "reused": any(e["type"] == "architecture.survey.reused" for e in survey_events),
            "surveyed": any(e["type"] == "architecture.survey.completed" for e in survey_events),
            "implementCarriesPointer": any(
                c["kind"] == "implement" and c.get("reverseEngineering") for c in calls
            ),
            "documentsPresent": sorted(p.name for p in documents.iterdir())
            if documents.is_dir()
            else [],
            "stateDigest": (after.get("state") or {}).get("digest"),
            "documentationDigest": ((after.get("reverseEngineering") or {}).get("digest")),
            "deviations": item.deviations,
        }
    )
    record["cancelExitCode"] = cancel(item, run_id) if run_id else None
    git(item.ws, "reset", "--hard", "-q")
    git(item.ws, "clean", "-fdq")
    record["brief"] = (
        f"calls={record['fixtureArchitectureCalls']}/{record['recordedArchitectureCalls']} "
        f"reused={record['reused']} surveyed={record['surveyed']}"
    )
    record["steps"] = item.h.steps[-40:]
    if step == LAST:
        cleanup(item.run_dir, args.keep_work)
    return record


# ----- summary ---------------------------------------------------------------------------------
def calls_of(record: dict[str, Any]) -> int:
    """The architecture calls of a run: the harness's record and the fixture's log agree, or
    the larger is taken (a disagreement is listed apart)."""
    return max(record["fixtureArchitectureCalls"], record["recordedArchitectureCalls"])


def reused_payload(record: dict[str, Any]) -> dict[str, Any]:
    for event in record["surveyEvents"]:
        if event["type"] == "architecture.survey.reused":
            return dict(event["payload"])
    return {}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = {r["step"]: r for r in ok if r["condition"] == condition}
        if not rows:
            continue
        reuse_steps = [f"s{n:02d}" for n in range(2, 11)]
        by_condition[condition] = {
            "callsByStep": {step: calls_of(r) for step, r in sorted(rows.items())},
            "reusedByStep": {step: r["reused"] for step, r in sorted(rows.items())},
            "reusedPayloadByStep": {
                step: reused_payload(r) for step, r in sorted(rows.items()) if r["reused"]
            },
            "layoutSimilarityBefore": {
                step: r["layoutSimilarityBefore"] for step, r in sorted(rows.items())
            },
            "implementCarriesPointer": proportion(
                sum(r["implementCarriesPointer"] for r in rows.values()), len(rows)
            ),
            "zeroCallsS02toS10": proportion(
                sum(1 for s in reuse_steps if s in rows and calls_of(rows[s]) == 0),
                sum(1 for s in reuse_steps if s in rows),
            ),
            "regenerations": {
                step: calls_of(rows[step]) for step in ("s11", "s13", "s15") if step in rows
            },
            "totalArchitectureCalls": sum(calls_of(r) for r in rows.values()),
            "countDisagreements": sorted(
                step
                for step, r in rows.items()
                if r["fixtureArchitectureCalls"] != r["recordedArchitectureCalls"]
            ),
        }
    rows = {r["step"]: r for r in ok if r["condition"] == "harness"}
    reuse_steps = [f"s{n:02d}" for n in range(2, 11)]
    reuse_ok = [
        s
        for s in reuse_steps
        if s in rows
        and calls_of(rows[s]) == 0
        and reused_payload(rows[s]).get("digest")
        and reused_payload(rows[s]).get("agentCalls") == 0
    ]
    regenerated = {s: calls_of(rows[s]) for s in ("s11", "s13", "s15") if s in rows}
    summary = {
        "suite": "H28",
        "title": "reuse of the reverse engineering with 0 calls",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a "
        "declared simulation); every run is cancelled at its stop (declared)",
        "steps": [c["id"] for c in CASES],
        "byCondition": by_condition,
    }
    summary["criteria"] = {
        "zeroCallsWhileLayoutHolds": {
            "value": f"{len(reuse_ok)} of {len(reuse_steps)} runs s02-s10 with 0 architecture "
            f"calls and architecture.survey.reused with digest and agentCalls 0 (harness): "
            f"missing {sorted(set(reuse_steps) - set(reuse_ok))}",
            "met": len(reuse_ok) == len(reuse_steps),
        },
        "oneCallPerRegeneration": {
            "value": regenerated,
            "met": len(regenerated) == 3 and all(v == 1 for v in regenerated.values()),
        },
        "note": "Pre-registered in PREREGISTRO.md (H28): condition harness; harness-auto and "
        "harness-nodoc are reported apart. Reported as is; a criterion with missing runs is not "
        "met.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H28",
            title="reuse of the reverse engineering with 0 calls",
            stem="h28-reverse-engineering",
            cases=CASES,
            conditions_of=lambda case: list(CONDITIONS),
            all_conditions=list(CONDITIONS),
            runner=runner,
            summarize=summarize,
        )
    )
