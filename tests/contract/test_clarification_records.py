from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.models import ClarificationRecord, ClarificationRequest
from governed_harness.orchestration.engine import EngineServices

SCHEMA_DIR = Path(__file__).parents[2] / "schemas" / "v1"
RESOURCE_DIR = (
    Path(__file__).parents[2] / "src" / "governed_harness" / "resources" / "schemas" / "v1"
)


def _schema(name: str) -> dict[str, object]:
    value: dict[str, object] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return value


def _validate(name: str, value: object) -> None:
    Draft202012Validator(_schema(name), format_checker=FormatChecker()).validate(value)


@pytest.mark.parametrize(
    "name", ["clarification-request.schema.json", "clarification-record.schema.json"]
)
def test_clarification_schemas_are_published_and_packaged(name: str) -> None:
    Draft202012Validator.check_schema(_schema(name))
    assert (RESOURCE_DIR / name).read_bytes() == (SCHEMA_DIR / name).read_bytes()


def test_project_schema_accepts_the_intake_section_and_its_absence(python_workspace: Path) -> None:
    value = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    _validate("project-config.schema.json", value)
    assert value["intake"] == {"criteriaPolicy": "enforce"}
    del value["intake"]
    _validate("project-config.schema.json", value)
    with pytest.raises(ValidationError):
        _validate(
            "project-config.schema.json", {**value, "intake": {"criteriaPolicy": "sometimes"}}
        )


def test_persisted_clarification_records_match_public_schemas(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(
        "title: Discount\nintent: Add a discount.\nacceptanceCriteria:\n  - It is fast.\n",
        encoding="utf-8",
    )
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    run = application.start_run(python_workspace, task.task_id)
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "answers": {"Q-1": "The search responds in under 200 ms.", "Q-2": "Search only."},
                "addCriteria": [{"criterionId": "ac_empty", "text": "An empty query returns []."}],
                "addRequirements": ["Search by title."],
            }
        ),
        encoding="utf-8",
    )
    application.clarify_task(
        python_workspace, task_id=task.task_id, answers_file=answers, actor_id="human.contract"
    )
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        (request,) = services.state.list(
            "clarification_request", ClarificationRequest, execution_id=run.execution_id
        )
        (record,) = services.state.list(
            "clarification", ClarificationRecord, execution_id=run.execution_id
        )
        assert [question.rule_id for question in request.questions] == ["C2", "T1"]
        assert record.added_criteria == ("ac_empty",)
        _validate(
            "clarification-request.schema.json", request.model_dump(mode="json", by_alias=True)
        )
        _validate("clarification-record.schema.json", record.model_dump(mode="json", by_alias=True))
        artifact = json.loads(services.artifacts.get(record.task_ref))
        assert artifact["task_id"] == task.task_id
        for event in services.events.list(run.execution_id):
            _validate("event.schema.json", event.as_dict())
    finally:
        services.close()


def test_a_task_without_criteria_and_its_c0_records_match_public_schemas(
    python_workspace: Path, tmp_path: Path
) -> None:
    """Under enforce a task may start without criteria (#33): the pending task, the C0
    questions and the answers that add its criteria are all valid records."""
    task_file = tmp_path / "task.yaml"
    task_file.write_text("title: Rides\nintent: Build a ride-sharing backend.\n", encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    pending = task.model_dump(mode="json", by_alias=True)
    assert pending["criteriaPending"] is True
    _validate("task.schema.json", pending)
    with pytest.raises(ValidationError):
        _validate("task.schema.json", {**pending, "criteriaPending": "yes"})
    run = application.start_run(python_workspace, task.task_id)
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        "answers:\n  Q-1: POST /rides returns 201.\n  Q-6: Payments.\n", encoding="utf-8"
    )
    result = application.clarify_task(
        python_workspace, task_id=task.task_id, answers_file=answers, actor_id="human.contract"
    )
    _validate("task.schema.json", result["task"])
    assert "criteriaPending" not in result["task"]
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        (request,) = services.state.list(
            "clarification_request", ClarificationRequest, execution_id=run.execution_id
        )
        (record,) = services.state.list(
            "clarification", ClarificationRecord, execution_id=run.execution_id
        )
        assert {question.rule_id for question in request.questions} == {"C0"}
        assert [answer.target for answer in record.answers] == [
            "task:results",
            "task:out-of-scope",
        ]
        _validate(
            "clarification-request.schema.json", request.model_dump(mode="json", by_alias=True)
        )
        _validate("clarification-record.schema.json", record.model_dump(mode="json", by_alias=True))
    finally:
        services.close()
