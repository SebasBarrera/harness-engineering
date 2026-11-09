"""Reverse engineering of an existing project (wave 11, #97): the survey writes persistent
documents inside the project, later requests point to them, the analysis is not repeated while
they hold (a recorded event proves it) and it is redone on demand."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind
from governed_harness.orchestration.reverse_engineering import (
    DIRECTORY,
    FILES,
    validate_documentation,
)
from tests.integration.architecture_agent import install
from tests.integration.test_engineering_flow import (
    GOOD,
    OWNED,
    TEST_AT_THRESHOLD,
    calls,
    create,
    events,
    findings,
)

SURVEYED = {"architecture": {"mode": "agent", "documentation": "project"}}
FILES_TO_WRITE = {"src/sample/pricing.py": GOOD, "tests/test_pricing.py": TEST_AT_THRESHOLD}
VALID: dict[str, Any] = {
    "organization": "Code in src/sample.",
    "components": [
        {"name": "pricing", "path": "src/sample/pricing.py", "responsibility": "Discounts."}
    ],
    "conventions": ["Type hints"],
    "guidance": ["Put new code in src/sample"],
}


def _approved(workspace: Path, tmp_path: Path, variant: str = "") -> tuple[Path, str, Any]:
    log = install(workspace, tmp_path, FILES_TO_WRITE, SURVEYED, variant)
    task = create(workspace, tmp_path, OWNED)
    application = HarnessApplication()
    run = application.start_run(workspace, task)
    return log, task, run


def _approve(workspace: Path, run: Any) -> None:
    application = HarnessApplication()
    state = application.architecture(workspace)["state"]
    application.decide_architecture(
        workspace,
        execution_id=run.execution_id,
        digest=state["digest"],
        rationale="Matches the code",
        decision=DecisionKind.APPROVE,
        actor_id="human.lead",
    )


def _reused(workspace: Path, run: Any) -> list[dict[str, Any]]:
    return [
        item["payload"]
        for item in events(workspace, run.execution_id)
        if item["eventType"] == "architecture.survey.reused"
    ]


def test_the_survey_writes_the_documents_inside_the_project(
    python_workspace: Path, tmp_path: Path
) -> None:
    log, _task, run = _approved(python_workspace, tmp_path)
    assert (run.current_phase.value, run.status.value) == ("DISCOVERY", "BLOCKED")
    folder = python_workspace / ".harness" / DIRECTORY
    assert sorted(item.name for item in folder.iterdir()) == sorted(FILES)
    components = (folder / "components.md").read_text(encoding="utf-8")
    assert "**pricing**: Applies discounts." in components
    structure = (folder / "structure.md").read_text(encoding="utf-8")
    assert "- `src/sample`" in structure
    assert "infrastructure in src/sample/infra" in structure
    agreement = (folder / "working-agreement.md").read_text(encoding="utf-8")
    assert "- Put new infrastructure code under src/sample/infra" in agreement
    assert "components" in calls(log, "architecture")[0]["instructions"]
    shown = HarnessApplication().architecture(python_workspace)["reverseEngineering"]
    assert sorted(shown["files"]) == sorted(FILES)
    assert shown["files"]["components.md"]["present"] is True


def test_later_requests_point_to_the_documents(python_workspace: Path, tmp_path: Path) -> None:
    log, _task, run = _approved(python_workspace, tmp_path)
    _approve(python_workspace, run)
    block = calls(log, "implement")[0]["architecture"]["reverseEngineering"]
    assert block["directory"] == ".harness/reverse-engineering"
    assert sorted(block["files"]) == sorted(FILES)
    assert "Put new infrastructure code" in block["workingAgreement"]
    assert "reverse engineering documents" in calls(log, "implement")[0]["instructions"]


def test_the_analysis_is_not_repeated_and_the_reuse_is_recorded(
    python_workspace: Path, tmp_path: Path
) -> None:
    log, task, first = _approved(python_workspace, tmp_path)
    _approve(python_workspace, first)
    folder = python_workspace / ".harness" / DIRECTORY
    before = {item.name: item.read_bytes() for item in folder.iterdir()}
    completed = [
        item["payload"]
        for item in events(python_workspace, first.execution_id)
        if item["eventType"] == "architecture.survey.completed"
    ]
    second = HarnessApplication().start_run(python_workspace, task)
    assert len(calls(log, "architecture")) == 1
    reuse = _reused(python_workspace, second)
    assert len(reuse) == 1
    assert reuse[0]["agentCalls"] == 0
    assert reuse[0]["documentation"] == completed[0]["documentation"]
    assert {item.name: item.read_bytes() for item in folder.iterdir()} == before


def test_refresh_surveys_again_and_rewrites_the_documents(
    python_workspace: Path, tmp_path: Path
) -> None:
    log, task, first = _approved(python_workspace, tmp_path)
    _approve(python_workspace, first)
    application = HarnessApplication()
    assert application.refresh_architecture(python_workspace)["status"] == "STALE"
    application.start_run(python_workspace, task)
    assert len(calls(log, "architecture")) == 2


def test_missing_documents_are_written_again_by_one_survey(
    python_workspace: Path, tmp_path: Path
) -> None:
    log, task, first = _approved(python_workspace, tmp_path)
    _approve(python_workspace, first)
    folder = python_workspace / ".harness" / DIRECTORY
    shutil.rmtree(folder)
    second = HarnessApplication().start_run(python_workspace, task)
    assert len(calls(log, "architecture")) == 2
    assert _reused(python_workspace, second) == []
    assert (folder / "README.md").is_file()


def test_a_survey_without_the_documentation_is_malformed(
    python_workspace: Path, tmp_path: Path
) -> None:
    _log, _task, run = _approved(python_workspace, tmp_path, variant="bare-survey")
    assert (run.current_phase.value, run.status.value) == ("DISCOVERY", "BLOCKED")
    assert not (python_workspace / ".harness" / DIRECTORY).exists()
    assert [
        item.rule_id
        for item in findings(python_workspace, run.execution_id)
        if item.rule_id == "architecture.malformed"
    ] == ["architecture.malformed"]


def test_without_the_key_nothing_is_written_or_asked(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = install(python_workspace, tmp_path, FILES_TO_WRITE, {"architecture": {"mode": "agent"}})
    task = create(python_workspace, tmp_path, OWNED)
    run = HarnessApplication().start_run(python_workspace, task)
    assert (run.current_phase.value, run.status.value) == ("DISCOVERY", "BLOCKED")
    assert "components" not in calls(log, "architecture")[0]["instructions"]
    assert not (python_workspace / ".harness" / DIRECTORY).exists()
    assert HarnessApplication().architecture(python_workspace)["reverseEngineering"] is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("organization", "", "organization"),
        ("components", [], "components"),
        ("components", [{"name": "x", "path": "../x", "responsibility": "y"}], "relative"),
        ("components", [{"name": "x", "path": "a"}], "responsibility"),
        ("guidance", [], "guidance"),
        ("conventions", "none", "conventions"),
    ],
)
def test_the_documentation_of_a_survey_is_validated(field: str, value: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_documentation({**VALID, field: value})


def test_a_complete_documentation_is_accepted() -> None:
    documented = validate_documentation(VALID)
    assert documented["components"][0]["name"] == "pricing"
