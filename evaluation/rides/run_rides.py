#!/usr/bin/env python3
"""One run of the ride-hailing and food delivery scenario (fixture/INTERFACE.md, SPEC.md).

Instruction levels (prompts.yaml):
  one_line      a one-sentence wish;
  paragraph     a paragraph that names the features but no rules;
  requirements  the full specification (docs/SPEC.md in the repository, 95 requirements);
  super         the specification plus a plan of 16 steps and approval conditions for every step.

Conditions:
  direct    the whole instruction in one agent invocation, without the harness;
  stepwise  (super only) the 16 steps sent one by one to the agent, without the harness and without
            any verification between steps;
  harness   governed tasks: one for paragraph (only criterion "It works.") and requirements, one per step
            for super; one_line is refused when the task is created (no acceptance criteria).

The harness applies the decision rule of run_eval.run_harness. A step that is not delivered leaves its
changes in the working tree and the next step continues from there. After the run the quality measures
of quality.py and the usage are recorded as one JSON line, with the hidden checks (one or more per
requirement) when the hidden suite is present. The runs are made without it, so that no agent can reach
it, and rescore.py scores the kept workspaces afterwards with the final suite.

Every agent call is resumable (agentlib.run_claude with resumable=True): a call stopped by the usage
limit waits and resumes its session. A cell whose runner died is resumed by running the same command
again: the unfinished run directory is reused, finished steps are skipped (progress.json), a direct
call in flight resumes its session, and a governed step in flight is started again as a new task.

Usage: python run_rides.py --level LEVEL --condition CONDITION --model MODEL --rep N --work DIR --out FILE
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
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "longitudinal"))
sys.path.insert(0, str(HERE))

import run_eval  # noqa: E402
from agentlib import build_prompt, run_claude  # noqa: E402
from quality import analyze  # noqa: E402
from run_eval import git, harness, run_harness  # noqa: E402
from run_session import calls_since, final_measures, usage  # noqa: E402

PROMPTS = yaml.safe_load((HERE / "prompts.yaml").read_text(encoding="utf-8"))
SPEC_TEXT = (HERE / "SPEC.md").read_text(encoding="utf-8")
HIDDEN = HERE / "hidden"
# Ruff and Mypy for quality.py: the repository's tool venv, found from the repository or from a copy in .local.
TOOLS_PYTHON = str(next(p for p in HERE.parents if (p / ".local" / "venv-repo").is_dir()) / ".local" / "venv-repo"
                   / "bin" / "python")
AGENT_TIMEOUT = 4 * 3600
AGENT_BUDGET = "200"
VALID = {("direct", lvl) for lvl in ("one_line", "paragraph", "requirements", "super")} | {
    ("stepwise", "super"), ("harness", "one_line"), ("harness", "paragraph"), ("harness", "requirements"),
    ("harness", "super")}


def requirements_by_section() -> dict[str, list[tuple[str, str]]]:
    """Requirement ids and texts of SPEC.md, by section letter."""
    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for match in re.finditer(r"^- \*\*([A-Z])(\d+)\.\*\* (.*?)(?=^- \*\*|^## |\Z)", SPEC_TEXT, re.M | re.S):
        text = " ".join(match.group(3).split())
        found[match.group(1)].append((f"{match.group(1)}{match.group(2)}", text))
    return found


SECTIONS = requirements_by_section()


def super_text() -> str:
    lines = [PROMPTS["requirements_intro"], "", "Work plan. Build it in this order, and do not start a step until "
             "the previous one is complete and every test passes:"]
    for number, step in enumerate(PROMPTS["steps"], start=1):
        lines.append(f"{number}. {step['title']} (sections {', '.join(step['sections'])} of docs/SPEC.md).")
    lines += ["", "Approval conditions for every step:", *[f"- {c}" for c in PROMPTS["approval_conditions"]], "",
              "When every step is done, reply with a short summary of each step and how it was verified."]
    return "\n".join(lines)


def step_task(step: dict[str, Any]) -> dict[str, Any]:
    reqs = [f"{rid}. {text}" for section in step["sections"] for rid, text in SECTIONS[section]]
    return {
        "taskId": f"task_{step['id'].lower()}",
        "title": step["title"],
        "intent": (f"Implement sections {', '.join(step['sections'])} of docs/SPEC.md ({step['title'].lower()}) "
                   "in the rides package, with pytest tests. The general rules X1 to X8 of docs/SPEC.md "
                   "apply to everything."),
        "requirements": reqs,
        "acceptanceCriteria": [
            "Every requirement above holds, exactly as written in docs/SPEC.md.",
            "tests/ contains pytest tests for every requirement above.",
            "python -m pytest -q passes, including the tests of earlier steps.",
        ],
        "constraints": list(PROMPTS["approval_conditions"][2:]) + ["Use only the Python standard library."],
    }


def whole_task(level: str) -> dict[str, Any]:
    if level == "one_line":
        return {"taskId": "task_platform", "title": "Ride-hailing and food delivery platform",
                "intent": PROMPTS["one_line"]}
    if level == "paragraph":
        return {"taskId": "task_platform", "title": "Ride-hailing and food delivery platform",
                "intent": PROMPTS["paragraph"], "acceptanceCriteria": ["It works."]}
    return {
        "taskId": "task_platform", "title": "Ride-hailing and food delivery platform",
        "intent": PROMPTS["requirements_intro"],
        "acceptanceCriteria": [
            "Every requirement of docs/SPEC.md holds, exactly as written.",
            "tests/ contains pytest tests for every requirement of docs/SPEC.md.",
            "python -m pytest -q passes.",
        ],
        "constraints": ["Use only the Python standard library.", "Follow INTERFACE.md exactly."],
    }


def prepare(workspace: Path, site_packages: Path, with_spec: bool) -> None:
    workspace.mkdir(parents=True)
    for item in (HERE / "fixture").iterdir():
        shutil.copy2(item, workspace / (".gitignore" if item.name == "gitignore.template" else item.name))
    if with_spec:
        (workspace / "docs").mkdir()
        shutil.copy2(HERE / "SPEC.md", workspace / "docs" / "SPEC.md")
    (site_packages / "zz_eval_workspace.pth").write_text(str(workspace / "src") + "\n", encoding="utf-8")
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "baseline")


def oracle(workspace: Path, scratch: Path) -> dict[str, Any]:
    if not (HIDDEN.is_dir() and (HERE / "hidden_cases.json").is_file()):
        return {"pending": True}
    target = workspace / "_hidden_eval"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(HIDDEN, target, ignore=shutil.ignore_patterns("__pycache__"))
    junit = scratch / "oracle.xml"
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
                           "--timeout=120" if _has_timeout() else "-q", f"--junitxml={junit}", str(target)],
                          cwd=workspace, capture_output=True, text=True, timeout=7200,
                          env={**os.environ, "PYTHONPATH": str(workspace / "src")})
    shutil.rmtree(target)
    cases: dict[str, bool] = {}
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            name = case.get("classname", "").rsplit(".", 1)[-1] + "::" + case.get("name", "")
            cases[name] = cases.get(name, True) and not [c for c in case if c.tag in {"failure", "error", "skipped"}]
    expected = expected_cases()
    by_req: dict[str, list[int]] = {}
    for name, req in expected.items():
        ok = cases.get(name, False)
        tally = by_req.setdefault(req, [0, 0])
        tally[0] += int(ok)
        tally[1] += 1
    passed = sum(v[0] for v in by_req.values())
    return {"passed": passed, "total": len(expected), "byRequirement": by_req,
            "requirementsMet": sum(1 for v in by_req.values() if v[0] == v[1]),
            "requirementsTotal": len(by_req), "seconds": round(time.monotonic() - started, 1),
            "exitCode": proc.returncode}


def _has_timeout() -> bool:
    try:
        import pytest_timeout  # noqa: F401
    except ImportError:
        return False
    return True


_EXPECTED: dict[str, str] | None = None


def expected_cases() -> dict[str, str]:
    """Cases of the hidden suite (``module::name[param]``) and their requirement id (see make_hidden_cases.py)."""
    global _EXPECTED
    if _EXPECTED is None:
        listing = json.loads((HERE / "hidden_cases.json").read_text(encoding="utf-8"))
        _EXPECTED = {name: req for name, req in listing.items()}
    return _EXPECTED


def configure_limits(workspace: Path, model: str) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProviders"]["claude"]["command"] = [
        "python", str(HERE.parent / "claude_provider.py"), "--model", model,
        "--timeout", str(AGENT_TIMEOUT), "--budget", AGENT_BUDGET, "--resumable"]
    # Waits for the usage limit happen inside the provider call, so the harness must not cut it short.
    config["runtime"]["commandTimeoutSeconds"] = 7 * 24 * 3600
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def governed(workspace: Path, run_dir: Path, task: dict[str, Any], model: str, name: str) -> dict[str, Any]:
    task_file = run_dir / f"{name}.yaml"
    task_file.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    if "acceptanceCriteria" not in task:
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
    prefix = f"rides-{args.level}-{args.condition}-{args.model}-r{args.rep}-"
    unfinished = sorted(d for d in args.work.glob(prefix + "*") if (d / "progress.json").is_file()
                        and not (d / "record.json").exists()) if args.work.is_dir() else []
    if unfinished:
        run_dir = unfinished[-1]
        workspace, scratch = run_dir / "ws", run_dir / "measure"
        (site_packages / "zz_eval_workspace.pth").write_text(str(workspace / "src") + "\n", encoding="utf-8")
        progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
        progress["restarts"] += 1
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = args.work / f"{prefix}{stamp}"
        workspace, scratch = run_dir / "ws", run_dir / "measure"
        prepare(workspace, site_packages, with_spec=args.level in {"requirements", "super"})
        scratch.mkdir(parents=True)
        (run_dir / "agent-calls").mkdir()
        progress = {"steps": [], "promptsSent": [], "elapsedSeconds": 0.0, "restarts": 0, "current": None}
    calls_dir = run_dir / "agent-calls"
    started = time.monotonic() - progress["elapsedSeconds"]
    steps: list[dict[str, Any]] = progress["steps"]
    prompts_sent: list[str] = progress["promptsSent"]
    done = {step["step"] for step in steps}

    def save_progress(current: str | None) -> None:
        progress["current"] = current
        progress["elapsedSeconds"] = round(time.monotonic() - started, 3)
        (run_dir / "progress.json").write_text(json.dumps(progress, indent=1), encoding="utf-8")

    def direct(text: str, label: str) -> None:
        before = len(list(calls_dir.glob("call-*.json")))
        log_path = calls_dir / f"call-{before + 1}.json"
        inflight = log_path.with_name(log_path.name + ".inflight")
        resume = json.loads(inflight.read_text(encoding="utf-8")).get("sessionId") if inflight.exists() else None
        # Resume the session only if Claude Code kept it on disk; otherwise send the prompt again.
        if resume and not list((Path.home() / ".claude" / "projects").glob(f"*/{resume}.jsonl")):
            resume = None
        t0 = time.monotonic()
        if progress["current"] != label:
            prompts_sent.append(text)
        save_progress(label)
        run_claude(text, workspace, args.model, log_path, timeout_seconds=AGENT_TIMEOUT,
                   max_budget_usd=AGENT_BUDGET, resumable=True, resume_session=resume)
        steps.append({"step": label, "seconds": round(time.monotonic() - t0, 3),
                      "usage": usage(calls_since(calls_dir, before))})
        save_progress(None)

    save_progress(progress["current"])
    if args.condition == "direct":
        text = {"one_line": PROMPTS["one_line"], "paragraph": PROMPTS["paragraph"],
                "requirements": PROMPTS["requirements_intro"], "super": super_text()}[args.level]
        if "whole" not in done:
            direct(text, "whole")
    elif args.condition == "stepwise":
        for step in PROMPTS["steps"]:
            if step["id"] not in done:
                direct(build_prompt(step_task(step)), step["id"])
    else:
        tasks = ([(s["id"], step_task(s)) for s in PROMPTS["steps"]] if args.level == "super"
                 else [("whole", whole_task(args.level))])
        refused = any(str((s.get("harness") or {}).get("outcome", "")).startswith("task-refused") for s in steps)
        for label, task in tasks:
            if label in done or refused:
                continue
            if progress["current"] == label:
                # The runner died during this governed step: start it again as a new task.
                task = {**task, "taskId": f"{task['taskId']}_restart{progress['restarts']}"}
            before = len(list(calls_dir.glob("call-*.json")))
            t0 = time.monotonic()
            prompts_sent.append(yaml.safe_dump(task, sort_keys=False))
            save_progress(label)
            result = governed(workspace, run_dir, task, args.model, label)
            steps.append({"step": label, "seconds": round(time.monotonic() - t0, 3), "harness": result,
                          "usage": usage(calls_since(calls_dir, before))})
            save_progress(None)
            if result["outcome"].startswith("task-refused"):
                break

    hidden = oracle(workspace, scratch)
    record = {
        "experiment": "rides",
        "level": args.level,
        "condition": args.condition,
        "model": args.model,
        "rep": args.rep,
        "runDir": str(run_dir),
        "restarts": progress["restarts"],
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "wallSeconds": round(time.monotonic() - started, 3),
        "steps": steps,
        "usage": usage(calls_since(calls_dir, 0)),
        "pausedSeconds": round(sum(c.get("pausedSeconds", 0) for c in calls_since(calls_dir, 0)), 3),
        "limitPauses": sum(c.get("limitPauses", 0) for c in calls_since(calls_dir, 0)),
        "final": {"hidden": hidden, **final_measures(workspace, scratch)},
        "quality": analyze(workspace, TOOLS_PYTHON),
    }
    (run_dir / "prompts-sent.json").write_text(json.dumps(prompts_sent, indent=1), encoding="utf-8")
    (run_dir / "record.json").write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps({k: record[k] for k in ("level", "condition", "model", "rep", "wallSeconds")}
                     | ({"hidden": "pending"} if hidden.get("pending") else
                        {"hidden": f"{hidden['passed']}/{hidden['total']}",
                         "requirements": f"{hidden['requirementsMet']}/{hidden['requirementsTotal']}"})
                     | {"usd": record["usage"]["costUsd"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
