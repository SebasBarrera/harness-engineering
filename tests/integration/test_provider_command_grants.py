"""The configured provider command needs a grant (#87). The provider is a shell command that
calls no model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import FindingSeverity, ResultStatus
from governed_harness.domain.models import Execution, Finding

TASK = (
    "taskId: task_grant\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
)
SH = {"capability": "process.execute", "scope": ["sh"]}


def configure(root: Path, command: list[str], **governance: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "probe"
    config["agentProviders"] = {"probe": {"kind": "command", "command": command}}
    config["runtime"].update({"agentSandbox": "off", "providerRetries": 0})
    config.setdefault("governance", {}).update(governance)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def extend(root: Path, *rules: dict[str, Any]) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["capabilities"]["extend"] = list(rules)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def create(root: Path, tmp_path: Path) -> None:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    HarnessApplication().create_task(root, source)


def start(root: Path, tmp_path: Path) -> Execution:
    create(root, tmp_path)
    return HarnessApplication().start_run(root, "task_grant")


def findings(root: Path, run: str) -> list[Finding]:
    with HarnessApplication()._services(root) as services:
        return services.state.list("finding", Finding, execution_id=run)


def rules_of(root: Path, run: str) -> set[str]:
    return {item.rule_id for item in findings(root, run)}


def test_an_ungranted_provider_command_is_refused_under_phase_capabilities(
    python_workspace: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "started"
    configure(python_workspace, ["sh", "-c", f"touch {marker}"], phaseCapabilities=True)
    run = start(python_workspace, tmp_path)
    assert not marker.exists()
    assert run.status is ResultStatus.BLOCKED
    denied = findings(python_workspace, run.execution_id)
    assert [item.rule_id for item in denied] == ["capabilities.command-denied"]
    assert denied[0].severity is FindingSeverity.HIGH
    assert "sh -c touch" in denied[0].message


def test_the_refusal_reason_names_the_command_and_the_grant(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, ["sh", "-c", "echo not-allowed"], phaseCapabilities=True)
    run = start(python_workspace, tmp_path)
    with HarnessApplication()._services(python_workspace) as services:
        completed = [
            item.payload
            for item in services.events.list(run.execution_id)
            if item.event_type == "phase.completed"
        ]
    summary = completed[-1]["summary"]
    assert completed[-1]["status"] == "BLOCKED"
    assert "agent.probe" in summary
    assert "process.execute" in summary
    assert "capabilities.extend" in summary


def test_the_refused_launch_exits_with_the_blocked_code(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, ["sh", "-c", "echo not-allowed"], phaseCapabilities=True)
    create(python_workspace, tmp_path)
    result = CliRunner().invoke(
        app, ["run", "start", "--path", str(python_workspace), "--task", "task_grant"]
    )
    assert result.exit_code == 6


def test_an_explicit_grant_lets_the_provider_command_start(
    python_workspace: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "started"
    configure(python_workspace, ["sh", "-c", f"touch {marker}"], phaseCapabilities=True)
    extend(python_workspace, SH)
    run = start(python_workspace, tmp_path)
    assert marker.exists()
    assert "capabilities.command-denied" not in rules_of(python_workspace, run.execution_id)


def test_without_phase_capabilities_the_refusal_keeps_its_earlier_form(
    python_workspace: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "started"
    configure(python_workspace, ["sh", "-c", f"touch {marker}"])
    run = start(python_workspace, tmp_path)
    assert not marker.exists()
    # 0.9.0 and 1.x: the refusal ends the run as ERROR, without a finding.
    assert run.status is ResultStatus.ERROR
    assert run.terminal_reason is not None
    assert run.terminal_reason.startswith("CapabilityDenied: agent.probe lacks process.execute")
    assert findings(python_workspace, run.execution_id) == []


def test_config_validate_warns_about_an_ungranted_provider_command(
    python_workspace: Path,
) -> None:
    configure(python_workspace, ["sh", "-c", "echo not-allowed"], phaseCapabilities=True)
    warnings = HarnessApplication().validate_config(python_workspace)["warnings"]
    ungranted = [item for item in warnings if item.startswith("agentProviders.probe.command")]
    assert len(ungranted) == 1
    assert "capabilities.extend" in ungranted[0]
    extend(python_workspace, SH)
    warnings = HarnessApplication().validate_config(python_workspace)["warnings"]
    assert not [item for item in warnings if item.startswith("agentProviders.")]
