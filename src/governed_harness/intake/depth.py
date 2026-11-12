"""Adaptive depth of the INTENT questions (``intake.ambiguityReview.depth: adaptive``).

A poor request is never refused: the harness asks, and asks more the poorer the request is,
starting with the basics. This module measures, without a language model, how little a task
says, and turns that measure into a plan: how many rounds of questions and how many questions
per round INTENT may ask, and which fundamental topics the first rounds cover.

The measure (``poverty``) adds four parts, 0 to 100 in total:

* the words of the intent: under 25 words 35 points, under 80 words 20, under 200 words 10;
* the observable acceptance criteria (those rule ``C1`` does not question): none 25, one or
  two 15, three to five 5;
* the requirements: none 25, one to four 15, five to fourteen 5;
* the entities, distinct concrete anchors in the intent and the requirements (numbers, quoted
  text, code identifiers, paths and file names): under three 15, under eight 8.

A score of 90 or more is ``very-poor``, 60 or more ``poor``, 30 or more ``partial``, anything
else ``detailed``. The plan starts from the configured ``maxRounds`` and ``maxQuestions``
(defaults 3 and 8): ``very-poor`` adds three rounds and four questions per round, ``poor`` one
round and two questions, ``partial`` and ``detailed`` keep the configured values. The plan never
goes over ``MAX_ROUNDS`` rounds and ``MAX_QUESTIONS`` questions per round (or the configured
value when it is larger).

The fundamental topics (``BASIC_TOPICS``) come first, in a fixed order: users, what they do,
main flows, platform and stack, data, business rules, limits and edge cases, observable
acceptance criteria, and priorities and scope of the first version. A ``very-poor`` request
covers them three per round, a ``poor`` one five per round; for these two levels the
deterministic intake asks one question per topic (category ``basics``, target
``task:<topic>``) before its other questions. A ``partial`` request passes all of them to the
agent review in its first round; a ``detailed`` one none.

The level is measured on the task as the person wrote it: the first answered revision freezes
it in ``metadata.intakeDepth``, so the answers that enrich the task do not shrink the plan in
the middle of the clarification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from governed_harness.domain.models import Task

DepthLevel = Literal["very-poor", "poor", "partial", "detailed"]

DEPTH_KEY = "intakeDepth"
"""Metadata key of the frozen measure of a task (``level`` and ``score``)."""
TOPICS_KEY = "intakeTopics"
"""Metadata key of the fundamental topics a person already answered."""
BASICS_CATEGORY = "basics"
"""Category of a deterministic question about a fundamental topic."""
TOPIC_TARGET_PREFIX = "task:"

MAX_ROUNDS = 8
MAX_QUESTIONS = 16

BASIC_TOPICS: tuple[str, ...] = (
    "users",
    "actions",
    "flows",
    "platform",
    "data",
    "rules",
    "edge-cases",
    "acceptance",
    "first-version",
)
"""The fundamental topics, in the order they are asked."""

BASIC_TARGETS: tuple[str, ...] = tuple(TOPIC_TARGET_PREFIX + topic for topic in BASIC_TOPICS)

TOPIC_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "users": "Users and roles",
        "actions": "What each user does",
        "flows": "Main flows",
        "platform": "Platform and stack",
        "data": "Data",
        "rules": "Business rules",
        "edge-cases": "Limits and edge cases",
        "acceptance": "Observable acceptance criteria",
        "first-version": "Priorities and scope of the first version",
    },
    "es": {
        "users": "Usuarios y roles",
        "actions": "Qué hace cada usuario",
        "flows": "Flujos principales",
        "platform": "Plataforma y stack",
        "data": "Datos",
        "rules": "Reglas de negocio",
        "edge-cases": "Límites y casos borde",
        "acceptance": "Criterios de aceptación observables",
        "first-version": "Prioridades y alcance de la primera versión",
    },
}
"""Labels of the topics; an answer becomes a requirement prefixed with its topic's label."""

TOPIC_QUESTIONS: dict[str, dict[str, str]] = {
    "en": {
        "users": (
            "Who uses '{title}'? List each kind of user or role and what distinguishes them."
        ),
        "actions": (
            "What does each kind of user do with '{title}'? One action per line, with the user "
            "who does it."
        ),
        "flows": (
            "Which are the main flows of '{title}', step by step from start to end (for example "
            "sign up, order, pay, deliver)?"
        ),
        "platform": (
            "On which platform and stack must '{title}' run (language, framework, interface such "
            "as a library, a CLI, a web API or an app, storage)?"
        ),
        "data": (
            "Which data does '{title}' keep, with which fields, and which states does each "
            "record go through?"
        ),
        "rules": (
            "Which business rules apply in '{title}' (prices, fees, permissions, assignments, "
            "cancellations), with their exact numbers?"
        ),
        "edge-cases": (
            "Which limits and edge cases must '{title}' handle, and what happens exactly in each "
            "one (empty input, nothing available, a value at the limit, a repeated action)?"
        ),
        "acceptance": (
            "How will you check that '{title}' is done? Give observable results, one per line "
            "(input or action -> exact expected result)."
        ),
        "first-version": (
            "What must the first version of '{title}' include, in order of priority, and what is "
            "left for later?"
        ),
    },
    "es": {
        "users": (
            "¿Quiénes usan '{title}'? Enumera cada tipo de usuario o rol y qué lo distingue."
        ),
        "actions": (
            "¿Qué hace cada tipo de usuario con '{title}'? Una acción por línea, con el usuario "
            "que la hace."
        ),
        "flows": (
            "¿Cuáles son los flujos principales de '{title}', paso a paso de principio a fin "
            "(por ejemplo registrarse, pedir, pagar, entregar)?"
        ),
        "platform": (
            "¿En qué plataforma y stack debe funcionar '{title}' (lenguaje, framework, interfaz "
            "como biblioteca, CLI, API web o app, almacenamiento)?"
        ),
        "data": (
            "¿Qué datos guarda '{title}', con qué campos, y por qué estados pasa cada registro?"
        ),
        "rules": (
            "¿Qué reglas de negocio aplican en '{title}' (precios, comisiones, permisos, "
            "asignaciones, cancelaciones), con sus números exactos?"
        ),
        "edge-cases": (
            "¿Qué límites y casos borde debe manejar '{title}' y qué pasa exactamente en cada uno "
            "(entrada vacía, nada disponible, un valor en el límite, una acción repetida)?"
        ),
        "acceptance": (
            "¿Cómo vas a comprobar que '{title}' está terminado? Da resultados observables, uno "
            "por línea (entrada o acción -> resultado exacto esperado)."
        ),
        "first-version": (
            "¿Qué debe incluir la primera versión de '{title}', en orden de prioridad, y qué "
            "queda para después?"
        ),
    },
}
"""Question texts of the topics, by language."""

_EXTRA: dict[DepthLevel, tuple[int, int, int]] = {
    "very-poor": (3, 4, 3),
    "poor": (1, 2, 5),
    "partial": (0, 0, len(BASIC_TOPICS)),
    "detailed": (0, 0, 0),
}
"""Rounds added, questions per round added and topics per round, by level."""

_ANCHORS = (
    re.compile(r"\d+(?:[.,]\d+)?"),
    re.compile(r"`[^`]+`|\"[^\"]+\"|“[^”]+”"),
    re.compile(r"[A-Za-z_][\w.]*\("),
    re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b"),  # NOSONAR(S6353)
    re.compile(r"\b[\w-]+\.[a-z]{1,5}\b"),
    re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b"),
)


@dataclass(frozen=True)
class Poverty:
    """How little a task says (see the module)."""

    words: int
    criteria: int
    requirements: int
    entities: int
    score: int
    level: DepthLevel

    def as_dict(self) -> dict[str, Any]:
        return {
            "words": self.words,
            "criteria": self.criteria,
            "requirements": self.requirements,
            "entities": self.entities,
            "score": self.score,
            "level": self.level,
        }


@dataclass(frozen=True)
class DepthPlan:
    """The rounds and questions INTENT may ask about a task, and its topics per round."""

    level: DepthLevel
    score: int
    rounds: int
    questions: int
    topics_per_round: int

    @property
    def deterministic_basics(self) -> bool:
        """Whether the deterministic intake asks the topics (``very-poor`` and ``poor``)."""
        return self.level in ("very-poor", "poor")

    def round_topics(self, round_number: int) -> tuple[str, ...]:
        """The topics the agent review covers in round ``round_number`` (1-based)."""
        size = self.topics_per_round
        if size <= 0 or round_number < 1:
            return ()
        start = (round_number - 1) * size
        return BASIC_TOPICS[start : start + size]

    def next_topics(self, covered: tuple[str, ...]) -> tuple[str, ...]:
        """The first topics nobody answered yet, as many as one round asks."""
        if not self.deterministic_basics:
            return ()
        return tuple(topic for topic in BASIC_TOPICS if topic not in covered)[
            : self.topics_per_round
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": self.score,
            "rounds": self.rounds,
            "questions": self.questions,
            "topicsPerRound": self.topics_per_round,
        }


def _entities(task: Task) -> int:
    text = "\n".join([task.intent, *(item.text for item in task.requirements)])
    found: set[str] = set()
    for pattern in _ANCHORS:
        found.update(match.group().lower() for match in pattern.finditer(text))
    return len(found)


def _points(value: int, steps: tuple[tuple[int, int], ...]) -> int:
    for limit, points in steps:
        if value < limit:
            return points
    return 0


def _level(score: int) -> DepthLevel:
    if score >= 90:
        return "very-poor"
    if score >= 60:
        return "poor"
    if score >= 30:
        return "partial"
    return "detailed"


def poverty(task: Task) -> Poverty:
    """The measure of a task as it stands (see the module). Criteria are read with the Spanish
    vocabularies, which include the English ones, so the measure does not depend on
    ``intake.language``."""
    from .clarification import no_observable_result

    words = len(task.intent.split())
    criteria = sum(
        1
        for criterion in task.acceptance_criteria
        if not no_observable_result(criterion, language="es")
    )
    requirements = len(task.requirements)
    entities = _entities(task)
    score = (
        _points(words, ((25, 35), (80, 20), (200, 10)))
        + _points(criteria, ((1, 25), (3, 15), (6, 5)))
        + _points(requirements, ((1, 25), (5, 15), (15, 5)))
        + _points(entities, ((3, 15), (8, 8)))
    )
    return Poverty(words, criteria, requirements, entities, score, _level(score))


def frozen_level(task: Task) -> tuple[DepthLevel, int] | None:
    """The level and score ``metadata.intakeDepth`` froze, if any."""
    value = task.metadata.get(DEPTH_KEY)
    if not isinstance(value, dict):
        return None
    level, score = value.get("level"), value.get("score")
    if level in _EXTRA and isinstance(score, int):
        return level, score
    return None


def covered_topics(task: Task) -> tuple[str, ...]:
    """The fundamental topics a person answered (``metadata.intakeTopics``)."""
    value = task.metadata.get(TOPICS_KEY)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if item in BASIC_TOPICS)


def depth_plan(task: Task, base_rounds: int, base_questions: int) -> DepthPlan:
    """The plan of a task from its frozen level, or from its measure when nothing froze it."""
    frozen = frozen_level(task)
    if frozen is None:
        measured = poverty(task)
        level, score = measured.level, measured.score
    else:
        level, score = frozen
    rounds_added, questions_added, per_round = _EXTRA[level]
    rounds = min(base_rounds + rounds_added, max(base_rounds, MAX_ROUNDS))
    questions = min(base_questions + questions_added, max(base_questions, MAX_QUESTIONS))
    return DepthPlan(level, score, rounds, questions, per_round)


def topic_language(language: str | None) -> str:
    return "es" if language == "es" else "en"


def topic_of(target: str) -> str | None:
    """The topic of a ``task:<topic>`` target, or ``None``."""
    topic = target.removeprefix(TOPIC_TARGET_PREFIX)
    return topic if target.startswith(TOPIC_TARGET_PREFIX) and topic in BASIC_TOPICS else None


__all__ = [
    "BASICS_CATEGORY",
    "BASIC_TARGETS",
    "BASIC_TOPICS",
    "DEPTH_KEY",
    "MAX_QUESTIONS",
    "MAX_ROUNDS",
    "TOPICS_KEY",
    "TOPIC_LABELS",
    "TOPIC_QUESTIONS",
    "DepthLevel",
    "DepthPlan",
    "Poverty",
    "covered_topics",
    "depth_plan",
    "frozen_level",
    "poverty",
    "topic_language",
    "topic_of",
]
