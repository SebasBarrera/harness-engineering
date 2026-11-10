#!/usr/bin/env python3
"""H18: an agent that never converges, stopped by the iteration cap of the agent contract
(``agentContract.maxIterations``) and by the correction budget (``runtime.verificationCorrections``).
No model is called.

The fixture provider (``suite_support.FIXTURE_AGENT``) answers every ``implement`` call with the
same failing change of the sample project (the pricing code keeps its old behaviour, the test it
adds fails) and every read-only call with an empty valid result; it logs each call with its
estimated tokens (characters / 4, as ``fault_provider.py:usage``).

Conditions (``CATALOGO.md`` H18):

* ``D*``: the direct-equivalent, **a model of the direct agent, not an agent**: a loop that sends
  the prompt of ``agentlib.build_prompt`` (with the last test output as feedback, as
  ``agentlib`` does for a correction) to the same fixture, applies its change and runs the tests,
  until they pass or the loop's own declared cap of 50 attempts (the harness sets no cap here; a
  real direct agent keeps only its money and time caps, ``agentlib.py:39-40``);
* ``H``: ``harness init`` as written (``maxIterations`` 5, ``verificationCorrections`` 2);
* ``H-max1``, ``H-max3``, ``H-max5``, ``H-max6``: ``agentContract.maxIterations`` 1, 3, 5, 6;
* ``H-contract-off``: ``agentContract.mode: off``;
* ``H-max7-rejected``: ``maxIterations: 7`` (above ``MAX_ITERATIONS_CEILING``), only
  ``harness config validate`` (the criterion says the value is rejected);
* extension, declared here and not in the catalogue: ``H-max5-corrections10`` and
  ``H-contract-off-corrections10`` raise ``runtime.verificationCorrections`` to its maximum (10),
  so the iteration cap, not the correction budget, is the first limit reached.

Measures per condition: agent calls by kind (fixture log), implement calls, estimated tokens,
seconds of the harness commands, final status and phase, the events ``agent.iteration``,
``agent.iterations.exhausted``, ``correction.authorized`` and ``correction.exhausted``, the
finding rules, whether the ``agent-iteration-cap`` evidence exists (its blob on disk and
``harness verify``), and whether a response with ``finalStatus: ITERATIONS_EXHAUSTED`` was
recorded.

Usage:
  python evaluation/deterministic/iteration_cap.py --out DIR [--work DIR] [--project-venv DIR]
      [--harness PATH] [--only-conditions D*,H,...] [--reps 1] [--wheel WHEEL]

Writes ``DIR/iteration-cap.jsonl``, ``DIR/iteration-cap-summary.json`` and
``DIR/environment.json``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    Harness,
    add_common_arguments,
    environment,
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
    find_blob,
    findings,
    portable_env,
    read_log,
    sample_change,
    sample_project,
    sample_task,
    start_run,
    status_of,
    write_fixture,
)

DIRECT_CAP = 50
EXTENSIONS = ("H-max5-corrections10", "H-contract-off-corrections10")
CONDITIONS = (
    "D*",
    "H",
    "H-max1",
    "H-max3",
    "H-max5",
    "H-max6",
    "H-contract-off",
    "H-max7-rejected",
    *EXTENSIONS,
)


def mutation(condition: str) -> Callable[[dict[str, Any]], None]:
    def mutate(config: dict[str, Any]) -> None:
        contract = config.setdefault("agentContract", {})
        runtime = config.setdefault("runtime", {})
        if condition.startswith("H-max"):
            contract["maxIterations"] = int(condition.removeprefix("H-max").split("-")[0])
        if condition.startswith("H-contract-off"):
            contract["mode"] = "off"
        if condition.endswith("corrections10"):
            runtime["verificationCorrections"] = 10

    return mutate


def direct(run_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    """D*: retry the same fixture until the tests pass or the declared cap of 50 attempts."""
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    sample_project(ws)
    log = Path(tempfile.mkdtemp(prefix="h18-direct-")) / "calls.jsonl"
    command = write_fixture(run_dir, {"log": str(log), "change": sample_change(correct=False)})
    python = str(bin_dir(args.project_venv) / "python")
    task = sample_task("task_h18_discount")
    feedback: str | None = None
    attempts: list[dict[str, Any]] = []
    started = time.monotonic()
    passed = False
    for attempt in range(1, DIRECT_CAP + 1):
        prompt = direct_prompt(task, feedback)
        request = {"kind": "implement", "prompt": prompt, "task": task}
        answer = subprocess.run(
            [python, *command[1:]],
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
        attempts.append(
            {
                "attempt": attempt,
                "promptTokens": math.ceil(len(prompt) / 4),
                "answerTokens": math.ceil(len(answer.stdout) / 4),
                "testsExitCode": tests.returncode,
            }
        )
        if tests.returncode == 0:
            passed = True
            break
        feedback = tests.stdout[-1500:]
    return {
        "calls": len(attempts),
        "implementCalls": len(attempts),
        "callsByKind": {"implement": len(attempts)},
        "estimatedTokens": sum(a["promptTokens"] + a["answerTokens"] for a in attempts),
        "seconds": round(time.monotonic() - started, 2),
        "finalState": "tests pass" if passed else f"external cap of {DIRECT_CAP} attempts reached",
        "stoppedBy": "tests pass" if passed else "external cap (declared, not a harness limit)",
        "fixtureCallsLogged": len(read_log(log)),
        "attempts": attempts[:3] + (attempts[-1:] if len(attempts) > 3 else []),
    }


def governed(condition: str, run_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    sample_project(ws)
    state_root = run_dir / "harness-state"
    h = Harness(args.harness, portable_env([bin_dir(args.project_venv)], state_root))
    h.run(ws, "init", "--path", ".", label="init")
    commit_init(ws)
    log = Path(tempfile.mkdtemp(prefix=f"h18-{condition}-")) / "calls.jsonl"
    command = write_fixture(run_dir, {"log": str(log), "change": sample_change(correct=False)})
    if condition == "H-max7-rejected":
        config, deviations = configure(
            ws, command, mutate=lambda c: c.setdefault("agentContract", {}).update(maxIterations=7)
        )
        code, payload, _ = h.run(ws, "config", "validate", "--path", ".", label="config validate")
        return {
            "deviations": deviations,
            "agentContract": config.get("agentContract"),
            "configValidateExitCode": code,
            "rejected": code != 0,
            "configValidateError": (
                str(payload.get("error")) if isinstance(payload, dict) else str(payload)
            )[:400],
            "calls": 0,
        }
    config, deviations = configure(ws, command, mutate=mutation(condition))
    database = state_database(h, ws)
    started = start_run(h, ws, run_dir, sample_task("task_h18_discount"))
    run_id = started["runId"]
    status = status_of(h, ws, run_id)
    execution = status.get("execution") or {}
    run_events = events(database, run_id)
    types = [e["type"] for e in run_events]
    exhausted = [e["payload"] for e in run_events if e["type"] == "agent.iterations.exhausted"]
    calls = read_log(log)
    by_kind: dict[str, int] = {}
    for call in calls:
        key = call["kind"] + (":panel" if call.get("panel") else "")
        by_kind[key] = by_kind.get(key, 0) + 1
    verify_code, _, _ = h.run(ws, "verify", "--path", ".", "--run", run_id, label="verify")
    _, contract_calls, _ = h.run(
        ws, "agent-contract", "calls", "--path", ".", "--run", run_id, label="agent-contract calls"
    )
    evidence = [str(item.get("evidenceRef") or "") for item in exhausted]
    blobs = [
        find_blob(run_dir, ref) is not None
        for ref in evidence
        if ref.startswith("artifact://sha256/")
    ]
    phase_summaries = [
        f"{e['payload'].get('phaseId')}:{e['payload'].get('status')}:{str(e['payload'].get('summary'))[:300]}"
        for e in run_events
        if e["type"] == "phase.completed" and e["payload"].get("status") not in {"PASSED", None}
    ]
    run_seconds = sum(
        s["seconds"] for s in h.steps if s["step"] in {"run start", "run continue", "task clarify"}
    )
    return {
        "deviations": deviations,
        "agentContract": config.get("agentContract"),
        "verificationCorrections": (config.get("runtime") or {}).get("verificationCorrections"),
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        "finalStatus": execution.get("status"),
        "finalPhase": execution.get("currentPhase"),
        "terminalReason": execution.get("terminalReason"),
        # What stopped the run when no cap event says it: the last events of the run.
        "lastEvents": [
            f"{e['type']}:{str(e['payload'].get('summary') or e['payload'].get('reason') or '')[:200]}"
            for e in run_events[-6:]
        ],
        "calls": len(calls),
        "implementCalls": sum(1 for c in calls if c["kind"] == "implement"),
        "callsByKind": dict(sorted(by_kind.items())),
        "estimatedTokens": sum(
            c["estimatedInputTokens"] + c["estimatedOutputTokens"] for c in calls
        ),
        "seconds": round(run_seconds, 2),
        "iterationEvents": {
            kind: sum(
                1
                for e in run_events
                if e["type"] == "agent.iteration" and e["payload"].get("callKind") == kind
            )
            for kind in sorted(
                {e["payload"].get("callKind") for e in run_events if e["type"] == "agent.iteration"}
            )
        },
        "iterationsExhausted": [
            {k: item.get(k) for k in ("callKind", "used", "max", "evidenceRef")}
            for item in exhausted
        ],
        "iterationCapEvidenceKept": bool(blobs) and all(blobs),
        "iterationsExhaustedResponseRecorded": "ITERATIONS_EXHAUSTED" in json.dumps(contract_calls),
        "correctionsAuthorized": types.count("correction.authorized"),
        "correctionExhausted": "correction.exhausted" in types,
        "findingRules": findings(h, ws, run_id),
        "blockedPhases": phase_summaries,
        "verifyExitCode": verify_code,
        "steps": list(h.steps),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, Any] = {}
    for record in records:
        condition = record["condition"]
        cap = (record.get("agentContract") or {}).get("maxIterations")
        enabled = (record.get("agentContract") or {}).get("mode", "enforce") != "off"
        item = by_condition.setdefault(condition, {"runs": []})
        item["extension"] = condition in EXTENSIONS
        item["runs"].append(
            {
                k: record.get(k)
                for k in (
                    "rep",
                    "calls",
                    "implementCalls",
                    "callsByKind",
                    "estimatedTokens",
                    "seconds",
                    "finalStatus",
                    "finalPhase",
                    "finalState",
                    "stoppedBy",
                    "correctionsAuthorized",
                    "correctionExhausted",
                    "iterationsExhausted",
                    "iterationCapEvidenceKept",
                    "iterationsExhaustedResponseRecorded",
                    "configValidateExitCode",
                    "rejected",
                    "notEvaluated",
                )
                if k in record
            }
        )
        if (
            condition.startswith("H")
            and condition != "H-max7-rejected"
            and "notEvaluated" not in record
        ):
            effective = cap or 5
            item["maxIterations"] = effective if enabled else None
            item["implementCallsWithinCap"] = (
                record.get("implementCalls", 0) <= effective if enabled else None
            )
    return {
        "estimate": "tokens = ceil(characters / 4) of what the fixture received and returned",
        "directEquivalent": "D* is a model of a direct agent (a loop with a declared external cap "
        f"of {DIRECT_CAP} attempts), not an agent",
        "byCondition": by_condition,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--only-conditions", default="")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "iteration-cap.jsonl"
    if not args.summarize_only:
        wanted = args.only_conditions.split(",") if args.only_conditions else list(CONDITIONS)
        unknown = sorted(set(wanted) - set(CONDITIONS))
        if unknown:
            parser.error(f"unknown conditions: {', '.join(unknown)}")
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "H18 iteration cap (fixture provider)",
                    "conditions": wanted,
                    "extensions": [c for c in wanted if c in EXTENSIONS],
                    "directCap": DIRECT_CAP,
                    "reps": args.reps,
                    "projectVenv": str(args.project_venv),
                },
            ),
        )
        records = []
        for rep in range(1, args.reps + 1):
            for condition in wanted:
                safe = condition.replace("*", "star")
                run_dir = args.work / f"h18-{safe}-r{rep}-{stamp()}"
                run_dir.mkdir(parents=True)
                result = (
                    direct(run_dir, args)
                    if condition == "D*"
                    else governed(condition, run_dir, args)
                )
                record = {"condition": condition, "rep": rep, **result}
                records.append(record)
                print(
                    condition,
                    rep,
                    record.get("calls"),
                    record.get("implementCalls"),
                    record.get("finalStatus") or record.get("finalState"),
                    record.get("finalPhase"),
                    [i.get("callKind") for i in record.get("iterationsExhausted", [])],
                    flush=True,
                )
        write_jsonl(records_path, records)
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_json(args.out / "iteration-cap-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
