"""From a one-sentence task to a run past INTENT: rule C0 elicits the criteria (#33)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.cli.main import app
from governed_harness.intake import C0_TARGETS

ONE_SENTENCE_TASK = (
    "taskId: task_one_sentence\n"
    "title: Discount\n"
    "intent: Add a discount to the order subtotal.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)

PARTIAL_ANSWERS = (
    "answers:\n"
    "  Q-2: apply_discount(subtotal, threshold, rate) takes and returns floats.\n"
    "  Q-6: Rounding and currencies.\n"
)

ANSWERS = (
    "answers:\n"
    "  Q-1: |\n"
    "    - apply_discount(100, 100, 0.1) returns 90.\n"
    "    - apply_discount(99, 100, 0.1) returns 99.\n"
    "  Q-3: The threshold is inclusive.\n"
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


def write(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def create_one_sentence_task(workspace: Path, tmp_path: Path) -> dict[str, Any]:
    code, task = invoke(
        workspace, "task", "create", "--file", write(tmp_path, "task.yaml", ONE_SENTENCE_TASK)
    )
    assert code == 0
    assert isinstance(task, dict)
    return task


def open_questions(workspace: Path, task_id: str) -> list[tuple[str, str, str]]:
    code, listing = invoke(workspace, "task", "questions", "--task", task_id)
    assert code == 0
    return [
        (item["questionId"], item["ruleId"], item["target"])
        for item in listing["openRequest"]["questions"]
    ]


C0_QUESTIONS = [(f"Q-{index}", "C0", target) for index, target in enumerate(C0_TARGETS, start=1)]


def clarify(workspace: Path, task_id: str, answers_file: str) -> dict[str, Any]:
    code, result = invoke(
        workspace,
        "task",
        "clarify",
        "--task",
        task_id,
        "--file",
        answers_file,
        "--actor",
        "human.author",
    )
    assert code == 0, result
    assert isinstance(result, dict)
    return result


def test_a_one_sentence_task_gets_its_criteria_elicited_in_intent(
    python_workspace: Path, tmp_path: Path
) -> None:
    task = create_one_sentence_task(python_workspace, tmp_path)
    assert task["acceptanceCriteria"] == []
    assert task["criteriaPending"] is True
    task_id = task["taskId"]

    code, run = invoke(python_workspace, "run", "start", "--task", task_id)
    assert code == 6
    assert (run["status"], run["currentPhase"]) == ("BLOCKED", "INTENT")
    run_id = run["executionId"]
    assert open_questions(python_workspace, task_id) == C0_QUESTIONS
    code, status = invoke(python_workspace, "status", "--run", run_id)
    assert status["phases"][0]["summary"] == "Intent needs clarification: 7 question(s)"

    # Answers that give no criterion keep the task pending: INTENT asks the C0 questions again.
    clarified = clarify(python_workspace, task_id, write(tmp_path, "partial.yaml", PARTIAL_ANSWERS))
    assert clarified["task"]["acceptanceCriteria"] == []
    assert clarified["task"]["criteriaPending"] is True
    assert clarified["task"]["constraints"] == ["Out of scope: Rounding and currencies."]
    code, run = invoke(python_workspace, "run", "continue", "--run", run_id)
    assert code == 6
    assert run["currentPhase"] == "INTENT"
    assert open_questions(python_workspace, task_id) == C0_QUESTIONS

    clarified = clarify(python_workspace, task_id, write(tmp_path, "answers.yaml", ANSWERS))
    revised = clarified["task"]
    assert [item["text"] for item in revised["acceptanceCriteria"]] == [
        "apply_discount(100, 100, 0.1) returns 90.",
        "apply_discount(99, 100, 0.1) returns 99.",
    ]
    assert "criteriaPending" not in revised
    assert clarified["clarification"]["addedCriteria"] == [
        item["criterionId"] for item in revised["acceptanceCriteria"]
    ]
    assert [
        (a["questionId"], a["ruleId"], a["target"]) for a in clarified["clarification"]["answers"]
    ] == [
        ("Q-1", "C0", "task:results"),
        ("Q-3", "C0", "task:limits"),
    ]

    code, run = invoke(python_workspace, "run", "continue", "--run", run_id)
    assert code == 4
    assert run["currentPhase"] == "DECISION"
    code, status = invoke(python_workspace, "status", "--run", run_id)
    intents = [phase for phase in status["phases"] if phase["phaseId"] == "INTENT"]
    assert [(phase["attempt"], phase["status"]) for phase in intents] == [
        (1, "BLOCKED"),
        (2, "BLOCKED"),
        (3, "PASSED"),
    ]
    assert status["gate"]["status"] == "PASSED"
    assert status["eventChainValid"] is True


@pytest.mark.parametrize("policy", ["warn", "off", None])
def test_other_policies_refuse_a_task_without_criteria(
    python_workspace: Path, tmp_path: Path, policy: str | None
) -> None:
    set_policy(python_workspace, policy)
    result = CliRunner().invoke(
        app,
        [
            "task",
            "create",
            "--file",
            write(tmp_path, "task.yaml", ONE_SENTENCE_TASK),
            "--path",
            str(python_workspace),
        ],
    )
    assert result.exit_code == 2
    assert "at least one acceptance criterion is required" in result.output


@pytest.mark.parametrize("policy", ["warn", "off", None])
def test_a_pending_task_never_passes_intent_after_the_policy_changes(
    python_workspace: Path, tmp_path: Path, policy: str | None
) -> None:
    task_id = create_one_sentence_task(python_workspace, tmp_path)["taskId"]
    set_policy(python_workspace, policy)
    code, run = invoke(python_workspace, "run", "start", "--task", task_id)
    assert code == 6
    assert (run["status"], run["currentPhase"]) == ("BLOCKED", "INTENT")
    code, listing = invoke(python_workspace, "task", "questions", "--task", task_id)
    assert listing["openRequest"]["policy"] == "enforce"
    assert open_questions(python_workspace, task_id) == C0_QUESTIONS
