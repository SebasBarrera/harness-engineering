"""The agent sandbox (``runtime.agentSandbox``) around command providers (issue #34).

The macOS tests run the provider under the real ``sandbox-exec``. The others inject the host, so
the wiring (blocking without a mechanism, wrapping every invocation) is checked on every
platform the suite runs on."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import (
    DecisionKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
)
from governed_harness.runtime.sandbox import SANDBOX_EXEC, SandboxHost

TASK = """\
title: Implement the threshold discount
intent: Apply a rate to subtotals at or above a threshold through a sandboxed agent.
acceptanceCriteria:
  - apply_discount(100, 100, 0.1) returns 90.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |

        def test_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""

APPLY_PATCHES = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "request = json.load(sys.stdin)\n"
    "for patch in request['task']['implementation']['patches']:\n"
    "    target = Path(patch['path'])\n"
    "    if patch['operation'] == 'replace':\n"
    "        target.write_text(patch['content'], encoding='utf-8')\n"
    "    else:\n"
    "        with target.open('a', encoding='utf-8') as handle:\n"
    "            handle.write(patch['content'])\n"
)

PASSED = "print(json.dumps({'status': 'PASSED', 'summary': 'patches applied'}))\n"


def configure(
    workspace: Path,
    agent: str,
    *,
    sandbox: str | None,
    write_paths: list[str] | None = None,
) -> None:
    (workspace / "agent_adapter.py").write_text(agent, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "sandboxed"
    config["agentProviders"] = {
        "sandboxed": {"kind": "command", "command": ["python", "agent_adapter.py"]}
    }
    if sandbox is None:
        config["runtime"].pop("agentSandbox")
    else:
        config["runtime"]["agentSandbox"] = sandbox
    if write_paths is not None:
        config["runtime"]["sandboxWritePaths"] = write_paths
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, Any]:
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK, encoding="utf-8")
    app = HarnessApplication()
    task = app.create_task(workspace, task_path)
    return app, app.start_run(workspace, task.task_id)


def events(app: HarnessApplication, workspace: Path, run: str) -> list[dict[str, Any]]:
    lines = app.trace(workspace, run, format="jsonl").decode().splitlines()
    return [json.loads(line) for line in lines if line]


def sandbox_findings(app: HarnessApplication, workspace: Path, run: str) -> list[Any]:
    return [
        item for item in app.list_findings(workspace, run) if item.validator_id == "harness.sandbox"
    ]


def inject(monkeypatch: pytest.MonkeyPatch, host: SandboxHost) -> None:
    monkeypatch.setattr(SandboxHost, "detect", classmethod(lambda cls: host))


macos_only = pytest.mark.skipif(
    sys.platform != "darwin" or not os.access(SANDBOX_EXEC, os.X_OK),
    reason="needs macOS sandbox-exec",
)


@macos_only
def test_writes_inside_the_workspace_and_declared_paths_succeed_and_outside_fail(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = tmp_path / "declared"
    outside = tmp_path / "outside"
    declared.mkdir()
    outside.mkdir()
    # The temporary directory of the host is the workspace itself, so the only writable places
    # are the workspace and the declared path (pytest's tmp_path lives under $TMPDIR).
    inject(
        monkeypatch,
        SandboxHost(
            system="Darwin",
            home=tmp_path / "home",
            temp_dir=python_workspace,
            sandbox_exec=SANDBOX_EXEC,
        ),
    )
    agent = (
        APPLY_PATCHES
        + "report = {}\n"
        + f"for name, target in (('declared', {str(declared / 'note.txt')!r}),"
        + f" ('outside', {str(outside / 'escape.txt')!r})):\n"
        + "    try:\n"
        + "        Path(target).write_text('x', encoding='utf-8')\n"
        + "        report[name] = 'written'\n"
        + "    except OSError as error:\n"
        + "        report[name] = type(error).__name__\n"
        + f"Path({str(declared / 'report.json')!r}).write_text(json.dumps(report))\n"
        + PASSED
    )
    configure(python_workspace, agent, sandbox="enforce", write_paths=[str(declared)])
    app, run = start(python_workspace, tmp_path)

    assert run.status is ResultStatus.BLOCKED and run.current_phase is PhaseId.DECISION
    assert json.loads((declared / "report.json").read_text()) == {
        "declared": "written",
        "outside": "PermissionError",
    }
    assert (
        (python_workspace / "src" / "sample" / "pricing.py")
        .read_text()
        .startswith("def apply_discount")
    )
    assert not (outside / "escape.txt").exists()
    applied = [
        item
        for item in events(app, python_workspace, run.execution_id)
        if item["eventType"] == "agent.sandbox.applied"
    ]
    assert len(applied) == 1
    payload = applied[0]["payload"]
    assert payload["mechanism"] == "sandbox-exec"
    assert payload["allowedPaths"] == [
        os.path.realpath(python_workspace),
        os.path.realpath(declared),
    ]
    evidence = app.list_evidence(python_workspace, run.execution_id)[0]["evidence"]
    sandbox_evidence = [
        item for item in evidence if item["summary"].startswith("Agent sandbox: sandbox-exec")
    ]
    assert len(sandbox_evidence) == 1
    assert sandbox_evidence[0]["phaseId"] == "IMPLEMENTATION"
    assert sandbox_findings(app, python_workspace, run.execution_id) == []


@macos_only
def test_a_provider_failing_on_a_denied_write_gets_a_finding(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "escape.txt"
    inject(
        monkeypatch,
        SandboxHost(
            system="Darwin",
            home=tmp_path / "home",
            temp_dir=python_workspace,
            sandbox_exec=SANDBOX_EXEC,
        ),
    )
    agent = APPLY_PATCHES + f"Path({str(target)!r}).write_text('x')\n" + PASSED
    configure(python_workspace, agent, sandbox="enforce", write_paths=[])
    app, run = start(python_workspace, tmp_path)

    assert run.status is ResultStatus.FAILED and run.current_phase is PhaseId.IMPLEMENTATION
    assert not target.exists()
    [finding] = sandbox_findings(app, python_workspace, run.execution_id)
    assert finding.rule_id == "sandbox.write-denied"
    assert finding.severity is FindingSeverity.MEDIUM
    assert finding.location is not None and finding.location.path == str(target)
    assert finding.evidence_refs


@pytest.mark.parametrize("system", ["Linux", "Windows", "Darwin"])
def test_enforce_without_a_mechanism_blocks_before_the_agent_runs(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str
) -> None:
    inject(monkeypatch, SandboxHost(system=system, home=tmp_path, temp_dir=tmp_path))
    configure(
        python_workspace,
        "from pathlib import Path\nPath('agent-ran.txt').write_text('ran')\n",
        sandbox="enforce",
    )
    app, run = start(python_workspace, tmp_path)

    assert run.status is ResultStatus.BLOCKED and run.current_phase is PhaseId.IMPLEMENTATION
    assert not (python_workspace / "agent-ran.txt").exists()
    [finding] = sandbox_findings(app, python_workspace, run.execution_id)
    assert finding.rule_id == "sandbox.unavailable"
    assert finding.severity is FindingSeverity.HIGH
    assert app.status(python_workspace, run.execution_id)["metrics"]["agent.invocations"][
        "value"
    ] in (0, None)


@pytest.mark.parametrize("sandbox", ["off", None])
def test_off_and_absent_run_the_agent_unconfined(
    python_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: str | None,
) -> None:
    inject(monkeypatch, SandboxHost(system="Windows", home=tmp_path, temp_dir=tmp_path))
    configure(python_workspace, APPLY_PATCHES + PASSED, sandbox=sandbox)
    app, run = start(python_workspace, tmp_path)

    assert run.status is ResultStatus.BLOCKED and run.current_phase is PhaseId.DECISION
    assert not any(
        item["eventType"] == "agent.sandbox.applied"
        for item in events(app, python_workspace, run.execution_id)
    )
    assert sandbox_findings(app, python_workspace, run.execution_id) == []


@pytest.mark.skipif(os.name == "nt", reason="the stand-in wrapper is a POSIX shell script")
def test_every_implementation_attempt_runs_confined(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction cycle starts the provider again; it runs under the sandbox again."""
    log = tmp_path / "wrapper.log"
    wrapper = tmp_path / "fake-sandbox-exec"
    wrapper.write_text(f'#!/bin/sh\necho "$1" >> "{log}"\nshift 2\nexec "$@"\n')
    wrapper.chmod(0o755)
    inject(
        monkeypatch,
        SandboxHost(system="Darwin", home=tmp_path, temp_dir=tmp_path, sandbox_exec=str(wrapper)),
    )
    configure(python_workspace, APPLY_PATCHES + PASSED, sandbox="enforce")
    app, run = start(python_workspace, tmp_path)
    assert run.current_phase is PhaseId.DECISION and run.change_set_digest
    app.decide_gate(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=run.change_set_digest,
        actor_id="human.reviewer",
        rationale="Run the agent again",
    )
    app.continue_run(python_workspace, run.execution_id)

    assert log.read_text().splitlines() == ["-p", "-p"]
    applied = [
        item["payload"]["profileDigest"]
        for item in events(app, python_workspace, run.execution_id)
        if item["eventType"] == "agent.sandbox.applied"
    ]
    assert len(applied) == 2 and applied[0] == applied[1]
