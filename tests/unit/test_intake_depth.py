"""intake.ambiguityReview.depth: adaptive: more questions and rounds, the basics first, the
poorer the request is; without the key nothing changes."""

from __future__ import annotations

from governed_harness.configuration.agent_results import AmbiguityReviewConfig
from governed_harness.domain.models import AcceptanceCriterion, Requirement, Task
from governed_harness.intake import ClarificationInput, assess_intent, revise_task, task_digest
from governed_harness.intake.depth import (
    BASIC_TOPICS,
    DEPTH_KEY,
    MAX_QUESTIONS,
    MAX_ROUNDS,
    TOPICS_KEY,
    depth_plan,
    poverty,
)

ONE_LINE = "haz una app como Uber con Uber Eats, viajes y domicilios de comida"
PARAGRAPH = (
    "Construye en Python el backend de una plataforma como Uber y Uber Eats. Pasajeros, "
    "conductores, repartidores, restaurantes y administradores tienen cuentas con contraseñas "
    "seguras. Los conductores registran vehículos y se conectan; los pasajeros obtienen una "
    "cotización con precio dinámico, piden un viaje y se les asigna un conductor cercano. Los "
    "restaurantes tienen menús; los pasajeros piden comida y un repartidor la entrega."
)
REQUIREMENTS = [
    "R1. quote(distance_km) returns 2500 + 1200 per km, rounded half up.",
    "R2. request_ride() assigns the nearest online driver within 5 km.",
    "R3. cancel_ride() after 120 seconds charges a fee of 3000.",
    "R4. A tip is between 0 and 50 percent of the fare.",
    "R5. place_order() rejects an order when the restaurant is closed.",
    "R6. Earnings are paid every Monday at 08:00.",
]


def one_line() -> Task:
    return Task(
        task_id="task_1",
        project_id="p",
        title="Plataforma",
        intent=ONE_LINE,
        criteria_pending=True,
        acceptance_criteria=(),
    )


def paragraph() -> Task:
    return Task(
        task_id="task_1",
        project_id="p",
        title="Plataforma",
        intent=PARAGRAPH,
        acceptance_criteria=(AcceptanceCriterion(criterion_id="AC-1", text="Funciona."),),
    )


def with_requirements() -> Task:
    return Task(
        task_id="task_1",
        project_id="p",
        title="Plataforma",
        intent=PARAGRAPH,
        requirements=tuple(
            Requirement(requirement_id=f"R{i}", text=text) for i, text in enumerate(REQUIREMENTS, 1)
        ),
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="AC-1", text="quote(10) returns 14500."),
            AcceptanceCriterion(criterion_id="AC-2", text="python -m pytest -q passes."),
            AcceptanceCriterion(criterion_id="AC-3", text="cancel_ride() at 121 s charges 3000."),
        ),
    )


def first_round(task: Task, language: str = "es") -> tuple:
    plan = depth_plan(task, 3, 8)
    return assess_intent(task, language=language, basic_topics=plan.next_topics(()))


# ----- the measure and the plan ------------------------------------------------------------
def test_the_measure_orders_the_three_requests() -> None:
    levels = [poverty(task).level for task in (one_line(), paragraph(), with_requirements())]
    assert levels == ["very-poor", "poor", "partial"]
    assert poverty(one_line()).score == 100


def test_a_poorer_request_gets_more_rounds_and_more_questions_per_round() -> None:
    plans = [depth_plan(task, 3, 8) for task in (one_line(), paragraph(), with_requirements())]
    assert [plan.rounds for plan in plans] == [6, 4, 3]
    assert [plan.questions for plan in plans] == [12, 10, 8]


def test_the_plan_stays_within_its_caps() -> None:
    plan = depth_plan(one_line(), 7, 15)
    assert (plan.rounds, plan.questions) == (MAX_ROUNDS, MAX_QUESTIONS)
    # A configured value over the cap is kept, never lowered.
    assert depth_plan(one_line(), 10, 20).rounds == 10


def test_the_measure_is_frozen_in_the_metadata() -> None:
    task = with_requirements().model_copy(
        update={"metadata": {DEPTH_KEY: {"level": "very-poor", "score": 100}}}
    )
    assert depth_plan(task, 3, 8).rounds == 6


# ----- the deterministic questions -----------------------------------------------------------
def test_a_one_line_request_asks_more_than_a_paragraph_and_this_more_than_requirements() -> None:
    counts = [len(first_round(task)) for task in (one_line(), paragraph(), with_requirements())]
    assert counts[0] > counts[1] > counts[2]


def test_the_first_questions_are_the_basics_in_order() -> None:
    questions = first_round(one_line())
    assert [q.target for q in questions[:3]] == ["task:users", "task:actions", "task:flows"]
    assert all(q.category == "basics" and q.rule_id == "C0" for q in questions[:3])
    assert questions[0].text.startswith("¿Quiénes usan")
    # Then the seven C0 questions, unchanged.
    assert [q.target for q in questions[3:]][0] == "task:results"
    assert [q.question_id for q in questions] == [f"Q-{i}" for i in range(1, len(questions) + 1)]


def test_a_paragraph_with_criteria_asks_five_basics_under_t1() -> None:
    questions = first_round(paragraph())
    basics = [q for q in questions if q.category == "basics"]
    assert [q.target.removeprefix("task:") for q in basics] == list(BASIC_TOPICS[:5])
    assert {q.rule_id for q in basics} == {"T1"}
    assert questions[: len(basics)] == tuple(basics)


def test_the_basics_follow_the_language() -> None:
    questions = first_round(one_line(), language="en")
    assert questions[0].text.startswith("Who uses")


def test_without_topics_the_questions_are_the_earlier_ones() -> None:
    for task in (one_line(), paragraph(), with_requirements()):
        assert assess_intent(task, language="es", basic_topics=()) == assess_intent(
            task, language="es"
        )
    assert [q.rule_id for q in assess_intent(one_line())] == ["C0"] * 7
    assert all(q.category is None for q in assess_intent(one_line()))


# ----- answers ---------------------------------------------------------------------------------
def test_answers_to_the_basics_become_requirements_and_move_to_the_next_topics() -> None:
    task = one_line()
    questions = first_round(task)
    answers = {"Q-1": "pasajeros y conductores", "Q-2": "piden y aceptan viajes"}
    revised = revise_task(task, questions, ClarificationInput(answers=answers)).task
    assert [item.text for item in revised.requirements] == [
        "Usuarios y roles: pasajeros y conductores",
        "Qué hace cada usuario: piden y aceptan viajes",
    ]
    assert revised.metadata[TOPICS_KEY] == ["users", "actions"]
    assert revised.metadata[DEPTH_KEY] == {"level": "very-poor", "score": 100}
    plan = depth_plan(revised, 3, 8)
    assert plan.level == "very-poor"
    assert plan.next_topics(("users", "actions")) == ("flows", "platform", "data")


def test_answers_without_basics_keep_the_metadata() -> None:
    task = one_line()
    questions = assess_intent(task, language="es")
    revised = revise_task(task, questions, ClarificationInput(answers={"Q-1": "Lista 3 viajes."}))
    assert revised.task.metadata == {}
    assert task_digest(revised.task) != task_digest(task)


# ----- the key ---------------------------------------------------------------------------------
def test_without_the_key_the_configuration_serialises_as_before() -> None:
    settings = AmbiguityReviewConfig.model_validate({"mode": "agent", "maxRounds": 3})
    assert not settings.adaptive
    assert "depth" not in settings.model_dump(by_alias=True)
    assert AmbiguityReviewConfig.model_validate({"depth": "adaptive"}).adaptive
