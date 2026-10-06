"""The task of a run cannot change under it and the decision is bound to the acceptance contract
(#48, ``governance.pinTaskRevision``)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind

TASK = (
    "taskId: task_discount\n"
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
    "implementation:\n  mode: patch\n  patches:\n"
    "    - path: src/sample/pricing.py\n      operation: replace\n      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
REPLACEMENT = TASK.replace(
    "A subtotal of 100 at ten percent returns 90.", "A subtotal of 100 returns 100."
)


def _task_file(tmp_path: Path, content: str, name: str = "task.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _create(workspace: Path, file: Path) -> object:
    return CliRunner().invoke(
        app, ["task", "create", "--file", str(file), "--path", str(workspace)]
    )


def _pending(workspace: Path, tmp_path: Path) -> tuple[str, str]:
    assert _create(workspace, _task_file(tmp_path, TASK)).exit_code == 0
    execution = HarnessApplication().start_run(workspace, "task_discount")
    assert execution.current_phase.value == "DECISION"
    assert execution.change_set_digest
    return execution.execution_id, execution.change_set_digest


def _drop_governance(workspace: Path) -> None:
    config = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    value.pop("governance")
    config.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_task_create_is_refused_while_a_run_is_open(python_workspace: Path, tmp_path: Path) -> None:
    run, digest = _pending(python_workspace, tmp_path)
    replaced = _create(python_workspace, _task_file(tmp_path, REPLACEMENT, "other.yaml"))
    assert replaced.exit_code == 5
    assert run in replaced.stderr
    stored = HarnessApplication().get_task(python_workspace, "task_discount")
    assert stored.acceptance_criteria[0].text.endswith("returns 90.")
    record, execution = HarnessApplication().decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    assert record.acceptance_contract_digest is not None
    assert execution.status.value == "PASSED"
    # Once the run is closed the id can be reused.
    assert _create(python_workspace, _task_file(tmp_path, REPLACEMENT, "other.yaml")).exit_code == 0


def test_phases_use_the_pinned_revision(python_workspace: Path, tmp_path: Path) -> None:
    """Even a task replaced behind the harness's back (the projection edited) does not reach
    the run: it works on, and the decision binds, the revision it was created with."""
    run, digest = _pending(python_workspace, tmp_path)
    status = HarnessApplication().status(python_workspace, run)
    created = HarnessApplication()
    with sqlite3.connect(python_workspace / ".harness" / "state.db") as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM records WHERE record_type='task' AND record_id=?",
                ("task_discount",),
            ).fetchone()[0]
        )
        payload["acceptanceCriteria"][0]["text"] = "Anything goes."
        connection.execute(
            "UPDATE records SET payload_json=? WHERE record_type='task' AND record_id=?",
            (json.dumps(payload), "task_discount"),
        )
    record, execution = created.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    assert execution.status.value == "PASSED"
    assert status["execution"]["taskId"] == "task_discount"
    events = HarnessApplication().trace(python_workspace, run, "jsonl").decode().splitlines()
    run_created = json.loads(events[0])
    assert run_created["eventType"] == "run.created"
    assert run_created["payload"]["taskDigest"].startswith("sha256:")
    assert record.acceptance_contract_digest


def test_decision_is_refused_when_the_contract_changed(
    python_workspace: Path, tmp_path: Path
) -> None:
    run, digest = _pending(python_workspace, tmp_path)
    with sqlite3.connect(python_workspace / ".harness" / "state.db") as connection:
        connection.execute(
            "UPDATE flags SET value=? WHERE key=?", ("sha256:" + "0" * 64, f"contract:{run}")
        )
    result = CliRunner().invoke(
        app,
        [
            "gate",
            "decide",
            "--run",
            run,
            "--decision",
            "APPROVE_EXCEPTION",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "reviewed",
            "--path",
            str(python_workspace),
        ],
    )
    assert result.exit_code == 5
    assert "acceptance contract" in result.stderr


def test_without_the_setting_task_create_replaces_as_before(
    python_workspace: Path, tmp_path: Path
) -> None:
    _drop_governance(python_workspace)
    run, digest = _pending(python_workspace, tmp_path)
    assert _create(python_workspace, _task_file(tmp_path, REPLACEMENT, "other.yaml")).exit_code == 0
    record, _ = HarnessApplication().decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    assert "acceptanceContractDigest" not in record.model_dump(mode="json", by_alias=True)
    events = HarnessApplication().trace(python_workspace, run, "jsonl").decode().splitlines()
    assert "taskDigest" not in json.loads(events[0])["payload"]
