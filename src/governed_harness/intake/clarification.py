"""Deterministic assessment of a task's intent before any work starts.

INTENT checks that a task is structurally complete; this module checks that its acceptance
criteria say something that can be observed. It uses fixed vocabularies and patterns, no
language model, so the same task always yields the same questions with the same ids.

The rules are deliberately conservative: a criterion is questioned only when nothing in it
(or in its verification hint) can be checked, so a false question is rarer than a missed one.

* ``C1`` no observable result: fewer than four words, or only vague vocabulary, and no anchor.
* ``C2`` quality without a measure: a quality word such as "fast" or "secure" and no number.
* ``C3`` duplicate: the same criterion text (and hint) as an earlier criterion.
* ``T1`` scope without breakdown: an intent under 25 words, no requirements and a single
  criterion that has no anchor.

An anchor is a digit, quoted or back-quoted text, a code identifier (``name()``, a
``snake_case`` name, a path, a file name, a ``CamelCase`` name such as ``ValueError``) or a
checkable result verb (returns, raises, rejects, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    AcceptanceCriterion,
    ClarificationQuestion,
    ClarificationRule,
    Requirement,
    Task,
)
from governed_harness.evidence import sha256_json

QUESTION_TEMPLATES: dict[ClarificationRule, str] = {
    "C1": (
        "Criterion {target} ('{text}') does not say what can be observed. For which input or "
        "action, and what exact result shows that it holds?"
    ),
    "C2": (
        "Criterion {target} ('{text}') asks for a quality ('{term}') without a measure. What "
        "number or threshold decides whether it holds (for example a time, a size or a rate)?"
    ),
    "C3": (
        "Criterion {target} ('{text}') repeats criterion {other}. What different result should "
        "it check, or which criterion should replace it?"
    ),
    "T1": (
        "The intent ('{text}') is short, has no requirements and a single acceptance criterion "
        "with nothing concrete to check. Which separate behaviours must the task deliver, and "
        "what is out of scope?"
    ),
}
"""English question templates, one per rule. The only place where question texts live."""

TASK_TARGET = "task"
"""Target of a question about the task as a whole rather than one criterion."""

MIN_CRITERION_WORDS = 4
MIN_INTENT_WORDS = 25
_EXCERPT = 120

_VAGUE_PHRASES = ("as expected", "user-friendly", "user friendly")
_VAGUE_WORDS = frozenset(
    {
        "work",
        "works",
        "worked",
        "working",
        "correctly",
        "properly",
        "good",
        "fine",
        "nice",
        "clean",
        "robust",
        "well",
    }
)
# Words that carry no observable content of their own: articles, auxiliaries, connectors
# and the generic nouns people use for "the thing being built".
_FILLER_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "everything",
        "all",
        "should",
        "must",
        "will",
        "shall",
        "can",
        "be",
        "is",
        "are",
        "was",
        "were",
        "been",
        "being",
        "has",
        "have",
        "and",
        "or",
        "but",
        "also",
        "just",
        "very",
        "really",
        "so",
        "still",
        "always",
        "now",
        "too",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "ok",
        "okay",
        "feature",
        "code",
        "system",
        "application",
        "app",
        "solution",
        "implementation",
        "functionality",
        "program",
        "software",
        "change",
        "changes",
        "thing",
        "things",
        "behaviour",
        "behavior",
        "result",
        "results",
        "output",
        "overall",
    }
)
_RESULT_VERBS = frozenset(
    {
        "return",
        "returns",
        "returned",
        "raise",
        "raises",
        "raised",
        "reject",
        "rejects",
        "rejected",
        "accept",
        "accepts",
        "accepted",
        "equal",
        "equals",
        "contain",
        "contains",
        "contained",
        "list",
        "lists",
        "listed",
        "store",
        "stores",
        "stored",
        "print",
        "prints",
        "printed",
        "exit",
        "exits",
        "exited",
        "respond",
        "responds",
        "responded",
        "create",
        "creates",
        "created",
        "delete",
        "deletes",
        "deleted",
        "match",
        "matches",
        "matched",
        "pass",
        "passes",
        "passed",
        "fail",
        "fails",
        "failed",
        "within",
        "before",
        "after",
    }
)
_RESULT_PHRASES = ("at most", "at least")
_QUALITY_WORDS = {
    "fast": "fast",
    "faster": "fast",
    "quick": "quick",
    "quickly": "quick",
    "performant": "performant",
    "efficient": "efficient",
    "efficiently": "efficient",
    "scalable": "scalable",
    "secure": "secure",
    "securely": "secure",
    "reliable": "reliable",
    "reliably": "reliable",
    "responsive": "responsive",
}
_NUMBER_WORDS = frozenset(
    {
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "twenty",
        "thirty",
        "fifty",
        "hundred",
        "thousand",
        "million",
        "half",
        "twice",
    }
)
_FILE_EXTENSIONS = (
    "py|pyi|js|mjs|cjs|ts|tsx|jsx|json|yaml|yml|toml|ini|cfg|md|rst|txt|csv|html|css|sh|sql|"
    "xml|java|kt|go|rs|rb|php|cs|c|h|cpp|lock|env|log"
)

_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]*")
_DIGIT = re.compile(r"\d")
_QUOTED = re.compile(r"`[^`]+`|\"[^\"]+\"|“[^”]+”|‘[^’]+’|(?<![A-Za-z])'[^']+'(?![A-Za-z])")
_CALL = re.compile(r"[A-Za-z_][\w.]*\(")
_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")
_PATH = re.compile(r"(?:^|[\s(])\.{0,2}/?[\w.-]+/[\w./-]+")
_FILE = re.compile(rf"\b[\w-]+\.(?:{_FILE_EXTENSIONS})\b")
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b")
_TRAILING = re.compile(r"[\s.!?;:,]+$")
_SPACE = re.compile(r"\s+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _has_anchor(text: str) -> bool:
    if (
        _DIGIT.search(text)
        or _QUOTED.search(text)
        or _CALL.search(text)
        or _SNAKE.search(text)
        or _PATH.search(text)
        or _FILE.search(text)
        or _CAMEL.search(text)
    ):
        return True
    lowered = _SPACE.sub(" ", text.lower())
    if any(phrase in lowered for phrase in _RESULT_PHRASES):
        return True
    return any(word in _RESULT_VERBS for word in _words(text))


def _only_vague(text: str) -> bool:
    """True when the text holds at least one vague term and nothing else but filler."""
    lowered = _SPACE.sub(" ", text.lower())
    found_phrase = False
    for phrase in _VAGUE_PHRASES:
        if phrase in lowered:
            found_phrase = True
            lowered = lowered.replace(phrase, " ")
    words = _words(lowered)
    vague = [word for word in words if word in _VAGUE_WORDS]
    rest = [word for word in words if word not in _VAGUE_WORDS and word not in _FILLER_WORDS]
    return (found_phrase or bool(vague)) and not rest


def _has_number(text: str) -> bool:
    return bool(_DIGIT.search(text)) or any(word in _NUMBER_WORDS for word in _words(text))


def _statement(criterion: AcceptanceCriterion) -> str:
    """What a criterion says can be observed: its text and, if any, its verification hint."""
    if criterion.verification_hint and criterion.verification_hint.strip():
        return f"{criterion.text}\n{criterion.verification_hint}"
    return criterion.text


def _normalise(text: str) -> str:
    return _TRAILING.sub("", _SPACE.sub(" ", text.casefold()).strip())


def _excerpt(text: str) -> str:
    flat = _SPACE.sub(" ", text).strip()
    return flat if len(flat) <= _EXCERPT else flat[: _EXCERPT - 3].rstrip() + "..."


def no_observable_result(criterion: AcceptanceCriterion) -> bool:
    """Rule C1."""
    statement = _statement(criterion)
    if _has_anchor(statement):
        return False
    return len(_words(statement)) < MIN_CRITERION_WORDS or _only_vague(statement)


def unmeasured_quality(criterion: AcceptanceCriterion) -> str | None:
    """Rule C2: the first quality word of a criterion without a number, or ``None``."""
    statement = _statement(criterion)
    if _has_number(statement):
        return None
    for word in _words(statement):
        if word in _QUALITY_WORDS:
            return _QUALITY_WORDS[word]
    return None


def scope_without_breakdown(task: Task) -> bool:
    """Rule T1: a short intent, no requirements and a single criterion with no anchor.

    A precise single criterion (one with a number, a quoted value, a code identifier or a
    result verb) already bounds a small task, so it is not questioned."""
    if len(task.intent.split()) >= MIN_INTENT_WORDS or task.requirements:
        return False
    if len(task.acceptance_criteria) != 1:
        return False
    return not _has_anchor(_statement(task.acceptance_criteria[0]))


def assess_intent(task: Task) -> tuple[ClarificationQuestion, ...]:
    """Return the clarification questions for a task, in a stable order.

    Criteria are examined in their order (C2, then C1 unless C2 already applies, then C3),
    followed by the task-level rule T1. Question ids are ``Q-1``, ``Q-2``, ... in that order.
    """
    findings: list[tuple[ClarificationRule, str, str]] = []
    seen: dict[str, str] = {}
    for criterion in task.acceptance_criteria:
        values = {"target": criterion.criterion_id, "text": _excerpt(criterion.text)}
        term = unmeasured_quality(criterion)
        if term is not None:
            findings.append(
                ("C2", criterion.criterion_id, QUESTION_TEMPLATES["C2"].format(**values, term=term))
            )
        elif no_observable_result(criterion):
            findings.append(
                ("C1", criterion.criterion_id, QUESTION_TEMPLATES["C1"].format(**values))
            )
        key = _normalise(criterion.text) + "\n" + _normalise(criterion.verification_hint or "")
        if key in seen:
            findings.append(
                (
                    "C3",
                    criterion.criterion_id,
                    QUESTION_TEMPLATES["C3"].format(**values, other=seen[key]),
                )
            )
        else:
            seen[key] = criterion.criterion_id
    if scope_without_breakdown(task):
        findings.append(
            ("T1", TASK_TARGET, QUESTION_TEMPLATES["T1"].format(text=_excerpt(task.intent)))
        )
    return tuple(
        ClarificationQuestion(question_id=f"Q-{index}", rule_id=rule, target=target, text=text)
        for index, (rule, target, text) in enumerate(findings, start=1)
    )


def task_digest(task: Task) -> str:
    """Digest of a task revision, as stored."""
    return sha256_json(task.model_dump(mode="json", by_alias=True))


@dataclass(frozen=True)
class ClarificationInput:
    """The content of an answers file: answers by question id and optional task changes."""

    answers: dict[str, str]
    replace_criteria: tuple[AcceptanceCriterion, ...] = ()
    add_criteria: tuple[AcceptanceCriterion, ...] = ()
    add_requirements: tuple[Requirement, ...] = ()


@dataclass(frozen=True)
class TaskRevision:
    task: Task
    replaced_criteria: tuple[str, ...]
    added_criteria: tuple[str, ...]
    added_requirements: tuple[str, ...]


def revise_task(
    task: Task, questions: tuple[ClarificationQuestion, ...], clarification: ClarificationInput
) -> TaskRevision:
    """Apply a person's answers to a task and return the new revision.

    Explicit changes are applied first: replaced criteria (by id), added criteria and added
    requirements. An answer then lands in the task where it belongs, so that the revised task
    carries it and the next assessment sees it:

    * an answer about a criterion that was not replaced becomes (or extends) its
      verification hint;
    * an answer about the task, when the file adds no requirement or criterion, becomes a
      requirement with source ``clarification``.
    """
    by_id = {question.question_id: question for question in questions}
    unknown = sorted(set(clarification.answers) - set(by_id))
    if unknown:
        raise ConfigurationError(f"unknown question id(s): {', '.join(unknown)}")
    if not clarification.answers:
        raise ConfigurationError("the answers file must answer at least one question")
    blank = sorted(qid for qid, text in clarification.answers.items() if not text.strip())
    if blank:
        raise ConfigurationError(f"empty answer(s): {', '.join(blank)}")

    criteria = list(task.acceptance_criteria)
    positions = {criterion.criterion_id: index for index, criterion in enumerate(criteria)}
    replaced: list[str] = []
    for replacement in clarification.replace_criteria:
        index = positions.get(replacement.criterion_id)
        if index is None:
            raise ConfigurationError(
                f"cannot replace unknown criterion: {replacement.criterion_id}"
            )
        if replacement.criterion_id in replaced:
            raise ConfigurationError(f"criterion replaced twice: {replacement.criterion_id}")
        criteria[index] = replacement
        replaced.append(replacement.criterion_id)
    added_criteria: list[str] = []
    for criterion in clarification.add_criteria:
        if criterion.criterion_id in positions or criterion.criterion_id in added_criteria:
            raise ConfigurationError(f"criterion id already exists: {criterion.criterion_id}")
        criteria.append(criterion)
        added_criteria.append(criterion.criterion_id)
    requirements = list(task.requirements)
    existing_requirements = {item.requirement_id for item in requirements}
    added_requirements: list[str] = []
    for requirement in clarification.add_requirements:
        if requirement.requirement_id in existing_requirements:
            raise ConfigurationError(f"requirement id already exists: {requirement.requirement_id}")
        requirements.append(requirement)
        existing_requirements.add(requirement.requirement_id)
        added_requirements.append(requirement.requirement_id)

    for question_id in sorted(clarification.answers, key=_question_order):
        question = by_id[question_id]
        answer = clarification.answers[question_id].strip()
        if question.target == TASK_TARGET:
            if clarification.add_requirements or clarification.add_criteria:
                continue
            requirement = Requirement(
                requirement_id=new_id("req"), text=answer, source="clarification"
            )
            requirements.append(requirement)
            added_requirements.append(requirement.requirement_id)
            continue
        if question.target in replaced:
            continue
        index = positions.get(question.target)
        if index is None:
            continue
        current = criteria[index]
        hint = (
            f"{current.verification_hint.strip()}\n{answer}"
            if current.verification_hint and current.verification_hint.strip()
            else answer
        )
        criteria[index] = current.model_copy(update={"verification_hint": hint})

    try:
        revised = Task.model_validate(
            {
                **task.model_dump(by_alias=True),
                "requirements": [item.model_dump(by_alias=True) for item in requirements],
                "acceptanceCriteria": [item.model_dump(by_alias=True) for item in criteria],
            }
        )
    except Exception as error:
        raise ConfigurationError(f"invalid revised task: {error}") from error
    return TaskRevision(
        task=revised,
        replaced_criteria=tuple(replaced),
        added_criteria=tuple(added_criteria),
        added_requirements=tuple(added_requirements),
    )


def _question_order(question_id: str) -> int:
    return int(question_id.removeprefix("Q-"))
