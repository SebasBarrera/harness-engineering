"""The ``trivial`` implement tier (#197): strict classification, its rung in the starting
tables and its escalation to the S rung."""

from __future__ import annotations

from typing import Any

import pytest

from governed_harness.agents.routing import (
    RoutingHistory,
    TaskSignals,
    classify_size,
    is_trivial,
    select,
    suggest_table,
)
from governed_harness.configuration.agent_results import (
    DEFAULT_ROUTING_TABLES,
    DEFAULT_TRIVIAL_THRESHOLDS,
    AgentRoutingConfig,
    SizeThresholds,
)

TRIVIAL = TaskSignals(requirements=1, files=1, loc=40)
TIERED = AgentRoutingConfig(mode="tiered")


def _policy(**data: Any) -> AgentRoutingConfig:
    return AgentRoutingConfig.model_validate({"mode": "tiered", **data})


# ----- classification ------------------------------------------------------------------------
def test_starting_thresholds_are_conservative() -> None:
    assert DEFAULT_TRIVIAL_THRESHOLDS == {"requirements": 1, "files": 1, "loc": 150}


def test_one_requirement_one_small_file_is_trivial() -> None:
    assert is_trivial(TRIVIAL, None) == (True, "all signals within trivial")
    assert is_trivial(TaskSignals(requirements=1, files=1, loc=150), None)[0]
    # The S/M/L classification used by contracts and metrics does not change.
    assert classify_size(TRIVIAL, None)[0] == "S"


@pytest.mark.parametrize(
    ("signals", "rule"),
    [
        (TaskSignals(requirements=2, files=1, loc=10), "requirements=2>1 -> not trivial"),
        (TaskSignals(requirements=1, files=2, loc=10), "files=2>1 -> not trivial"),
        (TaskSignals(requirements=1, files=1, loc=151), "loc=151>150 -> not trivial"),
        (TaskSignals(requirements=0, files=1, loc=10), "requirements=0<1 -> not trivial"),
        (TaskSignals(requirements=1, files=0, loc=0), "files=0<1 -> not trivial"),
        (
            TaskSignals(requirements=1, files=1, loc=10, risk_flags=("security",)),
            "risk:security -> M",
        ),
        (
            TaskSignals(requirements=1, files=1, loc=10, planned_large=True),
            "planned-large -> L",
        ),
    ],
)
def test_anything_else_is_not_trivial(signals: TaskSignals, rule: str) -> None:
    assert is_trivial(signals, None) == (False, rule)


def test_trivial_bounds_are_configurable_and_can_turn_the_tier_off() -> None:
    wider = SizeThresholds.model_validate({"trivial": {"files": 2, "loc": 400}})
    assert is_trivial(TaskSignals(requirements=1, files=2, loc=400), wider)[0]
    off = SizeThresholds.model_validate({"trivial": {"requirements": 0}})
    assert not is_trivial(TRIVIAL, off)[0]
    # An S bound below the trivial bound still wins: the task must be S first.
    tight = SizeThresholds.model_validate({"loc": [20, 100]})
    assert not is_trivial(TRIVIAL, tight)[0]


def test_negative_trivial_bounds_are_rejected() -> None:
    with pytest.raises(ValueError):
        SizeThresholds.model_validate({"trivial": {"loc": -1}})


# ----- routing table --------------------------------------------------------------------------
def test_starting_tables_have_a_trivial_rung() -> None:
    claude = DEFAULT_ROUTING_TABLES["claude-code"]["implement"]
    assert claude["trivial"] == {"model": "claude-haiku-5-5", "effort": "medium"}
    assert claude["S"]["model"] == claude["M"]["model"] == "claude-sonnet-5-5"
    codex = DEFAULT_ROUTING_TABLES["codex"]["implement"]
    assert codex["trivial"] == codex["S"]


def test_trivial_implement_runs_on_haiku() -> None:
    decision = select("implement", TRIVIAL, RoutingHistory(), TIERED, family="claude-code")
    assert (decision.rule, decision.size) == ("tier:implement:trivial", "trivial")
    assert (decision.model, decision.effort, decision.rung) == ("claude-haiku-5-5", "medium", None)
    assert decision.flags == ("--model", "claude-haiku-5-5", "--effort", "medium")
    assert decision.escalations == 0


@pytest.mark.parametrize("kind", ["clarify", "plan", "review", "acceptance", "architecture"])
def test_only_implement_uses_the_trivial_rung(kind: Any) -> None:
    decision = select(kind, TRIVIAL, RoutingHistory(), TIERED, family="claude-code")
    assert decision.size == "S"
    assert decision.model != "claude-haiku-5-5"


def test_non_trivial_small_task_stays_on_sonnet() -> None:
    signals = TaskSignals(requirements=2, files=1, loc=40)
    decision = select("implement", signals, RoutingHistory(), TIERED, family="claude-code")
    assert (decision.rule, decision.model, decision.effort) == (
        "tier:implement:S",
        "claude-sonnet-5-5",
        "medium",
    )


@pytest.mark.parametrize(
    ("escalations", "model", "effort", "rung"),
    [
        (1, "claude-sonnet-5-5", "medium", 0),
        (2, "claude-sonnet-5-5", "high", 1),
    ],
)
def test_trivial_escalates_to_the_s_rung_then_the_ladder(
    escalations: int, model: str, effort: str, rung: int
) -> None:
    decision = select(
        "implement", TRIVIAL, RoutingHistory(escalations=escalations), TIERED, family="claude-code"
    )
    assert decision.rule == f"escalation:{escalations}:implement:trivial"
    assert (decision.model, decision.effort, decision.rung) == (model, effort, rung)
    assert decision.escalations == escalations


def test_escalation_from_trivial_reaches_opus_with_a_higher_cap() -> None:
    policy = _policy(maxEscalations=3)
    decision = select(
        "implement", TRIVIAL, RoutingHistory(escalations=3), policy, family="claude-code"
    )
    assert (decision.model, decision.effort) == ("claude-opus-5-5", "high")


def test_codex_trivial_is_its_s_rung() -> None:
    decision = select("implement", TRIVIAL, RoutingHistory(), TIERED, family="codex")
    assert (decision.size, decision.model, decision.effort) == ("trivial", "gpt-6-luna", "high")
    escalated = select("implement", TRIVIAL, RoutingHistory(escalations=1), TIERED, family="codex")
    assert (escalated.model, escalated.effort) == ("gpt-6.1-sol", "medium")


def test_table_without_trivial_uses_its_s_rung() -> None:
    policy = _policy(tables={"claude-code": {"implement": {"S": {"model": "x", "effort": "low"}}}})
    decision = select("implement", TRIVIAL, RoutingHistory(), policy, family="claude-code")
    assert (decision.rule, decision.model, decision.effort) == (
        "tier:implement:trivial",
        "x",
        "low",
    )


def test_anchored_trivial_keeps_haiku_and_escalates_to_the_anchor() -> None:
    policy = AgentRoutingConfig(mode="anchored")
    anchor = "claude-sonnet-5-5"
    decision = select(
        "implement", TRIVIAL, RoutingHistory(), policy, family="claude-code", anchor=anchor
    )
    assert (decision.rule, decision.model, decision.effort) == (
        "anchored:implement:trivial",
        "claude-haiku-5-5",
        "medium",
    )
    escalated = select(
        "implement",
        TRIVIAL,
        RoutingHistory(escalations=1),
        policy,
        family="claude-code",
        anchor=anchor,
    )
    assert (escalated.rule, escalated.model, escalated.effort) == (
        "anchored:escalation:1:implement:trivial",
        "claude-sonnet-5-5",
        "medium",
    )
    assert escalated.escalations == 1


def test_anchored_trivial_without_anchor_keeps_the_provider_model() -> None:
    policy = AgentRoutingConfig(mode="anchored")
    decision = select("implement", TRIVIAL, RoutingHistory(), policy, family="claude-code")
    assert (decision.rule, decision.model, decision.escalations) == (
        "anchored:no-anchor:implement:trivial",
        None,
        0,
    )


def test_suggest_table_orders_trivial_first() -> None:
    rows = [
        {
            "family": "claude-code",
            "callKind": "implement",
            "size": size,
            "model": model,
            "effort": "medium",
            "costPerApprovedTask": 1.0,
            "approvedRuns": 2,
        }
        for size, model in (("S", "claude-sonnet-5-5"), ("trivial", "claude-haiku-5-5"))
    ]
    suggested = suggest_table(rows)
    assert list(suggested["claude-code"]) == ["trivial", "S"]
