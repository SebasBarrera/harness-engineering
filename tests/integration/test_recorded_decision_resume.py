"""A decision recorded just before the harness died is applied when the run resumes, once, and
only while it still holds (#179, H47 K10)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.models import Execution
from governed_harness.orchestration.engine import REJECTED_REASON, RunEngine


class _Crash(BaseException):
    """The harness process dies: nothing after this point of ``decide`` runs."""


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


def _events(workspace: Path, run: str, event_type: str) -> int:
    with sqlite3.connect(workspace / ".harness" / "state.db") as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM events WHERE execution_id=? AND event_type=?",
            (run, event_type),
        ).fetchone()
    return int(row[0])


def _crash_after_recording(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    tmp_path: Path,
    decision: DecisionKind,
) -> tuple[HarnessApplication, Execution]:
    """A run waiting in DECISION whose decision event is stored, then the harness dies before
    the run moves on (the point H47 K10 kills ``gate decide`` at)."""
    app = HarnessApplication()
    task = app.create_task(workspace, discount_task(tmp_path))
    pending = app.start_run(workspace, task.task_id)
    assert pending.current_phase is PhaseId.DECISION
    assert pending.status is ResultStatus.BLOCKED

    def crash(*args: object, **kwargs: object) -> Execution:
        raise _Crash

    with monkeypatch.context() as patch:
        patch.setattr(RunEngine, "_decided_execution", crash)
        with pytest.raises(_Crash):
            app.decide_gate(
                workspace,
                execution_id=pending.execution_id,
                decision=decision,
                change_set_digest=pending.change_set_digest or "",
                actor_id="human.reviewer",
                rationale="Reviewed",
            )
    assert _events(workspace, pending.execution_id, "human.decision.recorded") == 1
    stored = app.status(workspace, pending.execution_id)["execution"]
    assert stored["currentPhase"] == "DECISION"
    assert stored.get("humanDecisionId") is None
    return app, pending


def test_recorded_approval_is_applied_on_resume_once(
    monkeypatch: pytest.MonkeyPatch, python_workspace: Path, tmp_path: Path
) -> None:
    app, pending = _crash_after_recording(
        monkeypatch, python_workspace, tmp_path, DecisionKind.APPROVE
    )
    final = app.continue_run(python_workspace, pending.execution_id)
    assert final.status is ResultStatus.PASSED
    assert final.current_phase is PhaseId.CLOSURE
    assert final.human_decision_id is not None
    run = pending.execution_id
    assert _events(python_workspace, run, "human.decision.recorded") == 1
    assert _events(python_workspace, run, "decision.resumed") == 1
    # A second resume finds nothing left to apply.
    again = app.continue_run(python_workspace, run)
    assert again.status is ResultStatus.PASSED
    assert _events(python_workspace, run, "decision.resumed") == 1


def test_recorded_rejection_is_applied_on_resume(
    monkeypatch: pytest.MonkeyPatch, python_workspace: Path, tmp_path: Path
) -> None:
    app, pending = _crash_after_recording(
        monkeypatch, python_workspace, tmp_path, DecisionKind.REJECT
    )
    final = app.continue_run(python_workspace, pending.execution_id)
    assert final.status is ResultStatus.FAILED
    assert final.terminal_reason == REJECTED_REASON
    assert _events(python_workspace, pending.execution_id, "human.decision.recorded") == 1


def test_recorded_decision_for_a_changed_changeset_is_not_applied(
    monkeypatch: pytest.MonkeyPatch, python_workspace: Path, tmp_path: Path
) -> None:
    app, pending = _crash_after_recording(
        monkeypatch, python_workspace, tmp_path, DecisionKind.APPROVE
    )
    pricing = python_workspace / "src" / "sample" / "pricing.py"
    pricing.write_text(pricing.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    resumed = app.continue_run(python_workspace, pending.execution_id)
    assert resumed.status is not ResultStatus.PASSED
    assert resumed.human_decision_id is None
    assert _events(python_workspace, pending.execution_id, "decision.resumed") == 0
