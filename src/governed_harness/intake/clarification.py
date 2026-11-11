"""Deterministic assessment of a task's intent before any work starts.

INTENT checks that a task is structurally complete; this module checks that its acceptance
criteria say something that can be observed. It uses fixed vocabularies and patterns, no
language model, so the same task always yields the same questions with the same ids.

The rules are deliberately conservative: a criterion is questioned only when nothing in it
(or in its verification hint) can be checked, so a false question is rarer than a missed one.

* ``C0`` no acceptance criteria: a task accepted without criteria (``criteria_pending``, only
  under the ``enforce`` policy) gets one question per thing its criteria must settle, in a
  fixed order: results and how each is checked, inputs and outputs, limits, errors, behaviours
  in scope, what is out of scope and non-functional constraints. ``C0`` subsumes ``T1``.
* ``C1`` no observable result: fewer than four words, or only vague vocabulary, and no anchor.
* ``C2`` quality without a measure: a quality word such as "fast" or "secure" and no number.
* ``C3`` duplicate: the same criterion text (and hint) as an earlier criterion.
* ``T1`` scope without breakdown: an intent under 25 words, no requirements and a single
  criterion that has no anchor.

An anchor is a digit, quoted or back-quoted text, a code identifier (``name()``, a
``snake_case`` name, a path, a file name, a ``CamelCase`` name such as ``ValueError``) or a
checkable result verb (returns, raises, rejects, ...).

Since 2.0, ``intake.language`` (``auto``, ``en`` or ``es``, see :mod:`.language`) makes the
words Unicode tokens, adds the Spanish vocabularies under ``es`` and asks the questions in
Spanish. Without the key (``language=None``) the rules are the ones above, unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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

from . import language as lang

QUESTION_TEMPLATES: dict[str, str] = {
    "C0a": (
        "Task '{title}' has no acceptance criteria. Which observable results show that it is "
        "done, and how is each one checked (input or action -> exact expected result)? One "
        "result per line."
    ),
    "C0b": (
        "What are the main inputs and outputs of task '{title}', and in which formats (for "
        "example JSON fields, a CSV header or a function signature)?"
    ),
    "C0c": (
        "Which limits and boundaries must task '{title}' respect (numbers, sizes, times), and "
        "what happens exactly at each boundary?"
    ),
    "C0d": (
        "Which errors or rejections should task '{title}' produce for invalid input, and what "
        "exactly does each one return, raise or print?"
    ),
    "C0e": "Which behaviours are in scope for task '{title}'? One behaviour per line.",
    "C0f": "What is explicitly out of scope for task '{title}'? One item per line.",
    "C0g": (
        "Which non-functional constraints apply to task '{title}' (performance, security, "
        "persistence), and with which measure is each one checked?"
    ),
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
"""English question templates, one per rule and, for ``C0``, one per elicitation question
(``C0a`` to ``C0g``). The only place where question texts live."""

TASK_TARGET = "task"
"""Target of a question about the task as a whole rather than one criterion."""

C0_TARGETS: tuple[str, ...] = (
    "task:results",
    "task:interface",
    "task:limits",
    "task:errors",
    "task:scope",
    "task:out-of-scope",
    "task:non-functional",
)
"""Targets of the ``C0`` questions, in the order they are asked (templates ``C0a`` to ``C0g``).

Each names the part of the task its answer lands in when the task is revised."""

OUT_OF_SCOPE_PREFIX = "Out of scope: "
"""Prefix of the constraints made from the answer about what is out of scope."""

_C0_TEMPLATE_KEYS = tuple(f"C0{letter}" for letter in "abcdefg")

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
_QUOTED = (
    re.compile(r"`[^`]+`|\"[^\"]+\"|“[^”]+”"),
    re.compile(r"‘[^’]+’|(?<![A-Za-z])'[^']+'(?![A-Za-z])"),
)
_CALL = re.compile(r"[A-Za-z_][\w.]*\(")
# Explicit ASCII classes: \w would also match non-ASCII letters, and re.ASCII would change \b.
_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")  # NOSONAR(S6353)
_PATH = re.compile(r"(?:^|[\s(])\.{0,2}/?[\w.-]+/[\w./-]+")
_FILE = re.compile(rf"\b[\w-]+\.(?:{_FILE_EXTENSIONS})\b")
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]++(?:[A-Z][a-z0-9]*+)++\b")
# What _normalise strips from the end once whitespace runs are single spaces.
_TRAILING_CHARS = " .!?;:,"
_SPACE = re.compile(r"\s+")


def _ascii_words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _lowered(text: str) -> str:
    return _SPACE.sub(" ", text.lower())


def _folded(text: str) -> str:
    return _SPACE.sub(" ", lang.fold(text))


@dataclass(frozen=True)
class _Lexicon:
    """The tokenizer, vocabularies and question texts of one intake language."""

    words: Callable[[str], list[str]]
    phrase_text: Callable[[str], str]
    vague_phrases: tuple[str, ...]
    vague_words: frozenset[str]
    filler_words: frozenset[str]
    result_verbs: frozenset[str]
    result_phrases: tuple[str, ...]
    quality_words: dict[str, str]
    number_words: frozenset[str]
    templates: dict[str, str]


_LEGACY = _Lexicon(
    words=_ascii_words,
    phrase_text=_lowered,
    vague_phrases=_VAGUE_PHRASES,
    vague_words=_VAGUE_WORDS,
    filler_words=_FILLER_WORDS,
    result_verbs=_RESULT_VERBS,
    result_phrases=_RESULT_PHRASES,
    quality_words=_QUALITY_WORDS,
    number_words=_NUMBER_WORDS,
    templates=QUESTION_TEMPLATES,
)
_ENGLISH = _Lexicon(
    words=lang.unicode_words,
    phrase_text=_folded,
    vague_phrases=_VAGUE_PHRASES,
    vague_words=_VAGUE_WORDS,
    filler_words=_FILLER_WORDS,
    result_verbs=_RESULT_VERBS,
    result_phrases=_RESULT_PHRASES,
    quality_words=_QUALITY_WORDS,
    number_words=_NUMBER_WORDS,
    templates=QUESTION_TEMPLATES,
)
# Spanish tasks keep English code words ("returns", "fast"), so both vocabularies apply.
_SPANISH = _Lexicon(
    words=lang.unicode_words,
    phrase_text=_folded,
    vague_phrases=(*_VAGUE_PHRASES, *lang.VAGUE_PHRASES_ES),
    vague_words=_VAGUE_WORDS | lang.VAGUE_WORDS_ES,
    filler_words=_FILLER_WORDS | lang.FILLER_WORDS_ES,
    result_verbs=_RESULT_VERBS | lang.RESULT_VERBS_ES,
    result_phrases=(*_RESULT_PHRASES, *lang.RESULT_PHRASES_ES),
    quality_words={**_QUALITY_WORDS, **lang.QUALITY_WORDS_ES},
    number_words=_NUMBER_WORDS | lang.NUMBER_WORDS_ES,
    templates=lang.QUESTION_TEMPLATES_ES,
)


def task_text(task: Task) -> str:
    """The text language detection reads: title, intent, criteria and their hints."""
    parts = [task.title, task.intent]
    for criterion in task.acceptance_criteria:
        parts.append(criterion.text)
        if criterion.verification_hint:
            parts.append(criterion.verification_hint)
    return "\n".join(parts)


def resolve_language(task: Task, setting: str | None) -> lang.Language | None:
    """The language of a task under ``intake.language``: ``None`` without the key, the
    detected one under ``auto``, otherwise the configured one."""
    if setting is None:
        return None
    if setting == "auto":
        return lang.detect_language(task_text(task))
    if setting == "es":
        return "es"
    if setting == "en":
        return "en"
    raise ConfigurationError(f"unknown intake language: {setting!r}")


def _lexicon(language: str | None) -> _Lexicon:
    if language is None:
        return _LEGACY
    if language == "es":
        return _SPANISH
    if language == "en":
        return _ENGLISH
    raise ConfigurationError(f"unknown intake language: {language!r}")


def _has_anchor(text: str, lexicon: _Lexicon = _LEGACY) -> bool:
    if (
        _DIGIT.search(text)
        or any(pattern.search(text) for pattern in _QUOTED)
        or _CALL.search(text)
        or _SNAKE.search(text)
        or _PATH.search(text)
        or _FILE.search(text)
        or _CAMEL.search(text)
    ):
        return True
    lowered = lexicon.phrase_text(text)
    if any(phrase in lowered for phrase in lexicon.result_phrases):
        return True
    return any(word in lexicon.result_verbs for word in lexicon.words(text))


def _only_vague(text: str, lexicon: _Lexicon = _LEGACY) -> bool:
    """True when the text holds at least one vague term and nothing else but filler."""
    lowered = lexicon.phrase_text(text)
    found_phrase = False
    for phrase in lexicon.vague_phrases:
        if phrase in lowered:
            found_phrase = True
            lowered = lowered.replace(phrase, " ")
    words = lexicon.words(lowered)
    vague = [word for word in words if word in lexicon.vague_words]
    rest = [
        word
        for word in words
        if word not in lexicon.vague_words and word not in lexicon.filler_words
    ]
    return (found_phrase or bool(vague)) and not rest


def _has_number(text: str, lexicon: _Lexicon = _LEGACY) -> bool:
    return bool(_DIGIT.search(text)) or any(
        word in lexicon.number_words for word in lexicon.words(text)
    )


def _statement(criterion: AcceptanceCriterion) -> str:
    """What a criterion says can be observed: its text and, if any, its verification hint."""
    if criterion.verification_hint and criterion.verification_hint.strip():
        return f"{criterion.text}\n{criterion.verification_hint}"
    return criterion.text


def _normalise(text: str) -> str:
    return _SPACE.sub(" ", text.casefold()).strip().rstrip(_TRAILING_CHARS)


def _excerpt(text: str) -> str:
    flat = _SPACE.sub(" ", text).strip()
    return flat if len(flat) <= _EXCERPT else flat[: _EXCERPT - 3].rstrip() + "..."


def no_observable_result(criterion: AcceptanceCriterion, *, language: str | None = None) -> bool:
    """Rule C1 (``language``: a resolved intake language, ``None`` for the original rule)."""
    lexicon = _lexicon(language)
    statement = _statement(criterion)
    if _has_anchor(statement, lexicon):
        return False
    return len(lexicon.words(statement)) < MIN_CRITERION_WORDS or _only_vague(statement, lexicon)


def unmeasured_quality(
    criterion: AcceptanceCriterion, *, language: str | None = None
) -> str | None:
    """Rule C2: the first quality word of a criterion without a number, or ``None``."""
    lexicon = _lexicon(language)
    statement = _statement(criterion)
    if _has_number(statement, lexicon):
        return None
    for word in lexicon.words(statement):
        if word in lexicon.quality_words:
            return lexicon.quality_words[word]
    return None


def scope_without_breakdown(task: Task, *, language: str | None = None) -> bool:
    """Rule T1: a short intent, no requirements and a single criterion with no anchor.

    A precise single criterion (one with a number, a quoted value, a code identifier or a
    result verb) already bounds a small task, so it is not questioned."""
    if len(task.intent.split()) >= MIN_INTENT_WORDS or task.requirements:
        return False
    if len(task.acceptance_criteria) != 1:
        return False
    return not _has_anchor(_statement(task.acceptance_criteria[0]), _lexicon(language))


def no_acceptance_criteria(task: Task) -> bool:
    """Rule C0."""
    return not task.acceptance_criteria


def assess_intent(task: Task, *, language: str | None = None) -> tuple[ClarificationQuestion, ...]:
    """Return the clarification questions for a task, in a stable order.

    A task without acceptance criteria gets the seven ``C0`` questions and nothing else: no
    criterion can be examined and ``C0`` already asks what ``T1`` would (the behaviours in
    scope and what is out of scope). Otherwise criteria are examined in their order (C2, then
    C1 unless C2 already applies, then C3), followed by the task-level rule T1. Question ids
    are ``Q-1``, ``Q-2``, ... in that order.

    ``language`` is the value of ``intake.language`` (``auto``, ``en``, ``es``) or ``None``
    without the key; it changes vocabularies and question texts, never ids, rules or targets.
    """
    resolved = resolve_language(task, language)
    templates = _lexicon(resolved).templates
    findings: list[tuple[ClarificationRule, str, str]] = []
    if no_acceptance_criteria(task):
        title = _excerpt(task.title)
        findings = [
            ("C0", target, templates[key].format(title=title))
            for key, target in zip(_C0_TEMPLATE_KEYS, C0_TARGETS, strict=True)
        ]
    seen: dict[str, str] = {}
    for criterion in task.acceptance_criteria:
        values = {"target": criterion.criterion_id, "text": _excerpt(criterion.text)}
        term = unmeasured_quality(criterion, language=resolved)
        if term is not None:
            findings.append(
                ("C2", criterion.criterion_id, templates["C2"].format(**values, term=term))
            )
        elif no_observable_result(criterion, language=resolved):
            findings.append(("C1", criterion.criterion_id, templates["C1"].format(**values)))
        key = _normalise(criterion.text) + "\n" + _normalise(criterion.verification_hint or "")
        if key in seen:
            findings.append(
                (
                    "C3",
                    criterion.criterion_id,
                    templates["C3"].format(**values, other=seen[key]),
                )
            )
        else:
            seen[key] = criterion.criterion_id
    if scope_without_breakdown(task, language=resolved):
        findings.append(("T1", TASK_TARGET, templates["T1"].format(text=_excerpt(task.intent))))
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
    contract: dict[str, Any] | None = None
    """Answers to the operational-contract section of the request (since #55), by item."""
    confirm_contract: bool = False
    """The person confirms the contract summary the request showed (since #55)."""


@dataclass(frozen=True)
class TaskRevision:
    task: Task
    replaced_criteria: tuple[str, ...]
    added_criteria: tuple[str, ...]
    added_requirements: tuple[str, ...]


@dataclass
class _Draft:
    """The revision being built: criteria, requirements and constraints, with what changed."""

    criteria: list[AcceptanceCriterion]
    positions: dict[str, int]
    requirements: list[Requirement]
    constraints: list[str]
    replaced: list[str] = field(default_factory=list)
    added_criteria: list[str] = field(default_factory=list)
    added_requirements: list[str] = field(default_factory=list)

    def add_criterion(self, criterion: AcceptanceCriterion) -> None:
        self.criteria.append(criterion)
        self.added_criteria.append(criterion.criterion_id)

    def add_requirement(self, requirement: Requirement) -> None:
        self.requirements.append(requirement)
        self.added_requirements.append(requirement.requirement_id)


def _check_answers(
    by_id: dict[str, ClarificationQuestion], clarification: ClarificationInput
) -> None:
    unknown = sorted(set(clarification.answers) - set(by_id))
    if unknown:
        raise ConfigurationError(f"unknown question id(s): {', '.join(unknown)}")
    if not clarification.answers and not clarification.contract:
        raise ConfigurationError("the answers file must answer at least one question")
    blank = sorted(qid for qid, text in clarification.answers.items() if not text.strip())
    if blank:
        raise ConfigurationError(f"empty answer(s): {', '.join(blank)}")


def _replace_criteria(draft: _Draft, clarification: ClarificationInput) -> None:
    for replacement in clarification.replace_criteria:
        index = draft.positions.get(replacement.criterion_id)
        if index is None:
            raise ConfigurationError(
                f"cannot replace unknown criterion: {replacement.criterion_id}"
            )
        if replacement.criterion_id in draft.replaced:
            raise ConfigurationError(f"criterion replaced twice: {replacement.criterion_id}")
        draft.criteria[index] = replacement
        draft.replaced.append(replacement.criterion_id)


def _add_criteria(draft: _Draft, clarification: ClarificationInput) -> None:
    for criterion in clarification.add_criteria:
        known = criterion.criterion_id in draft.positions
        if known or criterion.criterion_id in draft.added_criteria:
            raise ConfigurationError(f"criterion id already exists: {criterion.criterion_id}")
        draft.add_criterion(criterion)


def _add_requirements(draft: _Draft, clarification: ClarificationInput) -> None:
    existing_requirements = {item.requirement_id for item in draft.requirements}
    for requirement in clarification.add_requirements:
        if requirement.requirement_id in existing_requirements:
            raise ConfigurationError(f"requirement id already exists: {requirement.requirement_id}")
        draft.requirements.append(requirement)
        existing_requirements.add(requirement.requirement_id)
        draft.added_requirements.append(requirement.requirement_id)


def _explicit_changes(task: Task, clarification: ClarificationInput) -> _Draft:
    """The task with the replaced criteria, added criteria and added requirements applied."""
    criteria = list(task.acceptance_criteria)
    draft = _Draft(
        criteria=criteria,
        positions={criterion.criterion_id: index for index, criterion in enumerate(criteria)},
        requirements=list(task.requirements),
        constraints=list(task.constraints),
    )
    _replace_criteria(draft, clarification)
    _add_criteria(draft, clarification)
    _add_requirements(draft, clarification)
    return draft


def _apply_c0_answer(
    draft: _Draft, question: ClarificationQuestion, answer: str, clarification: ClarificationInput
) -> None:
    """A ``C0`` answer lands by its target (see :func:`revise_task`)."""
    if question.target == "task:results":
        if not clarification.add_criteria:
            for line in _answer_lines(answer):
                draft.add_criterion(AcceptanceCriterion(criterion_id=new_id("ac"), text=line))
    elif question.target == "task:out-of-scope":
        draft.constraints.extend(OUT_OF_SCOPE_PREFIX + line for line in _answer_lines(answer))
    elif question.target in C0_TARGETS and not clarification.add_requirements:
        texts = _answer_lines(answer) if question.target == "task:scope" else [answer]
        for text in texts:
            draft.add_requirement(
                Requirement(requirement_id=new_id("req"), text=text, source="clarification")
            )


def _extend_hint(draft: _Draft, question: ClarificationQuestion, answer: str) -> None:
    """An answer about a criterion that was not replaced becomes (or extends) its hint."""
    if question.target in draft.replaced:
        return
    index = draft.positions.get(question.target)
    if index is None:
        return
    current = draft.criteria[index]
    hint = (
        f"{current.verification_hint.strip()}\n{answer}"
        if current.verification_hint and current.verification_hint.strip()
        else answer
    )
    draft.criteria[index] = current.model_copy(update={"verification_hint": hint})


def _apply_answer(
    draft: _Draft, question: ClarificationQuestion, answer: str, clarification: ClarificationInput
) -> None:
    """Land one answer in the task where it belongs (see :func:`revise_task`)."""
    if question.rule_id == "C0":
        _apply_c0_answer(draft, question, answer, clarification)
        return
    if question.rule_id == "P1":
        # A project setup answer (#56) is a decision about the project; the task keeps it
        # as a constraint so the agent sees it.
        part = question.target.removeprefix("project:")
        draft.constraints.append(f"Project setup ({part}): {' '.join(answer.split())}")
        return
    if question.rule_id == "A1" and question.category == "out-of-scope":
        # An agent's out-of-scope question is answered with what the task leaves out.
        draft.constraints.extend(OUT_OF_SCOPE_PREFIX + line for line in _answer_lines(answer))
        return
    if question.target == TASK_TARGET:
        if clarification.add_requirements or clarification.add_criteria:
            return
        draft.add_requirement(
            Requirement(requirement_id=new_id("req"), text=answer, source="clarification")
        )
        return
    _extend_hint(draft, question, answer)


def _revised_contract(task: Task, clarification: ClarificationInput) -> dict[str, Any]:
    if not clarification.contract:
        return {}
    # The operational-contract section (since #55): answers update the task's contract.
    return {
        **(task.contract.model_dump(mode="json", by_alias=True) if task.contract else {}),
        **{key: _contract_value(key, value) for key, value in clarification.contract.items()},
    }


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
      requirement with source ``clarification``;
    * an answer to a ``C0`` question lands by its target: ``task:results`` becomes acceptance
      criteria, one per non-empty line, unless the file adds criteria; ``task:scope`` becomes
      one requirement per non-empty line and ``task:interface``, ``task:limits``,
      ``task:errors`` and ``task:non-functional`` one requirement each, unless the file adds
      requirements; ``task:out-of-scope`` becomes one constraint per non-empty line, prefixed
      with ``Out of scope:``. A leading list marker (``-``, ``*``, ``+``, ``1.``) is removed.

    A revision that still has no criteria stays marked ``criteria_pending``, so INTENT asks
    the ``C0`` questions again; one that has criteria is no longer marked.
    """
    by_id = {question.question_id: question for question in questions}
    _check_answers(by_id, clarification)
    draft = _explicit_changes(task, clarification)
    for question_id in sorted(clarification.answers, key=_question_order):
        answer = clarification.answers[question_id].strip()
        _apply_answer(draft, by_id[question_id], answer, clarification)

    contract = _revised_contract(task, clarification)
    try:
        revised = Task.model_validate(
            {
                **task.model_dump(by_alias=True),
                "constraints": draft.constraints,
                "requirements": [item.model_dump(by_alias=True) for item in draft.requirements],
                "criteriaPending": not draft.criteria,
                "acceptanceCriteria": [item.model_dump(by_alias=True) for item in draft.criteria],
                **({"contract": contract} if contract else {}),
            }
        )
    except Exception as error:
        raise ConfigurationError(f"invalid revised task: {error}") from error
    return TaskRevision(
        task=revised,
        replaced_criteria=tuple(draft.replaced),
        added_criteria=tuple(draft.added_criteria),
        added_requirements=tuple(draft.added_requirements),
    )


_LIST_MARKER = re.compile(r"^(?:[-*+\u2022]|\d+[.)])(?:\s+|$)")

_CONTRACT_LISTS = frozenset({"examples", "scope", "scopePaths", "outOfScope", "definitionOfDone"})
_CONTRACT_FLAGS = frozenset({"push", "createPullRequest", "comment"})
_CONTRACT_KEYS = (
    _CONTRACT_LISTS
    | _CONTRACT_FLAGS
    | {
        "objective",
        "verificationLevel",
        "branch",
        "coverageThreshold",
    }
)


def _contract_value(key: str, value: Any) -> Any:
    """An answer to a contract item in the form the task's contract stores it: a list from a
    text answer, one item per line; a flag from yes or no."""
    if key not in _CONTRACT_KEYS:
        raise ConfigurationError(
            f"unknown contract item {key!r}; known: {', '.join(sorted(_CONTRACT_KEYS))}"
        )
    if key in _CONTRACT_LISTS and isinstance(value, str):
        return _answer_lines(value)
    if key in _CONTRACT_FLAGS and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"yes", "y", "true"}:
            return True
        if lowered in {"no", "n", "false"}:
            return False
        raise ConfigurationError(f"contract item {key} is yes or no, got {value!r}")
    return value


def _answer_lines(answer: str) -> list[str]:
    """The non-empty lines of an answer, without a leading list marker."""
    lines = (_LIST_MARKER.sub("", line.strip()).strip() for line in answer.splitlines())
    return [line for line in lines if line]


def _question_order(question_id: str) -> int:
    return int(question_id.removeprefix("Q-"))
