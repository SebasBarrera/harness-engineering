"""The agent-results settings as ``harness init`` writes them (#52): every key present, a
deterministic run end to end with the simulated provider, and a 1.0.0 file that keeps its
serialized form."""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration.models import ProjectConfiguration
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus

TASK = (
    "taskId: task_defaults\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "requirements:\n"
    "  - requirementId: R1\n"
    "    text: Apply the discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
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
    "        def test_r1_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)


def test_init_writes_every_agent_results_setting(python_workspace: Path) -> None:
    HarnessApplication().init(python_workspace, force=True)
    config = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    assert config["intake"]["ambiguityReview"] == "agent"
    assert config["review"]["agentReview"] == "enforce"
    assert config["governance"]["stopTheLine"] == "restore"
    assert {"planning", "context", "budget", "memory", "agentRouting"} <= set(config)
    # #85: the invoking model is the ceiling of the routing tables.
    assert config["agentRouting"]["mode"] == "anchored"
    summary = HarnessApplication().validate_config(python_workspace)["agentResults"]
    assert summary["agentReview"] == "enforce"
    assert summary["checks"]["differential"] is True


def test_a_run_with_the_init_defaults_reaches_a_decision(
    python_workspace: Path, tmp_path: Path
) -> None:
    HarnessApplication().init(python_workspace, force=True)
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(python_workspace, source)
    run = application.start_run(python_workspace, "task_defaults").execution_id
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    assert status["gate"]["status"] == ResultStatus.PASSED, status["gate"]["reasonCodes"]
    _, execution = application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=status["execution"]["changeSetDigest"],
        actor_id="human.reviewer",
        rationale="Criteria covered by tests",
    )
    assert execution.status is ResultStatus.PASSED


def test_a_file_without_the_settings_keeps_its_serialized_form() -> None:
    raw = {"configVersion": "1.0", "projectId": "p", "workspace": {"root": ".."}}
    dumped = ProjectConfiguration.model_validate(raw).model_dump(mode="json", by_alias=True)
    assert not {"planning", "context", "budget", "memory", "agentRouting"} & set(dumped)
    assert not {"standards", "testing", "architecture"} & set(dumped)
    assert "forge" not in dumped.get("delivery", {})
    assert set(dumped["runtime"]) == {
        "commandTimeoutSeconds",
        "maxOutputBytes",
        "maxParallel",
        "allowNetwork",
    }
    with_sections = ProjectConfiguration.model_validate(
        {**raw, "verification": {"requirementTraceability": "warn"}, "review": {}}
    ).model_dump(mode="json", by_alias=True)
    assert with_sections["verification"] == {"requirementTraceability": "warn"}
    assert with_sections["review"] == {}


def test_init_writes_the_engineering_settings(python_workspace: Path) -> None:
    """Wave 6 (#56): standards, principles, testing strategy, architecture, project setup."""
    HarnessApplication().init(python_workspace, force=True)
    config = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    assert config["standards"] == {
        "packs": ["auto"],
        "cards": "auto",
        "maxCards": 12,
        "tools": "detect",
    }
    assert config["testing"]["strategy"] == "auto"
    assert config["architecture"]["mode"] == "agent"
    assert config["intake"]["projectSetup"] == "ask"
    assert config["verification"]["principles"]["mode"] == "enforce"
    engineering = HarnessApplication().validate_config(python_workspace)["engineering"]
    assert engineering["standards"]["packs"] == ["python"]
    assert engineering["testing"]["strategy"] == "conventional"
    assert engineering["projectKind"] == "existing"
