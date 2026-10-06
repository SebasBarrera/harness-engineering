"""The converging agent review of a task in INTENT (#79).

In the 2.0.0 pilot the agent review raised new questions on every answered revision, so a run
under a small model never left INTENT. Under the object form of ``intake.ambiguityReview`` the
review converges:

* the ``clarify`` request carries every question already asked about the task and the answer it
  got (``previousQuestions``), the round number and the limits, and asks only for blocking
  ambiguity that the latest revision introduced or left open;
* a question already asked (same rule and normalised text) is dropped, also within a round, and
  a round asks at most ``maxQuestions``;
* after ``maxRounds`` rounds a person answered, the points the agent still raises are recorded,
  under ``onExhausted: assume``, as explicit assumptions in a new task revision
  (``metadata.assumptions``), where the operational contract, the decision brief and the gate
  contract show them; under ``block`` they are asked again and INTENT stays blocked.

A round is one clarification a person answered in this run that included agent questions
(rule ``A1``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.agent_results import AmbiguityReviewConfig
from governed_harness.domain.models import (
    ClarificationRecord,
    ClarificationRequest,
    Execution,
    Task,
)
from governed_harness.intake import task_digest

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

AGENT_RULE = "A1"
ASSUMPTIONS_KEY = "assumptions"
MAX_HISTORY = 80
_HISTORY_TEXT = 500
_WORD = re.compile(r"[^\W_]+")
"""A run of letters or digits, in any script."""

CONVERGE_SUFFIX = (
    "This is round {round} of at most {maxRounds} rounds of questions. previousQuestions lists "
    "every question already asked about this task and the answer it got (null when nobody "
    "answered it). Never ask again about a point a previous answer settles, in any wording. "
    "Ask only about blocking ambiguity that the latest revision introduced or left open: a "
    "point on which two reasonable implementations would behave differently in a way an "
    "acceptance test can observe. Do not ask about style, preferences or details the "
    "implementer can decide. Ask at most {maxQuestions} questions, the most blocking first; "
    "ask nothing when the task can be implemented and verified as it stands."
)

ASSUMPTION_TEXT = (
    "Not answered after {rounds} round(s) of clarification: the implementation takes the "
    "reading most consistent with the requirements and acceptance criteria and states the "
    "choice in its summary."
)

Item = tuple[str, str, str]
"""``(category, target, text)`` of an agent question."""


def normalized(text: str) -> str:
    """The words of a question in lower case, without punctuation or spacing differences."""
    return " ".join(_WORD.findall(text.lower()))


@dataclass(frozen=True)
class Convergence:
    """What the converging review knows before a round: the settings, the questions asked about
    the task before this revision with their answers, and the rounds already answered."""

    settings: AmbiguityReviewConfig
    history: tuple[dict[str, Any], ...]
    seen: frozenset[tuple[str, str]]
    rounds: int

    @property
    def round(self) -> int:
        return self.rounds + 1

    @property
    def exhausted(self) -> bool:
        return self.rounds >= self.settings.rounds

    def payload(self) -> dict[str, Any]:
        return {
            "previousQuestions": list(self.history[-MAX_HISTORY:]),
            "round": self.round,
            "maxRounds": self.settings.rounds,
            "maxQuestions": self.settings.questions,
        }

    def suffix(self) -> str:
        return CONVERGE_SUFFIX.format(
            round=self.round,
            maxRounds=self.settings.rounds,
            maxQuestions=self.settings.questions,
        )

    def converge(self, items: list[Item]) -> tuple[list[Item], int]:
        """The questions of a round without those already asked (same rule and normalised
        text, also within the round), at most ``maxQuestions``; and how many were dropped."""
        seen = set(self.seen)
        kept: list[Item] = []
        for item in items:
            key = (AGENT_RULE, normalized(item[2]))
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        bounded = kept[: self.settings.questions]
        return bounded, len(items) - len(bounded)


def _trimmed(text: str | None) -> str | None:
    return None if text is None else text[:_HISTORY_TEXT]


def convergence(
    results: ResultsHost, execution: Execution, task: Task, settings: AmbiguityReviewConfig
) -> Convergence:
    """The history of the task's questions and the rounds of this run (see the module)."""
    state = results.s.state
    digest = task_digest(task)
    records = [
        item
        for item in state.list("clarification", ClarificationRecord, project_id=task.project_id)
        if item.task_id == task.task_id
    ]
    answered = {
        (record.request_id, answer.question_id): answer.answer
        for record in records
        for answer in record.answers
    }
    requests = sorted(
        (
            item
            for item in state.list(
                "clarification_request", ClarificationRequest, project_id=task.project_id
            )
            if item.task_id == task.task_id and item.task_digest != digest
        ),
        key=lambda item: item.created_at,
    )
    history = tuple(
        {
            "ruleId": question.rule_id,
            "category": question.category,
            "target": question.target,
            "question": _trimmed(question.text),
            "answer": _trimmed(answered.get((request.request_id, question.question_id))),
        }
        for request in requests
        for question in request.questions
    )
    seen = frozenset((str(item["ruleId"]), normalized(str(item["question"]))) for item in history)
    rounds = sum(
        1
        for record in records
        if record.execution_id == execution.execution_id
        and any(answer.rule_id == AGENT_RULE for answer in record.answers)
    )
    return Convergence(settings, history, seen, rounds)


def assumptions_of(task: Task) -> list[dict[str, Any]]:
    """The assumptions a task revision records (``metadata.assumptions``)."""
    value = task.metadata.get(ASSUMPTIONS_KEY)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def with_assumptions(
    task: Task, items: list[Item], rounds: int
) -> tuple[Task, list[dict[str, Any]]]:
    """The task revision that records ``items`` as assumptions (those it does not record
    yet), and the new assumptions."""
    existing = assumptions_of(task)
    known = {normalized(str(item.get("question") or "")) for item in existing}
    added: list[dict[str, Any]] = []
    for category, target, text in items:
        if normalized(text) in known:
            continue
        known.add(normalized(text))
        added.append(
            {
                "assumptionId": f"AS-{len(existing) + len(added) + 1}",
                "category": category,
                "target": target,
                "question": text,
                "assumption": ASSUMPTION_TEXT.format(rounds=rounds),
                "rounds": rounds,
            }
        )
    if not added:
        return task, []
    data = task.model_dump(mode="json")
    data["metadata"] = {**task.metadata, ASSUMPTIONS_KEY: [*existing, *added]}
    return Task.model_validate(data), added


__all__ = [
    "AGENT_RULE",
    "ASSUMPTIONS_KEY",
    "ASSUMPTION_TEXT",
    "CONVERGE_SUFFIX",
    "Convergence",
    "assumptions_of",
    "convergence",
    "normalized",
    "with_assumptions",
]
