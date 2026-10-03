from __future__ import annotations

import pytest

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import AcceptanceCriterion, Requirement, Task
from governed_harness.intake import (
    QUESTION_TEMPLATES,
    TASK_TARGET,
    ClarificationInput,
    assess_intent,
    no_observable_result,
    revise_task,
    scope_without_breakdown,
    task_digest,
    unmeasured_quality,
)

LONG_INTENT = (
    "Apply a percentage discount to the order subtotal only when the subtotal reaches the "
    "configured threshold, leaving every other order untouched and keeping the public "
    "signature of the pricing function."
)


def criterion(
    text: str, hint: str | None = None, criterion_id: str = "AC-1"
) -> AcceptanceCriterion:
    return AcceptanceCriterion(criterion_id=criterion_id, text=text, verification_hint=hint)


def task(
    *criteria: AcceptanceCriterion,
    intent: str = LONG_INTENT,
    requirements: tuple[Requirement, ...] = (),
) -> Task:
    return Task(
        task_id="task_1",
        project_id="project_1",
        title="Discount",
        intent=intent,
        requirements=requirements,
        acceptance_criteria=criteria,
    )


# ----- C1: no observable result ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "It works.",
        "Works",
        "Done properly",
        "It should work correctly and as expected.",
        "The feature is user-friendly and robust.",
        "Everything works well and the code is clean.",
        "The output is good.",
    ],
)
def test_c1_flags_short_or_vague_criteria(text: str) -> None:
    assert no_observable_result(criterion(text))


@pytest.mark.parametrize(
    "text",
    [
        "A subtotal below the threshold is unchanged.",
        "Task values have the highest precedence.",
        "Existing repository precedence remains covered.",
        "A subtotal equal to the threshold is reduced by the rate.",
    ],
)
def test_c1_accepts_concrete_criteria_without_anchors(text: str) -> None:
    assert not no_observable_result(criterion(text))


@pytest.mark.parametrize(
    ("text", "anchor"),
    [
        ("Works for 3 items", "digit"),
        ("Shows 'Saved' properly", "single quotes"),
        ('Works "correctly"', "double quotes"),
        ("`make check` works", "backticks"),
        ("validate() works", "call"),
        ("apply_discount works", "snake_case identifier"),
        ("src/app works", "path"),
        ("config.yaml works", "file name"),
        ("Raises ValueError", "CamelCase name"),
        ("It returns", "result verb"),
        ("Rejects it", "result verb"),
        ("Tests pass.", "result verb"),
        ("It is at most fine", "at most"),
        ("Done within budget", "within"),
    ],
)
def test_c1_any_anchor_makes_a_criterion_observable(text: str, anchor: str) -> None:
    assert not no_observable_result(criterion(text)), anchor


def test_c1_reads_the_verification_hint() -> None:
    assert no_observable_result(criterion("It works."))
    assert not no_observable_result(
        criterion("It works.", hint="Calling apply_discount(100, 100, 0.1) returns 90.")
    )
    # A vague hint does not help.
    assert no_observable_result(criterion("It works.", hint="It works well."))


def test_c1_apostrophes_are_not_quotes() -> None:
    # Two apostrophes do not make quoted text, so nothing anchors these three words.
    assert no_observable_result(criterion("Isn't it fine's?"))
    assert no_observable_result(criterion("Don't it's fine"))


# ----- C2: quality without a measure ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("The search is fast.", "fast"),
        ("Pages load quickly for every user.", "quick"),
        ("The endpoint is secure.", "secure"),
        ("The import is reliable and scalable.", "reliable"),
        ("The dashboard stays responsive under load.", "responsive"),
    ],
)
def test_c2_flags_qualities_without_numbers(text: str, term: str) -> None:
    assert unmeasured_quality(criterion(text)) == term


@pytest.mark.parametrize(
    "text",
    [
        "The search responds in under 200 ms.",
        "The import is reliable for ten thousand rows.",
        "A fast-forward merge keeps the history linear.",
        "A subtotal below the threshold is unchanged.",
    ],
)
def test_c2_accepts_measured_or_unrelated_criteria(text: str) -> None:
    assert unmeasured_quality(criterion(text)) is None


def test_c2_measure_may_come_from_the_hint() -> None:
    assert unmeasured_quality(criterion("The search is fast.", hint="p95 below 200 ms")) is None


# ----- C3: duplicates and T1: scope -------------------------------------------------------


def test_c3_flags_a_repeated_criterion_with_folded_case_space_and_punctuation() -> None:
    questions = assess_intent(
        task(
            criterion("A subtotal below the threshold is unchanged.", criterion_id="AC-1"),
            criterion("a subtotal  below the THRESHOLD is unchanged", criterion_id="AC-2"),
        )
    )
    assert [(q.rule_id, q.target) for q in questions] == [("C3", "AC-2")]
    assert "repeats criterion AC-1" in questions[0].text


def test_c3_ignores_distinct_criteria_and_distinct_hints() -> None:
    assert not assess_intent(
        task(
            criterion("Returns 90 for 100.", criterion_id="AC-1"),
            criterion("Returns 90 for 100.", hint="With a 10% rate.", criterion_id="AC-2"),
            criterion("Returns 99 for 99.", criterion_id="AC-3"),
        )
    )


def test_t1_flags_a_short_intent_with_one_criterion_and_no_requirements() -> None:
    short = task(criterion("apply_discount(100, 100, 0.1) returns 90."), intent="Add a discount.")
    assert scope_without_breakdown(short)
    questions = assess_intent(short)
    assert [(q.rule_id, q.target) for q in questions] == [("T1", TASK_TARGET)]


def test_t1_needs_all_three_conditions() -> None:
    good = criterion("apply_discount(100, 100, 0.1) returns 90.")
    other = criterion("apply_discount(99, 100, 0.1) returns 99.", criterion_id="AC-2")
    requirement = Requirement(requirement_id="req_1", text="Discount at the threshold.")
    assert not scope_without_breakdown(task(good))
    assert not scope_without_breakdown(task(good, other, intent="Add a discount."))
    assert not scope_without_breakdown(
        task(good, intent="Add a discount.", requirements=(requirement,))
    )


# ----- assessment ----------------------------------------------------------------------


def test_assessment_is_deterministic_and_orders_questions() -> None:
    vague = task(
        criterion("It works.", criterion_id="AC-1"),
        criterion("It is fast.", criterion_id="AC-2"),
        criterion("It works.", criterion_id="AC-3"),
    )
    first = assess_intent(vague)
    assert first == assess_intent(vague)
    assert [(q.question_id, q.rule_id, q.target) for q in first] == [
        ("Q-1", "C1", "AC-1"),
        ("Q-2", "C2", "AC-2"),
        ("Q-3", "C1", "AC-3"),
        ("Q-4", "C3", "AC-3"),
    ]
    assert first[0].text == (
        "Criterion AC-1 ('It works.') does not say what can be observed. For which input or "
        "action, and what exact result shows that it holds?"
    )


def test_the_measured_discount_task_raises_no_question() -> None:
    assert not assess_intent(
        task(
            criterion("A subtotal below the threshold is unchanged.", criterion_id="ac_below"),
            criterion(
                "A subtotal equal to the threshold is reduced by the rate.",
                criterion_id="ac_at",
            ),
            intent="Apply a percentage discount only when the subtotal reaches the threshold.",
            requirements=(Requirement(requirement_id="req_1", text="Discount at threshold."),),
        )
    )


def test_templates_cover_every_rule() -> None:
    assert set(QUESTION_TEMPLATES) == {"C1", "C2", "C3", "T1"}


def test_long_texts_are_shortened_in_questions() -> None:
    text = "works " * 60
    (question,) = assess_intent(task(criterion(text), criterion("Returns 1.", criterion_id="B")))
    assert question.text.count("works") < 30
    assert "..." in question.text


# ----- revision --------------------------------------------------------------------------


def vague_task() -> Task:
    return task(criterion("It works.", criterion_id="AC-1"), intent="Add a discount.")


def test_answers_become_hints_and_requirements() -> None:
    original = vague_task()
    questions = assess_intent(original)
    assert [q.rule_id for q in questions] == ["C1", "T1"]
    revision = revise_task(
        original,
        questions,
        ClarificationInput(
            answers={
                "Q-1": "apply_discount(100, 100, 0.1) returns 90.",
                "Q-2": "Only the threshold rule; rounding is out of scope.",
            }
        ),
    )
    revised = revision.task
    assert revised.acceptance_criteria[0].verification_hint == (
        "apply_discount(100, 100, 0.1) returns 90."
    )
    assert [(item.text, item.source) for item in revised.requirements] == [
        ("Only the threshold rule; rounding is out of scope.", "clarification")
    ]
    assert revision.added_requirements == (revised.requirements[0].requirement_id,)
    assert task_digest(revised) != task_digest(original)
    assert not assess_intent(revised)


def test_explicit_changes_take_precedence_over_answers() -> None:
    original = vague_task()
    questions = assess_intent(original)
    revision = revise_task(
        original,
        questions,
        ClarificationInput(
            answers={"Q-1": "Replaced below.", "Q-2": "Split in two requirements."},
            replace_criteria=(criterion("Returns 90 for a subtotal of 100.", criterion_id="AC-1"),),
            add_criteria=(criterion("Returns 99 for a subtotal of 99.", criterion_id="AC-2"),),
            add_requirements=(
                Requirement(requirement_id="req_a", text="Discount.", source="clarification"),
            ),
        ),
    )
    revised = revision.task
    assert [item.criterion_id for item in revised.acceptance_criteria] == ["AC-1", "AC-2"]
    assert revised.acceptance_criteria[0].text == "Returns 90 for a subtotal of 100."
    assert revised.acceptance_criteria[0].verification_hint is None
    assert [item.requirement_id for item in revised.requirements] == ["req_a"]
    assert revision.replaced_criteria == ("AC-1",)
    assert revision.added_criteria == ("AC-2",)


def test_an_answer_extends_an_existing_hint() -> None:
    original = task(criterion("It works.", hint="Somehow.", criterion_id="AC-1"))
    revised = revise_task(
        original, assess_intent(original), ClarificationInput(answers={"Q-1": "Returns 1."})
    ).task
    assert revised.acceptance_criteria[0].verification_hint == "Somehow.\nReturns 1."


@pytest.mark.parametrize(
    ("clarification", "message"),
    [
        (ClarificationInput(answers={"Q-9": "x"}), "unknown question id"),
        (ClarificationInput(answers={"Q-1": "   "}), "empty answer"),
        (ClarificationInput(answers={}), "at least one"),
        (
            ClarificationInput(
                answers={"Q-1": "x"}, replace_criteria=(criterion("Returns 1.", criterion_id="X"),)
            ),
            "unknown criterion",
        ),
        (
            ClarificationInput(
                answers={"Q-1": "x"}, add_criteria=(criterion("Returns 1.", criterion_id="AC-1"),)
            ),
            "already exists",
        ),
    ],
)
def test_invalid_answers_are_rejected(clarification: ClarificationInput, message: str) -> None:
    original = vague_task()
    with pytest.raises(ConfigurationError, match=message):
        revise_task(original, assess_intent(original), clarification)
