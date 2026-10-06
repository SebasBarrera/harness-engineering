"""Located findings from validator output and SARIF fingerprints (#53)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication

TASK = """\
taskId: {task_id}
title: Discount with a failing test
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - requirementId: req_discount
    text: A subtotal at or above the threshold is reduced by the rate.
acceptanceCriteria:
  - criterionId: ac_at_threshold
    text: A subtotal of 100 with threshold 100 and rate 0.1 returns 90.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        import os


        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |


        def test_req_discount_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""


def failing_run(workspace: Path, tmp_path: Path, task_id: str = "task_located") -> str:
    source = tmp_path / f"{task_id}.yaml"
    source.write_text(TASK.format(task_id=task_id), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application.start_run(workspace, task_id).execution_id


def test_failing_validators_get_located_findings(python_workspace: Path, tmp_path: Path) -> None:
    run_id = failing_run(python_workspace, tmp_path)
    findings = HarnessApplication().list_findings(python_workspace, run_id)
    by_rule = {item.rule_id: item for item in findings}
    summary = by_rule["python.pytest.failed"]
    test = by_rule["python.pytest.test-failed"]
    assert test.location is not None and test.location.path == "tests/test_pricing.py"
    assert test.location.start_line is not None and test.location.start_line > 1
    assert test.severity == summary.severity
    assert "test_req_discount_at_threshold" in test.message
    lint = next(
        (
            item
            for item in findings
            if item.rule_id == "python.ruff.F401"
            and item.location is not None
            and item.location.path == "src/sample/pricing.py"
        ),
        None,
    )
    if "python.ruff.failed" in by_rule:  # ruff is optional where the tests run
        assert lint is not None and lint.location is not None
        assert lint.location.start_line == 1
        assert lint.severity.value == "MEDIUM"
    risks = HarnessApplication().review(python_workspace, run_id)["risks"]
    assert any(item["location"].startswith("tests/test_pricing.py:") for item in risks)


def test_without_the_key_only_the_summary_finding(python_workspace: Path, tmp_path: Path) -> None:
    path = python_workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value["verification"].pop("outputParsers")
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    run_id = failing_run(python_workspace, tmp_path)
    rules = {item.rule_id for item in HarnessApplication().list_findings(python_workspace, run_id)}
    assert "python.pytest.failed" in rules
    assert not any(rule.endswith(".test-failed") or ".F" in rule for rule in rules)


def test_sarif_fingerprints_are_stable_across_runs(python_workspace: Path, tmp_path: Path) -> None:
    application = HarnessApplication()
    first = failing_run(python_workspace, tmp_path, "task_one")
    sarif = json.loads(application.trace(python_workspace, first, "sarif"))
    results = sarif["runs"][0]["results"]
    assert all("harnessFinding/v1" in item["partialFingerprints"] for item in results)
    assert all(item["properties"]["executionId"] == first for item in results)
    fingerprints = {
        item["ruleId"]: item["partialFingerprints"]["harnessFinding/v1"] for item in results
    }
    # The same failure in another run of the project keeps its fingerprint.
    application.cancel_run(python_workspace, first, "reviewer")
    for name in ("src/sample/pricing.py", "tests/test_pricing.py"):
        (python_workspace / name).write_text(
            (python_workspace / name).read_text().split("\n\n\ndef test_req")[0]
        )
    second = failing_run(python_workspace, tmp_path, "task_two")
    again = json.loads(application.trace(python_workspace, second, "sarif"))["runs"][0]["results"]
    assert fingerprints["python.pytest.failed"] in {
        item["partialFingerprints"]["harnessFinding/v1"] for item in again
    }
