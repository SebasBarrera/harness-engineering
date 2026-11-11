#!/usr/bin/env python3
"""H47: crash and resume (the harness killed, and a "power cut"), ``CATALOGO.md`` §2.9 and
``PREREGISTRO.md`` (third freeze). No model is called.

The run of the threshold discount (the sample project, the correct change, the configuration of
``harness init`` with the fixture provider ``robust_agent.py``) advances with the declared
simulated person in a thread; at one of ten points the harness command that is running is
killed, and ``harness run continue`` has to recover it:

* K03, K04: the first ``architecture`` and ``acceptance`` call (the fixture blocks there and
  writes a marker; the suite kills when it sees it); K06 the first ``implement`` before it
  writes, K07 after it wrote only ``src/sample/pricing.py``;
* phase boundaries seen in the database (a declared race): K01, K02 and K05 as soon as
  ``phase.completed`` of INTENT, DISCOVERY and PLANNING is stored, K08 ``phase.started`` of
  VERIFICATION, K09 ``phase.started`` of INDEPENDENT_REVIEW (the amendment of 2026-11-10: the
  small task calls no ``clarify``, ``locate``, ``plan`` or ``review``);
* K10: the simulated person approves at DECISION and ``gate decide`` is killed as soon as
  ``human.decision.recorded`` is in the database.

Conditions: ``lease-kill`` and ``lease-power`` (``init``, ``governance.workspaceLease: true``),
``nolease-kill`` and ``nolease-power`` (``workspaceLease: false``). ``kill`` kills the harness
process (POSIX ``SIGKILL``, Windows ``TerminateProcess``); ``power`` kills its whole process tree
at once. Recovery: ``run continue`` (retried every 5 s while the lease is held, up to 180 s: on
Windows a dead holder is taken over only after 60 s), the simulated person, its approval at
DECISION, ``run continue`` until the run closes, then ``harness verify --run``.

Usage:
  python evaluation/deterministic/h47_crash_resume.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only K01,...]
      [--conditions lease-kill,lease-power,nolease-kill,nolease-power] [--reps 1]
      [--summarize-only] [--keep-work]

Writes ``DIR/h47-crash-resume.jsonl``, ``DIR/h47-crash-resume-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import simulated_person  # noqa: E402
from resource_support import (  # noqa: E402
    Utf8Harness,
    cleanup,
    discount_task,
    main,
    proportion,
    run_state,
    saved_config,
    stamp_dir,
)
from robustness_support import (  # noqa: E402
    APPROVABLE_CODE,
    WINDOWS,
    ProcHarness,
    alive,
    all_events,
    approvable_spec,
    expected_files,
    governed_robust,
    harness_python,
    is_own_fixture,
    kill_process,
    kill_tree,
    open_phase,
)
from suite_support import approve, start_run, status_of  # noqa: E402

LEASE_RETRY_SECONDS = 5
LEASE_WAIT_SECONDS = 180
POLL_SECONDS = 0.02
POINT_TIMEOUT_SECONDS = 900

CASES: list[dict[str, Any]] = [
    {"id": "K01", "point": "INTENT completed", "event": ("phase.completed", "INTENT")},
    {"id": "K02", "point": "DISCOVERY completed", "event": ("phase.completed", "DISCOVERY")},
    {"id": "K03", "point": "first architecture call", "block": "architecture"},
    {"id": "K04", "point": "first acceptance call", "block": "acceptance"},
    {"id": "K05", "point": "PLANNING completed", "event": ("phase.completed", "PLANNING")},
    {"id": "K06", "point": "first implement call, before it writes", "block": "implement"},
    {
        "id": "K07",
        "point": "first implement call, after it wrote only src/sample/pricing.py",
        "block": "implement",
        "partial": True,
    },
    {
        "id": "K08",
        "point": "VERIFICATION running (phase.started)",
        "event": ("phase.started", "VERIFICATION"),
    },
    {
        "id": "K09",
        "point": "INDEPENDENT_REVIEW started",
        "event": ("phase.started", "INDEPENDENT_REVIEW"),
    },
    {
        "id": "K10",
        "point": "gate decide APPROVE, once human.decision.recorded is stored",
        "event": ("human.decision.recorded", None),
        "approveFirst": True,
    },
]
CONDITIONS = ["lease-kill", "lease-power", "nolease-kill", "nolease-power"]


def conditions_of(case: dict[str, Any]) -> list[str]:
    del case
    return list(CONDITIONS)


def wait_for_point(
    case: dict[str, Any], marker: Path, database: Path, driver: threading.Thread
) -> tuple[bool, str]:
    """Wait until the kill point is reached; whether it was, and why not."""
    deadline = time.monotonic() + POINT_TIMEOUT_SECONDS
    event = case.get("event")
    while time.monotonic() < deadline:
        if event is None and marker.exists():
            return True, ""
        if event is not None:
            for item in all_events(database):
                if item["type"] == event[0] and (
                    event[1] is None or item["payload"].get("phaseId") == event[1]
                ):
                    return True, ""
        if not driver.is_alive():
            return False, "the run stopped before the point (the driver finished)"
        time.sleep(POLL_SECONDS if event is not None else 0.1)
    return False, f"the point was not reached in {POINT_TIMEOUT_SECONDS} s"


def recover(
    h: Utf8Harness,
    ws: Path,
    run_dir: Path,
    run_id: str,
    task_id: str,
    killed_at: float,
    *,
    approve_at_decision: bool,
) -> dict[str, Any]:
    """``run continue`` until the lease is taken over, the simulated person, the approval (not in
    K10, approved before the kill: PREREGISTRO.md) and the closure. Returns what each step did."""
    busy = 0
    code, payload = -1, None
    accepted_at = time.monotonic()
    while True:
        accepted_at = time.monotonic()
        code, payload, _ = h.run(
            ws, "run", "continue", "--path", ".", "--run", run_id, label="run continue (recover)"
        )
        text = json.dumps(payload) if not isinstance(payload, str) else payload
        if code == 5 and "in use" in text and time.monotonic() - killed_at < LEASE_WAIT_SECONDS:
            busy += 1
            time.sleep(LEASE_RETRY_SECONDS)
            continue
        break
    takeover = round(accepted_at - killed_at, 3)
    first_code = code
    waits: list[str] = []
    code = simulated_person(h, ws, run_id, task_id, run_dir, waits, code)
    decided = None
    status = status_of(h, ws, run_id)
    execution = status.get("execution") or {}
    waiting_after = (
        execution.get("currentPhase") == "DECISION" and execution.get("status") != "PASSED"
    )
    if waiting_after and approve_at_decision:
        decided = approve(h, ws, run_id, actor="human.reviewer-simulated")
    for _ in range(2):
        execution = status_of(h, ws, run_id).get("execution") or {}
        if execution.get("status") == "PASSED" or execution.get("currentPhase") != "CLOSURE":
            break
        code, _, _ = h.run(
            ws, "run", "continue", "--path", ".", "--run", run_id, label="run continue (close)"
        )
    return {
        "leaseBusyAttempts": busy,
        "firstContinueExitCode": first_code,
        "firstContinueError": re.sub(
            r"pid [0-9]+ on [^,)]+",
            "pid <pid> on <host>",
            str((payload or {}).get("message") or ""),
        )[:300]
        if isinstance(payload, dict) and first_code not in (0, 4, 6)
        else None,
        "takeoverSeconds": takeover,
        "recoveryWaits": waits,
        "approveExitCode": decided,
        "waitingAtDecisionAfterRecovery": waiting_after,
        "lastExitCode": code,
        "closeSeconds": round(time.monotonic() - killed_at, 3),
    }


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h47", case["id"], condition)
    marker = run_dir / "blocked.json"
    spec = approvable_spec()
    if case.get("block"):
        spec["block"] = {"kind": case["block"], "marker": str(marker), "maxSeconds": 600}
        if case.get("partial"):
            spec["block"]["partial"] = {"src/sample/pricing.py": APPROVABLE_CODE}
    lease = condition.startswith("lease")

    def mutate(config: dict[str, Any]) -> None:
        if not lease:
            config.setdefault("governance", {})["workspaceLease"] = False

    workspace = governed_robust(run_dir, args, spec, mutate=mutate)
    saved_config(args, condition, workspace)
    ws, database = workspace.ws, workspace.database
    task = discount_task(f"task_h47_{case['id'].lower()}")
    driver_h = ProcHarness(workspace.h.executable, workspace.h.env)
    outcome: dict[str, Any] = {}

    def drive() -> None:
        result = start_run(driver_h, ws, run_dir, task)
        outcome.update(result)
        if case.get("approveFirst") and result.get("runId"):
            outcome["approveExitCode"] = approve(
                driver_h, ws, result["runId"], actor="human.reviewer-simulated"
            )

    driver = threading.Thread(target=drive, name="h47-driver", daemon=True)
    driver.start()
    reached, why = wait_for_point(case, marker, database, driver)
    record: dict[str, Any] = {
        "point": case["point"],
        "killMode": "power" if condition.endswith("power") else "kill",
        "lease": lease,
        "platform": sys.platform,
        "harnessCommand": "python -m governed_harness"
        if len(harness_python(workspace.h.executable)) > 1
        else "harness executable",
        "pointReached": reached,
    }
    landed = False
    killed_at = time.monotonic()
    if reached:
        with driver_h.lock:
            process = driver_h.current
            driver_h.stopped.set()
            landed = process is not None and process.poll() is None
            killed_at = time.monotonic()
            if landed and process is not None:
                if record["killMode"] == "power":
                    record["killedPids"] = len(kill_tree(process.pid))
                else:
                    kill_process(process.pid)
                record["killedCommand"] = driver_h.current_label
        if process is not None:
            with contextlib.suppress(Exception):
                process.wait(timeout=60)
            record["killedExitCode"] = process.returncode
    else:
        driver_h.stopped.set()
    driver.join(timeout=300)
    record["killLanded"] = landed
    record["driverSteps"] = list(driver_h.steps)
    before = all_events(database)
    run_ids = sorted({e["runId"] for e in before})
    run_id = outcome.get("runId") or (run_ids[0] if run_ids else "")
    record["runId"] = bool(run_id)
    record["killedInPhase"] = open_phase([e for e in before if e["runId"] == run_id])
    record["eventsBeforeKill"] = len([e for e in before if e["runId"] == run_id])
    record["filesAtKill"] = {
        path: (ws / path).read_text(encoding="utf-8") == content
        for path, content in expected_files(task).items()
    }
    if not reached or not landed or not run_id:
        record["notEvaluatedBecause"] = (
            why
            if not reached
            else "the harness process had already exited when the kill came"
            if not landed
            else "no run was created"
        )
        record["recovered"] = None
        finish(run_dir, marker, args)
        return record
    if marker.exists():
        pid = int(json.loads(marker.read_text(encoding="utf-8")).get("pid", 0))
        record["blockedAgentAliveAfterKill"] = alive(pid)
    recovery_h = Utf8Harness(workspace.h.executable, workspace.h.env)
    record.update(
        recover(
            recovery_h,
            ws,
            run_dir,
            run_id,
            task["taskId"],
            killed_at,
            approve_at_decision=not case.get("approveFirst"),
        )
    )
    record["recoverySteps"] = list(recovery_h.steps)
    events = [e for e in all_events(database) if e["runId"] == run_id]
    status = status_of(recovery_h, ws, run_id)
    verify_code, verified, _ = recovery_h.run(
        ws, "verify", "--path", ".", "--run", run_id, label="verify"
    )
    record.update(run_state(workspace, run_id))
    completed: dict[str, int] = {}
    for event in events:
        if event["type"] == "phase.completed":
            key = str(event["phaseExecutionId"])
            completed[key] = completed.get(key, 0) + 1
    recovered_events = [e["payload"] for e in events if e["type"] == "run.recovered"]
    decisions = [e for e in events if e["type"] == "human.decision.recorded"]
    files = {
        path: (ws / path).read_text(encoding="utf-8") == content if (ws / path).exists() else False
        for path, content in expected_files(task).items()
    }
    leftover = None
    if marker.exists():
        pid = int(json.loads(marker.read_text(encoding="utf-8")).get("pid", 0))
        leftover = alive(pid)
    record.update(
        {
            "verifyExitCode": verify_code,
            "verifyStatus": verified.get("status") if isinstance(verified, dict) else None,
            # What did not verify (record and artifact problems, anchor), without local paths.
            "verifyProblems": verify_problems(verified) if verify_code else None,
            "eventChainValid": status.get("eventChainValid"),
            "decisions": len(decisions),
            "duplicatePhaseCompletions": sum(1 for n in completed.values() if n > 1),
            "runRecovered": recovered_events,
            "eventTypes": sorted({e["type"] for e in events}),
            "implementCalls": sum(
                1
                for line in workspace.log.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("kind") == "implement"
            )
            if workspace.log.exists()
            else 0,
            "finalFilesCorrect": files,
            "leftoverAgents": leftover,
            "deviations": workspace.deviations,
        }
    )
    record["duplicateDecision"] = record["decisions"] > 1
    record["duplicateEvents"] = record["duplicatePhaseCompletions"] > 0 or record["decisions"] > 1
    record["chainValid"] = verify_code == 0 and record["eventChainValid"] is True
    record["recovered"] = bool(
        record.get("runStatus") == "PASSED" and all(files.values()) and record["chainValid"]
    )
    record["brief"] = f"recovered={record['recovered']} decisions={record['decisions']}"
    finish(run_dir, marker, args)
    return record


def verify_problems(report: Any) -> Any:
    """The parts of a ``harness verify`` report that did not verify (no paths)."""
    if not isinstance(report, dict):
        return report
    runs = report.get("runs") if isinstance(report.get("runs"), list) else [report]
    out = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        anchor = run.get("anchor") if isinstance(run.get("anchor"), dict) else {}
        chain = run.get("eventChain") if isinstance(run.get("eventChain"), dict) else {}
        records = run.get("records") if isinstance(run.get("records"), dict) else {}
        artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
        out.append(
            {
                "valid": run.get("valid"),
                "chainValid": chain.get("valid"),
                "chainError": chain.get("error"),
                "anchor": anchor.get("status"),
                "recordProblems": (records.get("problems") or [])[:10],
                "artifactProblems": (artifacts.get("problems") or [])[:10],
            }
        )
    return out


def finish(run_dir: Path, marker: Path, args: argparse.Namespace) -> None:
    """Kill a blocked agent that is still alive (it would hold the directory), then clean up.
    Only that one process, and only when its command line is this case's fixture (a pid can be
    reused by another process once the agent exited)."""
    if marker.exists():
        with contextlib.suppress(Exception):
            pid = int(json.loads(marker.read_text(encoding="utf-8")).get("pid", 0))
            if alive(pid) and is_own_fixture(pid, run_dir):
                kill_process(pid)
                time.sleep(0.5)
    cleanup(run_dir, args.keep_work)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r]
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in ok if r["condition"] == condition]
        evaluated = [r for r in rows if r.get("recovered") is not None]
        good = [
            r
            for r in evaluated
            if r["recovered"] and r["chainValid"] and not r["duplicateDecision"]
        ]
        by_condition[condition] = {
            "records": len(rows),
            "evaluated": len(evaluated),
            "notEvaluated": sorted(
                f"{r['case']}/r{r['rep']}: {r.get('notEvaluatedBecause')}"
                for r in rows
                if r.get("recovered") is None
            ),
            "recoveredWithValidChainNoDuplicateDecision": proportion(len(good), len(rows)),
            "recovered": proportion(sum(1 for r in evaluated if r["recovered"]), len(evaluated)),
            "chainValid": proportion(sum(1 for r in evaluated if r["chainValid"]), len(evaluated)),
            "duplicateDecisions": sum(1 for r in evaluated if r["duplicateDecision"]),
            "duplicateEvents": sum(1 for r in evaluated if r["duplicateEvents"]),
            "runRecoveredEvent": sum(1 for r in evaluated if r.get("runRecovered")),
            "leaseBusyAttempts": [r.get("leaseBusyAttempts") for r in evaluated],
            "takeoverSeconds": [r.get("takeoverSeconds") for r in evaluated],
            "closeSeconds": [r.get("closeSeconds") for r in evaluated],
            "leftoverAgents": sum(1 for r in evaluated if r.get("leftoverAgents")),
            "byCase": {
                r["case"]: {
                    k: r.get(k)
                    for k in (
                        "recovered",
                        "chainValid",
                        "decisions",
                        "duplicateEvents",
                        "killedInPhase",
                        "killedCommand",
                        "firstContinueExitCode",
                        "runStatus",
                        "phase",
                        "terminalReason",
                        "finalFilesCorrect",
                        "leftoverAgents",
                        "notEvaluatedBecause",
                    )
                }
                for r in rows
                if r["rep"] == 1
            },
        }
    criteria: dict[str, Any] = {}
    for condition in ("lease-kill", "lease-power"):
        cell = by_condition[condition]["recoveredWithValidChainNoDuplicateDecision"]
        rep1 = [r for r in ok if r["condition"] == condition and r["rep"] == 1]
        passed = sum(
            1
            for r in rep1
            if r.get("recovered") and r.get("chainValid") and not r.get("duplicateDecision")
        )
        criteria[f"{condition}10of10"] = {
            "value": f"{passed}/{len(CASES)} recovered with a valid chain and no duplicate decision "
            f"(repetition 1; all repetitions: {cell['k']}/{cell['n']})",
            "met": passed == len(CASES) and len(rep1) == len(CASES),
        }
    criteria["note"] = (
        "Pre-registered in PREREGISTRO.md (H47); evaluated as is. A point not evaluated counts "
        "against the criterion. The conditions without the lease are a contrast, outside it."
    )
    return {
        "suite": "H47",
        "title": "crash and resume (SIGKILL and power cut)",
        "platform": sys.platform,
        "platformNote": "Windows: TerminateProcess and taskkill /T; a dead lease holder is taken "
        "over after 60 s; orphan process groups are not terminated (declared in PREREGISTRO.md)"
        if WINDOWS
        else "POSIX: SIGKILL; power = SIGSTOP + SIGKILL of the whole tree",
        "simulatedPerson": "human.reviewer-simulated answers the waits and approves at DECISION "
        "(a declared simulation)",
        "byCondition": by_condition,
        "criteria": criteria,
    }


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H47",
            title="crash and resume (SIGKILL and power cut)",
            stem="h47-crash-resume",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=CONDITIONS,
            runner=runner,
            summarize=summarize,
        )
    )
