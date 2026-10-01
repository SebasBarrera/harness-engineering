"""The gate consolidates the latest attempt of each validator for the current digest (#2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus


def discount_task(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        "title: Implement threshold discount\n"
        "intent: Apply a discount at or above the threshold.\n"
        "acceptanceCriteria:\n"
        "  - A subtotal of 100 with a ten percent rate returns 90.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/sample/pricing.py\n"
        "      operation: replace\n"
        "      content: |\n"
        "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
        "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        def test_at_threshold() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.e2e
def test_passing_retry_with_same_changeset_clears_the_earlier_failure(
    python_workspace: Path, tmp_path: Path
) -> None:
    # A test outside the ChangeSet is already broken when the run starts (brownfield baseline).
    legacy = python_workspace / "tests" / "test_legacy.py"
    legacy.write_text("def test_legacy() -> None:\n    assert 1 + 1 == 3\n", encoding="utf-8")
    app = HarnessApplication()
    task = app.create_task(python_workspace, discount_task(tmp_path))
    failed = app.start_run(python_workspace, task.task_id)
    assert failed.status is ResultStatus.FAILED
    assert failed.current_phase is PhaseId.VERIFICATION
    digest = failed.change_set_digest

    # The baseline is repaired without touching the owned files: the ChangeSet does not change.
    # The repaired file has a different size: Python reuses the bytecode cache of a source with
    # the same size rewritten within the same second, and pytest would run the broken test again.
    legacy.write_text(
        "def test_legacy() -> None:\n    assert 1 + 1 == 2  # repaired\n", encoding="utf-8"
    )
    pending = app.continue_run(python_workspace, failed.execution_id)
    assert pending.current_phase is PhaseId.DECISION
    assert pending.change_set_digest == digest
    status = app.status(python_workspace, pending.execution_id)
    assert status["gate"]["status"] == "PASSED"
    # The earlier attempt stays in the record as history.
    assert status["validationSummary"]["byStatus"].get("FAILED", 0) >= 1

    _, final = app.decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest or "",
        actor_id="human.reviewer",
        rationale="Baseline repaired; the latest verification passed",
    )
    assert final.status is ResultStatus.PASSED
