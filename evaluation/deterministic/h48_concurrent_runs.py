#!/usr/bin/env python3
"""H48: concurrent runs, ``CATALOGO.md`` §2.9 and ``PREREGISTRO.md`` (third freeze). No model.

The experiments of ``docs/es/trabajo-simultaneo.md`` on the code under test, with the fixture
provider ``robust_agent.py`` and the configuration of ``harness init``:

* ``worktree-parallel`` (W03, W10): 3 and 10 tasks on one project launched at once with
  ``run start --isolate worktree``; task i adds ``src/sample/feature_<i>.py`` and
  ``tests/test_feature_<i>.py`` (its ``ownedPaths``); the declared simulated person answers the
  waits of each run in that run's thread;
* ``worktree-sequential`` (W03, W10): the same runs one after another (time reference);
* ``same-dir`` (S05): five attempts, each on a fresh project: two ``run start`` of two tasks
  launched at once in the same directory without isolation (the fixture's first call sleeps 5 s
  so the two processes overlap);
* ``same-dir-waiting`` (S1W, reported outside the criterion): the second ``run start`` while
  the first waits at DECISION;
* ``clones`` (C02): two clones of one bare origin, the same task id started at once in each.

``--max-parallel N`` (local smoke only) caps the number of tasks of W03 and W10; a capped record
says so (``parallelCap``) and does not count for the criterion.

Usage:
  python evaluation/deterministic/h48_concurrent_runs.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only W03,...]
      [--conditions worktree-parallel,...] [--reps 1] [--summarize-only] [--keep-work]
      [--max-parallel N]

Writes ``DIR/h48-concurrent-runs.jsonl``, ``DIR/h48-concurrent-runs-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import git, simulated_person, state_database  # noqa: E402
from resource_support import (  # noqa: E402
    Utf8Harness,
    cleanup,
    finding_items,
    main,
    proportion,
    saved_config,
    stamp_dir,
)
from robustness_support import git_porcelain, governed_robust  # noqa: E402
from suite_support import status_of  # noqa: E402

MAX_PARALLEL: int | None = None
CASES: list[dict[str, Any]] = [
    {"id": "W03", "tasks": 3, "conditions": ["worktree-parallel", "worktree-sequential"]},
    {"id": "W10", "tasks": 10, "conditions": ["worktree-parallel", "worktree-sequential"]},
    {"id": "S05", "attempts": 5, "conditions": ["same-dir"]},
    {"id": "S1W", "conditions": ["same-dir-waiting"]},
    {"id": "C02", "conditions": ["clones"]},
]
CONDITIONS = ["worktree-parallel", "worktree-sequential", "same-dir", "same-dir-waiting", "clones"]


def conditions_of(case: dict[str, Any]) -> list[str]:
    return list(case["conditions"])


def feature(index: int) -> dict[str, str]:
    return {
        f"src/sample/feature_{index}.py": (
            f'WRITTEN_IN = "{{cwd}}"\n\n\ndef feature_{index}(value):\n    return value + {index}\n'
        ),
        f"tests/test_feature_{index}.py": (
            f"from sample.feature_{index} import feature_{index}\n\n\n"
            f"def test_req_f{index}() -> None:\n    assert feature_{index}(1) == {1 + index}\n\n\n"
            f"def test_ac_f{index}() -> None:\n    assert feature_{index}(0) == {index}\n"
        ),
    }


def feature_task(index: int, task_id: str | None = None) -> dict[str, Any]:
    return {
        "taskId": task_id or f"task_h48_f{index}",
        "title": f"Feature {index}",
        "intent": f"Add feature_{index}, which adds {index} to its argument.",
        "requirements": [
            {"requirementId": f"req_f{index}", "text": f"feature_{index}(1) returns {1 + index}."}
        ],
        "acceptanceCriteria": [
            {"criterionId": f"ac_f{index}", "text": f"feature_{index}(0) returns {index}."}
        ],
        "metadata": {
            "ownedPaths": [f"src/sample/feature_{index}.py", f"tests/test_feature_{index}.py"]
        },
    }


def spec_for(indices: list[int], sleep_first: float = 0.0) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "changeByTask": {feature_task(i)["taskId"]: feature(i) for i in indices},
    }
    if sleep_first:
        spec["sleepFirst"] = sleep_first
    return spec


def redact(text: str) -> str:
    """A lease message without the host name and pid (no machine identifiers in results)."""
    return re.sub(r"pid \d+ on [^,)]+", "pid <pid> on <host>", text)


def create_task(h: Utf8Harness, ws: Path, run_dir: Path, task: dict[str, Any]) -> int:
    path = run_dir / f"{task['taskId']}.yaml"
    path.write_text(json.dumps(task), encoding="utf-8")
    code, _, _ = h.run(
        ws, "task", "create", "--path", ".", "--file", str(path), label="task create"
    )
    return code


def drive(
    h: Utf8Harness, ws: Path, scratch: Path, task_id: str, isolate: bool, out: dict[str, Any]
) -> None:
    """``run start`` (isolated or not) and the simulated person for the waits before DECISION."""
    scratch.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    extra = ["--isolate", "worktree"] if isolate else []
    code, payload, _ = h.run(
        ws, "run", "start", "--path", ".", "--task", task_id, *extra, label="run start"
    )
    out["startExitCode"] = code
    out["startError"] = (
        redact(str(payload.get("message") or payload.get("error") or payload.get("stderr") or ""))[
            :300
        ]
        if isinstance(payload, dict) and code not in (0, 4, 6)
        else None
    )
    run_id = payload.get("executionId", "") if isinstance(payload, dict) else ""
    out["runId"] = run_id
    waits: list[str] = []
    if run_id:
        code = simulated_person(h, ws, run_id, task_id, scratch, waits, code)
    out["lastExitCode"] = code
    out["waits"] = waits
    out["seconds"] = round(time.monotonic() - started, 3)


def change_set_files(database: Path, run_id: str) -> list[str]:
    if not database.exists():
        return []
    connection = sqlite3.connect(database)
    try:
        rows = [
            json.loads(row[0]).get("files", [])
            for row in connection.execute(
                "SELECT payload_json FROM records WHERE record_type='change_set' "
                "AND execution_id=?",
                (run_id,),
            )
        ]
    finally:
        connection.close()
    return sorted(str(f.get("path")) for f in rows[-1]) if rows else []


def inspect_run(
    h: Utf8Harness, ws: Path, database: Path, run_id: str, own: int, indices: list[int]
) -> dict[str, Any]:
    """Phase, gate, chain and contamination of one isolated run."""
    status = status_of(h, ws, run_id)
    execution = status.get("execution") or {}
    gate = status.get("gate") or {}
    verify_code, _, _ = h.run(ws, "verify", "--path", ".", "--run", run_id, label="verify")
    tree = ws / ".harness" / "worktrees" / run_id
    foreign_in_tree = sorted(
        f"feature_{j}"
        for j in indices
        if j != own
        and (
            (tree / f"src/sample/feature_{j}.py").exists()
            or (tree / f"tests/test_feature_{j}.py").exists()
        )
    )
    files = change_set_files(database, run_id)
    foreign_in_change = sorted(
        path for path in files if "feature_" in path and f"feature_{own}." not in path
    )
    own_file = tree / f"src/sample/feature_{own}.py"
    return {
        "phase": execution.get("currentPhase"),
        "runStatus": execution.get("status"),
        "gate": gate.get("status"),
        "atDecision": execution.get("currentPhase") == "DECISION"
        and gate.get("status") == "PASSED",
        "verifyExitCode": verify_code,
        "eventChainValid": status.get("eventChainValid"),
        "worktreeExists": tree.exists(),
        "ownFileInWorktree": own_file.exists(),
        "foreignFilesInWorktree": foreign_in_tree,
        "changeSetFiles": files,
        "foreignFilesInChangeSet": foreign_in_change,
    }


def run_worktrees(case: dict[str, Any], condition: str, args: argparse.Namespace) -> dict[str, Any]:
    count = case["tasks"] if MAX_PARALLEL is None else min(case["tasks"], MAX_PARALLEL)
    indices = list(range(1, count + 1))
    run_dir = stamp_dir(args, "h48", case["id"], condition)
    workspace = governed_robust(run_dir, args, spec_for(indices))
    saved_config(args, "worktree", workspace)
    ws, h = workspace.ws, workspace.h
    for index in indices:
        create_task(h, ws, run_dir, feature_task(index))
    outs: dict[int, dict[str, Any]] = {i: {} for i in indices}
    harnesses = {i: Utf8Harness(h.executable, h.env) for i in indices}
    started = time.monotonic()
    if condition == "worktree-parallel":
        threads = [
            threading.Thread(
                target=drive,
                args=(
                    harnesses[i],
                    ws,
                    run_dir / f"run-{i}",
                    feature_task(i)["taskId"],
                    True,
                    outs[i],
                ),
                daemon=True,
            )
            for i in indices
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3600)
    else:
        for i in indices:
            drive(harnesses[i], ws, run_dir / f"run-{i}", feature_task(i)["taskId"], True, outs[i])
    wall = round(time.monotonic() - started, 3)
    runs = []
    for i in indices:
        entry = {"index": i, **outs[i]}
        if outs[i].get("runId"):
            entry.update(inspect_run(h, ws, workspace.database, outs[i]["runId"], i, indices))
        entry["runId"] = bool(outs[i].get("runId"))
        runs.append(entry)
    main_tree = git_porcelain(ws)
    contaminated = [
        r["index"]
        for r in runs
        if r.get("foreignFilesInWorktree") or r.get("foreignFilesInChangeSet")
    ]
    record = {
        "tasks": count,
        "preRegisteredTasks": case["tasks"],
        "parallelCap": MAX_PARALLEL if MAX_PARALLEL is not None and count < case["tasks"] else None,
        "wallSeconds": wall,
        "runs": runs,
        "atDecision": sum(1 for r in runs if r.get("atDecision")),
        "chainValid": sum(1 for r in runs if r.get("verifyExitCode") == 0),
        "contaminatedRuns": contaminated,
        "mainTreeChanges": main_tree,
        "contamination": bool(contaminated or main_tree),
        "deviations": workspace.deviations,
        "brief": f"{sum(1 for r in runs if r.get('atDecision'))}/{count} at DECISION, {wall} s",
    }
    cleanup(run_dir, args.keep_work)
    return record


def run_same_dir(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    attempts = []
    for attempt in range(1, case["attempts"] + 1):
        run_dir = stamp_dir(args, "h48", case["id"], f"a{attempt}")
        workspace = governed_robust(run_dir, args, spec_for([1, 2], sleep_first=5))
        saved_config(args, "same-dir", workspace)
        ws, h = workspace.ws, workspace.h
        for index in (1, 2):
            create_task(h, ws, run_dir, feature_task(index))
        outs: dict[int, dict[str, Any]] = {1: {}, 2: {}}
        threads = [
            threading.Thread(
                target=start_only,
                args=(Utf8Harness(h.executable, h.env), ws, feature_task(i)["taskId"], outs[i]),
                daemon=True,
            )
            for i in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1800)
        codes = [outs[1].get("startExitCode"), outs[2].get("startExitCode")]
        # One exits 5 (the lease) and the other goes on (a run exit code).
        pair_rejected = codes.count(5) == 1 and any(c in (0, 4, 6) for c in codes)
        attempts.append(
            {
                "attempt": attempt,
                "startExitCodes": codes,
                "errors": [outs[1].get("startError"), outs[2].get("startError")],
                "leaseMessage": any(
                    "in use" in str(o.get("startError") or "") for o in outs.values()
                ),
                "rejected": pair_rejected,
                "deviations": workspace.deviations,
            }
        )
        cleanup(run_dir, args.keep_work)
    rejected = sum(1 for a in attempts if a["rejected"])
    return {
        "attempts": attempts,
        "rejected": rejected,
        "rejection": proportion(rejected, len(attempts)),
        "brief": f"{rejected}/{len(attempts)} rejected",
    }


def start_only(h: Utf8Harness, ws: Path, task_id: str, out: dict[str, Any]) -> None:
    code, payload, seconds = h.run(
        ws, "run", "start", "--path", ".", "--task", task_id, label="run start"
    )
    out["startExitCode"] = code
    out["seconds"] = round(seconds, 3)
    out["startError"] = (
        redact(str(payload.get("message") or payload.get("error") or payload.get("stderr") or ""))[
            :300
        ]
        if isinstance(payload, dict) and code not in (0, 4, 6)
        else None
    )


def run_same_dir_waiting(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    run_dir = stamp_dir(args, "h48", case["id"], "waiting")
    workspace = governed_robust(run_dir, args, spec_for([1, 2]))
    saved_config(args, "same-dir", workspace)
    ws, h = workspace.ws, workspace.h
    for index in (1, 2):
        create_task(h, ws, run_dir, feature_task(index))
    first: dict[str, Any] = {}
    drive(h, ws, run_dir / "run-1", feature_task(1)["taskId"], False, first)
    first_state = status_of(h, ws, first.get("runId", "")) if first.get("runId") else {}
    second: dict[str, Any] = {}
    drive(h, ws, run_dir / "run-2", feature_task(2)["taskId"], False, second)
    second_findings = finding_items(h, ws, second["runId"]) if second.get("runId") else []
    second_state = status_of(h, ws, second.get("runId", "")) if second.get("runId") else {}
    first_after = status_of(h, ws, first.get("runId", "")) if first.get("runId") else {}
    record = {
        "firstAtDecision": (first_state.get("execution") or {}).get("currentPhase") == "DECISION",
        "secondStartExitCode": second.get("startExitCode"),
        "secondRejected": second.get("startExitCode") == 5,
        "secondDirtyTreeFinding": any(
            f["rule"] == "environment.dirty-tree" for f in second_findings
        ),
        "secondPhase": (second_state.get("execution") or {}).get("currentPhase"),
        "secondGate": (second_state.get("gate") or {}).get("status"),
        "firstPhaseAfter": (first_after.get("execution") or {}).get("currentPhase"),
        "firstDigestChanged": (first_state.get("execution") or {}).get("changeSetDigest")
        != (first_after.get("execution") or {}).get("changeSetDigest"),
        "deviations": workspace.deviations,
    }
    record["brief"] = f"second start exit {record['secondStartExitCode']}"
    cleanup(run_dir, args.keep_work)
    return record


def run_clones(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    del case
    run_dir = stamp_dir(args, "h48", "C02", "clones")
    workspace = governed_robust(run_dir, args, spec_for([1]))
    saved_config(args, "clones", workspace)
    origin = run_dir / "origin.git"
    git(run_dir, "clone", "-q", "--bare", str(workspace.ws), str(origin))
    config_text = (workspace.ws / ".harness" / "project.yaml").read_text(encoding="utf-8")
    clones: dict[str, Any] = {}
    for name in ("clone-a", "clone-b"):
        target = run_dir / name
        git(run_dir, "clone", "-q", str(origin), str(target))
        h = Utf8Harness(workspace.h.executable, workspace.h.env)
        h.run(target, "init", "--path", ".", label="init")
        (target / ".harness" / "project.yaml").write_text(config_text, encoding="utf-8")
        create_task(h, target, run_dir, feature_task(1))
        clones[name] = {"ws": target, "h": h, "out": {}}
    threads = [
        threading.Thread(
            target=drive,
            args=(
                c["h"],
                c["ws"],
                run_dir / f"run-{n}",
                feature_task(1)["taskId"],
                False,
                c["out"],
            ),
            daemon=True,
        )
        for n, c in clones.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3600)
    result: dict[str, Any] = {}
    run_ids = {n: c["out"].get("runId") for n, c in clones.items()}
    for name, clone in clones.items():
        ws, h = clone["ws"], clone["h"]
        other = "clone-b" if name == "clone-a" else "clone-a"
        _, listed, _ = h.run(ws, "run", "list", "--path", ".", label="run list")
        seen = [
            str(item.get("executionId") or item.get("runId"))
            for item in (listed if isinstance(listed, list) else [])
            if isinstance(item, dict)
        ]
        status = status_of(h, ws, run_ids[name] or "") if run_ids[name] else {}
        written = ws / "src" / "sample" / "feature_1.py"
        text = written.read_text(encoding="utf-8") if written.exists() else ""
        verify_code = (
            h.run(ws, "verify", "--path", ".", "--run", run_ids[name], label="verify")[0]
            if run_ids[name]
            else None
        )
        result[name] = {
            "startExitCode": clone["out"].get("startExitCode"),
            "lastExitCode": clone["out"].get("lastExitCode"),
            "phase": (status.get("execution") or {}).get("currentPhase"),
            "gate": (status.get("gate") or {}).get("status"),
            "seesOwnRun": run_ids[name] in seen,
            "seesOtherRun": bool(run_ids[other]) and run_ids[other] in seen,
            "runsListed": len(seen),
            "writtenBy": "self" if f'"{name}"' in text else other if f'"{other}"' in text else None,
            "verifyExitCode": verify_code,
            "registry": registry_name(h, ws),
            "porcelain": git_porcelain(ws),
        }
    contamination = any(
        r["writtenBy"] not in ("self", None) or r["seesOtherRun"] for r in result.values()
    )
    record = {
        "clones": result,
        "bothRan": all(r["startExitCode"] in (0, 4, 6) for r in result.values()),
        "contamination": contamination,
        "deviations": workspace.deviations,
        "brief": f"contamination={contamination}",
    }
    for name in ("clone-a", "clone-b"):
        shutil.rmtree(run_dir / name / ".harness" / "worktrees", ignore_errors=True)
    cleanup(run_dir, args.keep_work)
    return record


def registry_name(h: Utf8Harness, ws: Path) -> str:
    """The run registry of a clone, relative to the state directory (no local path)."""
    database = state_database(h, ws)
    root = Path(h.env.get("HARNESS_STATE_DIR", ""))
    try:
        return database.relative_to(root).as_posix()
    except ValueError:
        return "outside HARNESS_STATE_DIR: " + database.name


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    if condition in {"worktree-parallel", "worktree-sequential"}:
        return run_worktrees(case, condition, args)
    if condition == "same-dir":
        return run_same_dir(case, args)
    if condition == "same-dir-waiting":
        return run_same_dir_waiting(case, args)
    return run_clones(case, args)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r]
    worktree = [r for r in ok if r["condition"].startswith("worktree")]
    uncapped = [r for r in worktree if not r.get("parallelCap")]
    same = [r for r in ok if r["condition"] == "same-dir"]
    clones = [r for r in ok if r["condition"] == "clones"]
    pairs = []
    for case in ("W03", "W10"):
        parallel = [
            r for r in worktree if r["case"] == case and r["condition"] == "worktree-parallel"
        ]
        sequential = [
            r for r in worktree if r["case"] == case and r["condition"] == "worktree-sequential"
        ]
        if parallel and sequential:
            pairs.append(
                {
                    "case": case,
                    "tasks": parallel[0]["tasks"],
                    "parallelSeconds": [r["wallSeconds"] for r in parallel],
                    "sequentialSeconds": [r["wallSeconds"] for r in sequential],
                    "ratio": round(parallel[0]["wallSeconds"] / sequential[0]["wallSeconds"], 3)
                    if sequential[0]["wallSeconds"]
                    else None,
                    "capped": bool(parallel[0].get("parallelCap")),
                }
            )
    runs = [run for r in worktree for run in r["runs"]]
    rejected = sum(r["rejected"] for r in same)
    attempts = sum(len(r["attempts"]) for r in same)
    contaminated = [
        f"{r['case']}/{r['condition']}" for r in worktree + clones if r["contamination"]
    ]
    expected_worktree = {
        (c, k) for c in ("W03", "W10") for k in ("worktree-parallel", "worktree-sequential")
    }
    present_worktree = {(r["case"], r["condition"]) for r in uncapped if r["rep"] == 1}
    summary = {
        "suite": "H48",
        "title": "concurrent runs",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION "
        "(a declared simulation)",
        "worktree": {
            "records": len(worktree),
            "capped": sorted(
                f"{r['case']}/{r['condition']}" for r in worktree if r.get("parallelCap")
            ),
            "runsAtDecision": proportion(sum(1 for r in runs if r.get("atDecision")), len(runs)),
            "runsChainValid": proportion(
                sum(1 for r in runs if r.get("verifyExitCode") == 0), len(runs)
            ),
            "timeParallelVsSequential": pairs,
        },
        "sameDir": {
            "rejection": proportion(rejected, attempts),
            "exitCodes": [a["startExitCodes"] for r in same for a in r["attempts"]],
        },
        "sameDirWaiting": [
            {
                k: r.get(k)
                for k in (
                    "secondStartExitCode",
                    "secondRejected",
                    "secondDirtyTreeFinding",
                    "secondPhase",
                    "secondGate",
                    "firstPhaseAfter",
                    "firstDigestChanged",
                )
            }
            for r in ok
            if r["condition"] == "same-dir-waiting"
        ],
        "clones": [r.get("clones") for r in clones],
        "contaminated": contaminated,
    }
    summary["criteria"] = {
        "zeroContamination": {
            "value": f"{len(contaminated)} contaminated of {len(worktree) + len(clones)} records",
            "met": not contaminated
            and expected_worktree <= present_worktree
            and any(r["rep"] == 1 for r in clones),
        },
        "sameDirRejection100": {
            "value": f"{rejected}/{attempts} pairs rejected",
            "met": attempts == 5 and rejected == attempts,
        },
        "isolatedRunsAtDecisionWithValidChain": {
            "value": f"{sum(1 for r in runs if r.get('atDecision') and r.get('verifyExitCode') == 0)}"
            f"/{len(runs)}",
            "met": bool(runs)
            and expected_worktree <= present_worktree
            and all(r.get("atDecision") and r.get("verifyExitCode") == 0 for r in runs),
        },
        "note": "Pre-registered in PREREGISTRO.md (H48); evaluated as is. A record capped by "
        "--max-parallel (local smoke) does not count: with caps the criteria are not met.",
    }
    return summary


def take_max_parallel() -> None:
    """``--max-parallel N`` (local smoke) is read here and removed before the shared CLI."""
    global MAX_PARALLEL
    if "--max-parallel" in sys.argv:
        index = sys.argv.index("--max-parallel")
        MAX_PARALLEL = int(sys.argv[index + 1])
        del sys.argv[index : index + 2]


if __name__ == "__main__":
    take_max_parallel()
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H48",
            title="concurrent runs",
            stem="h48-concurrent-runs",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=CONDITIONS,
            runner=runner,
            summarize=summarize,
            extra_environment=lambda args: {"maxParallel": MAX_PARALLEL},
        )
    )
