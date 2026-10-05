from __future__ import annotations

import pytest
from pydantic import ValidationError

from governed_harness.domain.ids import new_id
from governed_harness.domain.models import FilePatch, Task


def test_new_id_has_prefix_and_unique_value() -> None:
    first = new_id("task")
    second = new_id("task")
    assert first.startswith("task_")
    assert first != second


@pytest.mark.parametrize("prefix", ["A", "1bad", "x-y", ""])
def test_new_id_rejects_invalid_prefix(prefix: str) -> None:
    with pytest.raises(ValueError):
        new_id(prefix)


@pytest.mark.parametrize("path", ["../secret", "/absolute", "a/../../b", "", "."])
def test_patch_rejects_unsafe_path(path: str) -> None:
    with pytest.raises(ValidationError):
        FilePatch(path=path, operation="create", content="x")


def test_patch_requires_content_for_write_operation() -> None:
    with pytest.raises(ValidationError):
        FilePatch(path="src/a.py", operation="replace")


def test_task_requires_acceptance_criterion() -> None:
    with pytest.raises(ValidationError):
        Task(
            task_id="task_001",
            project_id="project_001",
            title="x",
            intent="y",
            acceptance_criteria=(),
        )


def test_model_accepts_camel_case_aliases() -> None:
    task = Task.model_validate(
        {
            "taskId": "task_001",
            "projectId": "project_001",
            "title": "Example",
            "intent": "Do it",
            "acceptanceCriteria": [{"criterionId": "ac_001", "text": "It works"}],
        }
    )
    assert task.acceptance_criteria[0].criterion_id == "ac_001"
    assert task.model_dump(by_alias=True)["taskId"] == "task_001"


def test_only_a_task_marked_criteria_pending_may_have_no_criterion() -> None:
    pending = Task(
        task_id="task_001",
        project_id="project_001",
        title="x",
        intent="y",
        criteria_pending=True,
        acceptance_criteria=(),
    )
    stored = pending.model_dump(mode="json", by_alias=True)
    assert stored["criteriaPending"] is True
    assert Task.model_validate(stored) == pending


def test_criteria_pending_is_refused_on_a_task_with_criteria() -> None:
    with pytest.raises(ValidationError, match="criteriaPending is only allowed"):
        Task.model_validate(
            {
                "taskId": "task_001",
                "projectId": "project_001",
                "title": "Example",
                "intent": "Do it",
                "criteriaPending": True,
                "acceptanceCriteria": [{"criterionId": "ac_001", "text": "It works"}],
            }
        )


def test_a_task_with_criteria_serializes_without_the_marker() -> None:
    """Tasks with criteria keep the stored form (and digest) they had before the marker."""
    task = Task.model_validate(
        {
            "taskId": "task_001",
            "projectId": "project_001",
            "title": "Example",
            "intent": "Do it",
            "acceptanceCriteria": [{"criterionId": "ac_001", "text": "It works"}],
        }
    )
    assert "criteriaPending" not in task.model_dump(by_alias=True)
    assert "criteria_pending" not in task.model_dump(mode="json")
    assert "criteriaPending" not in task.model_dump_json(by_alias=True)
