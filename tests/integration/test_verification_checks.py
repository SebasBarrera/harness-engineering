"""The deterministic checks of the agent-results settings in VERIFICATION (#40, #52), the
differential verification against the baseline (#7), risk-factor acknowledgement in DECISION
and structured change requests."""

from __future__ import annotations

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
    ValidationKind,
)
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.models import ValidationResult

GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
TEST = (
    "from sample import apply_discount\n\n\n"
    "def test_below_threshold() -> None:\n"
    "    assert apply_discount(99, 100, 0.1) == 99\n\n\n"
    "def test_at_threshold() -> None:\n"
    "    assert apply_discount(100, 100, 0.1) == 90\n"
)


def task_yaml(
    patches: list[dict[str, str]],
    *,
    task_id: str = "task_checks",
    constraints: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    value: dict[str, Any] = {
        "taskId": task_id,
        "title": "Threshold discount",
        "intent": "Apply the configured discount at or above the threshold.",
        "acceptanceCriteria": [
            {"criterionId": "AC-1", "text": "apply_discount(100, 100, 0.1) returns 90."}
        ],
        "implementation": {"mode": "patch", "patches": patches},
    }
    if constraints:
        value["constraints"] = constraints
    if metadata:
        value["metadata"] = metadata
    return yaml.safe_dump(value, sort_keys=False)


def patch(path: str, content: str, operation: str = "replace") -> dict[str, str]:
    return {"path": path, "operation": operation, "content": content}


def configure(workspace: Path, verification: dict[str, Any], **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["verification"] = {"requirementTraceability": "off", **verification}
    config.update(sections)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def run_task(workspace: Path, tmp_path: Path, content: str) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(content, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, source)
    return application, application.start_run(workspace, task.task_id).execution_id


def latest(application: HarnessApplication, workspace: Path, run: str) -> dict[str, Any]:
    with application._services(workspace) as services:
        items = services.state.list("validation", ValidationResult, execution_id=run)
    result: dict[str, ValidationResult] = {}
    for item in items:
        if (
            item.validator_id not in result
            or item.finished_at >= result[item.validator_id].finished_at
        ):
            result[item.validator_id] = item
    return result


def rules(application: HarnessApplication, workspace: Path, run: str) -> dict[str, Any]:
    return {item.rule_id: item for item in application.list_findings(workspace, run)}


def test_interface_mismatch_fails_verification_under_enforce(
    python_workspace: Path, tmp_path: Path
) -> None:
    (python_workspace / "interfaces").mkdir()
    (python_workspace / "interfaces" / "pricing.pyi").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float = ...) -> float: ...\n"
    )
    configure(python_workspace, {"interface": "enforce"})
    broken = GOOD.replace("rate: float", "percent: float").replace("1 - rate", "1 - percent")
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml(
            [patch("src/sample/pricing.py", broken)],
            metadata={
                "interface": {"stub": "interfaces/pricing.pyi", "module": "src/sample/pricing.py"}
            },
        ),
    )
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.VERIFICATION
    assert latest(application, python_workspace, run)["harness.interface"].status is (
        ResultStatus.FAILED
    )
    finding = rules(application, python_workspace, run)["interface.signature"]
    assert finding.severity is FindingSeverity.HIGH
    assert finding.location is not None and finding.location.path == "src/sample/pricing.py"


def test_interface_under_warn_records_low_findings(python_workspace: Path, tmp_path: Path) -> None:
    (python_workspace / "interfaces").mkdir()
    (python_workspace / "interfaces" / "pricing.pyi").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float: ...\n"
    )
    configure(python_workspace, {"interface": "warn"})
    broken = GOOD.replace("rate: float", "percent: float").replace("1 - rate", "1 - percent")
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml(
            [patch("src/sample/pricing.py", broken)],
            metadata={
                "interface": {"stub": "interfaces/pricing.pyi", "module": "src/sample/pricing.py"}
            },
        ),
    )
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )
    assert rules(application, python_workspace, run)["interface.signature"].severity is (
        FindingSeverity.LOW
    )


def test_security_patterns_and_constraints(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, {"securityPatterns": True, "constraints": "enforce"})
    code = (
        "import pickle\n\nimport requests\n\n\n"
        "def load(data: bytes) -> object:\n    return pickle.loads(data)\n\n\n" + GOOD
    )
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml(
            [patch("src/sample/pricing.py", code)],
            constraints=["Use only the Python standard library."],
        ),
    )
    found = rules(application, python_workspace, run)
    assert found["security.unsafe-deserialization"].severity is FindingSeverity.HIGH
    assert found["constraints.non-stdlib-import"].severity is FindingSeverity.HIGH
    validations = latest(application, python_workspace, run)
    assert validations["harness.security-patterns"].status is ResultStatus.FAILED
    assert validations["harness.constraints"].status is ResultStatus.FAILED


def test_weakened_controls_and_context_secrets(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, {"weakenedControls": "enforce", "secrets": "context"})
    weakened = (
        "import pytest\n\nfrom sample import apply_discount\n\n\n"
        '@pytest.mark.skip(reason="later")\n'
        "def test_below_threshold() -> None:\n"
        '    password = "testpwd"\n'
        "    apply_discount(99, 100, 0.1)\n"
    )
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", GOOD), patch("tests/test_pricing.py", weakened)]),
    )
    found = rules(application, python_workspace, run)
    assert found["weakened.skip-added"].severity is FindingSeverity.HIGH
    assert found["weakened.assert-removed"].severity is FindingSeverity.HIGH
    # A dummy password in a test is information, not a CRITICAL secret, and the review's
    # pattern rule does not report it again.
    assert found["secrets.test-dummy"].severity is FindingSeverity.INFO
    assert "review.possible-secret" not in found


def test_risk_factors_need_acknowledgement(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, {"riskFactors": {"network": "acknowledge"}})
    code = "import urllib.request\n\n\n" + GOOD
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", code), patch("tests/test_pricing.py", TEST)]),
    )
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    digest = status["execution"]["changeSetDigest"]
    with pytest.raises(PolicyViolationError, match="network"):
        application.decide_gate(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE,
            change_set_digest=digest,
            actor_id="human.reviewer",
            rationale="ok",
        )
    with pytest.raises(ConfigurationError, match="unknown risk factor"):
        application.decide_gate(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE,
            change_set_digest=digest,
            actor_id="human.reviewer",
            rationale="ok",
            acknowledged_risks=("weather",),
        )
    record, execution = application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="The new HTTP client is expected",
        acknowledged_risks=("network",),
    )
    assert record.acknowledged_risks == ("network",)
    assert execution.status is ResultStatus.PASSED


def test_structured_change_requests_are_checked(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, {}, review={"structuredChanges": True})
    code = GOOD.replace("    return", '    print("debug")\n    return')
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", code), patch("tests/test_pricing.py", TEST)]),
    )
    digest = application.status(python_workspace, run)["execution"]["changeSetDigest"]
    record, execution = application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="Remove the debug output",
        change_requests=(r"No debug print::absent:print\(", "Keep it simple"),
    )
    assert [item.condition for item in record.change_requests] == [r"absent:print\(", "text"]
    execution = application.continue_run(python_workspace, run)
    # The simulated provider applies the same patch again: the requested change is not made.
    assert execution.current_phase is PhaseId.VERIFICATION
    found = rules(application, python_workspace, run)
    assert found["change-request.unmet"].severity is FindingSeverity.HIGH
    assert found["change-request.unverified"].severity is FindingSeverity.INFO


def test_change_requests_need_the_setting(python_workspace: Path, tmp_path: Path) -> None:
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", GOOD), patch("tests/test_pricing.py", TEST)]),
    )
    digest = application.status(python_workspace, run)["execution"]["changeSetDigest"]
    with pytest.raises(ConfigurationError, match="structuredChanges"):
        application.decide_gate(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.REQUEST_CHANGES,
            change_set_digest=digest,
            actor_id="human.reviewer",
            rationale="x",
            change_requests=("Do it::text",),
        )


def broken_legacy(workspace: Path) -> None:
    (workspace / "tests" / "test_legacy.py").write_text(
        "def test_legacy_behaviour() -> None:\n    assert 1 + 1 == 3\n"
    )


def test_preexisting_failure_does_not_block(python_workspace: Path, tmp_path: Path) -> None:
    broken_legacy(python_workspace)
    configure(python_workspace, {"differential": True})
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", GOOD), patch("tests/test_pricing.py", TEST)]),
    )
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    pytest_result = latest(application, python_workspace, run)["python.pytest"]
    assert pytest_result.kind is ValidationKind.PREEXISTING_ERROR
    assert pytest_result.status is ResultStatus.PASSED
    note = rules(application, python_workspace, run)["differential.preexisting"]
    assert note.introduced is False and "test_legacy" in note.message
    assert status["gate"]["status"] == ResultStatus.PASSED


def test_introduced_failure_blocks_with_its_own_finding(
    python_workspace: Path, tmp_path: Path
) -> None:
    broken_legacy(python_workspace)
    configure(python_workspace, {"differential": True})
    wrong = GOOD.replace("1 - rate", "1 + rate")
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", wrong), patch("tests/test_pricing.py", TEST)]),
    )
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.VERIFICATION
    )
    pytest_result = latest(application, python_workspace, run)["python.pytest"]
    assert pytest_result.kind is ValidationKind.INTRODUCED_ERROR
    found = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "differential.introduced"
    ]
    assert found and all(item.introduced for item in found)
    assert any("test_at_threshold" in item.message for item in found)
    assert not any("test_legacy" in item.message for item in found)


def test_without_the_setting_a_preexisting_failure_still_blocks(
    python_workspace: Path, tmp_path: Path
) -> None:
    broken_legacy(python_workspace)
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", GOOD), patch("tests/test_pricing.py", TEST)]),
    )
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.VERIFICATION
    )
