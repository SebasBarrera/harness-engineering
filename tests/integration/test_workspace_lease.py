"""One harness process per workspace and safe recovery after a crash (#47,
``governance.workspaceLease``)."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.runtime.lease import WorkspaceBusyError, WorkspaceLease

pytestmark = pytest.mark.skipif(os.name == "nt", reason="process groups and signals are POSIX")

AGENT = (
    "import json, sys, time\n"
    "from pathlib import Path\n"
    "json.load(sys.stdin)\n"
    "with open('src/sample/pricing.py', 'a', encoding='utf-8') as handle:\n"
    "    handle.write('# agent change\\n')\n"
    "slow = Path(sys.argv[1])\n"
    "if slow.exists():\n"
    "    Path(sys.argv[2]).write_text('started')\n"
    "    time.sleep(60)\n"
    "print(json.dumps({'status': 'PASSED', 'summary': 'done'}))\n"
)
TASK = (
    "taskId: task_lease\n"
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
)


def _configure(workspace: Path, tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "agent.py"
    script.write_text(AGENT, encoding="utf-8")
    slow, started = tmp_path / "slow", tmp_path / "started"
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {
        "fixture": {"kind": "command", "command": ["python", str(script), str(slow), str(started)]}
    }
    config["runtime"]["agentSandbox"] = "off"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (tmp_path / "task.yaml").write_text(TASK, encoding="utf-8")
    HarnessApplication().create_task(workspace, tmp_path / "task.yaml")
    return slow, started


def _lease_file(workspace: Path, pid: int, heartbeat: float) -> None:
    (workspace / ".harness" / "lease.json").write_text(
        json.dumps(
            {
                "token": "other",
                "pid": pid,
                "host": socket.gethostname(),
                "command": "run start",
                "runId": "run_other",
                "acquiredAt": "2026-10-04T00:00:00+00:00",
                "heartbeatAt": heartbeat,
            }
        ),
        encoding="utf-8",
    )


def test_second_harness_on_a_leased_workspace_is_refused(
    python_workspace: Path, tmp_path: Path
) -> None:
    _configure(python_workspace, tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        _lease_file(python_workspace, holder.pid, time.time())
        result = CliRunner().invoke(
            app, ["run", "start", "--task", "task_lease", "--path", str(python_workspace)]
        )
        assert result.exit_code == 5
        assert "in use by run run_other" in result.stderr
        assert HarnessApplication().list_runs(python_workspace) == []
    finally:
        holder.kill()
        holder.wait()
    # The holder is gone: its lease is stale and is taken over.
    result = CliRunner().invoke(
        app, ["run", "start", "--task", "task_lease", "--path", str(python_workspace)]
    )
    assert result.exit_code == 4, result.stderr
    assert not (python_workspace / ".harness" / "lease.json").exists()


def test_lease_is_exclusive_in_one_process(tmp_path: Path) -> None:
    first = WorkspaceLease(tmp_path, "run start").acquire()
    try:
        with pytest.raises(WorkspaceBusyError):
            WorkspaceLease(tmp_path, "run continue").acquire()
    finally:
        first.release()
    WorkspaceLease(tmp_path, "run continue").acquire().release()


def test_killed_harness_is_recovered_without_implementing_twice(
    python_workspace: Path, tmp_path: Path
) -> None:
    slow, started = _configure(python_workspace, tmp_path)
    slow.write_text("1")
    harness = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "governed_harness",
            "run",
            "start",
            "--task",
            "task_lease",
            "--path",
            str(python_workspace),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while not started.exists():
        assert time.monotonic() < deadline, "the agent did not start"
        assert harness.poll() is None, "the harness stopped before the agent started"
        time.sleep(0.1)
    # A hard kill: no handler runs, the agent keeps running and the phase stays RUNNING.
    harness.send_signal(signal.SIGKILL)
    harness.wait()
    run_id = HarnessApplication().list_runs(python_workspace)[0].execution_id
    status = HarnessApplication().status(python_workspace, run_id)
    assert status["execution"]["status"] == "RUNNING"
    flags = _flag(python_workspace, f"process:{run_id}")
    agent_group = int(next(iter(json.loads(flags).values()))["pgid"])
    os.killpg(agent_group, 0)  # the orphaned agent is still alive

    slow.unlink()
    resumed = HarnessApplication().continue_run(python_workspace, run_id)
    assert resumed.current_phase.value == "DECISION"
    with pytest.raises(ProcessLookupError):
        _wait_gone(agent_group)
    source = (python_workspace / "src" / "sample" / "pricing.py").read_text(encoding="utf-8")
    assert source.count("# agent change") == 1
    status = HarnessApplication().status(python_workspace, run_id)
    implementation = [item for item in status["phases"] if item["phaseId"] == "IMPLEMENTATION"]
    assert [item["status"] for item in implementation] == ["INTERRUPTED", "PASSED"]
    trace = HarnessApplication().trace(python_workspace, run_id, "jsonl").decode().splitlines()
    recovered = [json.loads(line) for line in trace if '"run.recovered"' in line]
    assert recovered[0]["payload"]["restoredPaths"] == ["src/sample/pricing.py"]
    assert recovered[0]["payload"]["terminatedProcessGroups"] == [agent_group]


def _flag(workspace: Path, key: str) -> str:
    import sqlite3

    with sqlite3.connect(workspace / ".harness" / "state.db") as connection:
        return str(connection.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()[0])


def _wait_gone(group: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        os.killpg(group, 0)  # raises ProcessLookupError once the group is gone
        time.sleep(0.05)


def test_sigterm_stops_the_agent_and_records_the_interruption(
    python_workspace: Path, tmp_path: Path
) -> None:
    slow, started = _configure(python_workspace, tmp_path)
    slow.write_text("1")
    harness = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "governed_harness",
            "run",
            "start",
            "--task",
            "task_lease",
            "--path",
            str(python_workspace),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while not started.exists():
        assert time.monotonic() < deadline, "the agent did not start"
        time.sleep(0.1)
    run_id = HarnessApplication().list_runs(python_workspace)[0].execution_id
    agent_group = int(
        next(iter(json.loads(_flag(python_workspace, f"process:{run_id}")).values()))["pgid"]
    )
    harness.send_signal(signal.SIGTERM)
    assert harness.wait(timeout=30) == 128 + signal.SIGTERM
    with pytest.raises(ProcessLookupError):
        _wait_gone(agent_group)
    status = HarnessApplication().status(python_workspace, run_id)
    assert status["execution"]["status"] == "INTERRUPTED"
    implementation = [item for item in status["phases"] if item["phaseId"] == "IMPLEMENTATION"]
    assert [item["status"] for item in implementation] == ["INTERRUPTED"]
    assert not (python_workspace / ".harness" / "lease.json").exists()
    slow.unlink()
    resumed = HarnessApplication().continue_run(python_workspace, run_id)
    assert resumed.current_phase.value == "DECISION"
    source = (python_workspace / "src" / "sample" / "pricing.py").read_text(encoding="utf-8")
    assert source.count("# agent change") == 1
