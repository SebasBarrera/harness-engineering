"""Rule C0: INTENT elicits the acceptance criteria of a task that has none (#33)."""

from __future__ import annotations

from governed_harness.domain.models import AcceptanceCriterion, Requirement, Task
from governed_harness.intake import (
    C0_TARGETS,
    OUT_OF_SCOPE_PREFIX,
    QUESTION_TEMPLATES,
    ClarificationInput,
    assess_intent,
    no_acceptance_criteria,
    revise_task,
    scope_without_breakdown,
    task_digest,
)


def pending_task(
    *,
    title: str = "Rides",
    intent: str = "Build a ride-sharing backend.",
    requirements: tuple[Requirement, ...] = (),
) -> Task:
    return Task(
        task_id="task_rides",
        project_id="project_1",
        title=title,
        intent=intent,
        requirements=requirements,
        criteria_pending=True,
        acceptance_criteria=(),
    )


# ----- questions -------------------------------------------------------------------------


def test_c0_asks_seven_separate_questions_in_a_fixed_order() -> None:
    task = pending_task()
    assert no_acceptance_criteria(task)
    questions = assess_intent(task)
    assert [(q.question_id, q.rule_id, q.target) for q in questions] == [
        (f"Q-{index}", "C0", target) for index, target in enumerate(C0_TARGETS, start=1)
    ]
    assert C0_TARGETS == (
        "task:results",
        "task:interface",
        "task:limits",
        "task:errors",
        "task:scope",
        "task:out-of-scope",
        "task:non-functional",
    )
    texts = [question.text for question in questions]
    assert all("'Rides'" in text for text in texts)
    assert len(set(texts)) == 7
    results, interface, limits, errors, scope, out_of_scope, non_functional = texts
    assert "observable results" in results
    assert "input or action -> exact expected result" in results
    assert "inputs and outputs" in interface and "formats" in interface
    assert "limits and boundaries" in limits and "numbers, sizes, times" in limits
    assert "errors or rejections" in errors and "invalid input" in errors
    assert "behaviours are in scope" in scope and "per line" in scope
    assert "out of scope" in out_of_scope and "per line" in out_of_scope
    assert "performance, security" in non_functional and "persistence" in non_functional
    assert "measure" in non_functional


def test_c0_texts_come_from_the_templates() -> None:
    questions = assess_intent(pending_task())
    assert [question.text for question in questions] == [
        QUESTION_TEMPLATES[f"C0{letter}"].format(title="Rides") for letter in "abcdefg"
    ]


def test_c0_is_deterministic() -> None:
    assert assess_intent(pending_task()) == assess_intent(pending_task())


def test_c0_subsumes_t1() -> None:
    """A short intent without requirements would meet T1, but C0 already asks for the
    behaviours in scope and what is out of scope: they are asked once."""
    task = pending_task()
    assert not scope_without_breakdown(task)
    questions = assess_intent(task)
    assert {question.rule_id for question in questions} == {"C0"}
    assert [q.target for q in questions if "out of scope" in q.text] == ["task:out-of-scope"]


def test_c0_does_not_depend_on_the_intent_or_requirements() -> None:
    task = pending_task(
        intent=" ".join(["word"] * 40),
        requirements=(Requirement(requirement_id="req_1", text="Match riders and drivers."),),
    )
    assert [question.rule_id for question in assess_intent(task)] == ["C0"] * 7


def test_c0_never_fires_for_a_task_with_criteria() -> None:
    task = Task(
        task_id="task_1",
        project_id="project_1",
        title="Discount",
        intent="Add a discount.",
        acceptance_criteria=(
            AcceptanceCriterion(
                criterion_id="AC-1", text="apply_discount(100, 100, 0.1) returns 90."
            ),
        ),
    )
    assert not no_acceptance_criteria(task)
    assert not assess_intent(task)


def test_c0_shortens_a_long_title() -> None:
    (first, *_) = assess_intent(pending_task(title="ride " * 60))
    assert first.text.count("ride") < 30
    assert "..." in first.text


# ----- revision ------------------------------------------------------------------------------

ALL_ANSWERS = {
    "Q-1": (
        "- POST /rides with a valid body returns 201 and the ride id.\n"
        "\n"
        "2. GET /rides/{id} for an unknown id returns 404.\n"
    ),
    "Q-2": "JSON bodies with pickup and dropoff coordinates; responses in JSON.",
    "Q-3": "At most 4 passengers per ride.",
    "Q-4": "A request without pickup returns 422.",
    "Q-5": "Request a ride\n* Cancel a ride\n",
    "Q-6": "Payments\nDriver ratings",
    "Q-7": "p95 latency under 300 ms for 50 concurrent requests.",
}


def revise(clarification: ClarificationInput) -> tuple[Task, Task]:
    original = pending_task()
    return original, revise_task(original, assess_intent(original), clarification).task


def test_the_answer_about_results_becomes_criteria_one_per_line() -> None:
    original = pending_task()
    revision = revise_task(original, assess_intent(original), ClarificationInput(ALL_ANSWERS))
    revised = revision.task
    assert [item.text for item in revised.acceptance_criteria] == [
        "POST /rides with a valid body returns 201 and the ride id.",
        "GET /rides/{id} for an unknown id returns 404.",
    ]
    assert all(item.priority == "MUST" for item in revised.acceptance_criteria)
    assert revision.added_criteria == tuple(
        item.criterion_id for item in revised.acceptance_criteria
    )
    assert revised.criteria_pending is False
    assert "criteriaPending" not in revised.model_dump(by_alias=True)
    assert [(item.text, item.source) for item in revised.requirements] == [
        ("JSON bodies with pickup and dropoff coordinates; responses in JSON.", "clarification"),
        ("At most 4 passengers per ride.", "clarification"),
        ("A request without pickup returns 422.", "clarification"),
        ("Request a ride", "clarification"),
        ("Cancel a ride", "clarification"),
        ("p95 latency under 300 ms for 50 concurrent requests.", "clarification"),
    ]
    assert revision.added_requirements == tuple(
        item.requirement_id for item in revised.requirements
    )
    assert revised.constraints == (
        f"{OUT_OF_SCOPE_PREFIX}Payments",
        f"{OUT_OF_SCOPE_PREFIX}Driver ratings",
    )
    assert task_digest(revised) != task_digest(original)
    assert not assess_intent(revised)


def test_added_criteria_replace_the_answer_about_results() -> None:
    _, revised = revise(
        ClarificationInput(
            answers={"Q-1": "See the criteria below.", "Q-2": "JSON in and out."},
            add_criteria=(
                AcceptanceCriterion(criterion_id="AC-1", text="POST /rides returns 201."),
            ),
        )
    )
    assert [item.criterion_id for item in revised.acceptance_criteria] == ["AC-1"]
    assert revised.criteria_pending is False
    # Requirements still come from the other answers: the file adds no requirement.
    assert [item.text for item in revised.requirements] == ["JSON in and out."]


def test_added_requirements_replace_the_answers_that_would_become_requirements() -> None:
    _, revised = revise(
        ClarificationInput(
            answers={
                "Q-1": "POST /rides returns 201.",
                "Q-2": "JSON in and out.",
                "Q-5": "Request a ride",
                "Q-6": "Payments",
            },
            add_requirements=(
                Requirement(
                    requirement_id="req_a", text="Riders request rides.", source="clarification"
                ),
            ),
        )
    )
    assert [item.text for item in revised.acceptance_criteria] == ["POST /rides returns 201."]
    assert [item.requirement_id for item in revised.requirements] == ["req_a"]
    assert revised.constraints == (f"{OUT_OF_SCOPE_PREFIX}Payments",)


def test_without_criteria_the_revision_stays_pending_and_c0_asks_again() -> None:
    original, revised = revise(
        ClarificationInput(answers={"Q-2": "JSON in and out.", "Q-6": "Payments"})
    )
    assert revised.acceptance_criteria == ()
    assert revised.criteria_pending is True
    assert [item.text for item in revised.requirements] == ["JSON in and out."]
    assert task_digest(revised) != task_digest(original)
    assert [question.rule_id for question in assess_intent(revised)] == ["C0"] * 7


def test_an_answer_about_results_with_only_list_markers_adds_no_criterion() -> None:
    original, revised = revise(ClarificationInput(answers={"Q-1": "-\n* \n1."}))
    assert revised.acceptance_criteria == ()
    assert revised.criteria_pending is True
    assert task_digest(revised) == task_digest(original)
    assert [question.rule_id for question in assess_intent(revised)] == ["C0"] * 7
