from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import (
    DecisionKind,
    EvidenceKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
)
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.domain.models import Evidence, Execution, Finding, Plan, ValidationResult
from governed_harness.orchestration.engine import EngineServices

TASK = """\
taskId: task_traceability
title: Apply a percentage discount above a threshold
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - "A1. A subtotal at or above the threshold is reduced by the rate."
  - requirementId: req_below
    text: A subtotal below the threshold is unchanged.
  - Keep the public signature of apply_discount.
acceptanceCriteria:
  - criterionId: ac_at_threshold
    text: apply_discount(100, 100, 0.1) returns 90.
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


        def test_a1_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""

TRACED_TASK = TASK.replace(
    "            assert apply_discount(100, 100, 0.1) == 90\n",
    "            assert apply_discount(100, 100, 0.1) == 90\n\n\n"
    "        def test_req_below_threshold() -> None:\n"
    "            assert apply_discount(99, 100, 0.1) == 99\n",
)


def set_policy(workspace: Path, policy: str | None) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    if policy is None:
        value.pop("verification", None)
    else:
        value["verification"] = {"requirementTraceability": policy}
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def start(workspace: Path, tmp_path: Path, content: str = TASK) -> Execution:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(content, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_file)
    run = application.start_run(workspace, task.task_id)
    assert run.status is ResultStatus.BLOCKED
    assert run.current_phase is PhaseId.DECISION
    return run


def records(workspace: Path, run_id: str) -> dict[str, Any]:
    services = EngineServices.open(ConfigurationResolver().resolve(workspace))
    try:
        traceability = [
            item
            for item in services.state.list("validation", ValidationResult, execution_id=run_id)
            if item.validator_id == "traceability.requirements"
        ]
        evidence = [
            item
            for item in services.state.list("evidence", Evidence, execution_id=run_id)
            if item.kind is EvidenceKind.TEST_REPORT
        ]
        reports = [json.loads(services.artifacts.get(item.artifact_ref)) for item in evidence]
        findings = [
            item
            for item in services.state.list("finding", Finding, execution_id=run_id)
            if item.validator_id == "traceability.requirements"
        ]
        (plan,) = services.state.list("plan", Plan, execution_id=run_id)
        return {
            "validations": traceability,
            "evidence": evidence,
            "reports": reports,
            "findings": findings,
            "plan": plan,
        }
    finally:
        services.close()


def test_untraced_requirement_fails_the_gate_under_enforce(
    python_workspace: Path, tmp_path: Path
) -> None:
    run = start(python_workspace, tmp_path)
    application = HarnessApplication()
    status = application.status(python_workspace, run.execution_id)
    assert status["gate"]["status"] == "FAILED"
    found = records(python_workspace, run.execution_id)

    (validation,) = found["validations"]
    assert validation.status is ResultStatus.PASSED
    assert validation.mandatory is True
    assert validation.summary == (
        "1 of 2 identified requirement(s) traced to tests, 1 untraced, "
        "1 without an identifier skipped"
    )
    (finding,) = found["findings"]
    assert finding.rule_id == "traceability.requirement-untested"
    assert finding.severity is FindingSeverity.HIGH
    assert finding.message == (
        "No test names requirement req_below: A subtotal below the threshold is unchanged."
    )
    assert finding.finding_id in validation.finding_ids
    assert f"BLOCKING_FINDING_{finding.finding_id}" in status["gate"]["reasonCodes"]
    assert "traceability.requirements" in found["plan"].validator_ids

    (evidence,) = found["evidence"]
    assert evidence.phase_id is PhaseId.VERIFICATION
    assert evidence.artifact_ref in validation.evidence_refs
    (report,) = found["reports"]
    assert report["policy"] == "enforce"
    assert report["testFiles"] == ["tests/test_pricing.py"]
    assert [
        (item["identifier"], item["identifierSource"], item["traced"])
        for item in report["requirements"]
    ] == [("A1", "text", True), ("req_below", "requirementId", False)]
    assert report["requirements"][0]["tests"] == [
        {
            "nodeId": "tests/test_pricing.py::test_a1_at_threshold",
            "path": "tests/test_pricing.py",
            "match": "name",
        }
    ]
    assert (report["tracedCount"], report["untracedCount"], report["skippedCount"]) == (1, 1, 1)

    with pytest.raises(PolicyViolationError):
        application.decide_gate(
            python_workspace,
            execution_id=run.execution_id,
            decision=DecisionKind.APPROVE,
            change_set_digest=run.change_set_digest or "",
            actor_id="human.reviewer",
            rationale="Tests pass",
        )


def test_traced_requirements_pass_the_gate_under_enforce(
    python_workspace: Path, tmp_path: Path
) -> None:
    run = start(python_workspace, tmp_path, TRACED_TASK)
    application = HarnessApplication()
    assert application.status(python_workspace, run.execution_id)["gate"]["status"] == "PASSED"
    found = records(python_workspace, run.execution_id)
    assert found["findings"] == []
    (report,) = found["reports"]
    assert [item["traced"] for item in report["requirements"]] == [True, True]
    _, final = application.decide_gate(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=run.change_set_digest or "",
        actor_id="human.reviewer",
        rationale="Every identified requirement is tested",
    )
    assert final.status is ResultStatus.PASSED


def test_warn_records_a_low_finding_and_the_gate_passes(
    python_workspace: Path, tmp_path: Path
) -> None:
    set_policy(python_workspace, "warn")
    run = start(python_workspace, tmp_path)
    status = HarnessApplication().status(python_workspace, run.execution_id)
    assert status["gate"]["status"] == "PASSED"
    found = records(python_workspace, run.execution_id)
    (validation,) = found["validations"]
    assert validation.mandatory is False
    (finding,) = found["findings"]
    assert finding.severity is FindingSeverity.LOW
    assert found["reports"][0]["policy"] == "warn"


@pytest.mark.parametrize("policy", ["off", None])
def test_off_and_absent_policy_run_as_in_1_0_0(
    python_workspace: Path, tmp_path: Path, policy: str | None
) -> None:
    set_policy(python_workspace, policy)
    run = start(python_workspace, tmp_path)
    status = HarnessApplication().status(python_workspace, run.execution_id)
    assert status["gate"]["status"] == "PASSED"
    found = records(python_workspace, run.execution_id)
    assert found["validations"] == []
    assert found["evidence"] == []
    assert found["findings"] == []
    assert "traceability.requirements" not in found["plan"].validator_ids


def test_a_mandatory_validator_failure_still_records_the_traceability_check(
    python_workspace: Path, tmp_path: Path
) -> None:
    failing = TASK.replace("== 90\n", "== 91\n")
    task_file = tmp_path / "task.yaml"
    task_file.write_text(failing, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    run = application.start_run(python_workspace, task.task_id)
    assert run.current_phase is PhaseId.VERIFICATION
    assert run.status is ResultStatus.FAILED
    found = records(python_workspace, run.execution_id)
    (validation,) = found["validations"]
    assert validation.status is ResultStatus.PASSED
    assert len(found["findings"]) == 1
