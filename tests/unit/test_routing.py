from __future__ import annotations

from typing import Any

import pytest

from governed_harness.agents.requests import CALL_KINDS
from governed_harness.agents.routing import (
    CalibrationRow,
    RoutingHistory,
    TaskSignals,
    calibrate,
    can_escalate,
    classify_size,
    flags_for,
    provider_family,
    select,
    suggest_table,
)
from governed_harness.configuration.agent_results import (
    AgentCallConfig,
    AgentRoutingConfig,
    SizeThresholds,
)

SMALL = TaskSignals(requirements=2, files=1, loc=100)
MEDIUM = TaskSignals(requirements=8, files=3, loc=500)
LARGE = TaskSignals(requirements=18, files=3, loc=500)
TIERED = AgentRoutingConfig(mode="tiered")


def _policy(**data: Any) -> AgentRoutingConfig:
    return AgentRoutingConfig.model_validate({"mode": "tiered", **data})


# ----- classify_size -------------------------------------------------------------------------
def test_small_task_is_s() -> None:
    assert classify_size(SMALL, None) == ("S", "all signals within S")


def test_largest_signal_decides() -> None:
    assert classify_size(LARGE, None) == ("L", "requirements=18>15 -> L")
    assert classify_size(MEDIUM, None) == ("M", "requirements=8>5 -> M")
    files_large = TaskSignals(requirements=8, files=25, loc=0)
    assert classify_size(files_large, None) == ("L", "files=25>20 -> L")
    loc_medium = TaskSignals(requirements=0, files=0, loc=2001)
    assert classify_size(loc_medium, None) == ("M", "loc=2001>2000 -> M")


def test_bounds_are_inclusive() -> None:
    assert classify_size(TaskSignals(requirements=5, files=5, loc=2000), None)[0] == "S"
    assert classify_size(TaskSignals(requirements=15, files=0, loc=0), None)[0] == "M"


def test_custom_thresholds_fall_back_per_signal() -> None:
    thresholds = SizeThresholds(requirements=(1, 2))
    assert classify_size(TaskSignals(requirements=3, files=0, loc=0), thresholds) == (
        "L",
        "requirements=3>2 -> L",
    )
    assert classify_size(TaskSignals(requirements=0, files=6, loc=0), thresholds)[0] == "M"


def test_risk_raises_s_to_m_only() -> None:
    risky = TaskSignals(requirements=1, files=1, loc=1, risk_flags=("interface",))
    assert classify_size(risky, None) == ("M", "risk:interface -> M")
    risky_large = TaskSignals(requirements=20, files=1, loc=1, risk_flags=("interface",))
    assert classify_size(risky_large, None)[0] == "L"


def test_planned_large_forces_l() -> None:
    planned = TaskSignals(requirements=1, files=1, loc=1, planned_large=True)
    assert classify_size(planned, None) == ("L", "planned-large -> L")


# ----- provider_family -----------------------------------------------------------------------
def test_family_from_command_and_id() -> None:
    assert provider_family("main", ["/usr/local/bin/claude", "-p"], None) == "claude-code"
    assert provider_family("main", ["codex", "exec"], None) == "codex"
    assert provider_family("claude-agent", ["run.sh"], None) == "claude-code"
    assert provider_family("local", ["python", "agent.py"], None) == "generic"


def test_explicit_family_mapping_wins() -> None:
    assert provider_family("main", ["claude"], {"main": "codex"}) == "codex"
    assert provider_family("other", ["claude"], {"main": "codex"}) == "claude-code"


# ----- select: fixed and override ------------------------------------------------------------
def test_no_policy_is_fixed() -> None:
    decision = select("implement", SMALL, RoutingHistory(), None, family="claude-code")
    assert (decision.rule, decision.model, decision.effort, decision.rung) == (
        "fixed",
        None,
        None,
        None,
    )
    assert decision.mode == "fixed"
    assert decision.size == "S"
    assert decision.flags == ()


def test_fixed_mode_is_fixed() -> None:
    policy = AgentRoutingConfig(mode="fixed")
    decision = select("review", LARGE, RoutingHistory(escalations=2), policy, family="codex")
    assert decision.rule == "fixed"
    assert decision.model is None


def test_generic_without_table_is_fixed() -> None:
    decision = select("implement", SMALL, RoutingHistory(), TIERED, family="generic")
    assert decision.rule == "fixed"
    assert decision.flags == ()


def test_generic_with_table_is_tiered() -> None:
    policy = _policy(tables={"generic": {"implement": {"S": {"model": "local-small"}}}})
    decision = select("implement", SMALL, RoutingHistory(), policy, family="generic")
    assert (decision.rule, decision.model, decision.effort) == (
        "tier:implement:S",
        "local-small",
        None,
    )
    assert decision.flags == ()


def test_override_wins() -> None:
    override = AgentCallConfig(model="claude-haiku-5", effort="low")
    decision = select(
        "implement", LARGE, RoutingHistory(), TIERED, family="claude-code", override=override
    )
    assert (decision.rule, decision.model, decision.effort) == ("override", "claude-haiku-5", "low")
    assert decision.size == "L"
    assert decision.flags == ("--model", "claude-haiku-5", "--effort", "low")


def test_override_without_model_or_effort_is_ignored() -> None:
    override = AgentCallConfig(provider="other")
    decision = select(
        "implement", SMALL, RoutingHistory(), TIERED, family="claude-code", override=override
    )
    assert decision.rule == "tier:implement:S"


# ----- select: tiered ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("signals", "size", "model", "effort", "rung"),
    [
        (SMALL, "S", "claude-sonnet-5-5", "medium", 0),
        (MEDIUM, "M", "claude-sonnet-5-5", "high", 1),
        (LARGE, "L", "claude-opus-5-5", "high", 2),
    ],
)
def test_tiered_implement_claude(
    signals: TaskSignals, size: str, model: str, effort: str, rung: int
) -> None:
    decision = select("implement", signals, RoutingHistory(), TIERED, family="claude-code")
    assert decision.rule == f"tier:implement:{size}"
    assert (decision.model, decision.effort, decision.rung) == (model, effort, rung)
    assert decision.flags == ("--model", model, "--effort", effort)
    assert decision.escalations == 0


@pytest.mark.parametrize(
    ("kind", "model", "effort"),
    [
        ("clarify", "claude-sonnet-5-5", "medium"),
        ("plan", "claude-opus-5-5", "high"),
        ("review", "claude-opus-5-5", "high"),
    ],
)
def test_tiered_planning_kinds_claude(kind: Any, model: str, effort: str) -> None:
    decision = select(kind, SMALL, RoutingHistory(), TIERED, family="claude-code")
    assert decision.rule == f"tier:{kind}:S"
    assert (decision.model, decision.effort) == (model, effort)


@pytest.mark.parametrize(
    ("kind", "signals", "model", "effort"),
    [
        ("implement", SMALL, "gpt-6-luna", "high"),
        ("implement", LARGE, "gpt-6.1-sol", "high"),
        ("clarify", SMALL, "gpt-6.1-sol", "medium"),
        ("plan", SMALL, "gpt-6.1-sol", "high"),
        ("review", SMALL, "gpt-6.1-sol", "high"),
    ],
)
def test_tiered_codex(kind: Any, signals: TaskSignals, model: str, effort: str) -> None:
    decision = select(kind, signals, RoutingHistory(), TIERED, family="codex")
    assert (decision.model, decision.effort) == (model, effort)
    assert decision.flags == ("-m", model, "-c", f'model_reasoning_effort="{effort}"')


def test_missing_rung_falls_back_to_the_implement_tier() -> None:
    # #59: a call kind without its own entry runs on the implement rung, with a warning; with
    # no implement rung for the size either, the provider keeps its own model.
    policy = _policy(tables={"claude-code": {"implement": {"S": {"model": "x"}}}})
    decision = select("plan", SMALL, RoutingHistory(), policy, family="claude-code")
    assert (decision.rule, decision.model) == ("fallback:implement:plan:S", "x")
    assert decision.warning is not None and "plan" in decision.warning
    assert decision.as_dict()["warning"] == decision.warning
    decision = select("implement", LARGE, RoutingHistory(), policy, family="claude-code")
    assert decision.rule == "fixed"
    assert decision.warning is None
    decision = select("plan", LARGE, RoutingHistory(), policy, family="claude-code")
    assert (decision.rule, decision.warning) == ("fixed", None)


@pytest.mark.parametrize("family", ["claude-code", "codex"])
@pytest.mark.parametrize("kind", list(CALL_KINDS))
def test_every_call_kind_has_a_default_routing_entry(family: Any, kind: Any) -> None:
    # #59: every call kind of provider protocol 1.1 is routed by the default tables, without
    # a fallback and without an error.
    for signals in (SMALL, MEDIUM, LARGE):
        decision = select(kind, signals, RoutingHistory(), TIERED, family=family)
        assert decision.model, (kind, signals)
        assert decision.warning is None
        assert decision.rule.startswith("tier:")


@pytest.mark.parametrize("kind", [item for item in CALL_KINDS if item != "implement"])
def test_every_call_kind_falls_back_to_the_implement_tier(kind: Any) -> None:
    policy = _policy(
        tables={"codex": {"implement": {size: {"model": f"m-{size}"} for size in "SML"}}}
    )
    decision = select(kind, MEDIUM, RoutingHistory(), policy, family="codex")
    assert decision.model == "m-M"
    assert decision.rule == f"fallback:implement:{kind}:M"
    assert decision.warning == (
        f"no routing entry for the {kind} call: the implement rung of size M is used"
    )


def test_policy_table_replaces_default() -> None:
    policy = _policy(tables={"claude-code": {"implement": {"S": {"model": "m1", "effort": "low"}}}})
    decision = select("implement", SMALL, RoutingHistory(), policy, family="claude-code")
    assert (decision.model, decision.effort, decision.rung) == ("m1", "low", None)


# ----- escalation ----------------------------------------------------------------------------
def test_escalation_moves_up_the_ladder() -> None:
    decision = select(
        "implement", SMALL, RoutingHistory(escalations=1), TIERED, family="claude-code"
    )
    assert decision.rule == "escalation:1:implement:S"
    assert (decision.model, decision.effort, decision.rung) == ("claude-sonnet-5-5", "high", 1)
    assert decision.escalations == 1


def test_escalation_is_capped_by_max_escalations() -> None:
    decision = select(
        "implement", SMALL, RoutingHistory(escalations=5), TIERED, family="claude-code"
    )
    assert decision.rule == "escalation:2:implement:S"
    assert decision.rung == 2
    policy = _policy(maxEscalations=0)
    decision = select(
        "implement", SMALL, RoutingHistory(escalations=5), policy, family="claude-code"
    )
    assert decision.rule == "tier:implement:S"


def test_escalation_is_capped_at_the_top_of_the_ladder() -> None:
    decision = select("review", SMALL, RoutingHistory(escalations=2), TIERED, family="claude-code")
    assert decision.rule == "escalation:2:review:S"
    assert (decision.model, decision.effort, decision.rung) == ("claude-opus-5-5", "xhigh", 3)


def test_escalation_from_unknown_rung_uses_model_or_start() -> None:
    by_model = _policy(
        tables={
            "codex": {
                "implement": {"S": {"model": "b", "effort": "odd"}},
                "ladder": [{"model": "a"}, {"model": "b", "effort": "low"}, {"model": "c"}],
            }
        }
    )
    decision = select("implement", SMALL, RoutingHistory(escalations=1), by_model, family="codex")
    assert (decision.model, decision.rung) == ("c", 2)
    unknown = _policy(
        tables={
            "codex": {
                "implement": {"S": {"model": "z"}},
                "ladder": [{"model": "a"}, {"model": "b"}],
            }
        }
    )
    decision = select("implement", SMALL, RoutingHistory(escalations=1), unknown, family="codex")
    assert (decision.model, decision.rung) == ("b", 1)


def test_clarify_and_plan_do_not_escalate() -> None:
    for kind in ("clarify", "plan"):
        decision = select(
            kind,
            SMALL,
            RoutingHistory(escalations=2),
            TIERED,
            family="claude-code",
        )
        assert decision.rule == f"tier:{kind}:S"
        assert decision.escalations == 0


def test_without_ladder_no_escalation() -> None:
    policy = _policy(tables={"codex": {"implement": {"S": {"model": "a"}}}})
    decision = select("implement", SMALL, RoutingHistory(escalations=1), policy, family="codex")
    assert (decision.rule, decision.model, decision.rung) == ("tier:implement:S", "a", None)


def test_can_escalate() -> None:
    assert can_escalate(RoutingHistory(), TIERED)
    assert can_escalate(RoutingHistory(escalations=1), TIERED)
    assert not can_escalate(RoutingHistory(escalations=2), TIERED)
    assert not can_escalate(RoutingHistory(), None)
    assert not can_escalate(RoutingHistory(), AgentRoutingConfig(mode="fixed"))
    assert can_escalate(RoutingHistory(escalations=3), _policy(maxEscalations=4))


# ----- flags and digest ----------------------------------------------------------------------
def test_flags_for_each_family() -> None:
    assert flags_for("claude-code", "m", "high") == ("--model", "m", "--effort", "high")
    assert flags_for("claude-code", None, "high") == ("--effort", "high")
    assert flags_for("claude-code", "m", None) == ("--model", "m")
    assert flags_for("codex", "m", "low") == ("-m", "m", "-c", 'model_reasoning_effort="low"')
    assert flags_for("codex", "m", None) == ("-m", "m")
    assert flags_for("generic", "m", "low") == ()


def test_policy_digest_is_stable_and_sensitive() -> None:
    first = select("implement", SMALL, RoutingHistory(), TIERED, family="claude-code")
    again = select("implement", MEDIUM, RoutingHistory(), _policy(), family="claude-code")
    assert first.policy_digest == again.policy_digest
    assert first.policy_digest.startswith("sha256:")
    other_family = select("implement", SMALL, RoutingHistory(), TIERED, family="codex")
    assert other_family.policy_digest != first.policy_digest
    other_policy = select(
        "implement", SMALL, RoutingHistory(), _policy(maxEscalations=1), family="claude-code"
    )
    assert other_policy.policy_digest != first.policy_digest
    none_digest = select("implement", SMALL, RoutingHistory(), None, family="claude-code")
    assert none_digest.policy_digest != first.policy_digest


def test_as_dict_uses_camel_case() -> None:
    decision = select("implement", SMALL, RoutingHistory(), TIERED, family="claude-code")
    data = decision.as_dict()
    assert list(data) == [
        "callKind",
        "mode",
        "family",
        "size",
        "rule",
        "model",
        "effort",
        "rung",
        "escalations",
        "policyDigest",
        "flags",
    ]
    assert data["callKind"] == "implement"
    assert data["mode"] == "tiered"
    assert data["flags"] == ["--model", "claude-sonnet-5-5", "--effort", "medium"]


# ----- calibration ---------------------------------------------------------------------------
def _row(
    run: str,
    *,
    model: str = "a",
    cost: float | None = 1.0,
    approved: bool = True,
    size: str | None = "S",
    kind: str = "implement",
    family: str = "codex",
) -> CalibrationRow:
    return CalibrationRow(
        family=family,
        call_kind=kind,
        size=size,
        model=model,
        effort="high",
        cost_usd=cost,
        approved=approved,
        run_id=run,
    )


def test_calibrate_cost_per_approved_task() -> None:
    rows = [
        _row("r1", cost=1.0),
        _row("r1", cost=0.5),
        _row("r2", cost=1.5, approved=False),
        _row("r3", model="b", cost=None),
        _row("r4", model="b", cost=None, approved=False),
    ]
    summary = calibrate(rows)
    assert [item["model"] for item in summary] == ["a", "b"]
    first, second = summary
    assert first["runs"] == 2
    assert first["approvedRuns"] == 1
    assert first["costUsd"] == pytest.approx(3.0)
    assert first["costPerApprovedTask"] == pytest.approx(3.0)
    assert first["approvalRate"] == pytest.approx(0.5)
    assert second["costUsd"] is None
    assert second["costPerApprovedTask"] is None


def test_calibrate_sorts_by_cost_with_none_last() -> None:
    rows = [
        _row("r1", model="none", cost=None),
        _row("r2", model="dear", cost=5.0),
        _row("r3", model="cheap", cost=1.0),
        _row("r4", model="never", cost=1.0, approved=False),
        _row("r5", model="m-size", size="M", cost=0.1),
    ]
    summary = calibrate(rows)
    assert [item["model"] for item in summary] == ["m-size", "cheap", "dear", "never", "none"]
    assert calibrate([]) == []


def test_suggest_table_picks_cheapest_with_two_approved_runs() -> None:
    rows = [
        _row("r1", model="cheap", cost=1.0),
        _row("r2", model="cheap", cost=1.0),
        _row("r3", model="lucky", cost=0.1),
        _row("r4", model="dear", cost=4.0),
        _row("r5", model="dear", cost=4.0),
        _row("r6", model="big", size="L", cost=9.0),
        _row("r7", model="big", size="L", cost=9.0),
        _row("r8", model="plan", kind="plan", cost=0.01),
        _row("r9", model="plan", kind="plan", cost=0.01),
        _row("x1", model="other", family="claude-code", cost=2.0),
    ]
    suggestion = suggest_table(calibrate(rows))
    assert suggestion == {
        "codex": {
            "S": {"model": "cheap", "effort": "high"},
            "L": {"model": "big", "effort": "high"},
        }
    }
    assert list(suggestion["codex"]) == ["S", "L"]
