"""A task without acceptance criteria is accepted only under the enforce policy (#33)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.application import load_task_file
from governed_harness.configuration.models import CriteriaPolicy
from governed_harness.domain.errors import ConfigurationError

ONE_SENTENCE = "title: Rides\nintent: Build a ride-sharing backend.\n"


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("criteria", ["", "acceptanceCriteria: []\n", "acceptanceCriteria:\n"])
def test_enforce_accepts_a_task_without_criteria_and_marks_it(
    tmp_path: Path, criteria: str
) -> None:
    task = load_task_file(
        write(tmp_path, ONE_SENTENCE + criteria), project_id="p", criteria_policy="enforce"
    )
    assert task.acceptance_criteria == ()
    assert task.criteria_pending is True


@pytest.mark.parametrize("policy", ["warn", "off", None])
def test_other_policies_keep_the_refusal(tmp_path: Path, policy: CriteriaPolicy | None) -> None:
    path = write(tmp_path, ONE_SENTENCE)
    with pytest.raises(ConfigurationError) as refused:
        load_task_file(path, project_id="p", criteria_policy=policy)
    # The same message as a loader that does not know the policy.
    with pytest.raises(ConfigurationError) as unaware:
        load_task_file(path, project_id="p")
    assert str(refused.value) == str(unaware.value)
    assert str(refused.value).startswith("invalid task definition:")
    assert "at least one acceptance criterion is required" in str(refused.value)


@pytest.mark.parametrize("policy", ["enforce", "warn", "off", None])
def test_a_task_with_criteria_is_not_marked(tmp_path: Path, policy: CriteriaPolicy | None) -> None:
    task = load_task_file(
        write(tmp_path, ONE_SENTENCE + "acceptanceCriteria: [Returns 1.]\n"),
        project_id="p",
        criteria_policy=policy,
    )
    assert task.criteria_pending is False
    assert "criteriaPending" not in task.model_dump(by_alias=True)


def test_the_marker_cannot_be_written_in_a_task_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="unknown task field"):
        load_task_file(
            write(tmp_path, ONE_SENTENCE + "criteriaPending: true\n"),
            project_id="p",
            criteria_policy="warn",
        )
