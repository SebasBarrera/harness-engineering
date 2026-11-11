"""The 2.0.1 cost settings, unit level: the read-only effort of the routing
(``agentRouting.readOnlyEffort``), the token budgets by call kind (``agentContract.budgetProfile``),
the keys ``harness init`` writes for them and for the intake (``skipSettled``, ``locate.reuse``)."""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.agents.routing import (
    RoutingHistory,
    TaskSignals,
    select,
    select_reviewer,
    takes_effort,
)
from governed_harness.configuration.agent_contract import (
    DEFAULT_KIND_BUDGETS,
    DEFAULT_SIZE_BUDGETS,
    AgentContractConfig,
)
from governed_harness.configuration.agent_results import (
    DEFAULT_READ_ONLY_EFFORT,
    AgentRoutingConfig,
    AmbiguityReviewConfig,
    LocateConfig,
)
from governed_harness.configuration.init_project import initialize_project

HAIKU_4 = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
MEDIUM = TaskSignals(requirements=8, files=3, loc=500)
EFFORT = {"readOnlyEffort": dict(DEFAULT_READ_ONLY_EFFORT)}


def tiered(**extra: object) -> AgentRoutingConfig:
    return AgentRoutingConfig.model_validate({"mode": "tiered", **extra})


def anchored(anchor: str, **extra: object) -> AgentRoutingConfig:
    return AgentRoutingConfig.model_validate({"mode": "anchored", "anchorModel": anchor, **extra})


# ----- read-only effort ---------------------------------------------------------------------
def test_without_the_key_the_tables_effort_stays() -> None:
    for policy in (tiered(), anchored(SONNET)):
        decision = select(
            "clarify", MEDIUM, RoutingHistory(), policy, family="claude-code", anchor=SONNET
        )
        assert decision.effort == "medium"
        assert not decision.rule.endswith(":read-only-effort")
    review = select("review", MEDIUM, RoutingHistory(), tiered(), family="claude-code")
    assert (review.model, review.effort) == (OPUS, "high")


def test_the_read_only_calls_take_the_configured_effort() -> None:
    policy = tiered(**EFFORT)
    expected = {"clarify": "low", "locate": "low", "architecture": "medium", "review": "medium"}
    for kind, effort in expected.items():
        decision = select(kind, MEDIUM, RoutingHistory(), policy, family="claude-code")  # type: ignore[arg-type]
        assert decision.effort == effort, kind
        assert decision.rule.endswith(":read-only-effort")
        assert ("--effort", effort) == decision.flags[-2:]
    # plan and acceptance are not in the starting map: their table effort stays.
    plan = select("plan", MEDIUM, RoutingHistory(), policy, family="claude-code")
    assert plan.effort == "high"


def test_implement_and_escalations_keep_their_effort() -> None:
    policy = tiered(**EFFORT)
    implement = select("implement", MEDIUM, RoutingHistory(), policy, family="claude-code")
    assert implement.effort == "high"
    escalated = select(
        "review", MEDIUM, RoutingHistory(escalations=1), policy, family="claude-code"
    )
    plain = select("review", MEDIUM, RoutingHistory(escalations=1), tiered(), family="claude-code")
    assert (escalated.model, escalated.effort) == (plain.model, plain.effort)


def test_a_model_that_takes_no_effort_gets_none() -> None:
    assert not takes_effort(HAIKU_4)
    assert takes_effort(SONNET) and takes_effort("claude-haiku-5-5")
    decision = select(
        "clarify",
        MEDIUM,
        RoutingHistory(),
        anchored(HAIKU_4, **EFFORT),
        family="claude-code",
        anchor=HAIKU_4,
    )
    assert decision.model == HAIKU_4
    assert decision.effort is None
    assert "--effort" not in decision.flags


def test_the_reviewers_of_the_panel_take_the_review_effort() -> None:
    plain = select_reviewer("quality", tiered(), family="claude-code")
    assert plain.effort == "high"
    lowered = select_reviewer("quality", tiered(**EFFORT), family="claude-code")
    assert (lowered.model, lowered.effort) == (plain.model, "medium")


def test_the_unset_keys_stay_out_of_the_configuration() -> None:
    assert "readOnlyEffort" not in tiered().model_dump(by_alias=True)
    assert "skipSettled" not in AmbiguityReviewConfig().model_dump(by_alias=True)
    assert "reuse" not in LocateConfig(mode="agent").model_dump(by_alias=True)
    assert "budgetProfile" not in AgentContractConfig().model_dump(by_alias=True)


# ----- token budgets by kind ------------------------------------------------------------------
def test_without_the_profile_the_budget_is_one_per_size() -> None:
    config = AgentContractConfig()
    for size in ("S", "M", "L"):
        assert config.budget_for(size, "implement") == DEFAULT_SIZE_BUDGETS[size]  # type: ignore[arg-type]
        assert config.budget_for(size, "clarify") == DEFAULT_SIZE_BUDGETS[size]  # type: ignore[arg-type]


def test_the_by_kind_profile_parts_implement_from_the_read_only_calls() -> None:
    config = AgentContractConfig.model_validate({"budgetProfile": "by-kind"})
    assert config.budget_for("S", "implement") == (120_000, DEFAULT_SIZE_BUDGETS["S"][1])
    assert config.budget_for("M", "implement") == (300_000, DEFAULT_SIZE_BUDGETS["M"][1])
    assert config.budget_for("S", "clarify") == (100_000, DEFAULT_SIZE_BUDGETS["S"][1])
    assert config.budget_for("M", "review")[0] == 200_000
    # Never below the uniform budget.
    for group in DEFAULT_KIND_BUDGETS.values():
        for size, tokens in group.items():
            assert tokens >= DEFAULT_SIZE_BUDGETS[size][0]
    # A size's own tokens win over the profile.
    own = AgentContractConfig.model_validate(
        {"budgetProfile": "by-kind", "budgets": {"S": {"tokens": 70_000}}}
    )
    assert own.budget_for("S", "implement")[0] == 70_000


# ----- harness init -------------------------------------------------------------------------
def test_init_writes_the_cost_keys(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    config = yaml.safe_load((tmp_path / ".harness" / "project.yaml").read_text("utf-8"))
    assert config["intake"]["ambiguityReview"]["skipSettled"] is True
    assert config["context"]["locate"]["reuse"] == "scope"
    assert config["agentContract"]["budgetProfile"] == "by-kind"
    assert config["agentRouting"]["readOnlyEffort"] == DEFAULT_READ_ONLY_EFFORT
    assert "implement" not in config["agentRouting"]["readOnlyEffort"]
