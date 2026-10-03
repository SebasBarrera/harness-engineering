from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.domain.models import ClarificationRecord, Evidence
from governed_harness.orchestration.engine import EngineServices

VAGUE_TASK = (
    "taskId: task_vague\n"
    "title: Discount\n"
    "intent: Add a discount.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: It works.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)

ANSWERS = (
    "answers:\n"
    "  Q-1: apply_discount(100, 100, 0.1) returns 90.\n"
    "  Q-2: Only the threshold rule; rounding is out of scope.\n"
)


def invoke(workspace: Path, *args: str) -> tuple[int, Any]:
    result = CliRunner().invoke(app, [*args, "--path", str(workspace)])
    try:
        return result.exit_code, json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def set_policy(workspace: Path, policy: str | None) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    if policy is None:
        value.pop("intake", None)
    else:
        value["intake"] = {"criteriaPolicy": policy}
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def create_vague_task(workspace: Path, tmp_path: Path) -> str:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(VAGUE_TASK, encoding="utf-8")
    code, task = invoke(workspace, "task", "create", "--file", str(task_file))
    assert code == 0
    return str(task["taskId"])


def write_answers(tmp_path: Path, content: str = ANSWERS, name: str = "answers.yaml") -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def event_types(workspace: Path, run_id: str) -> list[str]:
    services = EngineServices.open(ConfigurationResolver().resolve(workspace))
    try:
        return [event.event_type for event in services.events.list(run_id)]
    finally:
        services.close()


def blocked_run(workspace: Path, tmp_path: Path) -> tuple[str, str]:
    task_id = create_vague_task(workspace, tmp_path)
    code, run = invoke(workspace, "run", "start", "--task", task_id)
    assert code == 6
    assert run["status"] == "BLOCKED"
    assert run["currentPhase"] == "INTENT"
    return task_id, str(run["executionId"])


def test_enforce_blocks_intent_then_clarify_and_continue_proceed(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_id, run_id = blocked_run(python_workspace, tmp_path)

    code, status = invoke(python_workspace, "status", "--run", run_id)
    assert code == 0
    (intent,) = status["phases"]
    assert intent["phaseId"] == "INTENT"
    assert intent["status"] == "BLOCKED"
    assert intent["summary"] == "Intent needs clarification: 2 question(s)"
    assert status["findings"]["total"] == 0

    code, questions = invoke(python_workspace, "task", "questions", "--task", task_id)
    assert code == 0
    open_request = questions["openRequest"]
    assert open_request["executionId"] == run_id
    assert open_request["policy"] == "enforce"
    assert [(q["questionId"], q["ruleId"], q["target"]) for q in open_request["questions"]] == [
        ("Q-1", "C1", "AC-1"),
        ("Q-2", "T1", "task"),
    ]

    code, clarified = invoke(
        python_workspace,
        "task",
        "clarify",
        "--task",
        task_id,
        "--file",
        write_answers(tmp_path),
        "--actor",
        "human.author",
    )
    assert code == 0
    record = clarified["clarification"]
    assert record["actor"] == {
        "actorType": "HUMAN",
        "actorId": "human.author",
        "displayName": None,
        "version": None,
    }
    assert record["requestId"] == open_request["requestId"]
    assert record["previousTaskDigest"] == open_request["taskDigest"]
    assert record["taskDigest"] != record["previousTaskDigest"]
    assert [(a["questionId"], a["ruleId"]) for a in record["answers"]] == [
        ("Q-1", "C1"),
        ("Q-2", "T1"),
    ]
    assert record["answers"][0]["question"].startswith("Criterion AC-1 ('It works.')")
    revised = clarified["task"]
    assert revised["acceptanceCriteria"][0]["verificationHint"] == (
        "apply_discount(100, 100, 0.1) returns 90."
    )
    assert revised["requirements"][0]["source"] == "clarification"
    assert clarified["next"] == f"harness run continue --run {run_id}"

    code, shown = invoke(python_workspace, "task", "show", "--task", task_id)
    assert code == 0
    assert shown == revised

    code, questions = invoke(python_workspace, "task", "questions", "--task", task_id)
    assert questions["openRequest"] is None
    assert questions["taskDigest"] == record["taskDigest"]
    assert [item["clarificationId"] for item in questions["clarifications"]] == [
        record["clarificationId"]
    ]

    code, continued = invoke(python_workspace, "run", "continue", "--run", run_id)
    assert code == 4
    assert continued["currentPhase"] == "DECISION"

    code, status = invoke(python_workspace, "status", "--run", run_id)
    intents = [phase for phase in status["phases"] if phase["phaseId"] == "INTENT"]
    assert [(phase["attempt"], phase["status"]) for phase in intents] == [
        (1, "BLOCKED"),
        (2, "PASSED"),
    ]
    assert status["gate"]["status"] == "PASSED"
    assert status["eventChainValid"] is True

    types = event_types(python_workspace, run_id)
    requested = types.index("intent.clarification.requested")
    clarified_at = types.index("intent.clarified")
    assert requested < clarified_at
    # INTENT runs again after the answers, and nothing more is asked.
    assert types[clarified_at:].count("phase.started") >= 2
    assert types.count("intent.clarification.requested") == 1

    # The run is past INTENT now: the task can no longer be clarified.
    code, _ = invoke(
        python_workspace,
        "task",
        "clarify",
        "--task",
        task_id,
        "--file",
        write_answers(tmp_path, "answers:\n  Q-1: Again.\n", "again.yaml"),
    )
    assert code == 5


def test_clarification_trace_links_question_answer_and_revision(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_id, run_id = blocked_run(python_workspace, tmp_path)
    application = HarnessApplication()
    result = application.clarify_task(
        python_workspace,
        task_id=task_id,
        answers_file=Path(write_answers(tmp_path)),
        actor_id="human.author",
    )
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        (record,) = services.state.list("clarification", ClarificationRecord, execution_id=run_id)
        assert record.clarification_id == result["clarification"]["clarificationId"]
        previous = json.loads(services.artifacts.get(record.previous_task_ref))
        revised = json.loads(services.artifacts.get(record.task_ref))
        assert previous["acceptance_criteria"][0]["verification_hint"] is None
        assert revised["acceptance_criteria"][0]["verification_hint"]
        evidence = services.state.list("evidence", Evidence, execution_id=run_id)
        summaries = [item.summary for item in evidence]
        assert "Clarification request: 2 question(s)" in summaries
        assert "Clarification: 2 answer(s), task revised" in summaries
        clarified = [
            event
            for event in services.events.list(run_id)
            if event.event_type == "intent.clarified"
        ]
        assert clarified[0].actor["actorType"] == "HUMAN"
        assert clarified[0].payload["task_digest"] == record.task_digest
        assert services.events.verify_chain(run_id)
    finally:
        services.close()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("answers:\n  Q-7: Something.\n", "unknown question id"),
        ("answers:\n  Q-1: '  '\n", "empty answer"),
        ("answers:\n  Q-1:\n", "empty answer"),
        ("answers: {}\n", "answers"),
        ("answers:\n  Q-1: x\nremoveCriteria: [AC-1]\n", "unknown answers-file field"),
        (
            "answers:\n  Q-1: x\nreplaceCriteria:\n  - text: no id\n",
            "requires the criterionId",
        ),
    ],
)
def test_clarify_rejects_invalid_answers_files(
    python_workspace: Path, tmp_path: Path, content: str, message: str
) -> None:
    task_id, _ = blocked_run(python_workspace, tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "task",
            "clarify",
            "--task",
            task_id,
            "--file",
            write_answers(tmp_path, content),
            "--path",
            str(python_workspace),
        ],
    )
    assert result.exit_code == 2
    assert message in result.output


@pytest.mark.parametrize("actor", ["agent.simulated", "validator.python.pytest", "harness.core"])
def test_clarify_rejects_actor_ids_of_non_human_actors(
    python_workspace: Path, tmp_path: Path, actor: str
) -> None:
    task_id, _ = blocked_run(python_workspace, tmp_path)
    code, _ = invoke(
        python_workspace,
        "task",
        "clarify",
        "--task",
        task_id,
        "--file",
        write_answers(tmp_path),
        "--actor",
        actor,
    )
    assert code == 5


def test_clarify_rejects_a_non_human_actor_type(python_workspace: Path, tmp_path: Path) -> None:
    task_id, _ = blocked_run(python_workspace, tmp_path)
    with pytest.raises(PolicyViolationError, match="human"):
        HarnessApplication().clarify_task(
            python_workspace,
            task_id=task_id,
            answers_file=Path(write_answers(tmp_path)),
            actor_id="ci.pipeline",
            actor_type=ActorType.CI,
        )


def test_clarify_without_an_open_request_is_not_found(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_id = create_vague_task(python_workspace, tmp_path)
    code, _ = invoke(
        python_workspace, "task", "clarify", "--task", task_id, "--file", write_answers(tmp_path)
    )
    assert code == 3
    code, _ = invoke(
        python_workspace,
        "task",
        "clarify",
        "--task",
        "task_missing",
        "--file",
        write_answers(tmp_path),
    )
    assert code == 3


def test_partial_answers_leave_the_remaining_question_open(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_id, run_id = blocked_run(python_workspace, tmp_path)
    code, _ = invoke(
        python_workspace,
        "task",
        "clarify",
        "--task",
        task_id,
        "--file",
        write_answers(tmp_path, "answers:\n  Q-2: Only the threshold rule.\n"),
    )
    assert code == 0
    code, run = invoke(python_workspace, "run", "continue", "--run", run_id)
    assert code == 6
    assert run["currentPhase"] == "INTENT"
    code, questions = invoke(python_workspace, "task", "questions", "--task", task_id)
    assert [(q["questionId"], q["ruleId"]) for q in questions["openRequest"]["questions"]] == [
        ("Q-1", "C1")
    ]


def test_a_precise_answer_to_the_criterion_also_settles_the_scope(
    python_workspace: Path, tmp_path: Path
) -> None:
    """T1 only questions a single criterion without an anchor: once the answer gives the
    criterion an observable result, the reassessed task raises no question."""
    task_id, run_id = blocked_run(python_workspace, tmp_path)
    code, _ = invoke(
        python_workspace,
        "task",
        "clarify",
        "--task",
        task_id,
        "--file",
        write_answers(tmp_path, "answers:\n  Q-1: apply_discount(100, 100, 0.1) returns 90.\n"),
    )
    assert code == 0
    code, run = invoke(python_workspace, "run", "continue", "--run", run_id)
    assert code == 4
    assert run["currentPhase"] == "DECISION"


@pytest.mark.parametrize("policy", ["warn", None])
def test_warn_records_questions_and_lets_the_run_continue(
    python_workspace: Path, tmp_path: Path, policy: str | None
) -> None:
    """``None`` is a project.yaml written by 1.0.0, without the intake section."""
    set_policy(python_workspace, policy)
    task_id = create_vague_task(python_workspace, tmp_path)
    code, run = invoke(python_workspace, "run", "start", "--task", task_id)
    assert code == 4
    run_id = run["executionId"]
    code, status = invoke(python_workspace, "status", "--run", run_id)
    intent = status["phases"][0]
    assert intent["status"] == "PASSED"
    assert intent["summary"] == (
        "Intent is structured and identifiable; 2 clarification question(s) recorded as warnings"
    )
    assert status["gate"]["status"] == "PASSED"
    application = HarnessApplication()
    findings = [
        item
        for item in application.list_findings(python_workspace, run_id)
        if item.validator_id == "intake.clarification"
    ]
    assert [(item.rule_id, item.severity.value, item.validator_id) for item in findings] == [
        ("C1", "LOW", "intake.clarification"),
        ("T1", "LOW", "intake.clarification"),
    ]
    code, questions = invoke(python_workspace, "task", "questions", "--task", task_id)
    assert questions["openRequest"]["policy"] == "warn"
    assert "intent.clarification.requested" in event_types(python_workspace, run_id)


def test_off_skips_the_assessment(python_workspace: Path, tmp_path: Path) -> None:
    set_policy(python_workspace, "off")
    task_id = create_vague_task(python_workspace, tmp_path)
    code, run = invoke(python_workspace, "run", "start", "--task", task_id)
    assert code == 4
    run_id = run["executionId"]
    code, status = invoke(python_workspace, "status", "--run", run_id)
    assert status["phases"][0]["summary"] == "Intent is structured and identifiable"
    assert not [
        item
        for item in HarnessApplication().list_findings(python_workspace, run_id)
        if item.validator_id == "intake.clarification"
    ]
    code, questions = invoke(python_workspace, "task", "questions", "--task", task_id)
    assert questions["openRequest"] is None
    assert questions["requests"] == []
    types = event_types(python_workspace, run_id)
    assert "intent.clarification.requested" not in types
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        intent_evidence = [
            item
            for item in services.state.list("evidence", Evidence, execution_id=run_id)
            if item.phase_id is PhaseId.INTENT
        ]
        assert [item.summary for item in intent_evidence] == [
            "Structured intent and acceptance criteria"
        ]
    finally:
        services.close()


def test_a_clear_task_is_unaffected_by_enforce(python_workspace: Path, tmp_path: Path) -> None:
    task_file = tmp_path / "clear.yaml"
    task_file.write_text(
        VAGUE_TASK.replace(
            "intent: Add a discount.\n",
            "intent: Add a discount.\nrequirements:\n  - Discount at the threshold.\n",
        ).replace("text: It works.", "text: apply_discount(100, 100, 0.1) returns 90."),
        encoding="utf-8",
    )
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    run = application.start_run(python_workspace, task.task_id)
    assert run.status is ResultStatus.BLOCKED
    assert run.current_phase is PhaseId.DECISION
    assert application.list_clarifications(python_workspace, task.task_id)["requests"] == []
