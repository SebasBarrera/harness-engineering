#!/usr/bin/env python3
"""Run one controlled evaluation run and append its record to a JSONL file.

A run is one (scenario, condition, prompt level, model, repetition). Conditions (2.0.0 evaluation,
see ``conditions.py`` for the exact configuration of each):

* ``direct`` (``baseline`` in the 0.9.0 records): Claude Code alone on the prepared repository;
* ``harness-core``: the harness with the 1.0.0 configuration (no key added after 1.0.0);
* ``harness``: the harness as ``harness init`` configures it, routing fixed to the cell's model;
* ``harness-tiered``: the same with the router choosing the model and effort of every call;
* ``clarify``: the 1.1.0 pilot (legacy; ``harness init`` of the version under test, poor prompt).

Every governed run is driven by the simulated person (``person.py``: the 0.9.0 decision rule at
DECISION and a fixed rule for every other wait) with the simulated product owner
(``product_owner.py``) for clarification questions. Every run, whatever the condition, is measured
afterwards by ``measure.py`` in the same way; a run whose changes the harness quarantined (stop the
line) is measured on a copy of the workspace with the quarantined patch applied.

``--dry-run`` replaces the Claude Code CLI with ``fake_claude.py`` everywhere (no model call); with
``--provider simulated`` the governed conditions use the harness's deterministic provider instead
of the evaluation adapter. ``EVAL_HARNESS`` (a command line, default ``harness``) selects the harness
executable; the run records its version.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
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

import agentlib  # noqa: E402
import conditions  # noqa: E402
import harness_state  # noqa: E402
from agentlib import build_prompt, load_calls, run_claude, run_codex  # noqa: E402
from measure import measure  # noqa: E402
from person import MAX_CORRECTIONS, SimulatedPerson  # noqa: E402
from product_owner import ProductOwner  # noqa: E402

__all__ = [
    "MAX_CORRECTIONS",
    "configure_provider",
    "git",
    "harness",
    "harness_json",
    "prepare",
    "run_harness",
]

# Rounds of clarification answered by a clarifier (harness 1.1.0 and later, see product_owner.py).
MAX_CLARIFY_ROUNDS = 3
# Set by the clarify condition only: agent calls resume after a usage limit or a failure of the machine,
# and the harness waits for them; the evaluated conditions keep one plain call.
RESUMABLE_PROVIDER = False
HARNESS_CMD = shlex.split(os.environ.get("EVAL_HARNESS", "harness"))
# Where the harness runs the evaluation adapter from. The implement request shows the agent the
# provider's command line (``permissions.process``), so the launchers point this at a directory that
# holds only the adapter (agentlib.py, claude_provider.py): never the hidden tests or the references.
PROVIDER_CODE = Path(os.environ.get("EVAL_PROVIDER_CODE", str(HERE)))
# Extra environment of every harness command of the current run (the run registry inside the run
# directory, see harness_state.state_db).
HARNESS_ENV: dict[str, str] = {}
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
CONDITION_CHOICES = ["direct", "baseline", "harness-core", "harness", "harness-tiered", "clarify"]
GIT = [
    "git",
    "-c",
    "user.name=eval",
    "-c",
    "user.email=eval@example.invalid",
    "-c",
    "commit.gpgsign=false",
]
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        [*GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


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
    point_import_path(site_packages, workspace)
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "baseline")
    return git(workspace, "rev-parse", "HEAD").strip()


def point_import_path(site_packages: Path, workspace: Path) -> None:
    (site_packages / "zz_eval_workspace.pth").write_text(
        str(workspace / "src") + "\n", encoding="utf-8"
    )


def harness(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV, **HARNESS_ENV}
    return subprocess.run(
        [*HARNESS_CMD, *args], cwd=workspace, capture_output=True, text=True, env=env
    )


def harness_json(workspace: Path, *args: str) -> Any:
    proc = harness(workspace, *args)
    return json.loads(proc.stdout)


def harness_version() -> str:
    proc = subprocess.run([*HARNESS_CMD, "--version"], capture_output=True, text=True)
    return proc.stdout.strip() or proc.stderr.strip()


def configure_provider(
    workspace: Path, model: str, agent: str = "claude", effort: str = ""
) -> None:
    """The provider configuration of the 0.9.0 and 1.0.0 evaluations (kept for rides/ and large/)."""
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    command = ["python", str(HERE / "claude_provider.py"), "--model", model]
    if agent == "codex":
        command = ["python", str(HERE / "codex_provider.py"), "--model", model, "--effort", effort]
    config["agentProvider"] = agent
    config["agentProviders"] = {agent: {"kind": "command", "command": command, "model": model}}
    config["runtime"]["commandTimeoutSeconds"] = 1800
    if RESUMABLE_PROVIDER and agent == "claude":
        command.append("--resumable")
        config["runtime"]["commandTimeoutSeconds"] = 7 * 24 * 3600
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def feedback_from(workspace: Path, run_id: str, status: dict[str, Any]) -> str:
    gate = status.get("gate") or {}
    findings = harness_json(workspace, "findings", "list", "--path", ".", "--run", run_id)
    lines = [
        f"Gate status: {gate.get('status')}; reasons: {', '.join(gate.get('reasonCodes', []))}"
    ]
    for finding in findings:
        where = (finding.get("location") or {}).get("path") or "-"
        lines.append(
            f"- [{finding['severity']}] {finding['ruleId']} at {where}: {finding['message']}"
        )
    return "\n".join(lines)


def run_harness(
    workspace: Path,
    run_dir: Path,
    task_file: Path,
    model: str,
    agent: str = "claude",
    effort: str = "",
    clarifier: Any = None,
    *,
    condition: str | None = None,
    provider: str = "claude",
    claude_bin: str | None = None,
) -> dict[str, Any]:
    """Run one task through the harness and drive it with the simulated person.

    ``condition`` None keeps the configuration of the earlier evaluations (``harness init`` and
    ``configure_provider``, used by rides/ and large/); otherwise ``conditions.write_config``
    writes the configuration of that condition."""
    events: list[dict[str, Any]] = []

    def step(*args: str) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        proc = harness(workspace, *args)
        events.append(
            {
                "command": list(args[:2]),
                "exitCode": proc.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        return proc

    def tracked(ws: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return step(*args)

    if condition is None:
        step("init", "--path", ".")
        configure_provider(workspace, model, agent, effort)
    else:
        conditions.write_config(
            workspace,
            condition,
            model,
            run_dir=run_dir,
            code=PROVIDER_CODE,
            harness=tracked,
            provider=provider,
            claude_bin=claude_bin,
            resumable=RESUMABLE_PROVIDER,
        )
    shutil.copy2(workspace / ".harness" / "project.yaml", run_dir / "project.yaml")
    created = step("task", "create", "--path", ".", "--file", str(task_file))
    if created.returncode != 0:
        return {
            "runId": None,
            "outcome": f"task-refused-exit-{created.returncode}",
            "delivered": False,
            "corrections": 0,
            "gateHistory": [],
            "taskCreateError": created.stderr[-500:] or created.stdout[-500:],
            "commands": events,
        }
    task = json.loads(created.stdout)
    started = step(
        "run",
        "start",
        "--path",
        ".",
        "--task",
        task["taskId"],
        "--provider",
        "simulated" if provider == "simulated" else agent,
    )
    run_id = json.loads(started.stdout)["executionId"]
    person = SimulatedPerson(
        tracked,
        workspace,
        run_dir,
        clarifier=clarifier,
        feedback=lambda rid, status: feedback_from(workspace, rid, status),
    )
    driven = person.drive(run_id, task["taskId"], started.returncode)
    final = harness_json(workspace, "status", "--path", ".", "--run", run_id)
    findings = harness_json(workspace, "findings", "list", "--path", ".", "--run", run_id)
    state = harness_state.collect(harness, workspace, run_dir, run_id, final, findings)
    trace = state.get("trace") or {"present": 0, "requiredCount": 0}
    return {
        "runId": "run",  # anonymized: the local identifier is not published
        "outcome": driven["outcome"],
        "delivered": driven["outcome"] == "approved",
        "corrections": driven["corrections"],
        "gateHistory": driven["gateHistory"],
        "decisions": driven["decisions"],
        "waits": driven["waits"],
        "personSeconds": driven["personSeconds"],
        "finalStatus": final["execution"]["status"],
        "finalPhase": final["execution"]["currentPhase"],
        "terminalReason": final["execution"].get("terminalReason"),
        "configurationDigest": final["execution"].get("configurationDigest"),
        "validationSummary": final["validationSummary"],
        "findings": [
            {
                "severity": f["severity"],
                "ruleId": f["ruleId"],
                "validatorId": f.get("validatorId"),
                "path": (f.get("location") or {}).get("path"),
            }
            for f in findings
        ],
        "eventCount": final["eventCount"],
        "eventChainValid": final["eventChainValid"],
        "recordsValid": final.get("recordsValid"),
        "clarification": driven["clarification"],
        "harnessMetrics": {name: item.get("value") for name, item in final["metrics"].items()},
        "trace": trace,
        "state": {k: v for k, v in state.items() if k != "trace"},
        "commands": events,
        "_runId": run_id,
    }


def trace_completeness(
    workspace: Path, status: dict[str, Any], run_dir: Path | None = None
) -> dict[str, Any]:
    """The eight relations of the 0.9.0 evaluation (and the 2.0.0 ones), see harness_state."""
    path = harness_state.state_db(workspace, run_dir or workspace.parent)
    state = harness_state.State(path) if path else None
    if state is None:
        return {"relations": {}, "required": [], "present": 0, "requiredCount": 0}
    try:
        return harness_state.trace_completeness(state, status)
    finally:
        state.close()


def casual_task(task_file: Path, text: str, run_dir: Path) -> Path:
    """A one-line prompt as a task file: the text is the title and the intent, and there is no
    acceptance criterion (2.0.0 stores it with criteriaPending and asks for criteria in INTENT;
    the 1.0.0 configuration refuses it at task create)."""
    task = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    path = run_dir / "task-casual.yaml"
    line = text.strip().splitlines()[0]
    path.write_text(
        yaml.safe_dump(
            {"taskId": task["taskId"] + "_casual", "title": line[:120], "intent": text.strip()},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def measure_quarantine(
    scenario: str,
    workspace: Path,
    baseline: str,
    run_dir: Path,
    cache: Path,
    site_packages: Path,
    patch_ref: str,
) -> dict[str, Any]:
    """Measure the stopped run's own change: a copy of the workspace with the quarantined patch."""
    patch = harness(workspace, "artifact", "show", "--path", ".", patch_ref)
    copy = run_dir / "quarantine-ws"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns(".harness"), symlinks=True)
    patch_file = run_dir / "quarantined.patch"
    patch_file.write_text(patch.stdout, encoding="utf-8")
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch_file)],
        cwd=copy,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )
    point_import_path(site_packages, copy)
    try:
        measured = measure(
            scenario, copy, baseline, run_dir, cache, HERE, scratch_name="measure-quarantine"
        )
    finally:
        point_import_path(site_packages, workspace)
    measured["measuredOn"] = "quarantine-copy"
    measured["patchApply"] = {
        "exitCode": applied.returncode,
        "stderr": applied.stderr[-500:],
        "showExit": patch.returncode,
    }
    return measured


def summarize_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str) -> Any:
        return sum(c.get(key) or 0 for c in calls)

    return {
        "calls": len(calls),
        "costUsd": (
            round(sum(c["costUsd"] for c in calls), 6)
            if calls and all(c.get("costUsd") is not None for c in calls)
            else None
        ),
        "reasoningTokens": total("reasoningTokens"),
        "inputTokens": total("inputTokens"),
        "outputTokens": total("outputTokens"),
        "cacheReadTokens": total("cacheReadTokens"),
        "cacheCreationTokens": total("cacheCreationTokens"),
        "turns": total("numTurns"),
        "wallSeconds": round(sum(c.get("wallSeconds") or 0 for c in calls), 3),
        "apiSeconds": round(sum((c.get("durationApiMs") or 0) for c in calls) / 1000, 3),
        "errors": sum(1 for c in calls if c.get("isError")),
        "permissionDenials": sum(c.get("permissionDenials") or 0 for c in calls),
    }


def calls_by(calls: list[dict[str, Any]], *keys: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        label = "|".join(str(call.get(k) or ("implement" if k == "kind" else "-")) for k in keys)
        groups.setdefault(label, []).append(call)
    return {label: summarize_calls(items) for label, items in sorted(groups.items())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--condition", choices=CONDITION_CHOICES, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--agent", choices=["claude", "codex"], default="claude")
    parser.add_argument("--prompt", choices=["full", "poor", "casual"], default="full")
    parser.add_argument("--effort", default="")
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fake_claude.py instead of Claude Code (no model call)",
    )
    parser.add_argument(
        "--provider",
        choices=["claude", "simulated"],
        default="claude",
        help="governed conditions: the evaluation adapter or the harness's simulated provider",
    )
    parser.add_argument(
        "--fake-mode", default="", help="dry runs: behaviour of fake_claude.py (broken)"
    )
    parser.add_argument(
        "--core-reference-src",
        type=Path,
        default=None,
        help="harness-core: src/ of harness 1.0.0, to compare the configuration digest",
    )
    parser.add_argument(
        "--no-product-owner",
        action="store_true",
        help="governed conditions: no simulated product owner (task questions stop the run)",
    )
    args = parser.parse_args()
    condition = "direct" if args.condition == "baseline" else args.condition
    if args.provider == "simulated" and not args.dry_run:
        parser.error("--provider simulated is a dry run: add --dry-run")
    claude_bin = None
    if args.dry_run:
        claude_bin = str(agentlib.FAKE_CLAUDE)
        agentlib.CLAUDE_BIN = claude_bin
    elif args.agent == "claude":
        agentlib.ensure_account()

    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag = "" if args.prompt == "full" else f"-{args.prompt}"
    run_dir = args.work / f"{args.scenario}{tag}-{condition}-{args.model}-r{args.rep}-{stamp}"
    workspace = run_dir / "ws"
    baseline = prepare(args.scenario, workspace, args.cache, site_packages)
    HARNESS_ENV["HARNESS_STATE_DIR"] = str((run_dir / "state").resolve())
    if args.fake_mode:
        (run_dir / "fake-mode").write_text(args.fake_mode, encoding="utf-8")
    task_file = SCENARIOS[args.scenario]["task"]
    if args.prompt == "poor":
        task_file = task_file.with_name(task_file.stem + "-poor.yaml")
    casual_prompt = None
    if args.prompt == "casual":
        casual_prompt = task_file.with_name(task_file.stem + "-casual.txt").read_text(
            encoding="utf-8"
        )
    task = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    if condition == "clarify" and args.prompt != "poor":
        parser.error("the clarify condition applies to the poor prompt (harness 1.1.0 or later)")

    started = time.monotonic()
    record: dict[str, Any] = {
        "scenario": args.scenario,
        "condition": condition,
        "model": args.model,
        "agentName": args.agent,
        "prompt": args.prompt,
        "effort": args.effort or None,
        "rep": args.rep,
        "dryRun": args.dry_run,
        "provider": args.provider if condition != "direct" else None,
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    owner = None
    if condition == "direct":
        log = run_dir / "agent-calls" / "call-1.json"
        prompt = casual_prompt if casual_prompt is not None else build_prompt(task)
        if args.agent == "codex":
            run_codex(prompt, workspace, args.model, args.effort, log)
        else:
            run_claude(prompt, workspace, args.model, log)
        record["direct"] = {"delivered": True}
    else:
        record["harnessVersion"] = harness_version()
        governed_task = (
            casual_task(task_file, casual_prompt, run_dir)
            if casual_prompt is not None
            else task_file
        )
        # The simulated product owner knows the full task and the specification the prompt leaves out.
        full_task = SCENARIOS[args.scenario]["task"]
        spec = SCENARIOS[args.scenario].get("fixture", Path("-")) / "SPEC.md"
        knowledge = "Full task description:\n" + full_task.read_text(encoding="utf-8")
        if spec.is_file():
            knowledge += "\nSPEC.md:\n" + spec.read_text(encoding="utf-8")
        if not args.no_product_owner:
            owner = ProductOwner(knowledge, args.model, run_dir)
        if condition == "clarify":
            global RESUMABLE_PROVIDER  # noqa: PLW0603
            RESUMABLE_PROVIDER = True
            result = run_harness(
                workspace,
                run_dir,
                governed_task,
                args.model,
                args.agent,
                args.effort,
                clarifier=owner,
            )
        else:
            result = run_harness(
                workspace,
                run_dir,
                governed_task,
                args.model,
                args.agent,
                args.effort,
                clarifier=owner,
                condition=condition,
                provider=args.provider,
                claude_bin=claude_bin,
            )
        run_id = result.pop("_runId", None)
        if condition == "harness-core":
            result["coreCheck"] = conditions.check_core(workspace, harness, args.core_reference_src)
        record["harness"] = result
        record["productOwner"] = owner.rounds if owner else []
        record["projectYamlFile"] = "project.yaml"
        quarantine = ((result.get("state") or {}).get("measures") or {}).get("quarantine")
        record["quarantine"] = quarantine
        record["_run"] = run_id
    record["wallSeconds"] = round(time.monotonic() - started, 3)
    calls = load_calls(run_dir / "agent-calls")
    po_calls = load_calls(run_dir / "po-calls")
    record["agentCalls"] = [
        {k: v for k, v in c.items() if k not in ("summary", "resultText")} for c in calls
    ]
    record["agent"] = summarize_calls(calls)
    record["agentByKind"] = calls_by(calls, "kind")
    record["agentByKindModel"] = calls_by(calls, "kind", "model")
    governance = [c for c in calls if c.get("kind") not in (None, "implement")]
    record["agentSplit"] = {
        "implementation": summarize_calls(
            [c for c in calls if c.get("kind") in (None, "implement")]
        ),
        "governance": summarize_calls(governance),
    }
    record["productOwnerCalls"] = summarize_calls(po_calls)
    commands = (record.get("harness") or {}).get("commands") or []
    harness_seconds = sum(c["seconds"] for c in commands)
    model_seconds = record["agent"]["wallSeconds"] if condition != "direct" else 0.0
    # P15: the time of the harness's own processes apart from the time of the model calls they wait
    # for, and both apart from the simulated person (product owner calls) and the runner.
    record["timeSplit"] = {
        "totalSeconds": record["wallSeconds"],
        "agentCallSeconds": record["agent"]["wallSeconds"],
        "harnessCommandSeconds": round(harness_seconds, 3),
        "harnessProcessSeconds": round(harness_seconds - model_seconds, 3)
        if condition != "direct"
        else None,
        "productOwnerSeconds": record["productOwnerCalls"]["wallSeconds"],
    }
    record.pop("_run", None)
    if record.get("quarantine") and record["quarantine"].get("patchRef"):
        record["measures"] = measure_quarantine(
            args.scenario,
            workspace,
            baseline,
            run_dir,
            args.cache,
            site_packages,
            record["quarantine"]["patchRef"],
        )
        record["measuresFinalTree"] = measure(
            args.scenario,
            workspace,
            baseline,
            run_dir,
            args.cache,
            HERE,
            scratch_name="measure-final-tree",
            hidden_only=True,
        )
    else:
        record["measures"] = measure(args.scenario, workspace, baseline, run_dir, args.cache, HERE)
        record["measures"]["measuredOn"] = "workspace"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    summary = {
        k: record[k] for k in ("scenario", "condition", "prompt", "model", "rep", "wallSeconds")
    }
    summary["outcome"] = (record.get("harness") or {}).get("outcome", "direct")
    summary["calls"] = record["agent"]["calls"]
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
