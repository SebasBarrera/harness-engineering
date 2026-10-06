"""A provider launch refused by ``destructiveActionsDefault: deny`` blocks the run (#76): the run
is BLOCKED with the HIGH finding and ``run start`` exits with the blocked code, not as an
internal error. The provider is a shell command that calls no model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import FindingSeverity
from governed_harness.domain.models import Finding

TASK = (
    "taskId: task_destructive\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
)


def configure(root: Path, command: list[str], **governance: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "probe"
    config["agentProviders"] = {"probe": {"kind": "command", "command": command}}
    config["runtime"].update({"agentSandbox": "off", "providerRetries": 0})
    # The shell is granted, so the refusal comes from the destructive-command policy.
    config["capabilities"]["extend"] = [{"capability": "process.execute", "scope": ["sh"]}]
    config.setdefault("governance", {}).update(applyRepositoryPolicies=True, **governance)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(root: Path, tmp_path: Path) -> tuple[int, dict[str, Any]]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    HarnessApplication().create_task(root, source)
    result = CliRunner().invoke(
        app, ["--json", "run", "start", "--path", str(root), "--task", "task_destructive"]
    )
    body: dict[str, Any] = json.loads(result.stdout)
    return result.exit_code, body


def denied(root: Path, run: str) -> list[Finding]:
    with HarnessApplication()._services(root) as services:
        findings = services.state.list("finding", Finding, execution_id=run)
    return [item for item in findings if item.rule_id == "capabilities.destructive-denied"]


def test_a_refused_destructive_launch_blocks_the_run(
    python_workspace: Path, tmp_path: Path
) -> None:
    victim = tmp_path / "outside" / "victim"
    victim.mkdir(parents=True)
    configure(python_workspace, ["sh", "-c", f"rm -rf {victim}"])
    code, body = start(python_workspace, tmp_path)
    assert victim.is_dir()
    assert code == 6
    assert body["status"] == "BLOCKED"
    findings = denied(python_workspace, body["executionId"])
    assert [item.severity for item in findings] == [FindingSeverity.HIGH]


def test_the_blocked_reason_names_the_refused_command(
    python_workspace: Path, tmp_path: Path
) -> None:
    victim = tmp_path / "outside" / "victim"
    victim.mkdir(parents=True)
    configure(python_workspace, ["sh", "-c", f"rm -rf {victim}"], phaseCapabilities=True)
    code, body = start(python_workspace, tmp_path)
    assert code == 6
    with HarnessApplication()._services(python_workspace) as services:
        completed = [
            item.payload
            for item in services.events.list(body["executionId"])
            if item.event_type == "phase.completed"
        ]
    assert completed[-1]["status"] == "BLOCKED"
    assert completed[-1]["summary"].startswith("DestructiveActionDenied: agent.probe")
    assert "process.destructive" in completed[-1]["summary"]
