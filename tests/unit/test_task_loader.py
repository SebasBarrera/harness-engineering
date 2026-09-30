"""The task loader rejects incomplete or unknown task definitions (#29)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.application import load_task_file
from governed_harness.domain.errors import ConfigurationError


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("missing", ["title", "intent"])
def test_missing_required_field_is_rejected(tmp_path: Path, missing: str) -> None:
    fields = {"title": "t", "intent": "i"}
    fields.pop(missing)
    body = "".join(f"{key}: {value}\n" for key, value in fields.items())
    with pytest.raises(ConfigurationError, match=missing):
        load_task_file(write(tmp_path, body + "acceptanceCriteria: [x]\n"), project_id="p")


def test_criterion_object_without_text_is_rejected(tmp_path: Path) -> None:
    content = "title: t\nintent: i\nacceptanceCriteria:\n  - criterionId: ac_1\n"
    with pytest.raises(ConfigurationError, match="text"):
        load_task_file(write(tmp_path, content), project_id="p")


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    content = "title: t\nintent: i\nacceptanceCriteria: [x]\nacceptance: [typo]\n"
    with pytest.raises(ConfigurationError, match="acceptance"):
        load_task_file(write(tmp_path, content), project_id="p")


def test_valid_task_still_loads(tmp_path: Path) -> None:
    task = load_task_file(
        write(tmp_path, "title: t\nintent: i\nacceptanceCriteria: [works]\n"), project_id="p"
    )
    assert task.title == "t"
    assert task.intent == "i"
    assert task.acceptance_criteria[0].text == "works"
