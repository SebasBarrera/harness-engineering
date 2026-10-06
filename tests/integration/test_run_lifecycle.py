"""The run lifecycle around the waits for a person (wave 9): the exit code of a decision
recorded without continuing and a rejected run on ``run continue`` (#83), a run whose
corrections are spent by an agent that repeats its change (#77) and ``run continue`` after a
change made outside the run (#78)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from tests.integration import test_decomposition as decomposition
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


# ----- #77: a repeated change after the correction budget ----------------------------------------
REPEATING_AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
for item in request["task"]["requirements"]:
    number = item["requirement_id"][1:]
    Path(f"src/sample/f{number}.py").write_text(f"def f{number}() -> int:\\n    return 0\\n")
    Path(f"tests/test_req_{number}.py").write_text(
        f"from sample.f{number} import f{number}\\n\\n"
        f"def test_r{number}() -> None:\\n    assert f{number}() == {number}\\n"
    )
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def repeating(workspace: Path, tmp_path: Path, model: str) -> None:
    """An agent that writes the same wrong change on every attempt, under adaptive granularity
    with a threshold the task stays under, one correction and reproduce-first."""
    decomposition.configure(
        workspace,
        tmp_path,
        "repeat",
        planning={"decomposition": "agent", "threshold": 10, "granularity": "adaptive"},
    )
    (workspace / "agent.py").write_text(REPEATING_AGENT, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProviders"]["fixture_agent"]["model"] = model
    config["runtime"].update({"verificationCorrections": 1, "reproduceFirst": True})
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_a_repeated_change_of_another_model_stops_with_a_reason(
    python_workspace: Path, tmp_path: Path
) -> None:
    repeating(python_workspace, tmp_path, "small-model")
    application, run = decomposition.start(python_workspace, tmp_path)
    execution = application.status(python_workspace, run)["execution"]
    assert execution["currentPhase"] == PhaseId.VERIFICATION
    assert execution["status"] == ResultStatus.FAILED
    assert "after 1 correction cycle(s)" in execution["terminalReason"]
    assert "agent.empty-correction" in execution["terminalReason"]
    assert not decomposition.events(application, python_workspace, run, "planning.split-on-failure")


def test_a_coarse_model_still_returns_to_planning(python_workspace: Path, tmp_path: Path) -> None:
    repeating(python_workspace, tmp_path, "claude-sonnet-5-5")
    application, run = decomposition.start(python_workspace, tmp_path)
    assert decomposition.events(application, python_workspace, run, "planning.split-on-failure")
    execution = application.status(python_workspace, run)["execution"]
    assert execution["currentPhase"] == PhaseId.PLANNING
    assert execution["terminalReason"] is None


# ----- #78: run continue after an out-of-band change ---------------------------------------------
def edited_after_the_gate(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str, str]:
    """A run waiting in DECISION on a passed gate, then a manual edit of an owned file."""
    application = HarnessApplication()
    run = start(application, workspace, tmp_path, patch_task(GOOD))
    evaluated = application.status(workspace, run)["execution"]["changeSetDigest"]
    with (workspace / "src" / "sample" / "pricing.py").open("a", encoding="utf-8") as handle:
        handle.write("# adjusted after review\n")
    return application, run, evaluated


def test_run_continue_verifies_an_out_of_band_change_again(
    python_workspace: Path, tmp_path: Path
) -> None:
    set_keys(python_workspace, verification={"reverifyOnChange": True})
    application, run, evaluated = edited_after_the_gate(python_workspace, tmp_path)
    code, output = cli("--json", "run", "continue", "--path", str(python_workspace), "--run", run)
    assert code == 4, output
    status = application.status(python_workspace, run)
    assert status["gate"]["status"] == "PASSED"
    current = status["execution"]["changeSetDigest"]
    assert current != evaluated
    assert status["gate"]["changeSetDigest"] == current
    [authorized] = events(application, python_workspace, run, "verification.reverify.authorized")
    assert authorized.payload["paths"] == ["src/sample/pricing.py"]
    assert authorized.payload["previousDigest"] == evaluated
    with application._services(python_workspace) as services:
        evidence = json.loads(services.artifacts.get(authorized.payload["evidenceRef"]))
    assert evidence["currentDigest"] == current
    _record, execution = application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=current,
        actor_id=ACTOR,
        rationale="Verified again after the edit",
    )
    assert execution.status is ResultStatus.PASSED


def test_without_the_key_the_changed_run_stays_inconclusive(
    python_workspace: Path, tmp_path: Path
) -> None:
    application, run, _evaluated = edited_after_the_gate(python_workspace, tmp_path)
    application.continue_run(python_workspace, run)
    status = application.status(python_workspace, run)
    assert status["gate"]["status"] == "INCONCLUSIVE"
    assert not events(application, python_workspace, run, "verification.reverify.authorized")
