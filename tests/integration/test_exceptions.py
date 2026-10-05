"""Exceptions with expiry, scope, alternative evidence and follow-up (#53)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.application.exceptions import (
    ExceptionOptions,
    parse_expiry,
    parse_scope,
)
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import ExceptionRecord, HumanDecision
from governed_harness.orchestration.engine import EngineServices

FUNCTION = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
SECRET = 'password = "hunter22"\n'


def _task(task_id: str, content: str, test_name: str) -> str:
    body = "\n".join("        " + line if line else "" for line in content.splitlines())
    return f"""\
taskId: {task_id}
title: Discount with a configured credential
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
{body}
    - path: tests/test_pricing.py
      operation: append
      content: |


        def {test_name}() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""


def run_task(workspace: Path, tmp_path: Path, task_id: str, content: str, test: str) -> str:
    source = tmp_path / f"{task_id}.yaml"
    source.write_text(_task(task_id, content, test), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application.start_run(workspace, task_id).execution_id


def first_run(workspace: Path, tmp_path: Path) -> str:
    return run_task(
        workspace, tmp_path, "task_a", SECRET + "\n\n" + FUNCTION, "test_req_discount_a"
    )


def second_run(workspace: Path, tmp_path: Path) -> str:
    return run_task(
        workspace, tmp_path, "task_b", FUNCTION + "\n\n" + SECRET, "test_req_discount_b"
    )


def digest_of(workspace: Path, run_id: str) -> str:
    return str(HarnessApplication().review(workspace, run_id)["run"]["changeSetDigest"])


def grant(workspace: Path, run_id: str, **options: str) -> tuple[HumanDecision, str]:
    record, execution = HarnessApplication().decide_gate(
        workspace,
        execution_id=run_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=digest_of(workspace, run_id),
        actor_id="security.lead",
        rationale="Test credential of the local fixture; rotation tracked",
        exception=ExceptionOptions(**options),
    )
    return record, execution.status.value


def services(workspace: Path) -> EngineServices:
    return EngineServices.open(ConfigurationResolver().resolve(workspace))


def expire_everything(workspace: Path) -> None:
    opened = services(workspace)
    try:
        past = datetime.now(UTC) - timedelta(minutes=1)
        for record in opened.state.list("exception", ExceptionRecord):
            opened.state.put(
                "exception",
                record.exception_id,
                record.model_copy(
                    update={"granted_at": past - timedelta(days=1), "expires_at": past}
                ),
                execution_id=record.execution_id,
                project_id=record.project_id,
            )
        for decision in opened.state.list("decision", HumanDecision):
            if decision.expires_at:
                opened.state.put(
                    "decision",
                    decision.decision_id,
                    decision.model_copy(update={"expires_at": past}),
                    execution_id=decision.execution_id,
                )
    finally:
        opened.close()


def test_exception_is_recorded_with_expiry_scope_and_provenance(
    python_workspace: Path, tmp_path: Path
) -> None:
    run_id = first_run(python_workspace, tmp_path)
    brief = HarnessApplication().review(python_workspace, run_id)
    assert brief["gate"]["status"] == "FAILED"
    assert brief["risks"][0]["ruleId"] == "review.possible-secret"
    decision, status = grant(
        python_workspace,
        run_id,
        expires_in="7d",
        follow_up="ISSUE-17",
        alternative_evidence="Secret scanner allow-list entry",
    )
    assert status == "PASSED"
    assert decision.expires_at is not None
    assert timedelta(days=6) < decision.expires_at - decision.decided_at <= timedelta(days=7)
    [entry] = HarnessApplication().list_exceptions(python_workspace)
    assert entry["status"] == "ACTIVE" and entry["decisionId"] == decision.decision_id
    assert entry["followUp"] == "ISSUE-17"
    assert entry["alternativeEvidence"] == "Secret scanner allow-list entry"
    [scope] = entry["scope"]
    assert scope["ruleId"] == "review.possible-secret"
    assert scope["path"] == "src/sample/pricing.py"
    assert scope["fingerprint"].startswith("sha256:")
    opened = services(python_workspace)
    try:
        events = [item.event_type for item in opened.events.list(run_id)]
        assert events.index("exception.granted") < events.index("run.closed")
        assert opened.events.verify_chain(run_id)
    finally:
        opened.close()


def test_a_later_run_does_not_block_while_the_exception_is_in_force(
    python_workspace: Path, tmp_path: Path
) -> None:
    grant(python_workspace, first_run(python_workspace, tmp_path))
    later = second_run(python_workspace, tmp_path)
    brief = HarnessApplication().review(python_workspace, later)
    assert brief["gate"]["status"] == "PASSED"
    exception_id = HarnessApplication().list_exceptions(python_workspace)[0]["exceptionId"]
    assert f"EXCEPTION_APPLIED_{exception_id}" in [
        item["code"] for item in brief["gate"]["reasons"]
    ]
    assert brief["exceptions"][0]["reliedOnByGate"] is True
    assert HarnessApplication().list_exceptions(python_workspace)[0]["appliedIn"] == [later]
    # Once it expires the same findings block again, also for the run already waiting.
    expire_everything(python_workspace)
    HarnessApplication().continue_run(python_workspace, later)
    again = HarnessApplication().review(python_workspace, later)
    assert again["gate"]["status"] == "FAILED"
    assert HarnessApplication().list_exceptions(python_workspace, status="expired")


def test_an_expired_exception_blocks_its_own_run(python_workspace: Path, tmp_path: Path) -> None:
    run_id = first_run(python_workspace, tmp_path)
    record, execution = HarnessApplication().decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=digest_of(python_workspace, run_id),
        actor_id="security.lead",
        rationale="Accepted for now",
        continue_after=False,
    )
    assert record.expires_at is not None
    expire_everything(python_workspace)
    execution = HarnessApplication().continue_run(python_workspace, run_id)
    assert execution.status.value == "BLOCKED"
    status = HarnessApplication().status(python_workspace, run_id)
    assert "expired" in status["phases"][-1]["summary"]


def _disable(workspace: Path) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value.pop("review", None)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def test_without_the_key_an_exception_is_a_plain_decision(
    python_workspace: Path, tmp_path: Path
) -> None:
    _disable(python_workspace)
    run_id = first_run(python_workspace, tmp_path)
    with pytest.raises(ConfigurationError, match="review.exceptions"):
        grant(python_workspace, run_id, expires_in="7d")
    decision, status = grant(python_workspace, run_id)
    assert status == "PASSED" and decision.expires_at is None
    assert HarnessApplication().list_exceptions(python_workspace) == []
    later = second_run(python_workspace, tmp_path)
    assert HarnessApplication().review(python_workspace, later)["gate"]["status"] == "FAILED"


def test_options_need_approve_exception(python_workspace: Path, tmp_path: Path) -> None:
    run_id = first_run(python_workspace, tmp_path)
    with pytest.raises(ConfigurationError):
        HarnessApplication().decide_gate(
            python_workspace,
            execution_id=run_id,
            decision=DecisionKind.REJECT,
            change_set_digest=digest_of(python_workspace, run_id),
            actor_id="reviewer",
            rationale="No",
            exception=ExceptionOptions(follow_up="X"),
        )


def test_scope_option_widens_to_a_rule(python_workspace: Path, tmp_path: Path) -> None:
    grant(
        python_workspace, first_run(python_workspace, tmp_path), scope=("review.possible-secret",)
    )
    [entry] = HarnessApplication().list_exceptions(python_workspace)
    assert entry["scope"] == [
        {"ruleId": "review.possible-secret", "path": None, "fingerprint": None}
    ]


def test_cli_exception_options_and_listing(python_workspace: Path, tmp_path: Path) -> None:
    run_id = first_run(python_workspace, tmp_path)
    runner = CliRunner()
    path = ["--path", str(python_workspace)]
    result = runner.invoke(
        app,
        [
            "gate",
            "decide",
            "--run",
            run_id,
            "--decision",
            "APPROVE_EXCEPTION",
            "--change-set-digest",
            digest_of(python_workspace, run_id),
            "--rationale",
            "Fixture credential",
            "--expires-in",
            "36h",
            "--follow-up",
            "ISSUE-9",
            *path,
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["decision"]["expiresAt"]
    listed = runner.invoke(app, ["exceptions", "list", "--status", "active", *path])
    assert json.loads(listed.stdout)[0]["followUp"] == "ISSUE-9"
    soon = runner.invoke(app, ["exceptions", "list", "--expiring-within", "1", *path])
    assert len(json.loads(soon.stdout)) == 0
    within = runner.invoke(app, ["exceptions", "list", "--expiring-within", "2", *path])
    assert len(json.loads(within.stdout)) == 1
    text = runner.invoke(app, ["--no-json", "exceptions", "list", *path])
    assert "ACTIVE" in text.stdout and "ISSUE-9" in text.stdout


def test_interactive_exception(python_workspace: Path, tmp_path: Path) -> None:
    run_id = first_run(python_workspace, tmp_path)
    digest = digest_of(python_workspace, run_id)
    answers = (
        f"APPROVE_EXCEPTION\nFixture credential\n{digest[7:19]}\n3d\nScanner allow-list\nISSUE-4\n"
    )
    result = CliRunner().invoke(
        app,
        ["gate", "decide", "--run", run_id, "-i", "--path", str(python_workspace)],
        input=answers,
    )
    assert result.exit_code == 0, result.output
    [entry] = HarnessApplication().list_exceptions(python_workspace)
    assert entry["followUp"] == "ISSUE-4" and entry["alternativeEvidence"] == "Scanner allow-list"
    assert entry["daysLeft"] == 2


def test_parse_expiry_and_scope() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_expiry(
        expires_in="14d", expires_at=None, default_days=30, now=now
    ) == now + timedelta(days=14)
    assert parse_expiry(
        expires_in="36h", expires_at=None, default_days=30, now=now
    ) == now + timedelta(hours=36)
    assert parse_expiry(
        expires_in="2w", expires_at=None, default_days=30, now=now
    ) == now + timedelta(weeks=2)
    assert parse_expiry(
        expires_in=None, expires_at=None, default_days=30, now=now
    ) == now + timedelta(days=30)
    assert parse_expiry(
        expires_in=None, expires_at="2026-02-01T00:00:00", default_days=30, now=now
    ) == datetime(2026, 2, 1, tzinfo=UTC)
    for kwargs in (
        {"expires_in": "0d", "expires_at": None},
        {"expires_in": "400d", "expires_at": None},
        {"expires_in": "soon", "expires_at": None},
        {"expires_in": None, "expires_at": "2025-01-01T00:00:00+00:00"},
        {"expires_in": "1d", "expires_at": "2026-02-01T00:00:00+00:00"},
    ):
        with pytest.raises(ConfigurationError):
            parse_expiry(default_days=30, now=now, **kwargs)
    assert [item.path for item in parse_scope(("rule.a", "rule.b:src/x.py"))] == [None, "src/x.py"]
    with pytest.raises(ConfigurationError):
        parse_scope((":src/x.py",))
