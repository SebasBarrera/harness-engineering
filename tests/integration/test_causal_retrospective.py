"""Retrospective by cause, rule health across runs and outcomes after a run (#53)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_exceptions import first_run, grant
from test_located_findings import failing_run
from test_review import start
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError


def _without_causal(workspace: Path) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value.pop("retrospective")
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def test_cancelled_run_gets_a_retrospective_by_cause(
    python_workspace: Path, tmp_path: Path
) -> None:
    run_id = failing_run(python_workspace, tmp_path)
    application = HarnessApplication()
    application.cancel_run(python_workspace, run_id, "reviewer")
    # Generated when the run was cancelled, not by the call below.
    assert application.list_recommendations(python_workspace, run_id)
    retro = application.retrospect(python_workspace, run_id)
    assert retro.trigger == "CANCELLED"
    codes = {(item.reason_code, item.subject) for item in retro.causes}
    assert ("MANDATORY_VALIDATOR_FAILED", "python.pytest") in codes
    assert ("RUN_CANCELLED", "VERIFICATION") in codes
    # Optional validators that failed (ruff) are noted, not counted as causes.
    assert not any(item.subject == "python.ruff" for item in retro.causes)
    statements = [item.statement for item in retro.observations]
    if any("python.ruff" in item for item in statements):
        assert any("not counted as causes" in item for item in statements)
    categories = {item.category for item in retro.recommendations}
    assert "validation:python.pytest" in categories
    assert "validation" not in categories


def test_rejected_run_gets_a_retrospective(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    application = HarnessApplication()
    digest = application.review(python_workspace, run_id)["run"]["changeSetDigest"]
    application.decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.REJECT,
        change_set_digest=digest,
        actor_id="reviewer",
        rationale="Not what was asked",
    )
    retro = application.retrospect(python_workspace, run_id)
    assert retro.trigger == "REJECTED"
    assert [item.reason_code for item in retro.causes] == ["REJECTED"]
    assert retro.recommendations[0].category == "specification"


def test_closed_run_with_an_exception(python_workspace: Path, tmp_path: Path) -> None:
    run_id = first_run(python_workspace, tmp_path)
    grant(python_workspace, run_id)
    retro = HarnessApplication().retrospect(python_workspace, run_id)
    assert retro.trigger == "CLOSED"
    codes = {item.reason_code for item in retro.causes}
    assert {"BLOCKING_FINDING", "EXCEPTION_APPROVED", "EXCEPTION_GRANTED"} <= codes
    [rule] = [item for item in retro.recommendations if item.category.startswith("rule:")]
    assert "precise" in rule.statement and rule.risk == "MEDIUM"


def test_without_the_key_the_retrospective_keeps_its_1_0_form(
    python_workspace: Path, tmp_path: Path
) -> None:
    _without_causal(python_workspace)
    run_id = start(python_workspace, tmp_path)
    application = HarnessApplication()
    digest = application.review(python_workspace, run_id)["run"]["changeSetDigest"]
    application.decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest,
        actor_id="reviewer",
        rationale="Covered",
    )
    retro = application.retrospect(python_workspace, run_id)
    dumped = retro.model_dump(mode="json", by_alias=True)
    assert "causes" not in dumped and "trigger" not in dumped
    cancelled = failing_run(python_workspace, tmp_path, "task_cancelled")
    application.cancel_run(python_workspace, cancelled, "reviewer")
    with pytest.raises(Exception, match="no retrospective yet"):
        application.list_recommendations(python_workspace, cancelled)


def test_outcomes_and_rule_health(python_workspace: Path, tmp_path: Path) -> None:
    application = HarnessApplication()
    excepted = first_run(python_workspace, tmp_path)
    grant(python_workspace, excepted)
    with pytest.raises(PolicyViolationError):
        application.record_outcome(
            python_workspace,
            execution_id=excepted,
            kind="incident",
            summary="x",
            actor_id="agent.claude",
        )
    with pytest.raises(ConfigurationError):
        application.record_outcome(
            python_workspace, execution_id=excepted, kind="outage", summary="x", actor_id="you"
        )
    outcome = application.record_outcome(
        python_workspace,
        execution_id="latest",
        kind="incident",
        summary="Credential leaked to the logs",
        reference="INC-42",
        observed_at="2026-10-01T10:00:00+00:00",
        actor_id="oncall",
    )
    assert outcome.execution_id == excepted and outcome.kind == "INCIDENT"
    assert [item.outcome_id for item in application.list_outcomes(python_workspace)] == [
        outcome.outcome_id
    ]
    health = application.rule_health(python_workspace)
    assert health["runs"] == 1 and health["outcomes"]["INCIDENT"] == 1
    [secret] = [item for item in health["rules"] if item["ruleId"] == "review.possible-secret"]
    assert secret["fired"] >= 1 and secret["blockedGates"] >= 1
    assert secret["excepted"] == 1 and secret["runsWithLaterOutcomes"] == 1
    assert secret["signal"] == "often excepted when it blocks: check its precision"
    assert any(item["validatorId"] == "python.pytest" for item in health["validators"])
    runner = CliRunner()
    path = ["--path", str(python_workspace)]
    text = runner.invoke(app, ["--no-json", "rules", "health", *path])
    assert "review.possible-secret" in text.stdout and "RULE" in text.stdout
    listed = runner.invoke(app, ["outcome", "list", "--run", excepted, *path])
    assert json.loads(listed.stdout)[0]["reference"] == "INC-42"
    recorded = runner.invoke(
        app,
        [
            "outcome",
            "record",
            "--run",
            excepted,
            "--kind",
            "REVERT",
            "--summary",
            "Reverted",
            "--actor",
            "agent.x",
            *path,
        ],
    )
    assert recorded.exit_code == 5
