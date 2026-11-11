"""Tiered routing anchored at the invoking model (agentRouting.mode: anchored, #85): the
invoking model is the ceiling, cheaper rungs serve the simple call kinds and sizes, and
escalation climbs up to that model, never above."""

from __future__ import annotations

from typing import Any

import pytest

from governed_harness.agents.requests import CALL_KINDS
from governed_harness.agents.routing import (
    RoutingHistory,
    TaskSignals,
    anchored_ladder,
    can_escalate,
    invoking_model,
    model_argument,
    model_rank,
    select,
    select_reviewer,
)
from governed_harness.configuration.agent_results import (
    DEFAULT_ROUTING_TABLES,
    AgentCallConfig,
    AgentRoutingConfig,
    FamilyTable,
    Rung,
)
from governed_harness.configuration.models import ProjectConfiguration

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
ANCHORED = AgentRoutingConfig(mode="anchored")
SMALL = TaskSignals(requirements=2, files=1, loc=100)
LARGE = TaskSignals(requirements=18, files=3, loc=500)
CLAUDE = FamilyTable.model_validate(DEFAULT_ROUTING_TABLES["claude-code"])


def _decide(kind: Any, anchor: str | None, signals: TaskSignals = SMALL, steps: int = 0) -> Any:
    return select(
        kind,
        signals,
        RoutingHistory(escalations=steps),
        ANCHORED,
        family="claude-code",
        anchor=anchor,
    )


def _project(**data: Any) -> ProjectConfiguration:
    return ProjectConfiguration.model_validate(
        {"configVersion": "1.0", "projectId": "p", "workspace": {"root": "."}, **data}
    )


# ----- the three invoking models of the pilot route differently ------------------------------
def test_haiku_invoked_routes_every_call_to_haiku() -> None:
    for kind in CALL_KINDS:
        for signals in (SMALL, LARGE):
            decision = _decide(kind, HAIKU, signals)
            assert decision.model == HAIKU, (kind, decision.rule)


def test_sonnet_invoked_caps_the_opus_rungs_at_sonnet() -> None:
    plan = _decide("plan", SONNET)
    assert (plan.model, plan.effort) == (SONNET, "high")
    assert plan.rule == "anchored:ceiling:plan:S"
    implement = _decide("implement", SONNET)
    assert (implement.model, implement.effort) == (SONNET, "medium")
    assert implement.rule == "anchored:implement:S"


def test_opus_invoked_keeps_the_cheaper_rungs_of_the_table() -> None:
    assert (_decide("locate", OPUS).model, _decide("locate", OPUS).effort) == (SONNET, "medium")
    assert _decide("clarify", OPUS).model == SONNET
    assert _decide("implement", OPUS, LARGE).model == OPUS
    assert _decide("review", OPUS).model == OPUS


def test_the_three_cells_differ() -> None:
    models = {anchor: _decide("plan", anchor).model for anchor in (HAIKU, SONNET, OPUS)}
    assert models == {HAIKU: HAIKU, SONNET: SONNET, OPUS: OPUS}


@pytest.mark.parametrize("anchor", [HAIKU, SONNET, OPUS])
def test_no_call_routes_above_the_invoking_model(anchor: str) -> None:
    ceiling = model_rank(anchor, "claude-code", ())
    for kind in CALL_KINDS:
        for signals in (SMALL, LARGE):
            for steps in range(4):
                model = _decide(kind, anchor, signals, steps).model
                rank = model_rank(model or "", "claude-code", ())
                assert rank is not None
                assert ceiling is not None
                assert rank <= ceiling, (anchor, kind, steps, model)


# ----- escalation ----------------------------------------------------------------------------
def test_escalation_climbs_up_to_the_invoking_model_only() -> None:
    rungs = [
        (
            _decide("implement", SONNET, steps=steps).model,
            _decide("implement", SONNET, steps=steps).effort,
        )
        for steps in range(3)
    ]
    assert rungs == [(SONNET, "medium"), (SONNET, "high"), (SONNET, "high")]
    assert can_escalate(RoutingHistory(escalations=0), ANCHORED)


def test_escalation_under_opus_reaches_opus() -> None:
    decision = _decide("implement", OPUS, steps=2)
    assert (decision.model, decision.effort) == (OPUS, "high")
    assert decision.rule == "anchored:escalation:2:implement:S"


# ----- what the anchor is ----------------------------------------------------------------------
def test_without_an_anchor_the_provider_keeps_its_model() -> None:
    decision = _decide("implement", None)
    assert (decision.model, decision.effort, decision.flags) == (None, None, ())
    assert decision.rule == "anchored:no-anchor:implement:S"


def test_an_unknown_model_allows_only_itself() -> None:
    decision = _decide("review", "house-model")
    assert (decision.model, decision.effort) == ("house-model", None)
    assert anchored_ladder(CLAUDE, "house-model", "claude-code") == (Rung(model="house-model"),)


def test_a_family_without_tiers_ranks_by_its_ladder() -> None:
    codex = FamilyTable.model_validate(DEFAULT_ROUTING_TABLES["codex"])
    allowed = anchored_ladder(codex, "gpt-6-luna", "codex")
    assert [item.model for item in allowed] == ["gpt-6-luna"]
    decision = select(
        "plan", SMALL, RoutingHistory(), ANCHORED, family="codex", anchor="gpt-6-luna"
    )
    assert decision.model == "gpt-6-luna"


def test_a_call_kind_setting_still_wins() -> None:
    decision = select(
        "clarify",
        SMALL,
        RoutingHistory(),
        ANCHORED,
        family="claude-code",
        override=AgentCallConfig(model=OPUS),
        anchor=HAIKU,
    )
    assert (decision.rule, decision.model) == ("override", OPUS)


def test_the_record_names_the_anchor() -> None:
    data = _decide("implement", SONNET).as_dict()
    assert data["mode"] == "anchored"
    assert data["anchor"] == SONNET
    tiered = select(
        "implement",
        SMALL,
        RoutingHistory(),
        AgentRoutingConfig(mode="tiered"),
        family="claude-code",
        anchor=SONNET,
    )
    assert "anchor" not in tiered.as_dict()


def test_reviewers_are_capped_too() -> None:
    capped = select_reviewer("architecture", ANCHORED, family="claude-code", anchor=SONNET)
    assert (capped.model, capped.rule) == (SONNET, "anchored:ceiling:review:architecture")
    kept = select_reviewer("tests", ANCHORED, family="claude-code", anchor=OPUS)
    assert (kept.model, kept.effort) == (SONNET, "medium")
    assert kept.as_dict()["anchor"] == OPUS


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("python", "agent.py", "--model", HAIKU), HAIKU),
        (("codex", "-m", "gpt-6-luna"), "gpt-6-luna"),
        (("claude", f"--model={SONNET}"), SONNET),
        (("claude", "--model"), None),
        (("claude", "-p"), None),
    ],
)
def test_model_argument(argv: tuple[str, ...], expected: str | None) -> None:
    assert model_argument(argv) == expected


def test_invoking_model_order() -> None:
    providers = {
        "configured": {"kind": "command", "command": ["agent"], "model": OPUS},
        "flagged": {"kind": "command", "command": ["agent", "--model", HAIKU]},
        "native": {"kind": "claude-code", "args": ["--model", SONNET]},
        "bare": {"kind": "command", "command": ["agent"]},
    }
    project = _project(agentProviders=providers, agentProvider="flagged")
    assert invoking_model(project, "configured") == OPUS
    assert invoking_model(project, "flagged") == HAIKU
    assert invoking_model(project, "native") == SONNET
    assert invoking_model(project, "bare") is None
    assert invoking_model(project, "missing") is None
    # The embedded session's read-only calls go to agentProvider: its model is the anchor.
    assert invoking_model(project, "session") == HAIKU
    explicit = _project(
        agentProviders=providers,
        agentRouting={"mode": "anchored", "anchorModel": SONNET},
    )
    assert invoking_model(explicit, "configured") == SONNET
