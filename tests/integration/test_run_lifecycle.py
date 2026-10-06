"""The run lifecycle around the waits for a person (wave 9): the exit code of a decision
recorded without continuing and a rejected run on ``run continue`` (#83)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from tests.integration.test_friction import configure, risky_task
from tests.integration.test_friction import start as start_task
from tests.integration.test_stop_line_and_contract import (
    GOOD,
    ORIGINAL,
    events,
    patch_task,
    set_keys,
    start,
)

ACTOR = "human.reviewer"


def cli(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args))
    return result.exit_code, result.output


# ----- #83: plan decide --no-continue ------------------------------------------------------------
def test_plan_decide_without_continuing_exits_0(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace)
    application, run = start_task(python_workspace, tmp_path, risky_task("task_plan_cli"))
    digest = application.plan(python_workspace, run.execution_id)["approval"]["digest"]
    code, output = cli(
        "plan",
        "decide",
        "--path",
        str(python_workspace),
        "--run",
        run.execution_id,
        "--decision",
        "APPROVE",
        "--digest",
        digest,
        "--rationale",
        "The plan touches only pricing",
        "--actor",
        ACTOR,
        "--no-continue",
    )
    assert code == 0, output
    shown = application.plan(python_workspace, run.execution_id)
    assert shown["approval"]["status"] == "APPROVED"
    resumed = application.continue_run(python_workspace, run.execution_id)
    assert resumed.current_phase.value == "DECISION"


def test_plan_decide_reject_without_continuing_ends_the_run(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    application, run = start_task(python_workspace, tmp_path, risky_task("task_plan_reject"))
    digest = application.plan(python_workspace, run.execution_id)["approval"]["digest"]
    code, output = cli(
        "plan",
        "decide",
        "--path",
        str(python_workspace),
        "--run",
        run.execution_id,
        "--decision",
        "REJECT",
        "--digest",
        digest,
        "--rationale",
        "Not now",
        "--actor",
        ACTOR,
        "--no-continue",
    )
    assert code == 6, output


# ----- #83: run continue after REJECT ------------------------------------------------------------
def test_run_continue_reports_a_rejected_run(python_workspace: Path, tmp_path: Path) -> None:
    set_keys(python_workspace, governance={"stopTheLine": "restore"})
    application = HarnessApplication()
    run = start(application, python_workspace, tmp_path, patch_task(GOOD))
    before = application.status(python_workspace, run)["execution"]
    code, output = cli(
        "gate",
        "decide",
        "--path",
        str(python_workspace),
        "--run",
        run,
        "--decision",
        "REJECT",
        "--change-set-digest",
        before["changeSetDigest"],
        "--rationale",
        "Not now",
        "--actor",
        ACTOR,
        "--no-continue",
    )
    assert code == 6, output
    assert (python_workspace / "src" / "sample" / "pricing.py").read_text() == ORIGINAL
    phases = len(events(application, python_workspace, run, "phase.started"))
    code, output = cli("--json", "run", "continue", "--path", str(python_workspace), "--run", run)
    assert code == 6, output
    reported = json.loads(output)
    assert reported["status"] == "FAILED"
    assert reported["terminalReason"] == "Rejected by human decision"
    assert reported["gateEvaluationId"] == before["gateEvaluationId"]
    assert len(events(application, python_workspace, run, "phase.started")) == phases
