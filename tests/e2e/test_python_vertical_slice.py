from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError


def task_file(tmp_path: Path, *, secret: bool = False, wrong: bool = False) -> Path:
    implementation_lines = [
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:",
    ]
    if secret:
        implementation_lines.extend(
            [
                '    password = "supersecret123"',
                "    _ = password",
            ]
        )
    implementation_lines.append(
        "    return subtotal"
        if wrong
        else "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal"
    )
    implementation = "\n".join(implementation_lines) + "\n"
    path = tmp_path / "python-task.yaml"
    path.write_text(
        "title: Implement threshold discount\n"
        "intent: Apply a discount at or above the threshold.\n"
        "acceptanceCriteria:\n"
        "  - A subtotal of 100 with a ten percent rate returns 90.\n"
        "  - A subtotal below the threshold is unchanged.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/sample/pricing.py\n"
        "      operation: replace\n"
        "      content: |\n"
        + "".join(f"        {line}\n" for line in implementation.splitlines())
        + "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        def test_at_threshold() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.e2e
def test_python_execution_closes_after_digest_bound_approval(
    python_workspace: Path, tmp_path: Path
) -> None:
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file(tmp_path))
    pending = app.start_run(python_workspace, task.task_id)
    assert pending.status is ResultStatus.BLOCKED
    assert pending.current_phase is PhaseId.DECISION
    assert pending.change_set_digest
    decision, final = app.decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=pending.change_set_digest,
        actor_id="human.reviewer",
        rationale="All fixture validations passed",
    )
    assert decision.change_set_digest == pending.change_set_digest
    assert final.status is ResultStatus.PASSED
    status = app.status(python_workspace, final.execution_id)
    assert status["eventChainValid"] is True
    assert status["humanDecision"]["decision"] == "APPROVE"
    trace = app.trace(python_workspace, final.execution_id, "markdown").decode()
    assert "Execution trace" in trace
    assert "Recommendations require human review" in trace


@pytest.mark.e2e
def test_stale_approval_is_rejected_before_decision_record(
    python_workspace: Path, tmp_path: Path
) -> None:
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file(tmp_path))
    pending = app.start_run(python_workspace, task.task_id)
    old_digest = pending.change_set_digest
    assert old_digest
    source = python_workspace / "src" / "sample" / "pricing.py"
    source.write_text(source.read_text() + "\n# later owned-path change\n")
    with pytest.raises(PolicyViolationError):
        app.decide_gate(
            python_workspace,
            execution_id=pending.execution_id,
            decision=DecisionKind.APPROVE,
            change_set_digest=old_digest,
            actor_id="human.reviewer",
            rationale="stale",
        )


@pytest.mark.e2e
def test_failed_verification_can_resume_after_manual_correction(
    python_workspace: Path, tmp_path: Path
) -> None:
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file(tmp_path, wrong=True))
    failed = app.start_run(python_workspace, task.task_id)
    assert failed.status is ResultStatus.FAILED
    assert failed.current_phase is PhaseId.VERIFICATION
    (python_workspace / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    )
    pending = app.continue_run(python_workspace, failed.execution_id)
    assert pending.status is ResultStatus.BLOCKED
    assert pending.current_phase is PhaseId.DECISION


@pytest.mark.e2e
def test_blocking_review_requires_exception_or_changes(
    python_workspace: Path, tmp_path: Path
) -> None:
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file(tmp_path, secret=True))
    pending = app.start_run(python_workspace, task.task_id)
    status = app.status(python_workspace, pending.execution_id)
    assert status["gate"]["status"] == "FAILED"
    assert status["findings"]["bySeverity"]["CRITICAL"] >= 1
    with pytest.raises(PolicyViolationError):
        app.decide_gate(
            python_workspace,
            execution_id=pending.execution_id,
            decision=DecisionKind.APPROVE,
            change_set_digest=pending.change_set_digest or "",
            actor_id="human.reviewer",
            rationale="ordinary approval is not valid",
        )
    _, final = app.decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=pending.change_set_digest or "",
        actor_id="human.security",
        rationale="Controlled synthetic secret fixture; not production data",
    )
    assert final.status is ResultStatus.PASSED
