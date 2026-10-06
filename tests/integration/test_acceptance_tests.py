"""Independent, frozen acceptance tests (verification.acceptanceTests, #52)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError

TASK = (
    "taskId: task_acceptance\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py, tests/acceptance/test_ac_1.py]\n"
)

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
if request.get("kind") == "acceptance":
    expected = "apply_discount(99, 100, 0.1) == 99" if MODE == "weak" else (
        "apply_discount(100, 100, 0.1) == 90"
    )
    content = (
        "from sample import apply_discount\\n\\n\\n"
        f"def test_ac_1() -> None:\\n    assert {expected}\\n"
    )
    print(json.dumps({"status": "PASSED", "summary": "1 file",
                      "result": {"tests": [{"path": "tests/acceptance/test_ac_1.py", "content": content}]}}))
    sys.exit(0)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
if MODE == "tamper":
    Path("tests/acceptance/test_ac_1.py").write_text("def test_ac_1() -> None:\\n    assert True\\n")
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def configure(workspace: Path, tmp_path: Path, mode: str) -> Path:
    log = tmp_path / f"calls-{mode}.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("MODE", repr(mode))
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"].update(
        {"agentSandbox": "off", "providerRetries": 0, "verificationCorrections": 0}
    )
    config["verification"] = {
        "requirementTraceability": "off",
        "acceptanceTests": {"mode": "agent", "author": {"model": "author-model"}},
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_acceptance").execution_id


def approve(application: HarnessApplication, workspace: Path, run: str) -> dict[str, Any]:
    proposal = application.acceptance(workspace, run)
    return application.decide_acceptance(
        workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        digest=proposal["digest"],
        rationale="They test the criterion",
        actor_id="human.reviewer",
    )


def test_approved_tests_are_frozen_and_must_pass(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "good")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)["execution"]
    assert status["currentPhase"] == PhaseId.SPECIFICATION
    assert status["status"] == ResultStatus.BLOCKED
    assert not (python_workspace / "tests" / "acceptance").exists()
    with pytest.raises(PolicyViolationError):
        application.decide_acceptance(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE,
            digest=application.acceptance(python_workspace, run)["digest"],
            rationale="x",
            actor_id="agent.fixture_agent",
        )
    result = approve(application, python_workspace, run)
    assert result["acceptanceTests"]["failBefore"]["status"] == "FAILED"
    assert result["execution"]["currentPhase"] == PhaseId.DECISION
    calls = json.loads(log.read_text())
    assert calls[0]["kind"] == "acceptance" and calls[0]["routing"]["model"] == "author-model"
    assert calls[1]["acceptanceTests"]["paths"] == ["tests/acceptance/test_ac_1.py"]
    assert not [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id.startswith("acceptance.")
    ]


def test_a_modified_frozen_test_blocks(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "tamper")
    application, run = start(python_workspace, tmp_path)
    result = approve(application, python_workspace, run)
    assert result["execution"]["currentPhase"] == PhaseId.VERIFICATION
    [modified] = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "acceptance.modified"
    ]
    assert modified.severity is FindingSeverity.HIGH


def test_tests_that_pass_before_the_change_are_a_finding(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "weak")
    application, run = start(python_workspace, tmp_path)
    approve(application, python_workspace, run)
    [weak] = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "acceptance.passes-before"
    ]
    assert weak.severity is FindingSeverity.MEDIUM


# ----- #82: frozen files of two runs in one workspace ------------------------------------------
def test_a_later_run_never_overwrites_an_earlier_frozen_file(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "good")
    path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["verification"]["weakenedControls"] = "enforce"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    application, first = start(python_workspace, tmp_path)
    approve(application, python_workspace, first)
    target = python_workspace / "tests" / "acceptance" / "test_ac_1.py"
    earlier = target.read_bytes()
    # The second task proposes a test with another name at the same path.
    agent = python_workspace / "agent.py"
    agent.write_text(agent.read_text().replace("def test_ac_1()", "def test_ac_1_again()"))
    source = tmp_path / "second.yaml"
    source.write_text(TASK.replace("task_acceptance", "task_second"), encoding="utf-8")
    application.create_task(python_workspace, source)
    second = application.start_run(python_workspace, "task_second").execution_id
    result = approve(application, python_workspace, second)
    assert target.read_bytes() == earlier
    [(proposed, written)] = result["acceptanceTests"]["renamed"].items()
    assert proposed == "tests/acceptance/test_ac_1.py"
    assert written == f"tests/acceptance/test_ac_1_{second[-8:]}.py"
    assert list(result["acceptanceTests"]["frozen"]) == [written]
    assert not [
        item
        for item in application.list_findings(python_workspace, second)
        if item.rule_id in {"weakened.test-deleted", "acceptance.modified"}
    ]
