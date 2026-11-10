#!/usr/bin/env python3
"""Dry run of every 2.0.0 condition and runner, with zero model calls.

Runs each condition once with ``fake_claude.py`` in place of Claude Code (and the governed
conditions once more with the harness's own ``simulated`` provider), the fault paths of the
simulated person (REQUEST_CHANGES, REJECT and the measure of a quarantined change), one session of
block D and the embedded mode, then checks what each must show. The records go to
``<out>/dry-run.jsonl``, ``longitudinal-dry.jsonl`` and ``embedded-dry.jsonl``, the checks to
``<out>/dry-run-checks.json``. Exit code 1 when a required check fails; the checks marked
``observed`` document a behaviour of the harness under test and do not fail the dry run.

Usage: python dry_run.py --work DIR --cache DIR --out DIR [--core-reference-src DIR] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MODEL = "claude-haiku-4-5-20251001"
# name: (runner, arguments, checks); a check is (description, function of the record, required)
Check = tuple[str, Any, bool]


def hidden_ok(r: dict[str, Any]) -> bool:
    h = r["measures"]["hidden"]
    return bool(h["tests"]) and h["passed"] == h["tests"]


def outcome(r: dict[str, Any]) -> str:
    return str((r.get("harness") or {}).get("outcome"))


def no_model_call(r: dict[str, Any]) -> bool:
    """Every call of the run went to the fake CLI: zero cost and zero tokens."""
    groups = [r.get("agent") or {}, r.get("productOwnerCalls") or {}, r.get("hostSession") or {}]
    return all((g.get("costUsd") or 0) == 0 and (g.get("outputTokens") or 0) == 0 for g in groups)


def kinds(r: dict[str, Any]) -> set[str]:
    return set((r.get("agentByKind") or {}).keys())


def waits(r: dict[str, Any]) -> list[dict[str, Any]]:
    return list((r.get("harness") or {}).get("waits") or [])


def provider_granted(r: dict[str, Any], key: str) -> bool:
    """Wave 9 (#87): exactly the provider's command line is granted under ``key`` and
    ``harness config validate`` warns about no provider command."""
    command = r["projectConfig"]["agentProviders"]["claude"]["command"]
    rule = {"capability": "process.execute", "scope": [" ".join(command)]}
    check = r["harness"]["grantCheck"]
    return rule in (check.get(key) or []) and check["providerWarnings"] == []


def inbox_answered(r: dict[str, Any]) -> bool:
    """Every wait before DECISION the person answered was read from the inbox (#73)."""
    acted = [w for w in waits(r) if w.get("action") and w.get("wait") != "clarification"]
    return bool(acted) and all(w.get("source") == "inbox" for w in acted)


CASES: dict[str, tuple[str, list[str], list[Check]]] = {
    "direct": (
        "run_eval",
        ["--scenario", "greenfield", "--condition", "direct"],
        [
            ("hidden tests pass on the fake's reference change", hidden_ok, True),
            (
                "one call, no harness",
                lambda r: r["agent"]["calls"] == 1 and "harness" not in r,
                True,
            ),
        ],
    ),
    "harness-core": (
        "run_eval",
        ["--scenario", "greenfield", "--condition", "harness-core"],
        [
            ("approved", lambda r: outcome(r) == "approved", True),
            (
                "the configuration has only 1.0.0 keys",
                lambda r: r["harness"]["coreCheck"]["extraKeys"] == [],
                True,
            ),
            ("only implement calls (no read-only kind)", lambda r: kinds(r) == {"implement"}, True),
            (
                "eight 0.9.0 trace relations present",
                lambda r: r["harness"]["trace"]["present"] == 8,
                True,
            ),
            (
                "configuration digest equal to 1.0.0's (with --core-reference-src)",
                lambda r: r["harness"]["coreCheck"].get("digestEqual", True),
                True,
            ),
            (
                "the provider command is granted in capabilities.grants (the 1.0.0 key)",
                lambda r: (
                    provider_granted(r, "grants") and r["harness"]["grantCheck"]["extend"] is None
                ),
                True,
            ),
        ],
    ),
    "harness": (
        "run_eval",
        ["--scenario", "greenfield", "--condition", "harness"],
        [
            ("approved", lambda r: outcome(r) == "approved", True),
            (
                "read-only call kinds answered by the adapter",
                lambda r: {"acceptance", "architecture"} <= kinds(r),
                True,
            ),
            (
                "every model fixed to the cell's",
                lambda r: (
                    set(
                        m
                        for k in r["harness"]["state"]["measures"]["invocationsByKind"].values()
                        for m in k["models"]
                    )
                    == {MODEL}
                ),
                True,
            ),
            (
                "eight 0.9.0 trace relations and every 2.0.0 relation present",
                lambda r: (
                    r["harness"]["trace"]["present"] == 8
                    and r["harness"]["trace"]["present2"] == r["harness"]["trace"]["requiredCount2"]
                ),
                True,
            ),
            (
                "harness verify passes on the record",
                lambda r: r["harness"]["state"]["verifyExit"] == 0,
                True,
            ),
            (
                "the saved configuration routes every call to the cell's model (fixed)",
                lambda r: r["projectConfig"]["agentRouting"]["mode"] == "fixed",
                True,
            ),
            (
                "the provider command is granted in capabilities.extend, nothing wider (#87)",
                lambda r: (
                    provider_granted(r, "extend")
                    and len(r["projectConfig"]["capabilities"]["extend"]) == 1
                ),
                True,
            ),
            ("every answered wait read from the inbox (#73)", inbox_answered, True),
        ],
    ),
    "harness-anchored": (
        "run_eval",
        ["--scenario", "brownfield", "--condition", "harness-anchored"],
        [
            ("approved", lambda r: outcome(r) == "approved", True),
            (
                "init's anchored routing with the cell's model as the anchor",
                lambda r: (
                    r["projectConfig"]["agentRouting"]["mode"] == "anchored"
                    and r["projectConfig"]["agentRouting"]["anchorModel"] == MODEL
                ),
                True,
            ),
            (
                "every routed call anchored at the cell's model (#85)",
                lambda r: (
                    bool(r["harness"]["state"]["measures"]["routingDecisions"])
                    and all(
                        d.get("mode") == "anchored" and d.get("anchor") == MODEL
                        for d in r["harness"]["state"]["measures"]["routingDecisions"]
                    )
                ),
                True,
            ),
            (
                "invoked with Haiku, every call runs on Haiku (the ceiling)",
                lambda r: (
                    set(
                        m
                        for k in r["harness"]["state"]["measures"]["invocationsByKind"].values()
                        for m in k["models"]
                    )
                    == {MODEL}
                ),
                True,
            ),
            (
                "plan approval of a risky task answered, plan decide --no-continue exits 0 (#83)",
                lambda r: any(
                    w.get("wait") == "plan-approval" and str(w.get("action", "")).endswith("-> 0")
                    for w in waits(r)
                ),
                True,
            ),
            ("every answered wait read from the inbox (#73)", inbox_answered, True),
        ],
    ),
    "casual-harness": (
        "run_eval",
        ["--scenario", "brownfield", "--condition", "harness", "--prompt", "casual"],
        [
            ("approved", lambda r: outcome(r) == "approved", True),
            (
                "criteria asked (C0) and answered by the product owner",
                lambda r: any(
                    "C0" in c["questions"] and c.get("actor") == "human.product-owner-simulated"
                    for c in r["harness"]["clarification"]
                ),
                True,
            ),
        ],
    ),
    "casual-core": (
        "run_eval",
        ["--scenario", "brownfield", "--condition", "harness-core", "--prompt", "casual"],
        [
            (
                "1.0.0 configuration refuses a task without criteria",
                lambda r: outcome(r) == "task-refused-exit-2",
                True,
            ),
        ],
    ),
    "review-fail": (
        "run_eval",
        ["--scenario", "greenfield", "--condition", "harness", "--fake-mode", "review-fail"],
        [
            (
                "two REQUEST_CHANGES, then REJECT",
                lambda r: (
                    [d["decision"] for d in r["harness"]["decisions"]]
                    == ["REQUEST_CHANGES", "REQUEST_CHANGES", "REJECT"]
                ),
                True,
            ),
            (
                "the rejected run stays closed: run continue exits 6 (#83)",
                lambda r: (
                    outcome(r) == "rejected"
                    and r["harness"]["decisions"][-1]["continueAfterReject"] == 6
                    and r["harness"]["finalStatus"] == "FAILED"
                ),
                True,
            ),
            (
                "rejected change measured on the quarantine copy",
                lambda r: (
                    r["measures"].get("measuredOn") == "quarantine-copy"
                    and r["measures"]["patchApply"]["exitCode"] == 0
                ),
                True,
            ),
            (
                "panel finding grouped by domain",
                lambda r: (
                    "quality" in r["harness"]["state"]["measures"]["reviewFindings"]["byDomain"]
                ),
                True,
            ),
        ],
    ),
    "malformed-acceptance": (
        "run_eval",
        [
            "--scenario",
            "greenfield",
            "--condition",
            "harness",
            "--fake-mode",
            "malformed-acceptance",
        ],
        [
            (
                "approved: the harness sent the broken acceptance answer once more (#80)",
                lambda r: outcome(r) == "approved",
                True,
            ),
            (
                "the retry is the harness's (agent.call.contract-retry), not the person's",
                lambda r: (
                    r["harness"]["state"]["measures"]["eventTypes"].get(
                        "agent.call.contract-retry", 0
                    )
                    == 1
                    and "failed-call" not in [w.get("wait") for w in waits(r)]
                ),
                True,
            ),
        ],
    ),
    "simulated-provider": (
        "run_eval",
        ["--scenario", "greenfield", "--condition", "harness", "--provider", "simulated"],
        [
            ("no call to the adapter", lambda r: r["agent"]["calls"] == 0, True),
            (
                "the simulated provider changes nothing: the run stops",
                lambda r: outcome(r) == "stopped",
                True,
            ),
        ],
    ),
    "longitudinal": (
        "run_session",
        ["--condition", "harness"],
        [
            (
                "session recorded with five increments",
                lambda r: len([s for s in r["steps"] if s["kind"] == "main"]) == 5,
                True,
            ),
            (
                "first increment approved",
                lambda r: r["steps"][0]["harness"]["outcome"] == "approved",
                True,
            ),
        ],
    ),
    "embedded-no-acceptance": (
        "run_embedded",
        ["--scenario", "greenfield", "--variant", "no-acceptance"],
        [
            ("the host session created the task", lambda r: r["taskCreatedBySession"], True),
            ("approved", lambda r: outcome(r) == "approved", True),
            (
                "decisions by the simulated reviewer only",
                lambda r: (
                    set(r["harness"]["state"]["measures"]["decisionActors"])
                    == {"human.reviewer-simulated"}
                ),
                True,
            ),
        ],
    ),
    "embedded-init": (
        "run_embedded",
        ["--scenario", "greenfield", "--variant", "init"],
        [
            ("the host session created the task", lambda r: r["taskCreatedBySession"], True),
            (
                "observed: the init configuration (session provider + frozen acceptance tests + stop "
                "the line) is approved since wave 9 (#81)",
                lambda r: outcome(r) == "approved",
                False,
            ),
        ],
    ),
}


def run(
    name: str, runner: str, extra: list[str], args: argparse.Namespace, rep: int
) -> dict[str, Any] | None:
    out = (
        args.out
        / {
            "run_eval": "dry-run.jsonl",
            "run_session": "longitudinal-dry.jsonl",
            "run_embedded": "embedded-dry.jsonl",
        }[runner]
    )
    script = {
        "run_eval": HERE / "run_eval.py",
        "run_session": HERE / "longitudinal" / "run_session.py",
        "run_embedded": HERE / "run_embedded.py",
    }[runner]
    command = [
        sys.executable,
        str(script),
        *extra,
        "--model",
        MODEL,
        "--rep",
        str(rep),
        "--work",
        str(args.work / name),
        "--out",
        str(out),
        "--dry-run",
    ]
    if runner != "run_session":
        command += ["--cache", str(args.cache)]
    if runner == "run_eval" and args.core_reference_src:
        command += ["--core-reference-src", str(args.core_reference_src)]
    before = len(out.read_text().splitlines()) if out.exists() else 0
    proc = subprocess.run(command, capture_output=True, text=True)
    print(f"{name}: exit {proc.returncode} {proc.stdout.strip()[-300:]}", flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], flush=True)
        return None
    lines = out.read_text().splitlines()
    return json.loads(lines[-1]) if len(lines) > before else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--core-reference-src", type=Path, default=None)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--rep", type=int, default=1)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    failed = 0
    for name, (runner, extra, checks) in CASES.items():
        if args.only and name not in args.only:
            continue
        record = run(name, runner, extra, args, args.rep)
        rows = []
        for description, check, required in checks:
            try:
                ok = bool(record is not None and check(record))
            except (KeyError, TypeError, IndexError) as error:
                ok = False
                description += f" ({type(error).__name__}: {error})"
            rows.append({"check": description, "ok": ok, "required": required})
            failed += int(required and not ok)
        rows.append(
            {
                "check": "no model call",
                "ok": record is not None and no_model_call(record),
                "required": True,
            }
        )
        failed += int(not rows[-1]["ok"])
        results[name] = {
            "recorded": record is not None,
            "checks": rows,
            "outcome": outcome(record) if record else None,
        }
        for row in rows:
            print(
                f"  [{'ok' if row['ok'] else 'FAIL' if row['required'] else 'no'}] {row['check']}",
                flush=True,
            )
    path = args.out / "dry-run-checks.json"
    path.write_text(
        json.dumps({"failedRequired": failed, "cases": results}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {path}; failed required checks: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
