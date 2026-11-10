"""The catalogue of ten architecture styles and their experts (wave 11, #97): the greenfield flow
from the recommendation report to the expert prompt in every later request, and the project
that does not turn the key on keeping the earlier behaviour."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from governed_harness.application import HarnessApplication
from governed_harness.configuration.engineering import CATALOGUE_STYLES
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.orchestration.architecture_catalogue import catalogue, style_entry
from tests.integration.architecture_agent import install
from tests.integration.test_engineering_flow import (
    CORE,
    CORE_TEST,
    NEW_TASK,
    _new_project,
    calls,
    configure,
    create,
    events,
    findings,
)

CORE_FILES = {
    "src/app/core/__init__.py": "",
    "src/app/core/cart.py": CORE,
    "tests/test_cart.py": CORE_TEST,
}
OWNED_FILES = list(CORE_FILES)
RATIONALE = "The recommendation fits the project"
OPTIONS_BLOCK = {"architecture": {"mode": "agent", "catalogue": "experts"}}


def _blocked_new_project(tmp_path: Path, variant: str = "") -> tuple[Path, Path, str, Any]:
    root = _new_project(tmp_path / "new")
    log = install(root, tmp_path, CORE_FILES, OPTIONS_BLOCK, variant)
    task = create(root, tmp_path, OWNED_FILES, NEW_TASK)
    application = HarnessApplication()
    run = application.start_run(root, task)
    return root, log, task, run


@pytest.mark.parametrize("style", CATALOGUE_STYLES)
def test_every_style_has_documentation_and_an_expert_prompt(style: str) -> None:
    entry = style_entry(style)
    assert entry.style == style
    assert "## Best practices" in entry.document
    assert "## When it fits" in entry.document
    assert "## Structure" in entry.document
    assert "Expert prompt" not in entry.document
    assert len(entry.expert_prompt) > 200


def test_the_catalogue_has_ten_styles_and_the_package_ships_their_files() -> None:
    assert len(catalogue()) == 10
    assert len(set(CATALOGUE_STYLES)) == 10
    resources = Path(__file__).parents[2] / "src" / "governed_harness" / "resources"
    shipped = sorted(item.stem for item in (resources / "architecture").glob("*.md"))
    assert shipped == sorted(CATALOGUE_STYLES)
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"resources/architecture/*.md"' in pyproject


def test_the_report_names_the_recommendation_its_reasons_and_the_choices(tmp_path: Path) -> None:
    root, log, _task, run = _blocked_new_project(tmp_path)
    assert (run.current_phase.value, run.status.value) == ("INTENT", "BLOCKED")
    advice = calls(log, "architecture")[0]
    assert [item["id"] for item in advice["catalogue"]] == list(CATALOGUE_STYLES)
    assert "supports" in advice["instructions"]
    report = (root / ".harness" / "architecture-options.md").read_text(encoding="utf-8")
    assert "Recommended: **Hexagonal core**" in report
    assert "Business rules must be tested without a database." in report
    assert "- a database that may change" in report
    assert "--style <style>" in report
    shown = HarnessApplication().architecture(root)
    assert shown["recommendation"] == report
    proposed = [
        item["payload"]
        for item in events(root, run.execution_id)
        if item["eventType"] == "architecture.options.proposed"
    ]
    assert proposed[0]["recommended"] == "hex"


def test_accepting_the_recommendation_brings_the_expert_into_every_request(
    tmp_path: Path,
) -> None:
    root, log, task, run = _blocked_new_project(tmp_path)
    application = HarnessApplication()
    state = application.architecture(root)["state"]
    decided = application.decide_architecture(
        root,
        execution_id=run.execution_id,
        digest=state["digest"],
        rationale=RATIONALE,
        option="hex",
        actor_id="human.lead",
    )
    assert decided["architecture"]["acceptedRecommendation"] is True
    organize = [item for item in calls(log, "architecture") if item["mode"] == "organize"]
    assert len(organize) == 1
    assert organize[0]["readOnly"] is True
    assert style_entry("hexagonal").expert_prompt in organize[0]["instructions"]
    implement = calls(log, "implement")[0]["architecture"]
    assert implement["style"] == "hexagonal"
    assert implement["expert"]["prompt"] == style_entry("hexagonal").expert_prompt
    assert implement["organization"] == "Core and adapters."
    style_doc = (root / implement["expert"]["document"]).read_text(encoding="utf-8")
    assert style_doc == style_entry("hexagonal").document
    plan = (root / ".harness" / "architecture-organization.md").read_text(encoding="utf-8")
    assert "- `src/app/core`: business rules" in plan
    assert "expert" in calls(log, "implement")[0]["instructions"]
    application.start_run(root, task)
    assert len([i for i in calls(log, "architecture") if i["mode"] == "organize"]) == 1


def test_choosing_another_style_of_the_catalogue(tmp_path: Path) -> None:
    root, log, _task, run = _blocked_new_project(tmp_path)
    application = HarnessApplication()
    state = application.architecture(root)["state"]
    decided = application.decide_architecture(
        root,
        execution_id=run.execution_id,
        digest=state["digest"],
        rationale="We read and write very differently",
        style="cqrs",
        actor_id="human.lead",
    )
    assert decided["architecture"]["style"] == "cqrs"
    assert decided["architecture"]["acceptedRecommendation"] is False
    adr = (root / ".harness" / "adr" / "ADR-0001-architecture.md").read_text(encoding="utf-8")
    assert "cqrs" in adr
    assert "queries" in adr
    implement = calls(log, "implement")[0]["architecture"]
    assert implement["style"] == "cqrs"
    assert implement["expert"]["prompt"] == style_entry("cqrs").expert_prompt
    organize = [item for item in calls(log, "architecture") if item["mode"] == "organize"]
    assert organize[0]["style"] == "cqrs"


def test_choosing_a_proposed_style_keeps_the_layers_the_agent_proposed(tmp_path: Path) -> None:
    root, log, _task, run = _blocked_new_project(tmp_path)
    application = HarnessApplication()
    state = application.architecture(root)["state"]
    decided = application.decide_architecture(
        root,
        execution_id=run.execution_id,
        digest=state["digest"],
        rationale=RATIONALE,
        style="hexagonal",
        actor_id="human.lead",
    )
    assert decided["architecture"]["option"] == "hex"
    assert calls(log, "implement")[0]["architecture"]["layers"][0]["name"] == "core"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"style": "soa"}, "not a style of the catalogue"),
        ({"style": "cqrs", "option": "hex"}, "not both"),
    ],
)
def test_a_choice_outside_the_catalogue_is_refused(
    tmp_path: Path, kwargs: dict[str, str], message: str
) -> None:
    root, _log, _task, run = _blocked_new_project(tmp_path)
    application = HarnessApplication()
    state = application.architecture(root)["state"]
    with pytest.raises(PolicyViolationError, match=message):
        application.decide_architecture(
            root,
            execution_id=run.execution_id,
            digest=state["digest"],
            rationale=RATIONALE,
            actor_id="human.lead",
            **kwargs,
        )
    assert application.architecture(root)["state"]["status"] == "OPTIONS"


def test_a_report_without_reasons_is_malformed_and_blocks(tmp_path: Path) -> None:
    root, _log, _task, run = _blocked_new_project(tmp_path, variant="no-supports")
    assert (run.current_phase.value, run.status.value) == ("INTENT", "BLOCKED")
    assert HarnessApplication().architecture(root)["state"] is None
    assert [
        item.rule_id
        for item in findings(root, run.execution_id)
        if item.rule_id == "architecture.malformed"
    ] == ["architecture.malformed"]


def test_without_the_key_the_flow_is_the_earlier_one(tmp_path: Path) -> None:
    root = _new_project(tmp_path / "plain")
    log = configure(root, tmp_path, CORE_FILES, {"architecture": {"mode": "agent"}})
    task = create(root, tmp_path, OWNED_FILES, NEW_TASK)
    application = HarnessApplication()
    run = application.start_run(root, task)
    advice = calls(log, "architecture")[0]
    assert "catalogue" not in advice
    assert "event-driven" not in advice["styles"]
    assert not (root / ".harness" / "architecture-options.md").exists()
    state = application.architecture(root)["state"]
    with pytest.raises(PolicyViolationError, match="architecture.catalogue"):
        application.decide_architecture(
            root,
            execution_id=run.execution_id,
            digest=state["digest"],
            rationale=RATIONALE,
            style="cqrs",
            actor_id="human.lead",
        )
    application.decide_architecture(
        root,
        execution_id=run.execution_id,
        digest=state["digest"],
        rationale=RATIONALE,
        option="hex",
        actor_id="human.lead",
    )
    assert "expert" not in calls(log, "implement")[0]["architecture"]
    assert [item for item in calls(log, "architecture") if item["mode"] == "organize"] == []
