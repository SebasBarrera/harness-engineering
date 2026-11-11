#!/usr/bin/env python3
"""One run of the large-project scenario: the backend of a small online store (fixture/INTERFACE.md).

Instruction levels (prompts.yaml):
  one_line      a one-sentence wish;
  paragraph     a paragraph that names the features but no rules;
  requirements  every rule that the hidden checks verify;
  super         the requirements plus a plan of eight steps and approval conditions for each step.

Conditions:
  direct    the agent receives the whole instruction in one invocation, without the harness;
  stepwise  (super only) the eight steps are sent one by one to the agent, without the harness;
  harness   the instruction becomes governed tasks: one task for paragraph (whose only acceptance
            criterion is "It works.") and requirements, one task per step for super. one_line is
            refused by the harness when the task is created, because it has no acceptance criteria.

The harness condition applies the decision rule of run_eval.run_harness (approve a passing gate,
request changes up to twice, then reject). A step that is not delivered leaves its changes in the
working tree, as a developer who keeps going would; the next step continues from there.
After the run, the 68 hidden checks (parts a to j) and the final quality measures are recorded as
one JSON line.

Usage: python run_large.py --level LEVEL --condition CONDITION --model MODEL --rep N --work DIR --out FILE
"""

from __future__ import annotations

import argparse
import json
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
sys.path.insert(0, str(HERE.parent / "longitudinal"))

import run_eval  # noqa: E402
from agentlib import build_prompt, run_claude  # noqa: E402

git, harness, run_harness = run_eval.git, run_eval.harness, run_eval.run_harness
from run_session import calls_since, final_measures, usage  # noqa: E402

SPEC = yaml.safe_load((HERE / "prompts.yaml").read_text(encoding="utf-8"))
HIDDEN = HERE / "hidden" / "test_hidden_shop.py"
PARTS = "abcdefghij"
EXPECTED = {"a": 15, "b": 8, "c": 7, "d": 11, "e": 9, "f": 9, "g": 2, "h": 3, "i": 2, "j": 2}
AGENT_TIMEOUT = 3600
AGENT_BUDGET = "20"
LABELS = {
    "a": "Catalog", "b": "Stock", "c": "Orders, prices and taxes", "d": "Coupons", "e": "Shipping",
    "f": "Order lifecycle", "g": "Payments", "h": "Refunds and returns", "i": "Sales report", "j": "Persistence",
}
VALID = {("direct", lvl) for lvl in ("one_line", "paragraph", "requirements", "super")} | {
    ("stepwise", "super"), ("harness", "one_line"), ("harness", "paragraph"), ("harness", "requirements"),
    ("harness", "super")}


def all_rules() -> list[str]:
    return [rule for part in PARTS for rule in SPEC["rules"][part]]


def requirements_text() -> str:
    lines = ["Build the backend of a small online store in Python following INTERFACE.md.", "", "Requirements:"]
    lines += [f"- {rule}" for rule in all_rules()]
    lines += ["", "Constraints:", *[f"- {c}" for c in SPEC["common_constraints"]], "",
              "Write pytest tests in tests/ for every rule above; python -m pytest -q must pass."]
    return "\n".join(lines)


def super_text() -> str:
    lines = [requirements_text(), "", ("Work plan. Build it in this order, and do not start a step until the "
                                         "previous one is complete and every test passes:")]
    for number, inc in enumerate(SPEC["increments"], start=1):
        parts = ", ".join(LABELS[p] for p in inc["parts"])
        lines.append(f"{number}. {inc['title']} ({parts}).")
    lines += ["", "Approval conditions for every step:",
              "- every rule of the step has at least one pytest test that would fail if the rule were broken;",
              "- python -m pytest -q passes, including the tests of the earlier steps;",
              "- the change touches only src/shop and tests, adds no dependency and keeps INTERFACE.md intact;",
              "- money is never a float; every rounding is half up as stated.",
              "", "When every step is done, reply with a short summary of each step and how it was verified."]
    return "\n".join(lines)


def step_task(inc: dict[str, Any]) -> dict[str, Any]:
    return {
        "taskId": f"task_{inc['id'].lower()}",
        "title": inc["title"],
        "intent": inc["intent"],
        "requirements": [rule for part in inc["parts"] for rule in SPEC["rules"][part]],
        "acceptanceCriteria": [
            "Every requirement above holds for the Shop class.",
            "tests/ contains pytest tests for every requirement above.",
            "python -m pytest -q passes, including the tests of earlier steps.",
        ],
        "constraints": list(SPEC["common_constraints"]),
    }


def whole_task(level: str) -> dict[str, Any]:
    if level == "one_line":
        return {"taskId": "task_store", "title": "Online store backend", "intent": SPEC["one_line"]}
    if level == "paragraph":
        return {"taskId": "task_store", "title": "Online store backend", "intent": SPEC["paragraph"],
                "acceptanceCriteria": ["It works."]}
    return {
        "taskId": "task_store", "title": "Online store backend",
        "intent": "Build the backend of a small online store in Python following INTERFACE.md.",
        "requirements": all_rules(),
        "acceptanceCriteria": [
            "Every requirement above holds for the Shop class.",
            "tests/ contains pytest tests for every requirement above.",
            "python -m pytest -q passes.",
        ],
        "constraints": list(SPEC["common_constraints"]),
    }


def prepare(workspace: Path, site_packages: Path) -> None:
    workspace.mkdir(parents=True)
    for item in (HERE / "fixture").iterdir():
        shutil.copy2(item, workspace / (".gitignore" if item.name == "gitignore.template" else item.name))
    (site_packages / "zz_eval_workspace.pth").write_text(str(workspace / "src") + "\n", encoding="utf-8")
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "baseline")


def oracle(workspace: Path, scratch: Path, label: str) -> dict[str, Any]:
    target = workspace / "_hidden_eval"
    target.mkdir(exist_ok=True)
    shutil.copy2(HIDDEN, target)
    junit = scratch / f"oracle-{label}.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
                    f"--junitxml={junit}", str(target)], cwd=workspace, capture_output=True, text=True, timeout=900)
    shutil.rmtree(target)
    cases: dict[str, bool] = {}
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            cases[case.get("name", "")] = not [c for c in case if c.tag in {"failure", "error"}]
    # A collection error (no package, an import error) counts as every check of the part failing.
    by_part = {p: [0, n] for p, n in EXPECTED.items()}
    for name, ok in cases.items():
        match = re.match(r"test_([a-j])_", name)
        if match:
            by_part[match.group(1)][0] += int(ok)
    passed = sum(v[0] for v in by_part.values())
    return {"byPart": by_part, "passed": passed, "total": 68,
            "collected": bool(cases)}


def configure_limits(workspace: Path, model: str) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProviders"]["claude"]["command"] = [
        "python", str(HERE.parent / "claude_provider.py"), "--model", model,
        "--timeout", str(AGENT_TIMEOUT), "--budget", AGENT_BUDGET]
    config["runtime"]["commandTimeoutSeconds"] = AGENT_TIMEOUT + 300
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def governed(workspace: Path, run_dir: Path, task: dict[str, Any], model: str, name: str) -> dict[str, Any]:
    task_file = run_dir / f"{name}.yaml"
    task_file.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    if "acceptanceCriteria" not in task:  # the one-line wish: the harness refuses it at task creation
        harness(workspace, "init", "--path", ".")
        created = harness(workspace, "task", "create", "--path", ".", "--file", str(task_file))
        return {"outcome": f"task-refused-exit-{created.returncode}", "delivered": False, "corrections": 0}
    original = run_eval.configure_provider

    def with_limits(ws: Path, m: str, agent: str = "claude", effort: str = "") -> None:
        original(ws, m, agent, effort)
        configure_limits(ws, m)

    run_eval.configure_provider = with_limits
    try:
        outcome = run_harness(workspace, run_dir, task_file, model)
    finally:
        run_eval.configure_provider = original
    keep = ("outcome", "delivered", "corrections", "gateHistory", "finalStatus", "finalPhase", "eventCount",
            "eventChainValid", "validationSummary")
    result = {k: outcome[k] for k in keep}
    result["trace"] = {k: outcome["trace"][k] for k in ("present", "requiredCount")}
    result["findings"] = outcome["findings"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", choices=["one_line", "paragraph", "requirements", "super"], required=True)
    parser.add_argument("--condition", choices=["direct", "stepwise", "harness"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if (args.condition, args.level) not in VALID:
        parser.error(f"{args.condition} does not apply to {args.level}")
    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.work / f"large-{args.level}-{args.condition}-{args.model}-r{args.rep}-{stamp}"
    workspace, scratch = run_dir / "ws", run_dir / "measure"
    prepare(workspace, site_packages)
    scratch.mkdir(parents=True)
    calls_dir = run_dir / "agent-calls"
    calls_dir.mkdir()
    started = time.monotonic()
    steps: list[dict[str, Any]] = []

    def direct(text: str, label: str) -> None:
        before = len(list(calls_dir.glob("call-*.json")))
        t0 = time.monotonic()
        run_claude(text, workspace, args.model, calls_dir / f"call-{before + 1}.json",
                   timeout_seconds=AGENT_TIMEOUT, max_budget_usd=AGENT_BUDGET)
        steps.append({"step": label, "seconds": round(time.monotonic() - t0, 3),
                      "usage": usage(calls_since(calls_dir, before)), "oracle": oracle(workspace, scratch, label)})

    if args.condition == "direct":
        text = {"one_line": SPEC["one_line"], "paragraph": SPEC["paragraph"],
                "requirements": requirements_text(), "super": super_text()}[args.level]
        direct(text, "whole")
    elif args.condition == "stepwise":
        for inc in SPEC["increments"]:
            direct(build_prompt(step_task(inc)), inc["id"])
    else:
        tasks = ([(inc["id"], step_task(inc)) for inc in SPEC["increments"]] if args.level == "super"
                 else [("whole", whole_task(args.level))])
        for label, task in tasks:
            before = len(list(calls_dir.glob("call-*.json")))
            t0 = time.monotonic()
            result = governed(workspace, run_dir, task, args.model, label)
            steps.append({"step": label, "seconds": round(time.monotonic() - t0, 3), "harness": result,
                          "usage": usage(calls_since(calls_dir, before)),
                          "oracle": oracle(workspace, scratch, label)})
            if result["outcome"].startswith("task-refused"):
                break

    final = oracle(workspace, scratch, "final")
    record = {
        "experiment": "large",
        "level": args.level,
        "condition": args.condition,
        "model": args.model,
        "rep": args.rep,
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "wallSeconds": round(time.monotonic() - started, 3),
        "steps": steps,
        "usage": usage(calls_since(calls_dir, 0)),
        "final": {"hidden": final, **final_measures(workspace, scratch)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps({k: record[k] for k in ("level", "condition", "model", "rep", "wallSeconds")}
                     | {"hidden": f"{final['passed']}/{final['total']}", "usd": record["usage"]["costUsd"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
