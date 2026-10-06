#!/usr/bin/env python3
"""Embedded mode (N08): Claude Code as the host session drives the harness over MCP.

The host (Claude Code, non-interactive, model = the cell's host model) receives the task and the
instruction to govern it with the harness's MCP tools (``harness mcp serve``, registered with
``--mcp-config``) and the skill ``harness init --agent-skills`` writes into the workspace. It creates
the task, starts the run with the ``session`` provider, implements when the run waits for it,
runs ``harness_check`` and continues the run. The read-only calls of the run go to the project's
provider (the evaluation adapter with the same host model), never to the session.

A non-interactive session cannot ask a person, so the runner plays the person between session
segments: when the run waits for a person, the simulated person of ``person.py`` acts on the CLI
(the product owner answers clarification questions directly, not relayed by the session); when the
run waits for the session (its implementation, a failed verification, changes requested), the
runner resumes the same session (``--resume``) with what happened. At most ``--max-segments``
session segments per run.

Differences from the CLI conditions, by construction: the host runs without ``--safe-mode``
(which disables MCP servers and skills) and with ``--setting-sources project`` so the workspace's
skill is the only customization; its tool list adds the harness's MCP tools. Everything else of the
command line (tools, permission mode, budget, time limit, strict MCP configuration) is the direct
condition's. ``--dry-run`` replaces Claude Code with ``fake_claude.py``, which plays a host that calls
the MCP server itself (no model call).

Usage: run_embedded.py --scenario S --model HOST --rep N --work DIR --cache DIR --out FILE [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import agentlib  # noqa: E402
import conditions  # noqa: E402
import harness_state  # noqa: E402
import run_eval  # noqa: E402
from agentlib import allocate_call, build_prompt, load_calls  # noqa: E402
from measure import measure  # noqa: E402
from person import SimulatedPerson  # noqa: E402
from product_owner import ProductOwner  # noqa: E402

MAX_SEGMENTS = 6
SESSION_LIMIT_CODE = 75
EMBEDDED_INSTRUCTIONS = """
Govern this change with the Governed Agent Harness, through its MCP tools (server "harness") and
the harness skill of this repository:
1. Create the task with harness_task_create, passing exactly this task document as the task object:
```json
{task}
```
2. Start the run with harness_run_start (provider session).
3. When the run waits in IMPLEMENTATION for your edits, implement the task in this workspace, run
   harness_check until it passes, then call harness_run_continue.
4. When the harness waits for a person (clarification questions, acceptance tests, a plan, the
   architecture or the decision), stop and say what it waits for: a person decides in a terminal.
   Never answer or decide for the person.
"""
RESUME_TEMPLATE = (
    "The harness run {run} was updated by the person or by the harness. Current state: {state}.\n"
    "{reason}\n"
    "Read the run with harness_status (and harness_review if useful). If the run waits for your "
    "edits, implement or fix what the findings say, run harness_check and call "
    "harness_run_continue. If it waits for a person, stop and say so."
)


def session_command(prompt: str, model: str, run_dir: Path, session: list[str]) -> list[str]:
    """The direct condition's command line (agentlib._command) adapted to a host session."""
    command = agentlib._command(prompt, model, agentlib.MAX_BUDGET_USD, session)
    command.remove("--safe-mode")
    index = command.index("--allowedTools") + 1
    command.insert(index, "mcp__harness")
    return [
        *command,
        "--mcp-config",
        str(run_dir / "mcp.json"),
        "--setting-sources",
        "project",
    ]


def exit_code(status: dict[str, Any]) -> int:
    execution = status.get("execution") or {}
    state, phase = execution.get("status"), execution.get("currentPhase")
    if state == "PASSED":
        return 0
    if state == "BLOCKED" and phase == "DECISION":
        return 4
    if state == "ERROR":
        return 1
    return 6


class HostSession:
    """The host session's segments: the first prompt, then resumes with what happened."""

    def __init__(self, workspace: Path, run_dir: Path, model: str, max_segments: int) -> None:
        self.workspace = workspace
        self.run_dir = run_dir
        self.model = model
        self.max_segments = max_segments
        self.session_id = str(uuid.uuid4())
        self.segments: list[dict[str, Any]] = []

    def call(self, prompt: str) -> dict[str, Any]:
        first = not self.segments
        session = ["--session-id", self.session_id] if first else ["--resume", self.session_id]
        command = session_command(prompt, self.model, self.run_dir, session)
        record, stderr, _ = agentlib._invoke(
            command, self.workspace, self.model, agentlib.AGENT_TIMEOUT_SECONDS
        )
        record.update({"kind": "host-session", "segment": len(self.segments) + 1})
        log = allocate_call(self.run_dir / "session-calls")
        log.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.with_suffix(".stderr.txt").write_text(stderr[-20000:], encoding="utf-8")
        log.with_suffix(".prompt.txt").write_text(prompt, encoding="utf-8")
        self.segments.append(
            {
                k: record.get(k)
                for k in (
                    "exitCode",
                    "isError",
                    "numTurns",
                    "costUsd",
                    "wallSeconds",
                    "terminalReason",
                )
            }
        )
        return record

    def resume(self, run_id: str, reason: str) -> int:
        if len(self.segments) >= self.max_segments:
            return SESSION_LIMIT_CODE
        status = run_eval.harness_json(self.workspace, "status", "--path", ".", "--run", run_id)
        execution = status.get("execution") or {}
        state = f"{execution.get('status')} in {execution.get('currentPhase')}"
        self.call(RESUME_TEMPLATE.format(run=run_id, state=state, reason=reason.strip()))
        after = run_eval.harness_json(self.workspace, "status", "--path", ".", "--run", run_id)
        return exit_code(after)


def latest_run(workspace: Path) -> tuple[str | None, str | None]:
    proc = run_eval.harness(workspace, "run", "list", "--path", ".")
    try:
        runs = json.loads(proc.stdout)
    except ValueError:
        return None, None
    runs = runs.get("runs", runs) if isinstance(runs, dict) else runs
    if not runs:
        return None, None
    newest = runs[0]
    return newest.get("executionId"), newest.get("taskId")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(run_eval.SCENARIOS), required=True)
    parser.add_argument("--model", required=True, help="the host session's model")
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-segments", type=int, default=MAX_SEGMENTS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--variant",
        choices=["init", "no-acceptance"],
        default="init",
        help="init: the harness condition's configuration; no-acceptance: the same without "
        "verification.acceptanceTests and with governance.stopTheLine off (declared ablation, see README)",
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
    run_dir = args.work / f"{args.scenario}-embedded-{args.model}-r{args.rep}-{stamp}"
    workspace = run_dir / "ws"
    baseline = run_eval.prepare(args.scenario, workspace, args.cache, site_packages)
    state_dir = (run_dir / "state").resolve()
    run_eval.HARNESS_ENV["HARNESS_STATE_DIR"] = str(state_dir)
    started = time.monotonic()
    # The skill, then the harness condition's configuration (init's file, routing fixed, the
    # evaluation adapter with the host model for the read-only calls).
    skills = run_eval.harness(workspace, "init", "--path", ".", "--agent-skills")
    conditions.write_config(
        workspace,
        "harness",
        args.model,
        run_dir=run_dir,
        code=run_eval.PROVIDER_CODE,
        harness=run_eval.harness,
        claude_bin=claude_bin,
    )
    if args.variant == "no-acceptance":
        # Before wave 9 (#81), with the session provider, the frozen acceptance files the harness
        # writes counted as the session's edits, and a failed verification quarantined them (see
        # README); the variant is kept to compare with the init configuration.
        path = workspace / ".harness" / "project.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config.get("verification", {}).pop("acceptanceTests", None)
        config.setdefault("governance", {})["stopTheLine"] = "off"
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    shutil.copy2(workspace / ".harness" / "project.yaml", run_dir / "project.yaml")
    harness_exe = shutil.which(run_eval.HARNESS_CMD[0]) or run_eval.HARNESS_CMD[0]
    mcp = {
        "mcpServers": {
            "harness": {
                "command": harness_exe,
                "args": [*run_eval.HARNESS_CMD[1:], "mcp", "serve", "--path", str(workspace)],
                "env": {"HARNESS_STATE_DIR": str(state_dir)},
            }
        }
    }
    (run_dir / "mcp.json").write_text(json.dumps(mcp, indent=1), encoding="utf-8")
    task_file = run_eval.SCENARIOS[args.scenario]["task"]
    task = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    document = {**task, "implementation": {"mode": "none"}}
    prompt = build_prompt(task) + EMBEDDED_INSTRUCTIONS.format(task=json.dumps(document, indent=2))
    host = HostSession(workspace, run_dir, args.model, args.max_segments)
    host.call(prompt)
    run_id, task_id = latest_run(workspace)
    record: dict[str, Any] = {
        "scenario": args.scenario,
        "condition": "embedded" if args.variant == "init" else f"embedded-{args.variant}",
        "model": args.model,
        "prompt": "full",
        "rep": args.rep,
        "dryRun": args.dry_run,
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "skillsInit": skills.returncode,
        "harnessVersion": run_eval.harness_version(),
        "taskCreatedBySession": task_id is not None,
    }
    if run_id is None:
        record["harness"] = {"outcome": "no-run", "delivered": False}
    else:
        knowledge = "Full task description:\n" + task_file.read_text(encoding="utf-8")
        spec = run_eval.SCENARIOS[args.scenario].get("fixture", Path("-")) / "SPEC.md"
        if spec.is_file():
            knowledge += "\nSPEC.md:\n" + spec.read_text(encoding="utf-8")
        owner = ProductOwner(knowledge, args.model, run_dir)
        person = SimulatedPerson(
            run_eval.harness,
            workspace,
            run_dir,
            clarifier=owner,
            feedback=lambda rid, status: run_eval.feedback_from(workspace, rid, status),
            session=host.resume,
        )
        status = run_eval.harness_json(workspace, "status", "--path", ".", "--run", run_id)
        driven = person.drive(run_id, str(task_id), exit_code(status))
        if driven["outcome"] == f"exit-{SESSION_LIMIT_CODE}":
            driven["outcome"] = "session-limit"
        final = run_eval.harness_json(workspace, "status", "--path", ".", "--run", run_id)
        findings = run_eval.harness_json(
            workspace, "findings", "list", "--path", ".", "--run", run_id
        )
        state = harness_state.collect(run_eval.harness, workspace, run_dir, run_id, final, findings)
        record["harness"] = {
            **{
                k: driven[k]
                for k in (
                    "outcome",
                    "corrections",
                    "gateHistory",
                    "decisions",
                    "waits",
                    "clarification",
                    "personSeconds",
                )
            },
            "delivered": driven["outcome"] == "approved",
            "finalStatus": final["execution"]["status"],
            "finalPhase": final["execution"]["currentPhase"],
            "eventChainValid": final["eventChainValid"],
            "findings": [{"severity": f["severity"], "ruleId": f["ruleId"]} for f in findings],
            "trace": state.get("trace"),
            "state": {k: v for k, v in state.items() if k != "trace"},
        }
        record["productOwner"] = owner.rounds
    record["session"] = {"segments": host.segments, "calls": len(host.segments)}
    record["wallSeconds"] = round(time.monotonic() - started, 3)
    record["agent"] = run_eval.summarize_calls(load_calls(run_dir / "agent-calls"))
    record["hostSession"] = run_eval.summarize_calls(load_calls(run_dir / "session-calls"))
    record["productOwnerCalls"] = run_eval.summarize_calls(load_calls(run_dir / "po-calls"))
    quarantine = ((record["harness"].get("state") or {}).get("measures") or {}).get("quarantine")
    if quarantine and quarantine.get("patchRef"):
        record["measures"] = run_eval.measure_quarantine(
            args.scenario,
            workspace,
            baseline,
            run_dir,
            args.cache,
            site_packages,
            quarantine["patchRef"],
        )
    else:
        record["measures"] = measure(args.scenario, workspace, baseline, run_dir, args.cache, HERE)
        record["measures"]["measuredOn"] = "workspace"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write(
            agentlib.anonymize(json.dumps(record, sort_keys=True), run_dir, HERE, args.work) + "\n"
        )
    print(
        json.dumps(
            {
                "scenario": args.scenario,
                "model": args.model,
                "rep": args.rep,
                "outcome": record["harness"]["outcome"],
                "segments": len(host.segments),
                "wallSeconds": record["wallSeconds"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
