#!/usr/bin/env python3
"""Run one controlled evaluation run and append its record to a JSONL file.

A run is one (scenario, condition, model, repetition). The baseline condition invokes Claude Code
directly on the prepared repository; the harness condition runs the same task through
``harness run start`` with Claude Code as the command provider and applies the declared decision
rule. Both conditions are measured afterwards by ``measure.py`` in the same way.

Decision rule (harness condition), applied at DECISION:
  * gate PASSED -> APPROVE;
  * gate not PASSED and fewer than MAX_CORRECTIONS correction cycles -> REQUEST_CHANGES with the
    gate reasons and findings as feedback, then ``harness run continue``;
  * otherwise -> REJECT.
A run that stops before DECISION (exit code 6) is recorded as stopped and is not delivered.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agentlib import build_prompt, run_claude, run_codex  # noqa: E402
from measure import measure  # noqa: E402

MAX_CORRECTIONS = 2
# Rounds of clarification answered by a clarifier (harness 1.1.0 and later, see product_owner.py).
MAX_CLARIFY_ROUNDS = 3
SCENARIOS = {
    "greenfield": {
        "task": HERE / "tasks" / "greenfield-shipping.yaml",
        "fixture": HERE / "fixtures" / "greenfield-shipping",
    },
    "security": {
        "task": HERE / "tasks" / "greenfield-alerts.yaml",
        "fixture": HERE / "fixtures" / "greenfield-alerts",
    },
    "brownfield": {
        "task": HERE / "tasks" / "brownfield-itsdangerous.yaml",
        "archive": "itsdangerous-2.2.0.tar.gz",
        "archive_sha256": "7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b",
    },
}
GIT = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "-c", "commit.gpgsign=false"]
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run([*GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def prepare(scenario: str, workspace: Path, cache: Path, site_packages: Path) -> str:
    spec = SCENARIOS[scenario]
    workspace.mkdir(parents=True)
    if "fixture" in spec:
        for item in spec["fixture"].iterdir():
            target = ".gitignore" if item.name == "gitignore.template" else item.name
            shutil.copy2(item, workspace / target)
    else:
        with tarfile.open(cache / spec["archive"]) as archive:
            for member in archive.getmembers():
                member.name = member.name.split("/", 1)[1] if "/" in member.name else ""
                if member.name:
                    archive.extract(member, workspace, filter="data")
    # The project under test is importable from the workspace in both conditions.
    pth = site_packages / "zz_eval_workspace.pth"
    pth.write_text(str(workspace / "src") + "\n", encoding="utf-8")
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "baseline")
    return git(workspace, "rev-parse", "HEAD").strip()


def harness(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["harness", *args], cwd=workspace, capture_output=True, text=True)


def harness_json(workspace: Path, *args: str) -> Any:
    proc = harness(workspace, *args)
    return json.loads(proc.stdout)


def configure_provider(workspace: Path, model: str, agent: str = "claude", effort: str = "") -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    command = ["python", str(HERE / "claude_provider.py"), "--model", model]
    if agent == "codex":
        command = ["python", str(HERE / "codex_provider.py"), "--model", model, "--effort", effort]
    config["agentProvider"] = agent
    config["agentProviders"] = {agent: {"kind": "command", "command": command, "model": model}}
    config["runtime"]["commandTimeoutSeconds"] = 1800
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def feedback_from(workspace: Path, run_id: str, status: dict[str, Any]) -> str:
    gate = status.get("gate") or {}
    findings = harness_json(workspace, "findings", "list", "--path", ".", "--run", run_id)
    lines = [f"Gate status: {gate.get('status')}; reasons: {', '.join(gate.get('reasonCodes', []))}"]
    for finding in findings:
        where = (finding.get("location") or {}).get("path") or "-"
        lines.append(f"- [{finding['severity']}] {finding['ruleId']} at {where}: {finding['message']}")
    return "\n".join(lines)


def run_harness(
    workspace: Path,
    run_dir: Path,
    task_file: Path,
    model: str,
    agent: str = "claude",
    effort: str = "",
    clarifier: Any = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    def step(*args: str) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        proc = harness(workspace, *args)
        events.append({"command": args[:2], "exitCode": proc.returncode, "seconds": round(time.monotonic() - started, 3)})
        return proc

    step("init", "--path", ".")
    configure_provider(workspace, model, agent, effort)
    task = json.loads(step("task", "create", "--path", ".", "--file", str(task_file)).stdout)
    started = step("run", "start", "--path", ".", "--task", task["taskId"], "--provider", agent)
    run_id = json.loads(started.stdout)["executionId"]
    code = started.returncode
    clarification: list[dict[str, Any]] = []
    # Without a clarifier the flow is the one of the evaluated runs. With one, a run blocked in INTENT
    # by clarification questions gets them answered (harness task clarify) and continues.
    while clarifier is not None and code == 6 and len(clarification) < MAX_CLARIFY_ROUNDS:
        request = harness_json(workspace, "task", "questions", "--path", ".", "--task", task["taskId"]).get(
            "openRequest"
        )
        if not request:
            break
        current = harness_json(workspace, "task", "show", "--path", ".", "--task", task["taskId"])
        answers = clarifier(current, request)
        answers_file = run_dir / f"answers-{task['taskId']}-{len(clarification) + 1}.yaml"
        answers_file.write_text(yaml.safe_dump(answers, sort_keys=False, allow_unicode=True), encoding="utf-8")
        clarified = step("task", "clarify", "--path", ".", "--task", task["taskId"], "--file", str(answers_file),
                         "--actor", getattr(clarifier, "actor", "human.local"))
        clarification.append({"questions": [q["ruleId"] for q in request["questions"]],
                              "answered": sorted(answers["answers"]), "clarifyExit": clarified.returncode})
        if clarified.returncode != 0:
            break
        code = step("run", "continue", "--path", ".", "--run", run_id).returncode
    corrections, outcome, gate_history = 0, "error", []
    while True:
        status = harness_json(workspace, "status", "--path", ".", "--run", run_id)
        if code != 4:
            outcome = "stopped" if code == 6 else f"exit-{code}"
            break
        gate = status["gate"]["status"]
        gate_history.append(gate)
        digest = status["execution"]["changeSetDigest"]
        if gate == "PASSED":
            decision, rationale = "APPROVE", "Gate passed; verification and review evidence inspected."
        elif corrections < MAX_CORRECTIONS:
            (run_dir / "feedback.md").write_text(feedback_from(workspace, run_id, status), encoding="utf-8")
            decision, rationale = "REQUEST_CHANGES", "Gate did not pass; changes requested with the findings as feedback."
        else:
            decision, rationale = "REJECT", "Gate did not pass after the allowed correction cycles."
        step("gate", "decide", "--path", ".", "--run", run_id, "--decision", decision,
             "--change-set-digest", digest, "--actor", "human.reviewer", "--rationale", rationale)
        if decision == "APPROVE":
            outcome = "approved"
            break
        if decision == "REJECT":
            outcome = "rejected"
            break
        corrections += 1
        code = step("run", "continue", "--path", ".", "--run", run_id).returncode
    final = harness_json(workspace, "status", "--path", ".", "--run", run_id)
    return {
        "runId": "run",  # anonymized: the local identifier is not published
        "outcome": outcome,
        "delivered": outcome == "approved",
        "corrections": corrections,
        "gateHistory": gate_history,
        "finalStatus": final["execution"]["status"],
        "finalPhase": final["execution"]["currentPhase"],
        "terminalReason": final["execution"].get("terminalReason"),
        "validationSummary": final["validationSummary"],
        "findings": [
            {"severity": f["severity"], "ruleId": f["ruleId"], "validatorId": f.get("validatorId")}
            for f in harness_json(workspace, "findings", "list", "--path", ".", "--run", run_id)
        ],
        "eventCount": final["eventCount"],
        "eventChainValid": final["eventChainValid"],
        "clarification": clarification,
        "harnessMetrics": {name: item.get("value") for name, item in final["metrics"].items()},
        "trace": trace_completeness(workspace, final),
        "commands": events,
    }


def trace_completeness(workspace: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Required causal relations present in the persisted records, by run progress."""
    execution = status["execution"]
    db = sqlite3.connect(workspace / ".harness" / "state.db")
    try:
        def records(kind: str) -> list[dict[str, Any]]:
            rows = db.execute(
                "select payload_json from records where record_type = ? and (execution_id = ? or execution_id is null)",
                (kind, execution["executionId"]),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

        digest = execution.get("changeSetDigest")
        phases = {phase["phaseId"] for phase in status["phases"]}
        gates = [g for g in records("gate") if g.get("changeSetDigest") == digest]
        decisions = [d for d in records("decision") if d.get("changeSetDigest") == digest]
        relations = {
            "task": any(t.get("taskId") == execution["taskId"] for t in records("task")),
            "plan": any(p.get("taskId") == execution["taskId"] for p in records("plan")),
            "agentInvocation": any(a.get("model") and a.get("promptDigest") for a in records("agent_invocation")),
            "changeSet": any(c.get("digest") == digest for c in records("change_set")),
            "validationsBound": any(
                v.get("changeSetDigest") == digest and v.get("mandatory") for v in records("validation")
            ),
            "gateBound": any(g.get("inputRefs") for g in gates),
            "decisionBound": any(
                d.get("gateEvaluationId") in {g["gateEvaluationId"] for g in gates} for d in decisions
            ),
            "eventChain": bool(status["eventChainValid"]),
        }
    finally:
        db.close()
    required = ["task", "plan", "eventChain"]
    if "IMPLEMENTATION" in phases:
        required += ["agentInvocation", "changeSet"]
    if "VERIFICATION" in phases:
        required += ["validationsBound"]
    if "DECISION" in phases:
        required += ["gateBound", "decisionBound"]
    present = [name for name in required if relations[name]]
    return {"relations": relations, "required": required, "present": len(present), "requiredCount": len(required)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--condition", choices=["baseline", "harness"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--agent", choices=["claude", "codex"], default="claude")
    parser.add_argument("--prompt", choices=["full", "poor", "casual"], default="full")
    parser.add_argument("--effort", default="")
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag = "" if args.prompt == "full" else f"-{args.prompt}"
    run_dir = args.work / f"{args.scenario}{tag}-{args.condition}-{args.model}-r{args.rep}-{stamp}"
    workspace = run_dir / "ws"
    baseline = prepare(args.scenario, workspace, args.cache, site_packages)
    task_file = SCENARIOS[args.scenario]["task"]
    if args.prompt == "poor":
        task_file = task_file.with_name(task_file.stem + "-poor.yaml")
    casual_prompt = None
    if args.prompt == "casual":
        if args.condition != "baseline":
            parser.error("the casual prompt has no task file; the harness rejects it at task create")
        casual_prompt = task_file.with_name(task_file.stem + "-casual.txt").read_text(encoding="utf-8")
    task = yaml.safe_load(task_file.read_text(encoding="utf-8"))

    started = time.monotonic()
    record: dict[str, Any] = {
        "scenario": args.scenario,
        "condition": args.condition,
        "model": args.model,
        "agentName": args.agent,
        "prompt": args.prompt,
        "effort": args.effort or None,
        "rep": args.rep,
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if args.condition == "baseline":
        log = run_dir / "agent-calls" / "call-1.json"
        prompt = casual_prompt if casual_prompt is not None else build_prompt(task)
        if args.agent == "codex":
            run_codex(prompt, workspace, args.model, args.effort, log)
        else:
            run_claude(prompt, workspace, args.model, log)
        record["baseline"] = {"delivered": True}
    else:
        record["harness"] = run_harness(workspace, run_dir, task_file, args.model, args.agent, args.effort)
    record["wallSeconds"] = round(time.monotonic() - started, 3)
    calls = [json.loads(p.read_text()) for p in sorted((run_dir / "agent-calls").glob("call-*.json"))]
    record["agentCalls"] = [{k: v for k, v in c.items() if k != "summary"} for c in calls]
    record["agent"] = {
        "calls": len(calls),
        "costUsd": (
            round(sum(c["costUsd"] for c in calls), 6)
            if calls and all(c["costUsd"] is not None for c in calls)
            else None
        ),
        "reasoningTokens": sum(c.get("reasoningTokens") or 0 for c in calls),
        "inputTokens": sum(c["inputTokens"] or 0 for c in calls),
        "outputTokens": sum(c["outputTokens"] or 0 for c in calls),
        "cacheReadTokens": sum(c["cacheReadTokens"] or 0 for c in calls),
        "cacheCreationTokens": sum(c["cacheCreationTokens"] or 0 for c in calls),
        "turns": sum(c["numTurns"] or 0 for c in calls),
        "wallSeconds": round(sum(c["wallSeconds"] for c in calls), 3),
        "errors": sum(1 for c in calls if c["isError"]),
        "permissionDenials": sum(c["permissionDenials"] for c in calls),
    }
    record["measures"] = measure(args.scenario, workspace, baseline, run_dir, args.cache, HERE)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps({k: record[k] for k in ("scenario", "condition", "model", "rep", "wallSeconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
