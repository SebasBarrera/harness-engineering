"""Stop the line (governance.stopTheLine), out-of-scope writes, the gate contract in the
provider request, the per-call permissions and ``harness check`` (#52)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError

ORIGINAL = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal\n"
)
WRONG = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 + rate)\n"
)
GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)


def patch_task(content: str, task_id: str = "task_line") -> str:
    return yaml.safe_dump(
        {
            "taskId": task_id,
            "title": "Threshold discount",
            "intent": "Apply the configured discount at or above the threshold.",
            "acceptanceCriteria": [
                {"criterionId": "AC-1", "text": "apply_discount(100, 100, 0.1) returns 90."}
            ],
            "implementation": {
                "mode": "patch",
                "patches": [
                    {"path": "src/sample/pricing.py", "operation": "replace", "content": content}
                ],
            },
        },
        sort_keys=False,
    )


def set_keys(workspace: Path, **sections: dict[str, Any]) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for section, values in sections.items():
        config.setdefault(section, {}).update(values)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(
    application: HarnessApplication, workspace: Path, tmp_path: Path, content: str
) -> str:
    source = tmp_path / f"task-{len(list(tmp_path.glob('task-*')))}.yaml"
    source.write_text(content, encoding="utf-8")
    task = application.create_task(workspace, source)
    return application.start_run(workspace, task.task_id).execution_id


def events(application: HarnessApplication, workspace: Path, run: str, kind: str) -> list[Any]:
    with application._services(workspace) as services:
        return [item for item in services.events.list(run) if item.event_type == kind]


def test_restore_quarantines_a_failed_run(python_workspace: Path, tmp_path: Path) -> None:
    set_keys(python_workspace, governance={"stopTheLine": "restore"})
    application = HarnessApplication()
    run = start(application, python_workspace, tmp_path, patch_task(WRONG))
    status = application.status(python_workspace, run)["execution"]
    assert status["currentPhase"] == PhaseId.VERIFICATION
    assert status["status"] == ResultStatus.FAILED
    pricing = python_workspace / "src" / "sample" / "pricing.py"
    assert pricing.read_text() == ORIGINAL
    [quarantined] = events(application, python_workspace, run, "workspace.quarantined")
    assert quarantined.payload["restoredPaths"] == ["src/sample/pricing.py"]
    with application._services(python_workspace) as services:
        patch = services.artifacts.get(quarantined.payload["patchRef"]).decode()
    assert "+    return subtotal * (1 + rate)" in patch
    # The line is free: the next run starts on the baseline.
    again = start(application, python_workspace, tmp_path, patch_task(GOOD, "task_next"))
    assert application.status(python_workspace, again)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )


def test_block_refuses_new_runs_until_quarantined(python_workspace: Path, tmp_path: Path) -> None:
    set_keys(python_workspace, governance={"stopTheLine": "block"})
    application = HarnessApplication()
    run = start(application, python_workspace, tmp_path, patch_task(WRONG))
    pricing = python_workspace / "src" / "sample" / "pricing.py"
    assert pricing.read_text() == WRONG
    assert events(application, python_workspace, run, "workspace.line-stopped")
    with pytest.raises(PolicyViolationError, match="quarantine"):
        start(application, python_workspace, tmp_path, patch_task(GOOD, "task_next"))
    with pytest.raises(PolicyViolationError):
        application.quarantine_run(python_workspace, run, "agent.claude")
    result = application.quarantine_run(python_workspace, run, "human.reviewer")
    assert result["quarantined"] is True
    assert pricing.read_text() == ORIGINAL
    again = start(application, python_workspace, tmp_path, patch_task(GOOD, "task_next"))
    assert application.status(python_workspace, again)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )


def test_reject_restores_the_baseline(python_workspace: Path, tmp_path: Path) -> None:
    set_keys(python_workspace, governance={"stopTheLine": "restore"})
    application = HarnessApplication()
    run = start(application, python_workspace, tmp_path, patch_task(GOOD))
    digest = application.status(python_workspace, run)["execution"]["changeSetDigest"]
    application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.REJECT,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="Not now",
    )
    assert (python_workspace / "src" / "sample" / "pricing.py").read_text() == ORIGINAL


AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
Path(LOG).write_text(json.dumps(request))
Path("src/sample/pricing.py").write_text(GOOD)
Path("notes.md").write_text("out of scope")
print(json.dumps({"status": "PASSED", "summary": "done"}))
"""


def command_agent(workspace: Path, tmp_path: Path) -> Path:
    log = tmp_path / "request.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("GOOD", repr(GOOD))
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"]["agentSandbox"] = "off"
    config["runtime"]["gateContract"] = True
    config["governance"]["phasePermissions"] = True
    config["governance"]["stopTheLine"] = "block"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


TASK = (
    "taskId: task_contract\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)


def test_the_request_carries_the_gate_contract_and_permissions(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = command_agent(python_workspace, tmp_path)
    application = HarnessApplication()
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application.create_task(python_workspace, source)
    run = application.start_run(python_workspace, "task_contract").execution_id
    request = json.loads(log.read_text())
    assert request["kind"] == "implement" and request["schemaVersion"] == "1.1"
    assert "harness check" in request["instructions"] or "check command" in request["instructions"]
    gate = request["gate"]
    assert {item["id"] for item in gate["validators"]} >= {"python.pytest"}
    assert gate["checkCommand"][:2] == ["harness", "check"] and run in gate["checkCommand"]
    assert gate["workspace"] == str(python_workspace.resolve())
    assert "review.possible-secret" in {item["ruleId"] for item in gate["reviewRules"]}
    permissions = request["permissions"]
    assert permissions["callKind"] == "implement" and permissions["readOnly"] is False
    assert "src/**" in permissions["filesystem"]["write"]
    # The write outside ownedPaths is a HIGH finding of a mandatory validation.
    findings = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "workspace.outside-owned-paths"
    ]
    assert findings and findings[0].severity is FindingSeverity.HIGH
    assert findings[0].location is not None and findings[0].location.path == "notes.md"


def test_harness_check_runs_the_gate_without_recording(
    python_workspace: Path, tmp_path: Path
) -> None:
    command_agent(python_workspace, tmp_path)
    set_keys(python_workspace, verification={"securityPatterns": True})
    application = HarnessApplication()
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application.create_task(python_workspace, source)
    run = application.start_run(python_workspace, "task_contract").execution_id
    with application._services(python_workspace) as services:
        before = len(services.events.list(run))
    result = application.check(python_workspace, run)
    assert {item["id"]: item["status"] for item in result["validators"]}["python.pytest"] == (
        "PASSED"
    )
    assert [item["id"] for item in result["checks"]] == ["harness.security-patterns"]
    assert result["status"] == "PASSED"
    (python_workspace / "src" / "sample" / "store.py").write_text(
        "import pickle\n\n\ndef load(data: bytes) -> object:\n    return pickle.loads(data)\n"
    )
    failed = application.check(python_workspace)
    assert failed["status"] == "FAILED" and failed["blockingIssues"] == 1
    with application._services(python_workspace) as services:
        assert len(services.events.list(run)) == before
