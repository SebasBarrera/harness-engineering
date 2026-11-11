"""intake.language through the CLI: a Spanish casual task gets Spanish questions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.intake import QUESTION_TEMPLATES, QUESTION_TEMPLATES_ES

PATCH = (
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
SPANISH_TASK = (
    "taskId: task_es\n"
    "title: Descuento\n"
    "intent: Agrega un descuento.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: Funciona.\n"
    "  - criterionId: AC-2\n"
    "    text: Debe ser rápido.\n" + PATCH
)
ENGLISH_TASK = (
    "taskId: task_en\n"
    "title: Discount\n"
    "intent: Add a discount.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: It works.\n"
    "  - criterionId: AC-2\n"
    "    text: It must be fast.\n" + PATCH
)


def invoke(workspace: Path, *args: str) -> tuple[int, Any]:
    result = CliRunner().invoke(app, [*args, "--path", str(workspace)])
    try:
        return result.exit_code, json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def set_intake(workspace: Path, intake: dict[str, str] | None) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if intake is None:
        value.pop("intake", None)
    else:
        value["intake"] = intake
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def questions_of(workspace: Path, tmp_path: Path, content: str) -> list[dict[str, Any]]:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(content, encoding="utf-8")
    code, task = invoke(workspace, "task", "create", "--file", str(task_file))
    assert code == 0, task
    code, run = invoke(workspace, "run", "start", "--task", task["taskId"])
    assert code == 6, run
    code, questions = invoke(workspace, "task", "questions", "--task", task["taskId"])
    assert code == 0
    return list(questions["openRequest"]["questions"])


def test_a_spanish_casual_task_gets_spanish_questions_judged_like_english(
    python_workspace: Path, tmp_path: Path
) -> None:
    set_intake(python_workspace, {"criteriaPolicy": "enforce", "language": "auto"})
    spanish = questions_of(python_workspace, tmp_path, SPANISH_TASK)
    english = questions_of(python_workspace, tmp_path, ENGLISH_TASK)

    ids = [(q["questionId"], q["ruleId"], q["target"]) for q in english]
    assert ids == [("Q-1", "C1", "AC-1"), ("Q-2", "C2", "AC-2")]
    assert [(q["questionId"], q["ruleId"], q["target"]) for q in spanish] == ids
    assert spanish[0]["text"] == QUESTION_TEMPLATES_ES["C1"].format(target="AC-1", text="Funciona.")
    assert spanish[1]["text"] == QUESTION_TEMPLATES_ES["C2"].format(
        target="AC-2", text="Debe ser rápido.", term="rápido"
    )
    assert english[0]["text"] == QUESTION_TEMPLATES["C1"].format(target="AC-1", text="It works.")


def test_without_the_key_a_spanish_task_keeps_the_earlier_rules(
    python_workspace: Path, tmp_path: Path
) -> None:
    """The original tokenizer reads "rápido" as "r" and "pido": AC-2 has four words, so only
    the one-word criterion is questioned, in English."""
    set_intake(python_workspace, {"criteriaPolicy": "enforce"})
    spanish = questions_of(python_workspace, tmp_path, SPANISH_TASK)
    assert [(q["ruleId"], q["target"]) for q in spanish] == [("C1", "AC-1")]
    assert spanish[0]["text"].startswith("Criterion AC-1 ('Funciona.')")


def test_warn_findings_recommend_the_next_step_in_spanish(
    python_workspace: Path, tmp_path: Path
) -> None:
    set_intake(python_workspace, {"criteriaPolicy": "warn", "language": "es"})
    task_file = tmp_path / "task.yaml"
    task_file.write_text(SPANISH_TASK, encoding="utf-8")
    code, task = invoke(python_workspace, "task", "create", "--file", str(task_file))
    assert code == 0
    code, run = invoke(python_workspace, "run", "start", "--task", task["taskId"])
    findings = [
        item
        for item in HarnessApplication().list_findings(python_workspace, run["executionId"])
        if item.validator_id == "intake.clarification"
    ]
    assert [item.rule_id for item in findings] == ["C1", "C2"]
    assert findings[0].recommendation is not None
    assert findings[0].recommendation.startswith("Responde Q-1 con harness task clarify")
    assert findings[0].message.startswith("El criterio AC-1")


def test_the_key_is_omitted_from_the_snapshot_when_absent(python_workspace: Path) -> None:
    set_intake(python_workspace, {"criteriaPolicy": "enforce"})
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert resolved.project.intake is not None
    assert "language" not in resolved.project.intake.model_dump(by_alias=True)
    assert HarnessApplication().validate_config(python_workspace)["intake"] == {
        "criteriaPolicy": "enforce"
    }
    set_intake(python_workspace, {"criteriaPolicy": "enforce", "language": "auto"})
    assert HarnessApplication().validate_config(python_workspace)["intake"] == {
        "criteriaPolicy": "enforce",
        "language": "auto",
    }


def test_init_writes_the_auto_language(python_workspace: Path) -> None:
    HarnessApplication().init(python_workspace, force=True)
    config = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    assert config["intake"]["language"] == "auto"
    assert ConfigurationResolver().resolve(python_workspace).project.intake is not None
