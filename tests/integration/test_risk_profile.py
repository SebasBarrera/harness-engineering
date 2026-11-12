"""Governance by risk (``governance.riskProfile``): the light profile of a small, risk-free task
keeps every guarantee, the full profile is the full flow, and without the key nothing changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.models import AgentInvocation
from tests.integration.test_friction import (
    GOOD,
    TEST,
    events,
    of_type,
    patch_task,
    records,
    replace,
    start,
)

BAD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal > threshold else subtotal\n"
)
"""Off by one at the threshold: ``test_at_threshold`` fails."""


def configure(workspace: Path, profile: str | None, **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if profile is not None:
        config.setdefault("governance", {})["riskProfile"] = profile
    for key, value in sections.items():
        config[key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def small_task(task_id: str, code: str = GOOD, **extra: Any) -> dict[str, Any]:
    return patch_task(
        task_id,
        [replace("src/sample/pricing.py", code), replace("tests/test_pricing.py", TEST)],
        **extra,
    )


def kinds(workspace: Path, run: str) -> list[str]:
    return [item.call_kind for item in records(workspace, "agent_invocation", AgentInvocation, run)]


def test_auto_gives_a_small_task_the_light_profile_without_a_fast_lane_section(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        "auto",
        intake={"criteriaPolicy": "warn", "ambiguityReview": "agent"},
        friction={"planApproval": "risk"},
    )
    _application, run = start(python_workspace, tmp_path, small_task("task_light"))
    # The light profile still stops at DECISION for a person.
    assert run.current_phase is PhaseId.DECISION
    assert run.status is ResultStatus.BLOCKED
    chain = events(python_workspace, run.execution_id)
    lane = of_type(chain, "lane.classified")[0].payload
    assert lane["lane"] == "fast"
    assert lane["profile"] == "auto"
    assert lane["governance"] == "light"
    assert {"locate", "planApproval", "ambiguityReview"} <= set(lane["skip"])
    assert "clarify" not in kinds(python_workspace, run.execution_id)
    # The mandatory validators ran on the ChangeSet.
    assert of_type(chain, "validation.completed")


def test_the_light_profile_still_blocks_a_defective_change(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, "auto")
    _application, run = start(python_workspace, tmp_path, small_task("task_bad", BAD))
    lane = of_type(events(python_workspace, run.execution_id), "lane.classified")[0].payload
    assert lane["governance"] == "light"
    assert run.status in {ResultStatus.FAILED, ResultStatus.BLOCKED}
    assert run.current_phase in {PhaseId.IMPLEMENTATION, PhaseId.VERIFICATION}
    assert not of_type(events(python_workspace, run.execution_id), "human.decision.recorded")


def test_a_real_security_task_takes_the_full_profile_and_waits_for_its_plan(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, "auto", friction={"planApproval": "risk"})
    _application, run = start(
        python_workspace,
        tmp_path,
        small_task("task_auth", intent="Check the user's password before applying the discount."),
    )
    assert run.current_phase is PhaseId.PLANNING
    assert run.status is ResultStatus.BLOCKED
    chain = events(python_workspace, run.execution_id)
    lane = of_type(chain, "lane.classified")[0].payload
    assert lane["lane"] == "full"
    assert lane["governance"] == "full"
    assert lane["reasons"][0].startswith("size M (risk:security")
    assert of_type(chain, "plan.approval.requested")


def test_a_loose_word_no_longer_makes_a_task_risky(python_workspace: Path, tmp_path: Path) -> None:
    intent = "Apply the discount at the threshold and discard the card of zero tokens."
    configure(python_workspace, "auto")
    _application, run = start(python_workspace, tmp_path, small_task("task_word", intent=intent))
    lane = of_type(events(python_workspace, run.execution_id), "lane.classified")[0].payload
    assert lane["lane"] == "fast"
    assert lane["rule"] == "all signals within S"


def test_without_the_key_the_word_list_of_1x_still_applies(
    python_workspace: Path, tmp_path: Path
) -> None:
    intent = "Apply the discount at the threshold and discard the card of zero tokens."
    configure(python_workspace, None, friction={"fastLane": {"mode": "auto"}})
    _application, run = start(python_workspace, tmp_path, small_task("task_legacy", intent=intent))
    lane = of_type(events(python_workspace, run.execution_id), "lane.classified")[0].payload
    assert lane["lane"] == "full"
    assert lane["rule"] == "risk:security -> M"
    assert "profile" not in lane
    assert "governance" not in lane


def test_without_the_key_and_without_a_fast_lane_no_lane_is_classified(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, None)
    _application, run = start(python_workspace, tmp_path, small_task("task_plain"))
    assert run.current_phase is PhaseId.DECISION
    assert not of_type(events(python_workspace, run.execution_id), "lane.classified")


def test_full_gives_every_task_the_full_flow(python_workspace: Path, tmp_path: Path) -> None:
    configure(
        python_workspace,
        "full",
        friction={"fastLane": {"mode": "auto"}, "planApproval": "risk"},
    )
    _application, run = start(python_workspace, tmp_path, small_task("task_full"))
    assert run.current_phase is PhaseId.DECISION
    chain = events(python_workspace, run.execution_id)
    assert not of_type(chain, "lane.classified")
    assert not of_type(chain, "lane.step.skipped")


def test_light_gives_a_larger_risk_free_task_the_light_profile(
    python_workspace: Path, tmp_path: Path
) -> None:
    requirements = [
        {"requirementId": f"req_{index}", "text": f"Requirement {index} holds."}
        for index in range(6)
    ]
    configure(python_workspace, "light")
    _application, run = start(
        python_workspace, tmp_path, small_task("task_m", requirements=requirements)
    )
    lane = of_type(events(python_workspace, run.execution_id), "lane.classified")[0].payload
    assert lane["size"] == "M"
    assert lane["lane"] == "fast"
    assert lane["reasons"] == ["no risk flag"]
