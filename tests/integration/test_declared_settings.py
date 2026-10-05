"""Declared settings that now take effect, and the ones reported as declarative (#51)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.configuration.models import WorkflowPhaseDefinition
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import Execution
from governed_harness.orchestration import engine as engine_module
from governed_harness.orchestration.engine import EngineServices, RunEngine
from governed_harness.orchestration.retention import RetentionCollector
from governed_harness.runtime.sandbox import (
    SandboxHost,
    SandboxPath,
    build_sandbox,
    bwrap_arguments,
)

TASK = (
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
    "implementation:\n  mode: patch\n  patches:\n"
    "    - path: src/sample/pricing.py\n      operation: replace\n      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    "\n"
    "\n"
    "        def never_called() -> int:\n"
    "            return 1\n"
)


def _update_config(workspace: Path, change: dict[str, object]) -> None:
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for dotted, value in change.items():
        target = config
        *parents, last = dotted.split(".")
        for part in parents:
            target = target.setdefault(part, {})
        if value is None:
            target.pop(last, None)
        else:
            target[last] = value
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _pending(workspace: Path, tmp_path: Path) -> Execution:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_file)
    return application.start_run(workspace, task.task_id)


def test_config_validate_reports_declarative_settings(python_workspace: Path) -> None:
    result = CliRunner().invoke(app, ["config", "validate", "--path", str(python_workspace)])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    keys = {item["key"] for item in body["declarative"]}
    assert {"workspace.units", "workflow.phases[].allowedCapabilities"} <= keys
    # Enforced under governance.enforceWorkflow (#3), which the fixture leaves out.
    assert not keys & {"runtime.maxParallel", "workflow.phases[].dependsOn"}
    assert any(item.startswith("runtime.maxParallel is declarative") for item in body["warnings"])
    assert any("set governance.enforceWorkflow: true" in item for item in body["warnings"])
    assert "retention is applied only when harness gc --apply runs" in body["warnings"]
    assert body["governance"]["applyWorkflowSettings"] is True
    assert body["governance"]["decisionExpiryHours"] == 72
    # Without the governance section the declared-but-not-applied settings are named.
    _update_config(python_workspace, {"governance": None})
    body = json.loads(
        CliRunner().invoke(app, ["config", "validate", "--path", str(python_workspace)]).stdout
    )
    assert any("maxAttempts, timeoutSeconds and exitGate" in item for item in body["warnings"])
    assert any("missingTestCommand" in item for item in body["warnings"])


def test_schema_marks_declarative_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    project = json.loads((root / "schemas" / "v1" / "project-config.schema.json").read_text())
    workflow = json.loads((root / "schemas" / "v1" / "workflow.schema.json").read_text())
    assert project["$defs"]["WorkspaceConfig"]["properties"]["units"]["x-declarative"] is True
    assert "x-declarative" not in project["$defs"]["RuntimeConfig"]["properties"]["maxParallel"]
    phase = workflow["$defs"]["WorkflowPhaseDefinition"]["properties"]
    assert phase["allowedCapabilities"]["x-declarative"] is True
    assert "x-declarative" not in phase["parallelizable"]
    assert "x-declarative" not in phase["dependsOn"]
    assert "x-declarative" not in phase["maxAttempts"]


def test_invalid_profile_policy_is_a_configuration_error(python_workspace: Path) -> None:
    _update_config(python_workspace, {"policies.missingTestCommand": "PASSED"})
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)
    _update_config(
        python_workspace,
        {"policies.missingTestCommand": "FAILED", "policies.coverage": {"minimumPercent": 140}},
    )
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)


def test_decision_expires(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An exception under review.exceptions carries its own expiry; this test is about the
    # expiry of any decision (governance.decisionExpiryHours).
    _update_config(python_workspace, {"review.exceptions": False})
    pending = _pending(python_workspace, tmp_path)
    record, execution = HarnessApplication().decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=pending.change_set_digest,
        actor_id="human.reviewer",
        rationale="reviewed",
        continue_after=False,
    )
    assert record.expires_at == record.decided_at + timedelta(hours=72)
    later = datetime.now(UTC) + timedelta(hours=73)
    monkeypatch.setattr(engine_module, "utc_now", lambda: later)
    resumed = HarnessApplication().continue_run(python_workspace, pending.execution_id)
    assert resumed.status is ResultStatus.BLOCKED
    assert resumed.current_phase is PhaseId.DECISION
    phases = HarnessApplication().status(python_workspace, pending.execution_id)["phases"]
    assert "expired" in phases[-1]["summary"]


def test_phase_timeout_bounds_the_agent(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "agent.py"
    script.write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
    _update_config(
        python_workspace,
        {
            "agentProvider": "slow",
            "agentProviders": {"slow": {"kind": "command", "command": ["python", str(script)]}},
            "runtime.agentSandbox": "off",
            "runtime.providerRetries": 0,
        },
    )
    original = RunEngine._phase_definition

    def short(self: RunEngine, phase_id: PhaseId) -> WorkflowPhaseDefinition | None:
        definition = original(self, phase_id)
        if definition is not None and phase_id is PhaseId.IMPLEMENTATION:
            return definition.model_copy(update={"timeout_seconds": 2})
        return definition

    monkeypatch.setattr(RunEngine, "_phase_definition", short)
    execution = _pending(python_workspace, tmp_path)
    assert execution.current_phase is PhaseId.IMPLEMENTATION
    assert execution.status is ResultStatus.TIMED_OUT
    trace = HarnessApplication().trace(python_workspace, execution.execution_id, "jsonl")
    completed = [
        json.loads(line)["payload"]
        for line in trace.decode().splitlines()
        if '"phase.completed"' in line
    ]
    assert completed[-1]["exitGate"] == "candidate_changeset"
    assert completed[-1]["exitGateMet"] is False
    assert completed[-1]["timeoutSeconds"] == 2


def test_coverage_threshold(python_workspace: Path, tmp_path: Path) -> None:
    _update_config(python_workspace, {"policies.coverage": {"minimumPercent": 100}})
    pending = _pending(python_workspace, tmp_path)
    validations = HarnessApplication().status(python_workspace, pending.execution_id)
    findings = HarnessApplication().list_findings(python_workspace, pending.execution_id)
    has_coverage = (
        subprocess.run(
            ["python", "-c", "import coverage"], capture_output=True, check=False
        ).returncode
        == 0
    )
    assert pending.current_phase is PhaseId.VERIFICATION
    if has_coverage:
        # never_called() is not covered: the mandatory coverage validator fails.
        assert pending.status is ResultStatus.FAILED
        assert any(item.validator_id == "python.coverage" for item in findings)
    else:
        assert pending.status is ResultStatus.BLOCKED
    assert validations["validationSummary"]["total"] >= 2


def test_network_policy_reaches_the_sandbox(tmp_path: Path) -> None:
    host = SandboxHost(system="Darwin", home=tmp_path, temp_dir=tmp_path, sandbox_exec="/x")
    denied = build_sandbox(tmp_path, (), host, allow_network=False)
    assert denied.profile.endswith('(deny network-outbound (remote ip "*:*"))\n')
    assert denied.evidence()["network"] == "denied"
    assert "network" not in build_sandbox(tmp_path, (), host).evidence()
    arguments, _, _ = bwrap_arguments(
        (SandboxPath(str(tmp_path), "subpath", "workspace"),), allow_network=False
    )
    assert arguments[-1] == "--unshare-net"


@pytest.mark.skipif(
    not os.access("/usr/bin/sandbox-exec", os.X_OK), reason="needs macOS sandbox-exec"
)
def test_denied_network_on_macos(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        plan = build_sandbox(tmp_path, (), SandboxHost.detect(), allow_network=False)
        probe = (
            "import socket, sys\n"
            f"s = socket.create_connection(('127.0.0.1', {port}), timeout=5)\n"
            "print('connected')\n"
        )
        result = subprocess.run(
            [*plan.prefix, sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode != 0, result.stdout
        allowed = build_sandbox(tmp_path, (), SandboxHost.detect())
        result = subprocess.run(
            [*allowed.prefix, sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
    finally:
        listener.close()


def test_gc_applies_retention(python_workspace: Path, tmp_path: Path) -> None:
    pending = _pending(python_workspace, tmp_path)
    _, closed = HarnessApplication().decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=pending.change_set_digest,
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    assert closed.status is ResultStatus.PASSED
    report = HarnessApplication().gc(python_workspace)
    assert report["applied"] is False and report["artifactsRemoved"] == 0
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        collector = RetentionCollector(services)
        plan = collector.plan(now=datetime.now(UTC) + timedelta(days=31))
        assert plan.pruned_runs and not plan.removed_runs
        blobs = len(list(services.artifacts.list()))
        collector.apply(plan)
        assert len(list(services.artifacts.list())) == blobs - len(plan.artifacts)
    finally:
        services.close()
    verified = HarnessApplication().verify(python_workspace, pending.execution_id)
    assert verified["valid"] is True, verified
    services = EngineServices.open(resolved)
    try:
        collector = RetentionCollector(services)
        plan = collector.plan(now=datetime.now(UTC) + timedelta(days=366))
        assert plan.removed_runs == [pending.execution_id]
        collector.apply(plan)
    finally:
        services.close()
    assert HarnessApplication().list_runs(python_workspace) == []
