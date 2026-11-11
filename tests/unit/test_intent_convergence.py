"""The pure parts of the converging agent review (#79): normalisation, deduplication by rule and
normalised text, the bound of a round, and the assumptions of a task revision."""

from __future__ import annotations

from governed_harness.configuration.agent_results import AmbiguityReviewConfig
from governed_harness.configuration.models import IntakeConfig
from governed_harness.domain.models import Task
from governed_harness.intake import task_digest
from governed_harness.orchestration.intent_convergence import (
    Convergence,
    assumptions_of,
    normalized,
    with_assumptions,
)

TASK = Task.model_validate(
    {
        "taskId": "t",
        "projectId": "p",
        "title": "Discount",
        "intent": "Apply the discount.",
        "acceptanceCriteria": [{"criterionId": "AC-1", "text": "apply(100) returns 90."}],
    }
)


def _context(rounds: int = 0, seen: frozenset[tuple[str, str]] = frozenset()) -> Convergence:
    return Convergence(AmbiguityReviewConfig(maxQuestions=2), (), seen, rounds)


def test_normalised_text_ignores_case_punctuation_and_spacing() -> None:
    assert normalized("Is the  threshold INCLUSIVE??") == "is the threshold inclusive"
    assert normalized("¿Qué pasa con 'ab=c'?") == "qué pasa con ab c"


def test_a_question_already_asked_is_dropped_and_a_round_is_bounded() -> None:
    context = _context(seen=frozenset({("A1", "is the threshold inclusive")}))
    items = [
        ("ambiguity", "task", "Is the threshold inclusive?"),
        ("edge-cases", "task", "What below zero?"),
        ("edge-cases", "task", "what BELOW zero"),
        ("errors", "task", "Which error?"),
        ("errors", "task", "Which status code?"),
    ]
    kept, dropped = context.converge(items)
    assert kept == [("edge-cases", "task", "What below zero?"), ("errors", "task", "Which error?")]
    assert dropped == 3


def test_the_round_and_the_cap() -> None:
    assert _context(rounds=0).round == 1
    assert not _context(rounds=2).exhausted
    assert Convergence(AmbiguityReviewConfig(maxRounds=2), (), frozenset(), 2).exhausted
    assert "round 1 of at most 3" in _context().suffix()


def test_assumptions_make_a_new_revision_once() -> None:
    items = [("ambiguity", "AC-1", "Is it inclusive?"), ("errors", "task", "Which error?")]
    revised, added = with_assumptions(TASK, items, 3)
    assert [item["assumptionId"] for item in added] == ["AS-1", "AS-2"]
    assert assumptions_of(revised) == added
    assert task_digest(revised) != task_digest(TASK)
    again, more = with_assumptions(revised, [("errors", "task", "which ERROR")], 4)
    assert more == []
    assert again is revised


def test_the_bare_value_keeps_the_review_of_1_1() -> None:
    assert IntakeConfig.model_validate({"ambiguityReview": "agent"}).ambiguity_settings is None
    converging = IntakeConfig.model_validate({"ambiguityReview": {"onExhausted": "assume"}})
    settings = converging.ambiguity_settings
    assert settings is not None
    assert (settings.rounds, settings.questions, settings.exhausted) == (3, 8, "assume")
    assert AmbiguityReviewConfig().exhausted == "block"
    off = IntakeConfig.model_validate({"ambiguityReview": {"mode": False}})
    assert not off.agent_review_enabled
