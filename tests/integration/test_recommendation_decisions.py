from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind, RecommendationDecision, ResultStatus
from governed_harness.domain.models import AgentInvocation

TASK = (
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
    "        def test_at_threshold_{n}() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)


def closed_run(application: HarnessApplication, workspace: Path, tmp_path: Path, n: int) -> str:
    task_path = tmp_path / f"task-{n}.yaml"
    task_path.write_text(TASK.replace("{n}", str(n)), encoding="utf-8")
    task = application.create_task(workspace, task_path)
    pending = application.start_run(workspace, task.task_id)
    assert pending.change_set_digest
    _, final = application.decide_gate(
        workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=pending.change_set_digest,
        actor_id="human.reviewer",
        rationale="Validators passed",
    )
    assert final.status is ResultStatus.PASSED
    return final.execution_id


def context_records(
    application: HarnessApplication, workspace: Path, execution_id: str
) -> dict[str, dict[str, object]]:
    with application._services(workspace) as services:
        invocation = services.state.list(
            "agent_invocation", AgentInvocation, execution_id=execution_id
        )[-1]
        assert invocation.context_manifest_ref
        manifest = json.loads(services.artifacts.get(invocation.context_manifest_ref))
    return {item["key"]: item for item in manifest["records"]}


def test_accepted_recommendation_becomes_context_of_the_next_run(
    python_workspace: Path, tmp_path: Path
) -> None:
    """RD-11: a recommendation changes later runs only after a person decides on it."""
    application = HarnessApplication()
    first = closed_run(application, python_workspace, tmp_path, 1)
    listing = application.list_recommendations(python_workspace, first)
    assert [item["decision"] for item in listing] == [None]
    recommendation = listing[0]["recommendation"]

    second = closed_run(application, python_workspace, tmp_path, 2)
    assert context_records(application, python_workspace, second) == {}

    record = application.decide_recommendation(
        python_workspace,
        execution_id=first,
        recommendation_id=recommendation["recommendationId"],
        decision=RecommendationDecision.ACCEPT,
        actor_id="human.lead",
        rationale="Two runs without findings; keep the controls as they are.",
    )
    assert record.approved is True
    assert record.provenance.actor.actor_id == "human.lead"
    assert record.value["decision"] == "ACCEPT"
    assert record.value["statement"] == recommendation["statement"]
    listed = application.list_recommendations(python_workspace, first)
    assert listed[0]["decision"] == "ACCEPT"
    assert listed[0]["memoryId"] == record.memory_id

    third = closed_run(application, python_workspace, tmp_path, 3)
    records = context_records(application, python_workspace, third)
    assert list(records) == [record.key]
    assert records[record.key]["value"]["decisionRationale"].startswith("Two runs")  # type: ignore[index]


def test_edited_and_rejected_recommendations(python_workspace: Path, tmp_path: Path) -> None:
    application = HarnessApplication()
    first = closed_run(application, python_workspace, tmp_path, 1)
    second = closed_run(application, python_workspace, tmp_path, 2)
    edit_id = application.list_recommendations(python_workspace, first)[0]["recommendation"][
        "recommendationId"
    ]
    reject_id = application.list_recommendations(python_workspace, second)[0]["recommendation"][
        "recommendationId"
    ]
    edited = application.decide_recommendation(
        python_workspace,
        execution_id=first,
        recommendation_id=edit_id,
        decision=RecommendationDecision.EDIT,
        actor_id="human.lead",
        rationale="Narrow it to the pricing module.",
        statement="Keep the current controls for the pricing module.",
    )
    assert edited.approved is True
    assert edited.value["statement"] == "Keep the current controls for the pricing module."
    assert edited.value["originalStatement"]
    rejected = application.decide_recommendation(
        python_workspace,
        execution_id=second,
        recommendation_id=reject_id,
        decision=RecommendationDecision.REJECT,
        actor_id="human.lead",
        rationale="Not informative for this project.",
    )
    assert rejected.approved is False
    assert rejected.valid_until is not None

    third = closed_run(application, python_workspace, tmp_path, 3)
    assert list(context_records(application, python_workspace, third)) == [edited.key]
    statuses = {
        item["record"]["memoryId"]: item["status"]
        for item in application.list_memory(python_workspace)
    }
    assert statuses == {edited.memory_id: "active", rejected.memory_id: "expired"}


def test_recommendation_decide_fails_closed(python_workspace: Path, tmp_path: Path) -> None:
    application = HarnessApplication()
    run = closed_run(application, python_workspace, tmp_path, 1)
    recommendation = application.list_recommendations(python_workspace, run)[0]["recommendation"][
        "recommendationId"
    ]
    runner = CliRunner()
    base = ["recommendation", "decide", "--run", run, "--path", str(python_workspace)]
    unknown = runner.invoke(
        app,
        [*base, "--recommendation", "recommendation_x", "--decision", "accept", "--rationale", "r"],
    )
    assert unknown.exit_code == 3
    edit_without_text = runner.invoke(
        app, [*base, "--recommendation", recommendation, "--decision", "edit", "--rationale", "r"]
    )
    assert edit_without_text.exit_code == 2
    accept_with_text = runner.invoke(
        app,
        [
            *base,
            "--recommendation",
            recommendation,
            "--decision",
            "accept",
            "--rationale",
            "r",
            "--statement",
            "s",
        ],
    )
    assert accept_with_text.exit_code == 2
    accepted = runner.invoke(
        app,
        [
            *base,
            "--recommendation",
            recommendation,
            "--decision",
            "accept",
            "--rationale",
            "Keep it",
            "--actor",
            "human.lead",
        ],
    )
    assert accepted.exit_code == 0
    assert json.loads(accepted.stdout)["level"] == "RETROSPECTIVE"
    twice = runner.invoke(
        app, [*base, "--recommendation", recommendation, "--decision", "reject", "--rationale", "r"]
    )
    assert twice.exit_code == 5
    listed = runner.invoke(
        app, ["recommendation", "list", "--run", run, "--path", str(python_workspace)]
    )
    assert listed.exit_code == 0
    assert json.loads(listed.stdout)[0]["decision"] == "ACCEPT"


def test_run_without_retrospective_has_no_recommendations(
    python_workspace: Path, tmp_path: Path
) -> None:
    application = HarnessApplication()
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK.replace("{n}", "1"), encoding="utf-8")
    task = application.create_task(python_workspace, task_path)
    pending = application.start_run(python_workspace, task.task_id)
    result = CliRunner().invoke(
        app,
        ["recommendation", "list", "--run", pending.execution_id, "--path", str(python_workspace)],
    )
    assert result.exit_code == 3
