"""``harness verify`` and the integrity of the record (#49): status reports a broken chain instead
of aborting, the records are checked against the events, the head of the chain is anchored
outside ``.harness`` and ``trace`` refuses a run that does not verify."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind

TASK = (
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
    "implementation:\n  mode: patch\n  patches:\n"
    "    - path: src/sample/pricing.py\n      operation: replace\n      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)


def _closed_run(workspace: Path, tmp_path: Path) -> str:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_file)
    pending = application.start_run(workspace, task.task_id)
    assert pending.change_set_digest
    _, execution = application.decide_gate(
        workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=pending.change_set_digest,
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    assert execution.status.value == "PASSED"
    return pending.execution_id


def _db(workspace: Path) -> sqlite3.Connection:
    return sqlite3.connect(workspace / ".harness" / "state.db")


def _set_governance(workspace: Path, **settings: object) -> None:
    config = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    if settings:
        value["governance"].update(settings)
    else:
        value.pop("governance")
    config.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _cli(*args: str) -> object:
    return CliRunner().invoke(app, list(args))


def test_verify_accepts_an_untouched_run(python_workspace: Path, tmp_path: Path) -> None:
    run = _closed_run(python_workspace, tmp_path)
    result = _cli("verify", "--run", run, "--path", str(python_workspace))
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["valid"] is True
    verified = report["runs"][0]
    assert verified["eventChain"]["valid"] is True
    assert verified["anchor"]["status"] == "matched"
    assert verified["anchor"]["mode"] == "file"
    assert verified["records"]["checked"] > 10
    assert verified["records"]["problems"] == []
    assert verified["artifacts"]["checked"] > 5
    every = _cli("verify", "--path", str(python_workspace))
    assert every.exit_code == 0
    assert json.loads(every.stdout)["runCount"] == 1


def test_edited_event_is_reported_by_status_not_raised(
    python_workspace: Path, tmp_path: Path
) -> None:
    run = _closed_run(python_workspace, tmp_path)
    with _db(python_workspace) as connection:
        connection.execute(
            "UPDATE events SET payload_json=? WHERE execution_id=? AND event_type=?",
            (json.dumps({"forged": True}), run, "human.decision.recorded"),
        )
    status = _cli("status", "--run", run, "--path", str(python_workspace))
    assert status.exit_code == 0, status.stderr
    body = json.loads(status.stdout)
    assert body["eventChainValid"] is False
    assert "event digest mismatch" in body["eventChainError"]
    assert body["recordsValid"] is False
    verified = _cli("verify", "--run", run, "--path", str(python_workspace))
    assert verified.exit_code == 6
    assert json.loads(verified.stdout)["runs"][0]["eventChain"]["valid"] is False
    trace = _cli("trace", "--run", run, "--format", "jsonl", "--path", str(python_workspace))
    assert trace.exit_code == 6
    assert "does not verify" in trace.stderr


def test_status_does_not_abort_without_governance_settings(
    python_workspace: Path, tmp_path: Path
) -> None:
    """Reporting instead of raising is a defect fix; exporting an unverified trace is the 1.0.0
    behaviour a project.yaml without the setting keeps."""
    _set_governance(python_workspace)
    run = _closed_run(python_workspace, tmp_path)
    with _db(python_workspace) as connection:
        connection.execute(
            "UPDATE events SET payload_json=? WHERE execution_id=? AND event_type=?",
            (json.dumps({"forged": True}), run, "gate.evaluated"),
        )
    status = _cli("status", "--run", run, "--path", str(python_workspace))
    assert status.exit_code == 0
    body = json.loads(status.stdout)
    assert body["eventChainValid"] is False
    assert "recordsValid" not in body
    trace = _cli("trace", "--run", run, "--format", "json", "--path", str(python_workspace))
    assert trace.exit_code == 0


def test_truncated_chain_and_forged_projection_are_detected(
    python_workspace: Path, tmp_path: Path
) -> None:
    """The measured attack: delete the last events and rewrite the decider in the projection.
    The shorter chain is still well linked; the anchor and the record check catch it."""
    run = _closed_run(python_workspace, tmp_path)
    with _db(python_workspace) as connection:
        cut = connection.execute(
            "SELECT execution_sequence FROM events WHERE execution_id=? "
            "AND event_type='human.decision.recorded'",
            (run,),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM events WHERE execution_id=? AND execution_sequence>=?", (run, cut)
        )
        row = connection.execute(
            "SELECT record_id, payload_json FROM records WHERE record_type='decision'"
        ).fetchone()
        payload = json.loads(row[1])
        payload["actor"]["actorId"] = "mallory"
        connection.execute(
            "UPDATE records SET payload_json=? WHERE record_type='decision' AND record_id=?",
            (json.dumps(payload), row[0]),
        )
    status = json.loads(_cli("status", "--run", run, "--path", str(python_workspace)).stdout)
    assert status["eventChainValid"] is True  # a truncated chain is still well linked
    assert status["recordsValid"] is False
    result = _cli("verify", "--run", run, "--path", str(python_workspace))
    assert result.exit_code == 6
    report = json.loads(result.stdout)["runs"][0]
    assert report["anchor"]["status"] == "truncated"
    problems = {(item["recordType"], item["problem"]) for item in report["records"]["problems"]}
    assert ("decision", "no-event") in problems
    assert ("execution", "differs") in problems


def test_edited_artifact_is_reported(python_workspace: Path, tmp_path: Path) -> None:
    run = _closed_run(python_workspace, tmp_path)
    with _db(python_workspace) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM records WHERE record_type='evidence' LIMIT 1"
        ).fetchone()[0]
    digest = json.loads(payload)["artifactRef"].removeprefix("artifact://sha256/")
    target = python_workspace / ".harness" / "artifacts" / "blobs" / "sha256" / digest[:2]
    target = target / digest[2:]
    target.chmod(0o600)
    target.write_bytes(b"tampered")
    report = json.loads(_cli("verify", "--run", run, "--path", str(python_workspace)).stdout)
    problems = report["runs"][0]["artifacts"]["problems"]
    assert any(item["problem"] == "content-differs" for item in problems)


def test_git_note_anchor(python_workspace: Path, tmp_path: Path) -> None:
    _set_governance(python_workspace, chainAnchor="git-note")
    run = _closed_run(python_workspace, tmp_path)
    report = json.loads(_cli("verify", "--run", run, "--path", str(python_workspace)).stdout)
    anchor = report["runs"][0]["anchor"]
    assert anchor["mode"] == "git-note"
    assert anchor["status"] == "matched"
    with _db(python_workspace) as connection:
        connection.execute(
            "DELETE FROM events WHERE execution_id=? AND execution_sequence=("
            "SELECT MAX(execution_sequence) FROM events WHERE execution_id=?)",
            (run, run),
        )
    report = json.loads(_cli("verify", "--run", run, "--path", str(python_workspace)).stdout)
    assert report["runs"][0]["anchor"]["status"] == "truncated"


def test_verify_unknown_run_is_not_found(python_workspace: Path) -> None:
    assert _cli("verify", "--run", "run_missing", "--path", str(python_workspace)).exit_code == 3


@pytest.mark.parametrize("mode", ["off", None])
def test_no_anchor_mode_reports_off(python_workspace: Path, tmp_path: Path, mode: str) -> None:
    if mode is None:
        _set_governance(python_workspace)
    else:
        _set_governance(python_workspace, chainAnchor=mode)
    run = _closed_run(python_workspace, tmp_path)
    report = json.loads(_cli("verify", "--run", run, "--path", str(python_workspace)).stdout)
    assert report["valid"] is True
    assert report["runs"][0]["anchor"] == {"mode": "off", "status": "off"}
