#!/usr/bin/env python3
"""Fault-injection probes: exercise each control of the harness with a known defect.

Every probe prepares the brownfield repository (pallets/itsdangerous 2.2.0), runs the brownfield
task with a deterministic provider that applies the reference solution plus one fault, and
records the exit codes, statuses, gate reasons and findings. Each probe is repeated to check that
the outcome is deterministic.

Usage: python fault_probes.py --work DIR --cache DIR --out FILE [--reps N] [--nopytest-venv DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_eval import prepare  # noqa: E402

TASK = HERE / "tasks" / "brownfield-itsdangerous.yaml"
PROBES = [
    "correct",
    "regression",
    "failing-test",
    "secret",
    "dynamic-eval",
    "todo",
    "missing-tool",
    "later-change",
    "tamper-events",
    "out-of-scope",
    "timeout",
    "output-flood",
    "unauthorized-command",
]


def run(workspace: Path, env: dict[str, str], *args: str) -> tuple[int, Any]:
    proc = subprocess.run(["harness", *args], cwd=workspace, env=env, capture_output=True, text=True)
    payload: Any = {"stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]}
    for stream in (proc.stdout, proc.stderr):
        try:
            payload = json.loads(stream)
            break
        except json.JSONDecodeError:
            continue
    return proc.returncode, payload


def snapshot(workspace: Path, env: dict[str, str], run_id: str) -> dict[str, Any]:
    status_code, status = run(workspace, env, "status", "--path", ".", "--run", run_id)
    if "execution" not in status:
        trace_code, _ = run(workspace, env, "trace", "--path", ".", "--run", run_id, "--format", "json",
                            "--output", str(workspace.parent / "trace.json"))
        return {"statusExitCode": status_code, "statusError": status.get("errorType"),
                "traceExportExitCode": trace_code}
    _, findings = run(workspace, env, "findings", "list", "--path", ".", "--run", run_id)
    gate = status.get("gate") or {}
    db = sqlite3.connect(workspace / ".harness" / "state.db")
    try:
        files = [json.loads(r[0]).get("files", []) for r in db.execute(
            "select payload_json from records where record_type='change_set' and execution_id=?", (run_id,))]
        tools = [json.loads(r[0]) for r in db.execute(
            "select payload_json from records where record_type='tool_invocation' and execution_id=?", (run_id,))]
    finally:
        db.close()
    latest_files = sorted(f.get("path") for f in files[-1]) if files else []
    return {
        "status": status["execution"]["status"],
        "phase": status["execution"]["currentPhase"],
        "terminalReason": status["execution"].get("terminalReason"),
        "gate": gate.get("status"),
        "gateReasons": [r if not r.startswith("BLOCKING_FINDING_") else "BLOCKING_FINDING" for r in gate.get("reasonCodes", [])],
        "validations": status["validationSummary"],
        "findings": sorted({f"{f['severity']}:{f['ruleId']}" for f in findings}) if isinstance(findings, list) else [],
        "changeSetFiles": latest_files,
        "toolStatuses": sorted({t.get("status") for t in tools}),
        "eventCount": status["eventCount"],
        "eventChainValid": status["eventChainValid"],
    }


def probe(name: str, rep: int, args: argparse.Namespace, site_packages: Path) -> dict[str, Any]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.work / f"probe-{name}-r{rep}-{stamp}"
    workspace = run_dir / "ws"
    venv_bin = Path(sys.executable).parent
    if name == "missing-tool":
        venv_bin = args.nopytest_venv / "bin"
        site_packages = next((args.nopytest_venv / "lib").glob("python3*/site-packages"))
    prepare("brownfield", workspace, args.cache, site_packages)
    env = {
        "PATH": f"{venv_bin}:/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "LANG": "en_US.UTF-8",
    }
    steps: list[dict[str, Any]] = []

    def step(label: str, *cmd: str) -> tuple[int, Any]:
        code, payload = run(workspace, env, *cmd)
        steps.append({"step": label, "exitCode": code})
        return code, payload

    step("init", "init", "--path", ".")
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text())
    fault = name if name in {"correct", "regression", "failing-test", "secret", "dynamic-eval", "todo",
                             "out-of-scope", "timeout", "output-flood", "missing-tool"} else "correct"
    command = ["python", str(HERE / "fault_provider.py"), "--fault", fault]
    if name == "unauthorized-command":
        command = ["sh", "-c", "echo not-allowed"]
    config["agentProvider"] = "probe"
    config["agentProviders"] = {"probe": {"kind": "command", "command": command, "model": "deterministic-probe"}}
    if name == "timeout":
        config["runtime"]["commandTimeoutSeconds"] = 5
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    task_file = TASK
    if name == "out-of-scope":
        task = yaml.safe_load(TASK.read_text())
        task["metadata"] = {"ownedPaths": ["src/itsdangerous/encoding.py", "tests/test_itsdangerous/test_encoding.py"]}
        task_file = run_dir / "task.yaml"
        task_file.write_text(yaml.safe_dump(task, sort_keys=False))
    _, task_record = step("task create", "task", "create", "--path", ".", "--file", str(task_file))
    code, started = step("run start", "run", "start", "--path", ".", "--task", task_record["taskId"])
    run_id = started.get("executionId", "")
    after_start = snapshot(workspace, env, run_id)
    result: dict[str, Any] = {"probe": name, "rep": rep, "afterStart": after_start}

    digest_status = run(workspace, env, "status", "--path", ".", "--run", run_id)[1]
    digest = digest_status["execution"].get("changeSetDigest") or ""
    delivered = False
    if name == "later-change" and code == 4:
        encoding = workspace / "src" / "itsdangerous" / "encoding.py"
        encoding.write_text(encoding.read_text() + "\n# adjusted after review\n")
        step("APPROVE with the evaluated digest", "gate", "decide", "--path", ".", "--run", run_id, "--decision",
             "APPROVE", "--change-set-digest", digest, "--actor", "human.reviewer", "--rationale", "approve")
        step("run continue", "run", "continue", "--path", ".", "--run", run_id)
        result["afterContinue"] = snapshot(workspace, env, run_id)
    elif code == 4:
        approve, _ = step("APPROVE", "gate", "decide", "--path", ".", "--run", run_id, "--decision", "APPROVE",
                          "--change-set-digest", digest, "--actor", "human.reviewer", "--rationale", "approve")
        delivered = approve == 0
        if approve != 0:
            step("APPROVE_EXCEPTION without rationale", "gate", "decide", "--path", ".", "--run", run_id,
                 "--decision", "APPROVE_EXCEPTION", "--change-set-digest", digest, "--actor", "human.reviewer",
                 "--rationale", " ")
            step("REQUEST_CHANGES", "gate", "decide", "--path", ".", "--run", run_id, "--decision",
                 "REQUEST_CHANGES", "--change-set-digest", digest, "--actor", "human.reviewer",
                 "--rationale", "remove the blocking finding")
            result["afterRequestChanges"] = snapshot(workspace, env, run_id)
        if name == "tamper-events" and delivered:
            db = sqlite3.connect(workspace / ".harness" / "state.db")
            db.execute(
                "update events set payload_json = replace(payload_json, 'approve', 'approved!') "
                "where event_type = 'human.decision.recorded' and execution_id = ?", (run_id,))
            db.commit()
            db.close()
    result["final"] = snapshot(workspace, env, run_id)
    tree = subprocess.run(["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True,
                          env={**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}).stdout
    result["treeChangedFiles"] = sorted(line[3:] for line in tree.splitlines() if ".harness" not in line)
    result["steps"] = steps
    result["delivered"] = delivered
    result["providerArtifactBytes"] = max(
        (p.stat().st_size for p in (workspace / ".harness" / "artifacts").rglob("*") if p.is_file()), default=0
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--nopytest-venv", type=Path, required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    names = args.only.split(",") if args.only else PROBES
    with args.out.open("a", encoding="utf-8") as handle:
        for rep in range(1, args.reps + 1):
            for name in names:
                record = probe(name, rep, args, site_packages)
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                final = record["final"]
                print(name, rep, [s["exitCode"] for s in record["steps"]], final.get("status"),
                      final.get("phase"), final.get("gate"), final.get("eventChainValid"),
                      final.get("statusError", ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
