"""Every wait for a person before DECISION is in the inbox (#73): the plan-approval checkpoint,
a proposed decomposition, proposed acceptance tests, the architecture (inferred rules and options
of a new project), the operational contract to confirm and, on a decision, the risk factors an
APPROVE must acknowledge. Each entry has its kind, the digest the answer binds to and the
command that answers it; batch decisions keep working."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from tests.integration import test_acceptance_tests as acceptance
from tests.integration import test_decomposition as decomposition
from tests.integration import test_engineering_flow as engineering
from tests.integration import test_friction as friction
from tests.integration import test_intake_contract as contract
from tests.integration import test_verification_checks as checks

ACTOR = "human.reviewer"


def entries(workspace: Path, kind: str) -> list[dict[str, Any]]:
    return [item for item in HarnessApplication().inbox(workspace) if item["kind"] == kind]


def text_inbox(workspace: Path) -> str:
    result = CliRunner().invoke(app, ["--no-json", "inbox", "--path", str(workspace)])
    assert result.exit_code == 0, result.output
    return result.output


def json_inbox(workspace: Path) -> list[dict[str, Any]]:
    result = CliRunner().invoke(app, ["--json", "inbox", "--path", str(workspace)])
    assert result.exit_code == 0, result.output
    value: list[dict[str, Any]] = json.loads(result.output)
    return value


def test_a_plan_waiting_for_approval_is_in_the_inbox(
    python_workspace: Path, tmp_path: Path
) -> None:
    friction.configure(python_workspace)
    application, run = friction.start(python_workspace, tmp_path, friction.risky_task("task_p"))
    approval = application.plan(python_workspace, run.execution_id)["approval"]
    [entry] = [item for item in json_inbox(python_workspace) if item["kind"] == "plan"]
    assert entry["executionId"] == run.execution_id
    assert entry["digest"] == approval["digest"]
    assert entry["next"].startswith(f"harness plan decide --run {run.execution_id}")
    assert approval["digest"] in entry["next"]
    assert "risk flag(s) security" in entry["summary"]
    assert "plan approval" in text_inbox(python_workspace)


def test_a_proposed_decomposition_is_in_the_inbox(python_workspace: Path, tmp_path: Path) -> None:
    decomposition.configure(python_workspace, tmp_path, "good")
    application, run = decomposition.start(python_workspace, tmp_path)
    plan = application.plan(python_workspace, run)
    [entry] = entries(python_workspace, "decomposition")
    assert entry["digest"] == plan["digest"]
    assert entry["summary"] == "2 sub-task(s) proposed"
    assert f"--digest {plan['digest']}" in entry["next"]


def test_proposed_acceptance_tests_are_in_the_inbox(python_workspace: Path, tmp_path: Path) -> None:
    acceptance.configure(python_workspace, tmp_path, "good")
    application, run = acceptance.start(python_workspace, tmp_path)
    proposal = application.acceptance(python_workspace, run)
    [entry] = entries(python_workspace, "acceptance")
    assert entry["digest"] == proposal["digest"]
    assert entry["next"].startswith(f"harness acceptance decide --run {run}")
    # Once decided, the wait leaves the inbox.
    acceptance.approve(application, python_workspace, run)
    assert not entries(python_workspace, "acceptance")


def test_inferred_architecture_rules_are_in_the_inbox(
    python_workspace: Path, tmp_path: Path
) -> None:
    engineering.configure(
        python_workspace,
        tmp_path,
        {"src/sample/pricing.py": engineering.GOOD},
        {"architecture": {"mode": "agent"}},
    )
    task = engineering.create(python_workspace, tmp_path, engineering.OWNED)
    application = HarnessApplication()
    run = application.start_run(python_workspace, task)
    state = application.architecture(python_workspace)["state"]
    [entry] = entries(python_workspace, "architecture")
    assert entry["executionId"] == run.execution_id
    assert entry["digest"] == state["digest"]
    assert "--decision APPROVE" in entry["next"]


def test_architecture_options_of_a_new_project_are_in_the_inbox(tmp_path: Path) -> None:
    root = engineering._new_project(tmp_path / "new")
    engineering.configure(root, tmp_path, {}, {"architecture": {"mode": "agent"}})
    task = engineering.create(root, tmp_path, ["src/app/cart.py"], engineering.NEW_TASK)
    HarnessApplication().start_run(root, task)
    [entry] = entries(root, "architecture")
    assert entry["summary"] == "2 architecture option(s): hex, layered"
    assert "--option <id>" in entry["next"]


def test_a_contract_to_confirm_is_in_the_inbox(python_workspace: Path, tmp_path: Path) -> None:
    contract.configure(python_workspace, {"operationalContract": "enforce"})
    application = contract.create(python_workspace, tmp_path, contract.CLEAR)
    run = application.start_run(python_workspace, "task_clear")
    # Items not settled are asked as clarification questions first, not as a confirmation.
    assert not entries(python_workspace, "contract")
    answers = tmp_path / "contract.yaml"
    settled = {
        "verificationLevel": "L1",
        "branch": "feature/discount",
        "push": "no",
        "createPullRequest": "no",
        "comment": "yes",
        "coverageThreshold": 80,
        "scope": "src/sample/pricing.py",
    }
    answers.write_text(yaml.safe_dump({"contract": settled}), encoding="utf-8")
    application.clarify_task(
        python_workspace, task_id="task_clear", answers_file=answers, actor_id=ACTOR
    )
    application.continue_run(python_workspace, run.execution_id)
    digest = application.review(python_workspace, run.execution_id)["contract"]["digest"]
    [entry] = entries(python_workspace, "contract")
    assert entry["digest"] == digest
    assert entry["next"] == f"harness task confirm --task task_clear --digest {digest}"
    application.confirm_contract(
        python_workspace, task_id="task_clear", digest=digest, actor_id=ACTOR
    )
    assert not entries(python_workspace, "contract")


def test_risk_factors_to_acknowledge_are_on_the_decision_and_in_a_batch(
    python_workspace: Path, tmp_path: Path
) -> None:
    checks.configure(python_workspace, {"riskFactors": {"network": "acknowledge"}})
    code = "import urllib.request\n\n\n" + checks.GOOD
    application, run = checks.run_task(
        python_workspace,
        tmp_path,
        checks.task_yaml(
            [
                checks.patch("src/sample/pricing.py", code),
                checks.patch("tests/test_pricing.py", checks.TEST),
            ]
        ),
    )
    [entry] = entries(python_workspace, "decision")
    assert entry["acknowledgeRisks"] == ["network"]
    assert entry["digest"] == entry["changeSetDigest"]
    assert "acknowledge risk(s): network" in text_inbox(python_workspace)
    batch = tmp_path / "decisions.yaml"
    batch.write_text(
        yaml.safe_dump(
            [
                {
                    "run": run,
                    "decision": "APPROVE",
                    "changeSetDigest": entry["digest"],
                    "acknowledgeRisks": ["network"],
                }
            ]
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "inbox",
            "--path",
            str(python_workspace),
            "--decisions",
            str(batch),
            "--rationale",
            "The HTTP client is expected",
            "--actor",
            ACTOR,
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["recorded"] == 1
    assert application.status(python_workspace, run)["execution"]["status"] == "PASSED"
