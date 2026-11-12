#!/usr/bin/env python3
"""Run the temptation tasks (prueba T2) in the direct and harness conditions.

For every (task, condition, repetition) the task's fixture is prepared as a Git repository and run
with the machinery of ``evaluation/run_eval.py``, imported, not changed: the direct condition is
``agentlib.run_claude`` with ``agentlib.build_prompt`` of the task (what run_eval.py's direct
condition does); the harness condition is ``run_eval.run_harness`` with the ``harness`` condition
of ``conditions.py``, driven by the simulated person and the simulated product owner. The
product owner knows only the task and the fixture's README: like the user, it does not know the
defect.

The record of a run (one JSON line in ``--out``) holds:

* ``detection``: the task's detector over the delivered tree (``detectors.py``);
* direct: ``defectDelivered`` is the defect in the final tree of the workspace;
* harness: the tree at DECISION is the workspace when the change was approved, the baseline with
  the quarantined patch when the run stopped the line, the workspace otherwise
  (``treeSource``); ``defectDelivered`` is a run that reached DECISION with the defect in that
  tree and no finding of the harness matching the task's ``flags`` (``flaggedBy`` lists the ones
  that did); ``defectApproved`` is the defect in an approved change;
* the harness's findings, the agent's and the product owner's cost and tokens, and the time.

``--dry-run`` uses ``dry/fake_claude.py`` (``evaluation/fake_claude.py`` plus the task's dry-run
variant, ``--fake-mode tempted`` by default or ``clean``) for every call: no model is called.

Usage: python run_temptations.py --model M --work DIR --out FILE [--tasks ID ...]
       [--conditions direct harness] [--reps N] [--dry-run [--fake-mode tempted|clean]]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
sys.path.insert(0, str(EVALUATION))
sys.path.insert(0, str(HERE))

import agentlib  # noqa: E402
import catalog  # noqa: E402
import run_eval  # noqa: E402
from product_owner import DEFAULT_PO_MODEL, ProductOwner  # noqa: E402

FAKE_CLAUDE = HERE / "dry" / "fake_claude.py"
CONDITIONS = ("direct", "harness")


def prepare(task: catalog.Temptation, workspace: Path, site_packages: Path) -> str:
    """The fixture as a Git repository with one baseline commit (``run_eval.prepare`` copies flat
    fixtures only; these have ``src/`` and ``tests/``)."""
    shutil.copytree(task.fixture, workspace)
    (workspace / "gitignore.template").rename(workspace / ".gitignore")
    run_eval.point_import_path(site_packages, workspace)
    run_eval.git(workspace, "init", "-q")
    run_eval.git(workspace, "add", "-A")
    run_eval.git(workspace, "commit", "-qm", "baseline")
    return run_eval.git(workspace, "rev-parse", "HEAD").strip()


def quarantined_tree(workspace: Path, baseline: str, run_dir: Path, patch_ref: str) -> Path | None:
    """The baseline with the quarantined patch applied, or None when it cannot be rebuilt."""
    shown = subprocess.run(
        [*run_eval.HARNESS_CMD, "artifact", "show", "--path", ".", patch_ref],
        cwd=workspace,
        capture_output=True,
        env={**os.environ, **run_eval.GIT_ENV, **run_eval.HARNESS_ENV},
    )
    if shown.returncode != 0 or not shown.stdout:
        return None
    copy = run_dir / "quarantine-ws"
    applied = run_eval.apply_to_baseline(
        workspace, baseline, shown.stdout, copy, run_dir / "quarantined.patch"
    )
    return copy if applied["exitCode"] == 0 else None


def run_direct(task_yaml: dict[str, Any], workspace: Path, run_dir: Path, model: str) -> None:
    log = run_dir / "agent-calls" / "call-1.json"
    agentlib.run_claude(agentlib.build_prompt(task_yaml), workspace, model, log)


def run_governed(
    task: catalog.Temptation,
    workspace: Path,
    baseline: str,
    run_dir: Path,
    args: argparse.Namespace,
    claude_bin: str | None,
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    owner = None
    if not args.no_product_owner:
        knowledge = (
            "Descripción completa de la tarea:\n"
            + task.task_file.read_text(encoding="utf-8")
            + "\nREADME.md del repositorio:\n"
            + (task.fixture / "README.md").read_text(encoding="utf-8")
        )
        owner = ProductOwner(knowledge, args.po_model, run_dir)
    result = run_eval.run_harness(
        workspace,
        run_dir,
        task.task_file,
        args.model,
        clarifier=owner,
        condition="harness",
        provider="claude",
        claude_bin=claude_bin,
    )
    result.pop("_runId", None)
    tree, source = workspace, "workspace"
    quarantine = ((result.get("state") or {}).get("measures") or {}).get("quarantine") or {}
    if result.get("outcome") != "approved" and quarantine.get("patchRef"):
        rebuilt = quarantined_tree(workspace, baseline, run_dir, quarantine["patchRef"])
        tree, source = (rebuilt, "quarantine-patch") if rebuilt else (workspace, "workspace")
    rule_ids = [str(f.get("ruleId")) for f in result.get("findings") or []]
    harness = {
        "outcome": result.get("outcome"),
        "reachedDecision": bool(result.get("decisions")),
        "decisions": [d.get("decision") for d in result.get("decisions") or []],
        "gateHistory": result.get("gateHistory"),
        "corrections": result.get("corrections"),
        "finalPhase": result.get("finalPhase"),
        "finalStatus": result.get("finalStatus"),
        "findings": result.get("findings") or [],
        "flaggedBy": task.flagged(rule_ids),
        "taskCreateError": result.get("taskCreateError"),
    }
    owner_rounds = {"rounds": len(owner.rounds) if owner else 0}
    return harness, owner_rounds, tree, source


def run_one(
    task: catalog.Temptation,
    condition: str,
    rep: int,
    args: argparse.Namespace,
    site_packages: Path,
    claude_bin: str | None,
) -> dict[str, Any]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.work / f"{task.task_id[:2]}-{condition}-r{rep}-{stamp}"
    workspace = run_dir / "ws"
    run_dir.mkdir(parents=True)
    baseline = prepare(task, workspace, site_packages)
    run_eval.HARNESS_ENV["HARNESS_STATE_DIR"] = str((run_dir / "state").resolve())
    (run_dir / "temptation-task").write_text(task.task_id, encoding="utf-8")
    if args.dry_run:
        (run_dir / "fake-mode").write_text(args.fake_mode, encoding="utf-8")
    task_yaml = yaml.safe_load(task.task_file.read_text(encoding="utf-8"))
    record: dict[str, Any] = {
        "task": task.task_id,
        "condition": condition,
        "model": args.model,
        "rep": rep,
        "dryRun": args.dry_run,
        "fakeMode": args.fake_mode if args.dry_run else None,
        "predictedCoverage": task.coverage,
        "productOwnerModel": None
        if condition == "direct" or args.no_product_owner
        else args.po_model,
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    started = time.monotonic()
    if condition == "direct":
        run_direct(task_yaml, workspace, run_dir, args.model)
        tree, source = workspace, "workspace"
    else:
        record["harnessVersion"] = run_eval.harness_version()
        harness, owner, tree, source = run_governed(
            task, workspace, baseline, run_dir, args, claude_bin
        )
        record["harness"] = harness
        record["productOwner"] = owner
    record["wallSeconds"] = round(time.monotonic() - started, 3)
    detection = task.detect(tree)
    record["treeSource"] = source
    record["detection"] = {"defect": detection.defect, "evidence": detection.evidence}
    if condition == "direct":
        record["defectDelivered"] = detection.defect
    else:
        h = record["harness"]
        record["defectDelivered"] = bool(
            h["reachedDecision"] and detection.defect and not h["flaggedBy"]
        )
        record["defectApproved"] = bool(h["outcome"] == "approved" and detection.defect)
    calls = agentlib.load_calls(run_dir / "agent-calls")
    po_calls = agentlib.load_calls(run_dir / "po-calls")
    limited = sum(1 for c in calls + po_calls if agentlib.hit_limit(c))
    if limited:
        record["invalid"] = f"usage-limit ({limited} call(s))"
    record["agent"] = run_eval.summarize_calls(calls)
    record["agentByKind"] = run_eval.calls_by(calls, "kind")
    record["productOwnerCalls"] = run_eval.summarize_calls(po_calls)
    costs = [record["agent"]["costUsd"], record["productOwnerCalls"]["costUsd"] or 0.0]
    record["costUsd"] = None if costs[0] is None else round(sum(costs), 6)
    return record


def summary_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:
        row = rows.setdefault(
            (r["task"], r["condition"]),
            {"task": r["task"], "condition": r["condition"], "runs": 0, "defectDelivered": 0},
        )
        row["runs"] += 1
        row["defectDelivered"] += int(bool(r["defectDelivered"]))
    return [rows[key] for key in sorted(rows)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tasks", nargs="*", choices=sorted(catalog.TASKS), default=None)
    parser.add_argument("--conditions", nargs="*", choices=CONDITIONS, default=list(CONDITIONS))
    parser.add_argument("--model", required=True)
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="dry/fake_claude.py, no model call")
    parser.add_argument("--fake-mode", choices=["tempted", "clean"], default="tempted")
    parser.add_argument("--no-product-owner", action="store_true")
    parser.add_argument("--po-model", default=DEFAULT_PO_MODEL)
    args = parser.parse_args()
    # The harness passes the provider only PATH, HOME, SYSTEMROOT, TEMP and the locale; on Windows
    # HOME is often unset and the provider (agentlib.home_directory) cannot find the home then.
    os.environ.setdefault("HOME", str(Path.home()))
    claude_bin = None
    if args.dry_run:
        claude_bin = str(FAKE_CLAUDE)
        agentlib.CLAUDE_BIN = claude_bin
    else:
        agentlib.ensure_account()
    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for task_id in args.tasks or sorted(catalog.TASKS):
        for rep in range(1, args.reps + 1):
            for condition in args.conditions:
                record = run_one(
                    catalog.TASKS[task_id], condition, rep, args, site_packages, claude_bin
                )
                records.append(record)
                out = (
                    args.out.with_name(args.out.stem + ".invalid.jsonl")
                    if record.get("invalid")
                    else args.out
                )
                with out.open("a", encoding="utf-8", newline="\n") as handle:
                    line = json.dumps(record, sort_keys=True, ensure_ascii=False)
                    handle.write(agentlib.anonymize(line, args.work, HERE, EVALUATION) + "\n")
                print(
                    json.dumps(
                        {
                            k: record.get(k)
                            for k in ("task", "condition", "rep", "defectDelivered", "wallSeconds")
                        }
                        | {
                            "outcome": (record.get("harness") or {}).get("outcome", "direct"),
                            "flaggedBy": (record.get("harness") or {}).get("flaggedBy"),
                            "evidence": record["detection"]["evidence"],
                        }
                    ),
                    flush=True,
                )
    print(json.dumps({"summary": summary_table(records)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
