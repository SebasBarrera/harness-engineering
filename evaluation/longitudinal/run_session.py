#!/usr/bin/env python3
"""One longitudinal session: the same five increments of a small library, built with one-line
casual prompts (baseline), with the structured tasks sent directly to the agent (structured), or
with the structured tasks through the harness (harness). The structured condition receives exactly
the prompt that the harness adapter builds from the task, so it isolates the effect of the harness
from the effect of structuring the task.

After every increment an external oracle (the hidden tests of the parts delivered so far) plays the
user who notices defects: if checks fail, a fix request with the bug report of each failing check
(``bug_reports.yaml``) is sent, in the condition's own style, and the oracle runs again, for at most
two rounds. The same reports reach both conditions. Usage records, oracle results and final quality
measures are appended as one JSON line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import agentlib  # noqa: E402
import run_eval  # noqa: E402
from agentlib import build_prompt, call_records, load_calls, run_claude  # noqa: E402
from measure import _bandit, _ruff  # noqa: E402
from product_owner import ProductOwner  # noqa: E402
from run_eval import git, run_harness  # noqa: E402

INCREMENTS = yaml.safe_load((HERE / "increments.yaml").read_text(encoding="utf-8"))
HIDDEN = HERE / "hidden" / "test_hidden_inventory.py"
BUG_REPORTS = yaml.safe_load((HERE / "bug_reports.yaml").read_text(encoding="utf-8"))
MAX_FIX_ROUNDS = 2


def prepare(workspace: Path, site_packages: Path) -> None:
    workspace.mkdir(parents=True)
    for item in (HERE / "fixture").iterdir():
        shutil.copy2(
            item, workspace / (".gitignore" if item.name == "gitignore.template" else item.name)
        )
    (site_packages / "zz_eval_workspace.pth").write_text(
        str(workspace / "src") + "\n", encoding="utf-8"
    )
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "baseline")


def oracle(workspace: Path, scratch: Path, label: str) -> dict[str, Any]:
    target = workspace / "_hidden_eval"
    target.mkdir(exist_ok=True)
    shutil.copy2(HIDDEN, target)
    junit = scratch / f"oracle-{label}.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            str(target),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=600,
    )
    shutil.rmtree(target)
    cases: dict[str, bool] = {}
    messages: dict[str, str] = {}
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            problems = [child for child in case if child.tag in {"failure", "error"}]
            cases[case.get("name", "")] = not problems
            if problems:
                text = (problems[0].get("message") or "").strip().splitlines()
                messages[re.sub(r"\[.*\]$", "", case.get("name", ""))] = (text[0] if text else "")[
                    :160
                ]
    if not cases:  # collection failed: every check counts as failing
        cases = {"collection": False}
    by_part: dict[str, list[int]] = {}
    for name, ok in cases.items():
        part = name[5] if re.match(r"test_[a-d]_", name) else "x"
        tally = by_part.setdefault(part, [0, 0])
        tally[0] += int(ok)
        tally[1] += 1
    return {"cases": cases, "byPart": by_part, "messages": messages}


def failing(result: dict[str, Any], parts: list[str]) -> list[str]:
    """Failing checks of the delivered parts; any failure outside named checks (for example an import
    or collection error) is reported as 'collection', as a user would notice that nothing runs."""
    names: set[str] = set()
    for n, ok in result["cases"].items():
        if ok:
            continue
        if re.match(r"test_[a-d]_", n):
            if n[5] in parts:
                names.add(re.sub(r"\[.*\]$", "", n))
        else:
            names.add("collection")
    return sorted(names)


def calls_since(calls_dir: Path, start: int) -> list[dict[str, Any]]:
    return load_calls(calls_dir)[start:]


def usage(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "calls": len(calls),
        "costUsd": round(sum(c["costUsd"] or 0 for c in calls), 6),
        "inputTokens": sum(
            (c["inputTokens"] or 0) + (c["cacheReadTokens"] or 0) + (c["cacheCreationTokens"] or 0)
            for c in calls
        ),
        "outputTokens": sum(c["outputTokens"] or 0 for c in calls),
        "turns": sum(c["numTurns"] or 0 for c in calls),
        "agentSeconds": round(sum(c["wallSeconds"] for c in calls), 3),
        "errors": sum(1 for c in calls if c["isError"]),
        # 2.0.0: the calls the harness made besides the implementation (read-only call kinds).
        "governanceCalls": sum(1 for c in calls if c.get("kind") not in (None, "implement")),
        "governanceCostUsd": round(
            sum(c["costUsd"] or 0 for c in calls if c.get("kind") not in (None, "implement")), 6
        ),
    }


def final_measures(workspace: Path, scratch: Path) -> dict[str, Any]:
    src = sorted((workspace / "src").rglob("*.py")) if (workspace / "src").exists() else []
    tests = (
        sorted((workspace / "tests").rglob("test*.py")) if (workspace / "tests").exists() else []
    )
    junit = scratch / "visible.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=600,
    )
    visible = 0
    if junit.exists():
        root = ET.parse(junit).getroot()
        visible = sum(
            int(s.get("tests", 0)) for s in ([root] if root.tag == "testsuite" else list(root))
        )
    env = {**os.environ, "COVERAGE_FILE": str(scratch / ".coverage")}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=src",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=600,
    )
    report = scratch / "coverage.json"
    subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-q", "-o", str(report)],
        cwd=workspace,
        env=env,
        capture_output=True,
    )
    coverage = json.loads(report.read_text())["totals"] if report.exists() else {}
    return {
        "sourceFiles": len(src),
        "sourceLines": sum(len(p.read_text(errors="ignore").splitlines()) for p in src),
        "testFiles": len(tests),
        "visibleTests": visible,
        "visibleSuiteExit": proc.returncode,
        "lineCoverage": round(coverage["covered_lines"] / coverage["num_statements"], 4)
        if coverage.get("num_statements")
        else None,
        "branchCoverage": round(coverage["covered_branches"] / coverage["num_branches"], 4)
        if coverage.get("num_branches")
        else None,
        "ruffFindings": _ruff(src, workspace),
        "bandit": _bandit(src, workspace),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--condition",
        choices=[
            "baseline",
            "structured",
            "harness",
            "harness-core",
            "harness-tiered",
            "harness-legacy",
        ],
        required=True,
        help="baseline: casual prompts; structured: the structured tasks without the harness; harness, "
        "harness-core, harness-tiered: the 2.0.0 conditions of run_eval.py; harness-legacy: the "
        "configuration of the 0.9.0 sessions (harness init + provider)",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--dry-run", action="store_true", help="fake_claude.py instead of Claude Code"
    )
    args = parser.parse_args()
    claude_bin = None
    if args.dry_run:
        claude_bin = str(agentlib.FAKE_CLAUDE)
        agentlib.CLAUDE_BIN = claude_bin
    else:
        agentlib.ensure_account()
    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.work / f"longitudinal-{args.condition}-{args.model}-r{args.rep}-{stamp}"
    workspace, scratch = run_dir / "ws", run_dir / "measure"
    prepare(workspace, site_packages)
    scratch.mkdir(parents=True)
    # The run registry of the governed conditions lives in the session directory (one project for
    # the five increments, so project setup, architecture and lessons carry over between them).
    run_eval.HARNESS_ENV["HARNESS_STATE_DIR"] = str((run_dir / "state").resolve())
    spec_text = (HERE / "fixture" / "SPEC.md").read_text(encoding="utf-8")
    calls_dir = run_dir / "agent-calls"
    calls_dir.mkdir()
    started = time.monotonic()
    steps: list[dict[str, Any]] = []

    def act(
        kind: str, increment: dict[str, Any], text: str | None, task: dict[str, Any] | None
    ) -> dict[str, Any]:
        (run_dir / "feedback.md").unlink(missing_ok=True)
        before = len(call_records(calls_dir))
        t0 = time.monotonic()
        step: dict[str, Any] = {"increment": increment["id"], "kind": kind}
        if args.condition == "baseline":
            run_claude(text or "", workspace, args.model, calls_dir / f"call-{before + 1}.json")
        elif args.condition == "structured":
            prompt = build_prompt(
                {"taskId": f"task_{increment['id'].lower()}_{kind}", **(task or {})}
            )
            run_claude(prompt, workspace, args.model, calls_dir / f"call-{before + 1}.json")
        else:
            task_file = run_dir / f"task-{increment['id']}-{kind}.yaml"
            task_file.write_text(
                yaml.safe_dump(
                    {"taskId": f"task_{increment['id'].lower()}_{kind}", **(task or {})},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            if args.condition == "harness-legacy":
                outcome = run_harness(workspace, run_dir, task_file, args.model)
            else:
                # The simulated product owner knows the increment's structured task and SPEC.md.
                owner = ProductOwner(
                    "Structured task:\n"
                    + yaml.safe_dump(task or {}, sort_keys=False)
                    + "\nSPEC.md:\n"
                    + spec_text,
                    args.model,
                    run_dir,
                )
                outcome = run_harness(
                    workspace,
                    run_dir,
                    task_file,
                    args.model,
                    clarifier=owner,
                    condition=args.condition,
                    claude_bin=claude_bin,
                )
                (run_dir / f"project-{increment['id']}-{kind}.yaml").write_text(
                    (run_dir / "project.yaml").read_text(encoding="utf-8"), encoding="utf-8"
                )
                step["productOwner"] = owner.rounds
            keys = (
                "outcome",
                "delivered",
                "corrections",
                "gateHistory",
                "finalStatus",
                "finalPhase",
                "eventCount",
                "eventChainValid",
                "waits",
                "clarification",
                "decisions",
            )
            step["harness"] = {k: outcome.get(k) for k in keys}
            step["harness"]["trace"] = {
                k: outcome.get("trace", {}).get(k)
                for k in ("present", "requiredCount", "present2", "requiredCount2")
            }
            measures = (outcome.get("state") or {}).get("measures") or {}
            step["harness"]["measures"] = {
                k: measures.get(k)
                for k in (
                    "invocationsByKind",
                    "routingDecisions",
                    "certification",
                    "reviewFindings",
                    "unsupportedClaims",
                    "writeFindings",
                    "laneEvents",
                    "quarantine",
                    "phaseSeconds",
                )
            }
        step["seconds"] = round(time.monotonic() - t0, 3)
        step["usage"] = usage(calls_since(calls_dir, before))
        return step

    for increment in INCREMENTS:
        step = act("main", increment, increment["casual"], increment["task"])
        check = oracle(workspace, scratch, f"{increment['id']}-main")
        step["oracle"] = check["byPart"]
        bad = failing(check, increment["parts"])
        step["failingChecks"] = bad
        steps.append(step)
        for round_number in range(1, MAX_FIX_ROUNDS + 1):
            if not bad:
                break
            reports = [BUG_REPORTS.get(name, f"{name} fails") for name in bad]
            fix_text = (
                "some things are broken:\n" + "\n".join(f"- {r}" for r in reports) + "\nfix them"
            )
            fix_task = {
                "title": f"Fix reported defects after {increment['id']} (round {round_number})",
                "intent": "Fix the reported defects without breaking other behavior, and add tests for them.",
                "requirements": reports,
                "acceptanceCriteria": [f"Fixed: {r}" for r in reports]
                + ["All tests in tests/ pass."],
                "constraints": ["Use only the Python standard library."],
            }
            fix = act(f"fix{round_number}", increment, fix_text, fix_task)
            check = oracle(workspace, scratch, f"{increment['id']}-fix{round_number}")
            fix["oracle"] = check["byPart"]
            bad = failing(check, increment["parts"])
            fix["failingChecks"] = bad
            steps.append(fix)

    final_oracle = oracle(workspace, scratch, "final")
    record = {
        "experiment": "longitudinal",
        "condition": args.condition,
        "model": args.model,
        "rep": args.rep,
        "dryRun": args.dry_run,
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "wallSeconds": round(time.monotonic() - started, 3),
        "steps": steps,
        "usage": usage(calls_since(calls_dir, 0)),
        "prompts": len(steps),
        "fixRequests": sum(1 for s in steps if s["kind"].startswith("fix")),
        "final": {
            "oracle": final_oracle["byPart"],
            "hiddenPassed": sum(v[0] for v in final_oracle["byPart"].values()),
            "hiddenTotal": sum(v[1] for v in final_oracle["byPart"].values()),
            **final_measures(workspace, scratch),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(
        json.dumps({k: record[k] for k in ("condition", "model", "rep", "wallSeconds", "prompts")})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
