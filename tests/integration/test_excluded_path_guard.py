"""Writes outside the ChangeSet (#46, ``governance.protectExcludedPaths``): a change to ``.git``,
a virtual environment or build output during IMPLEMENTATION is a CRITICAL finding that fails the
gate, and the agent sandbox keeps ``.harness`` and ``.git`` read-only."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind, FindingSeverity, ResultStatus
from governed_harness.domain.models import Execution
from governed_harness.runtime.guard import ExcludedPathGuard
from governed_harness.runtime.sandbox import (
    SandboxHost,
    SandboxPath,
    build_sandbox,
    bwrap_arguments,
    seatbelt_profile,
)

AGENT = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "json.load(sys.stdin)\n"
    "Path('src/sample/pricing.py').write_text(\n"
    "    'def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n'\n"
    "    '    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n')\n"
    "WRITES\n"
    "print(json.dumps({'status': 'PASSED', 'summary': 'done'}))\n"
)
OUTSIDE = (
    "hook = Path('.git/hooks/pre-commit'); hook.write_text('#!/bin/sh\\ncurl evil\\n')\n"
    "Path('venv/lib').mkdir(parents=True, exist_ok=True)\n"
    "Path('venv/lib/dep.py').write_text('PATCHED = True\\n')\n"
    "Path('dist').mkdir(exist_ok=True); Path('dist/payload.py').write_text('x = 1\\n')\n"
)
TASK = (
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
)


def _configure(workspace: Path, tmp_path: Path, writes: str, governance: bool = True) -> None:
    script = tmp_path / "agent.py"
    script.write_text(AGENT.replace("WRITES\n", writes), encoding="utf-8")
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {"fixture": {"kind": "command", "command": ["python", str(script)]}}
    # The guard does not depend on the sandbox; hosts without a mechanism run these tests too.
    config["runtime"]["agentSandbox"] = "off"
    if not governance:
        config.pop("governance")
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _start(workspace: Path, tmp_path: Path) -> Execution:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_file)
    return application.start_run(workspace, task.task_id)


def test_writes_outside_the_changeset_fail_the_gate(python_workspace: Path, tmp_path: Path) -> None:
    _configure(python_workspace, tmp_path, OUTSIDE)
    pending = _start(python_workspace, tmp_path)
    assert pending.current_phase.value == "DECISION"
    status = HarnessApplication().status(python_workspace, pending.execution_id)
    assert status["gate"]["status"] == "FAILED"
    findings = HarnessApplication().list_findings(python_workspace, pending.execution_id)
    guard = [item for item in findings if item.rule_id == "workspace.out-of-changeset-write"]
    assert len(guard) == 1
    assert guard[0].severity is FindingSeverity.CRITICAL
    for path in (".git/hooks/pre-commit", "venv/lib/dep.py", "dist/payload.py"):
        assert path in guard[0].message
    # The ChangeSet still shows only the source file.
    summary = HarnessApplication().decision_summary(python_workspace, pending.execution_id)
    assert [item["path"] for item in summary["files"]] == ["src/sample/pricing.py"]
    approve = CliRunner().invoke(
        app,
        [
            "gate",
            "decide",
            "--run",
            pending.execution_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            pending.change_set_digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "looks fine",
            "--path",
            str(python_workspace),
        ],
    )
    assert approve.exit_code == 5
    evidence = HarnessApplication().list_evidence(python_workspace, pending.execution_id)
    assert any(
        item["summary"].startswith("Paths outside the ChangeSet")
        for item in evidence[0]["evidence"]
    )


def test_a_clean_agent_gets_no_finding(python_workspace: Path, tmp_path: Path) -> None:
    _configure(python_workspace, tmp_path, "")
    pending = _start(python_workspace, tmp_path)
    findings = HarnessApplication().list_findings(python_workspace, pending.execution_id)
    assert not [item for item in findings if item.validator_id == "harness.workspace-guard"]
    _, final = HarnessApplication().decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=pending.change_set_digest,
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    assert final.status is ResultStatus.PASSED


def test_without_the_setting_the_writes_stay_invisible(
    python_workspace: Path, tmp_path: Path
) -> None:
    _configure(python_workspace, tmp_path, OUTSIDE, governance=False)
    pending = _start(python_workspace, tmp_path)
    findings = HarnessApplication().list_findings(python_workspace, pending.execution_id)
    assert not [item for item in findings if item.validator_id == "harness.workspace-guard"]


@pytest.mark.skipif(os.name == "nt", reason="creating symbolic links needs privileges on Windows")
def test_guard_ignores_harness_state_and_git_reads(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "artifacts").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "src").mkdir()
    guard = ExcludedPathGuard(tmp_path)
    before = guard.fingerprint()
    (tmp_path / ".harness" / "state.db").write_bytes(b"db")
    (tmp_path / ".harness" / "artifacts" / "blob").write_bytes(b"x")
    (tmp_path / ".git" / "index").write_bytes(b"index")
    (tmp_path / "src" / "plain.py").write_text("x = 1\n")
    assert guard.compare(before, guard.fingerprint()) == ()
    (tmp_path / "src" / "link.py").symlink_to("/etc/hosts")
    (tmp_path / ".harness" / "project.yaml").write_text("changed\n")
    changes = {(item.path, item.status) for item in guard.compare(before, guard.fingerprint())}
    assert changes == {("src/link.py", "ADDED"), (".harness/project.yaml", "ADDED")}


@pytest.mark.skipif(os.name == "nt", reason="the sandbox profiles use POSIX paths")
def test_sandbox_keeps_harness_and_git_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".harness").mkdir()
    host = SandboxHost(system="Darwin", home=tmp_path, temp_dir=tmp_path, sandbox_exec="/x")
    plan = build_sandbox(
        workspace, (), host, protected=(workspace / ".harness", workspace / ".git")
    )
    real = str(workspace.resolve())
    assert plan.profile.endswith(
        f'(deny file-write*\n  (subpath "{real}/.harness")\n  (subpath "{real}/.git"))\n'
    )
    assert plan.evidence()["protectedPaths"] == [f"{real}/.harness", f"{real}/.git"]
    unprotected = build_sandbox(workspace, (), host)
    assert "protectedPaths" not in unprotected.evidence()
    assert unprotected.profile == seatbelt_profile(
        (
            SandboxPath(real, "subpath", "workspace"),
            SandboxPath(str(tmp_path.resolve()), "subpath", "tempdir"),
        )
    )
    arguments, _, _ = bwrap_arguments(
        (SandboxPath(real, "subpath", "workspace"),), (f"{real}/.git", f"{real}/missing")
    )
    assert arguments[-6:] == ["--bind", real, real, "--ro-bind", f"{real}/.git", f"{real}/.git"]


@pytest.mark.parametrize("protect", [True, False])
def test_write_grants_on_harness_state(python_workspace: Path, protect: bool) -> None:
    config_path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["governance"]["protectExcludedPaths"] = protect
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    resolved = ConfigurationResolver().resolve(python_workspace)
    writes = next(
        rule.scope
        for rule in resolved.effective_capabilities
        if rule.capability == "filesystem.write"
    )
    assert (".harness/**" in writes) is not protect
    assert json.dumps(sorted(writes))
