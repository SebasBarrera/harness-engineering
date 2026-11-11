#!/usr/bin/env python3
"""H50: a degraded provider (retries, fallback and failing closed), ``CATALOGO.md`` §2.9 and
``PREREGISTRO.md`` (third freeze). No model is called.

The primary provider (``robust_agent.py``) fails in one of eight declared ways and answers well
afterwards: M1 ``implement`` transient once (``Error: 429 rate limit``, exit 1); M2 ``implement``
transient always; M3 ``acceptance`` transient once (``overloaded``); M4 ``acceptance`` garbage
once (not JSON, exit 0); M5 ``acceptance`` garbage always; M6 ``acceptance`` empty once; M7
``implement`` garbage once; M8 ``implement`` sleeps 60 s once under
``runtime.commandTimeoutSeconds: 20``. (M3-M6 fail in ``acceptance``, not ``plan``: the small
task calls no ``plan``; the amendment of 2026-11-10 in PREREGISTRO.md.)

Conditions: ``R0`` (``runtime.providerRetries: 0``), ``R3`` (3, as ``harness init``) and
``R3-fallback`` (R3 and ``runtime.contractRetry.fallbackProvider: backup``, a second, healthy
fixture provider). In all three ``providerRetryDelaySeconds: 1`` (init writes 60; declared).

Every record says whether the run ended in a defined state (not RUNNING, PENDING or INTERRUPTED,
the last run command exited 4 or 6, the owned files are the baseline's or the whole change's)
with a valid chain (``harness verify --run`` exit 0) - the criterion - and compares the outcome,
the retries and the contract retries with the expectation read in the code (reported, not the
criterion).

Usage:
  python evaluation/deterministic/h50_degraded_provider.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only M1,...]
      [--conditions R0,R3,R3-fallback] [--reps 1] [--summarize-only] [--keep-work]

Writes ``DIR/h50-degraded-provider.jsonl``, ``DIR/h50-degraded-provider-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conditions  # noqa: E402
from resource_support import (  # noqa: E402
    cleanup,
    discount_task,
    main,
    proportion,
    read_log,
    run_state,
    run_task,
    saved_config,
    stamp_dir,
)
from robustness_support import (  # noqa: E402
    ROBUST_AGENT,
    all_events,
    approvable_spec,
    expected_files,
    governed_robust,
)
from suite_support import SAMPLE_CODE, SAMPLE_TEST  # noqa: E402

CONDITIONS = ["R0", "R3", "R3-fallback"]
# Expectation read in the code (PREREGISTRO.md, H50): outcome, provider retries, contract
# retries, by condition.
CASES: list[dict[str, Any]] = [
    {
        "id": "M1",
        "fault": {
            "kind": "implement",
            "mode": "transient",
            "times": 1,
            "message": "Error: 429 rate limit",
        },
        "expect": {
            "R0": ("blocked", 0, 0),
            "R3": ("decision", 1, 0),
            "R3-fallback": ("decision", 1, 0),
        },
    },
    {
        "id": "M2",
        "fault": {
            "kind": "implement",
            "mode": "transient",
            "times": -1,
            "message": "Error: 429 rate limit",
        },
        "expect": {
            "R0": ("blocked", 0, 0),
            "R3": ("blocked", 3, 0),
            "R3-fallback": ("blocked", 3, 0),
        },
    },
    {
        "id": "M3",
        "fault": {"kind": "acceptance", "mode": "transient", "times": 1, "message": "overloaded"},
        "expect": {
            "R0": ("blocked", 0, 0),
            "R3": ("decision", 1, 0),
            "R3-fallback": ("decision", 1, 0),
        },
    },
    {
        "id": "M4",
        "fault": {"kind": "acceptance", "mode": "garbage", "times": 1},
        "expect": {
            "R0": ("decision", 0, 1),
            "R3": ("decision", 0, 1),
            "R3-fallback": ("decision", 0, 1),
        },
    },
    {
        "id": "M5",
        "fault": {"kind": "acceptance", "mode": "garbage", "times": -1},
        "expect": {
            "R0": ("blocked", 0, 1),
            "R3": ("blocked", 0, 1),
            "R3-fallback": ("decision", 0, 1),
        },
    },
    {
        "id": "M6",
        "fault": {"kind": "acceptance", "mode": "empty", "times": 1},
        "expect": {
            "R0": ("decision", 0, 1),
            "R3": ("decision", 0, 1),
            "R3-fallback": ("decision", 0, 1),
        },
    },
    {
        "id": "M7",
        "fault": {"kind": "implement", "mode": "garbage", "times": 1},
        "expect": {
            "R0": ("blocked", 0, 0),
            "R3": ("blocked", 0, 0),
            "R3-fallback": ("blocked", 0, 0),
        },
    },
    {
        "id": "M8",
        "fault": {"kind": "implement", "mode": "sleep", "times": 1, "seconds": 60},
        "timeout": 20,
        "expect": {
            "R0": ("blocked", 0, 0),
            "R3": ("blocked", 0, 0),
            "R3-fallback": ("blocked", 0, 0),
        },
    },
]
UNDEFINED_STATUSES = {"RUNNING", "PENDING", "INTERRUPTED", None}


def conditions_of(case: dict[str, Any]) -> list[str]:
    del case
    return list(CONDITIONS)


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h50", case["id"], condition)
    spec = {**approvable_spec(), "name": "primary", "faults": [case["fault"]]}
    backup_spec = run_dir / "backup-spec.json"
    backup_command = ["python", str(ROBUST_AGENT), "--spec", str(backup_spec)]

    def mutate(config: dict[str, Any]) -> None:
        runtime = config.setdefault("runtime", {})
        runtime["providerRetries"] = 0 if condition == "R0" else 3
        runtime["providerRetryDelaySeconds"] = 1
        if case.get("timeout"):
            runtime["commandTimeoutSeconds"] = case["timeout"]
        if condition == "R3-fallback":
            config["agentProviders"]["backup"] = {
                "kind": "command",
                "command": backup_command,
                "model": "deterministic-fixture",
            }
            conditions.grant_provider_command(config, backup_command, core=False)
            retry = runtime.setdefault("contractRetry", {"mode": "once"})
            retry["fallbackProvider"] = "backup"

    workspace = governed_robust(run_dir, args, spec, mutate=mutate)
    backup_spec.write_text(
        json.dumps({**approvable_spec(), "name": "backup", "log": str(workspace.log)}),
        encoding="utf-8",
    )
    saved_config(args, f"{condition}-{case['id']}" if case.get("timeout") else condition, workspace)
    task = discount_task(f"task_h50_{case['id'].lower()}")
    result = run_task(workspace, task)
    run_id = result["runId"]
    # The last run command (run start / run continue): the person's own commands (acceptance
    # decide, plan decide) are not run commands (PREREGISTRO.md, H50, "estado definido").
    run_codes = [
        step.get("exitCode")
        for step in workspace.h.steps
        if str(step.get("step", "")).startswith(("run start", "run continue"))
    ]
    last_run_code = run_codes[-1] if run_codes else None
    record: dict[str, Any] = {
        "fault": case["fault"],
        "runExitCode": result["runExitCode"],
        "lastRunCommandExitCode": last_run_code,
        "waits": result["waits"],
        "deviations": workspace.deviations
        + ["runtime.providerRetryDelaySeconds 1 (init writes 60)"],
    }
    record.update(run_state(workspace, run_id) if run_id else {})
    events = [e for e in all_events(workspace.database) if e["runId"] == run_id]
    retried = [e["payload"] for e in events if e["type"] == "agent.invocation.retried"]
    contract = [e["payload"] for e in events if e["type"] == "agent.call.contract-retry"]
    routing = [e["payload"] for e in events if e["type"] == "agent.routing.fallback"]
    calls = read_log(workspace.log)
    verify_code = (
        workspace.h.run(workspace.ws, "verify", "--path", ".", "--run", run_id, label="verify")[0]
        if run_id
        else None
    )
    baseline = {"src/sample/pricing.py": SAMPLE_CODE, "tests/test_pricing.py": SAMPLE_TEST}
    change = expected_files(task)
    current = {
        path: (workspace.ws / path).read_text(encoding="utf-8")
        if (workspace.ws / path).exists()
        else None
        for path in baseline
    }
    files = "baseline" if current == baseline else "change" if current == change else "partial"
    defined = (
        bool(run_id)
        and record.get("runStatus") not in UNDEFINED_STATUSES
        and last_run_code in (4, 6)
        and files in {"baseline", "change"}
    )
    outcome = (
        "decision"
        if record.get("phase") == "DECISION" and record.get("gate") == "PASSED"
        else "blocked"
    )
    expected = case["expect"][condition]
    retry_providers = sorted({str(c.get("retryProvider")) for c in contract})
    expected_retry_provider = (
        ["backup"]
        if condition == "R3-fallback" and expected[2]
        else ["fixture"]
        if expected[2]
        else []
    )
    record.update(
        {
            "providerRetries": len(retried),
            "retryPatterns": sorted({str(r.get("matchedPattern")) for r in retried}),
            "contractRetries": len(contract),
            "contractRetryProviders": retry_providers,
            "routingFallbackEvents": len(routing),
            "callsByProvider": {
                name: [c["kind"] for c in calls if c.get("provider") == name]
                for name in sorted({str(c.get("provider")) for c in calls})
            },
            "faultsServed": [
                f"{c['provider']}:{c['kind']}:{c['fault']}" for c in calls if c.get("fault")
            ],
            "verifyExitCode": verify_code,
            "ownedFiles": files,
            "definedState": defined,
            "chainValid": verify_code == 0,
            "definedWithValidChain": defined and verify_code == 0,
            "outcome": outcome,
            "expected": {
                "outcome": expected[0],
                "providerRetries": expected[1],
                "contractRetries": expected[2],
                "contractRetryProviders": expected_retry_provider,
            },
        }
    )
    record["matchesExpectation"] = (
        outcome == expected[0]
        and len(retried) == expected[1]
        and len(contract) == expected[2]
        and retry_providers == expected_retry_provider
    )
    record["brief"] = (
        f"{outcome} retries={len(retried)} contract={len(contract)} "
        f"defined={defined} chain={verify_code == 0}"
    )
    cleanup(run_dir, args.keep_work)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r]
    by_condition = {}
    criteria: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in ok if r["condition"] == condition]
        rep1 = [r for r in rows if r["rep"] == 1]
        good = sum(1 for r in rep1 if r.get("definedWithValidChain"))
        by_condition[condition] = {
            "records": len(rows),
            "definedWithValidChain": proportion(
                sum(1 for r in rows if r.get("definedWithValidChain")), len(rows)
            ),
            "matchesExpectation": proportion(
                sum(1 for r in rows if r.get("matchesExpectation")), len(rows)
            ),
            "byMode": {
                r["case"]: {
                    k: r.get(k)
                    for k in (
                        "outcome",
                        "expected",
                        "matchesExpectation",
                        "providerRetries",
                        "contractRetries",
                        "contractRetryProviders",
                        "routingFallbackEvents",
                        "runStatus",
                        "phase",
                        "gate",
                        "runExitCode",
                        "ownedFiles",
                        "definedState",
                        "chainValid",
                        "blockedPhases",
                    )
                }
                for r in rep1
            },
        }
        criteria[f"{condition}8of8"] = {
            "value": f"{good}/{len(CASES)} in a defined state with a valid chain",
            "met": good == len(CASES) and len(rep1) == len(CASES),
        }
    criteria["note"] = (
        "Pre-registered in PREREGISTRO.md (H50); evaluated as is. The expectation of outcome and "
        "retries is read in the code and reported, not the criterion."
    )
    return {
        "suite": "H50",
        "title": "degraded provider: retries, fallback and failing closed",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION "
        "(a declared simulation)",
        "byCondition": by_condition,
        "criteria": criteria,
    }


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H50",
            title="degraded provider: retries, fallback and failing closed",
            stem="h50-degraded-provider",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=CONDITIONS,
            runner=runner,
            summarize=summarize,
        )
    )
