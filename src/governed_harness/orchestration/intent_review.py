"""Agent review of a task in INTENT (#37) and the check of a person's answers (#52, N9).

The deterministic rules (C0-C3, T1) run first. Under ``intake.ambiguityReview: agent`` the
harness then asks a provider (call kind ``clarify``) for ambiguity and completeness questions,
grouped by category. The review runs once per task revision, keyed by the task digest: resuming
a run or answering nothing new reuses the stored questions instead of calling again. An answer
of no questions is recorded as evidence. A malformed answer is a finding and blocks INTENT, as
does a provider failure: the questions may be what makes the task workable.

Under ``intake.validateAnswers`` the answers that produced the current revision are checked for
references the task and the workspace cannot resolve (a requirement id such as ``A1`` that
appears nowhere, a document such as ``SPEC.md`` that is not in the workspace); each one becomes
a question asking the person to attach the document or transcribe what it says. The agent review
of the revised task also receives the answers and checks them against the documents they cite.

Under the object form of ``intake.ambiguityReview`` (#79) the review converges and is bounded;
see :mod:`governed_harness.orchestration.intent_convergence`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.agents.requests import CLARIFY_CATEGORIES
from governed_harness.domain.enums import FindingSeverity, ResultStatus
from governed_harness.domain.models import (
    ClarificationQuestion,
    ClarificationRecord,
    Execution,
    PhaseExecution,
    Task,
)
from governed_harness.intake import resolve_language, task_digest
from governed_harness.intake.depth import TOPIC_LABELS, DepthPlan, depth_plan, topic_language
from governed_harness.orchestration.intent_convergence import (
    Convergence,
    Item,
    convergence,
    with_assumptions,
)
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

MALFORMED_RULE = "intake.agent-review-malformed"
MAX_AGENT_QUESTIONS = 40
_TEXT_LIMIT = 1000
_DOC_SUFFIXES = (".md", ".txt", ".rst", ".pdf", ".doc", ".docx", ".yaml", ".yml", ".json")
_PATH_RUN = re.compile(r"[\w/.-]+", re.I)
_DOC_EXTENSION = re.compile(r"(?:md|txt|rst|pdf|docx?|ya?ml|json)(?!\w)", re.I)
_ID_REFERENCE = re.compile(
    r"\b(?:per|see|as in|according to|following|by|from|under|in)\s+"
    r"(?:requirements?|rules?|sections?|criteri(?:on|a)|items?\s+)?\s*"
    r"([A-Z]{1,3}-?\d{1,3}(?:\.\d+)?)\b"
)
_LIST_REFERENCE = re.compile(r"\brequirements?\s+([A-Z]{1,3}\d{0,3})\s*(?:to|-|through)\s*", re.I)
_SEARCH_SUFFIXES = (".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".py", ".ts", ".js")
_MAX_SEARCH_BYTES = 2_000_000


CONVERGE_KEY = ":converge"
"""Suffix of the stored review of a revision under the converging review (#79)."""
LAST_ROUND_KEY = "agentclarifylast"
"""Since 2.0.1 (``skipSettled``): the digest and question count of the last review of a task
in a run."""
STOP_EVENT = "stop.condition"
AMBIGUITY_STOP = "unresolvable-ambiguity"

DEPTH_SUFFIX = (
    " The request is {level} (depth.level, measured from its words, criteria, requirements and "
    "entities): this review may take up to {maxRounds} rounds of at most {maxQuestions} "
    "questions. {topics}"
)
DEPTH_TOPICS = (
    "In this round cover first, in this order, the fundamental topics in depth.topics that the "
    "task and the previous answers leave open ({labels}), with concrete questions a product "
    "owner can answer, before any detail."
)
DEPTH_DETAILS = (
    "The fundamental topics were covered in earlier rounds: ask about the details that remain "
    "open (business rules with numbers, edge cases, errors, observable acceptance results)."
)


@dataclass(frozen=True)
class ReviewOutcome:
    questions: tuple[ClarificationQuestion, ...]
    evidence_refs: tuple[str, ...]
    blocked: str | None = None
    revised: Task | None = None
    """Since #79: the task revision that records the open points as assumptions."""


@dataclass(frozen=True)
class _Items:
    """The agent's questions about one revision, the evidence and why it blocked."""

    items: list[Item]
    refs: tuple[str, ...]
    blocked: str | None = None


def _questions_from(result: dict[str, Any], task: Task) -> list[tuple[str, str, str]]:
    """``(category, target, text)`` of every question in a ``clarify`` result; raises
    ``ValueError`` for a malformed result."""
    raw = result.get("questions")
    if not isinstance(raw, list):
        raise ValueError("the clarify result needs a 'questions' list")
    criteria = {item.criterion_id for item in task.acceptance_criteria}
    items: list[tuple[str, str, str]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"question {index} is not an object")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"question {index} has no text")
        category = str(entry.get("category") or "ambiguity").strip().lower()
        if category not in CLARIFY_CATEGORIES:
            category = "ambiguity"
        target = str(entry.get("target") or "task").strip()
        if target not in criteria:
            target = "task"
        items.append((category, target, " ".join(text.split())[:_TEXT_LIMIT]))
    # Grouped by category, in the order of CLARIFY_CATEGORIES, then in the agent's order.
    order = {name: position for position, name in enumerate(CLARIFY_CATEGORIES)}
    items = sorted(items, key=lambda item: order[item[0]])
    return items[:MAX_AGENT_QUESTIONS]


def _numbered(
    start: int, rule: str, items: list[tuple[str, str, str]]
) -> tuple[ClarificationQuestion, ...]:
    return tuple(
        ClarificationQuestion.model_validate(
            {
                "questionId": f"Q-{number}",
                "ruleId": rule,
                "target": target,
                "text": text,
                "category": category,
            }
        )
        for number, (category, target, text) in enumerate(items, start=start)
    )


class IntentReview:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def enabled(self) -> bool:
        intake = self.results.project.intake
        return bool(intake and intake.agent_review_enabled)

    @property
    def validates_answers(self) -> bool:
        intake = self.results.project.intake
        return bool(intake and intake.validate_answers)

    def questions(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        deterministic: tuple[ClarificationQuestion, ...],
        *,
        agent: bool = True,
    ) -> ReviewOutcome:
        """The questions the agent-results settings add after the deterministic ones.
        ``agent`` false (the fast lane of #58) leaves out the agent's ambiguity review. Under
        the converging review (#79) an exhausted review may revise the task instead
        (``revised``: its assumptions)."""
        refs: list[str] = []
        added: list[ClarificationQuestion] = []
        start = len(deterministic) + 1
        answers = self._current_answers(execution, task)
        if self.validates_answers and answers:
            dangling = self.dangling_references(task, answers)
            added.extend(_numbered(start, "A2", dangling))
            start += len(dangling)
        # intake.projectSetup (#56): architecture, testing strategy and standards, once per
        # project, for what neither the configuration nor the repository establishes.
        setup = self.results.project_setup.questions(execution, phase, start)
        added.extend(setup)
        start += len(setup)
        revised: Task | None = None
        if self.enabled and agent:
            outcome = self._agent_questions(execution, phase, task, deterministic, answers, start)
            refs.extend(outcome.evidence_refs)
            if outcome.blocked is not None:
                return ReviewOutcome(tuple(added), tuple(refs), outcome.blocked)
            added.extend(outcome.questions)
            revised = outcome.revised
        return ReviewOutcome(tuple(added), tuple(refs), revised=revised)

    # ----- agent review ----------------------------------------------------------------------
    def _agent_questions(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        deterministic: tuple[ClarificationQuestion, ...],
        answers: list[dict[str, str]],
        start: int,
    ) -> ReviewOutcome:
        intake = self.results.project.intake
        settings = intake.ambiguity_settings if intake else None
        plan = self._plan(task)
        if settings is not None and plan is not None:
            # depth: adaptive: the rounds and the questions of this task's plan.
            settings = settings.model_copy(
                update={"max_rounds": plan.rounds, "max_questions": plan.questions}
            )
        context = convergence(self.results, execution, task, settings) if settings else None
        fetched = self._review_items(execution, phase, task, deterministic, answers, context)
        if fetched.blocked is not None:
            return ReviewOutcome((), fetched.refs, fetched.blocked)
        items = fetched.items
        if context is None or not items or not context.exhausted:
            return ReviewOutcome(_numbered(start, "A1", items), fetched.refs)
        return self._exhausted(execution, phase, task, items, fetched.refs, context, start)

    def _review_items(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        deterministic: tuple[ClarificationQuestion, ...],
        answers: list[dict[str, str]],
        context: Convergence | None,
    ) -> _Items:
        """The agent's questions about this revision: stored once per task revision (and per
        review form), so a resumed run or a second run of the revision makes no call."""
        results = self.results
        digest = task_digest(task)
        key = f"agentclarify:{execution.project_id}:{digest}"
        if context is not None:
            key += CONVERGE_KEY
        cached = results.flag_json(key)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            items = [(str(a), str(b), str(c)) for a, b, c in cached["items"]]
            results.s.events.append(
                execution.execution_id,
                "intent.agent-review.reused",
                {"taskDigest": digest, "questions": len(items), "evidenceRef": cached["ref"]},
            )
            self._remember_round(execution, task, context, len(items))
            return _Items(items, (cached["ref"],))
        reason = self._settled(execution, task, context)
        if reason is not None and context is not None:
            ref = self._record_skip(execution, phase, task, reason, context)
            results.set_flag_json(key, {"items": [], "ref": ref})
            return _Items([], (ref,))
        fetched = self._call_review(execution, phase, task, deterministic, answers, context)
        if fetched.blocked is None:
            results.set_flag_json(
                key, {"items": [list(item) for item in fetched.items], "ref": fetched.refs[-1]}
            )
            self._remember_round(execution, task, context, len(fetched.items))
        return fetched

    # ----- a settled review (2.0.1, ``skipSettled``) -------------------------------------------
    @staticmethod
    def _skips(context: Convergence | None) -> bool:
        return context is not None and context.settings.skips_settled

    def _remember_round(
        self, execution: Execution, task: Task, context: Convergence | None, questions: int
    ) -> None:
        if not self._skips(context):
            return
        self.results.set_flag_json(
            f"{LAST_ROUND_KEY}:{execution.execution_id}:{task.task_id}",
            {"taskDigest": task_digest(task), "questions": questions},
        )

    def _settled(self, execution: Execution, task: Task, context: Convergence | None) -> str | None:
        """Why the review of this new revision is not called (``skipSettled``): the previous
        round of this run left no agent question, or the ``unresolvable-ambiguity`` stop
        condition was recorded in this run; ``None`` otherwise."""
        if not self._skips(context):
            return None
        stopped = any(
            item.event_type == STOP_EVENT and item.payload.get("condition") == AMBIGUITY_STOP
            for item in self.results.s.events.list(execution.execution_id)
        )
        if stopped:
            return AMBIGUITY_STOP
        last = self.results.flag_json(f"{LAST_ROUND_KEY}:{execution.execution_id}:{task.task_id}")
        if (
            isinstance(last, dict)
            and last.get("questions") == 0
            and last.get("taskDigest") != task_digest(task)
        ):
            return "no-new-questions"
        return None

    def _record_skip(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        reason: str,
        context: Convergence,
    ) -> str:
        """Evidence and event of a review that was not called: the run goes on as after an
        exhausted review with no open point (``onExhausted`` has nothing to ask or assume)."""
        results = self.results
        record = {
            "taskDigest": task_digest(task),
            "reason": reason,
            "rounds": context.rounds,
            "maxRounds": context.settings.rounds,
            "onExhausted": context.settings.exhausted,
            "questions": [],
        }
        ref = results.record_json(
            execution,
            phase.phase_id,
            record,
            kind="agent-clarify-skipped",
            summary=f"Agent review of the task not called again ({reason})",
        )
        results.s.events.append(
            execution.execution_id,
            "intent.agent-review.skipped",
            {**record, "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        return ref

    def _call_review(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        deterministic: tuple[ClarificationQuestion, ...],
        answers: list[dict[str, str]],
        context: Convergence | None,
    ) -> _Items:
        results = self.results
        payload: dict[str, Any] = {
            "deterministicQuestions": [
                item.model_dump(mode="json", by_alias=True) for item in deterministic
            ],
            "categories": list(CLARIFY_CATEGORIES),
        }
        if answers:
            payload["clarifications"] = answers
        suffix = ""
        if context is not None:
            payload.update(context.payload())
            suffix = context.suffix()
            plan = self._plan(task)
            if plan is not None:
                depth, depth_suffix = _depth_request(plan, context, self._language(task))
                payload["depth"] = depth
                suffix += depth_suffix
        outcome = results.call_agent(
            execution,
            phase,
            "clarify",
            payload,
            task=task,
            instructions_suffix=suffix,
            validate=lambda result: _questions_from(result, task),
        )
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return _Items(
                [],
                outcome.evidence_refs,
                f"Agent review of the task did not answer ({outcome.status}): {outcome.summary}",
            )
        try:
            items = _questions_from(outcome.result, task)
        except ValueError as error:
            results.record_finding(
                execution,
                validator_id="intake.agent-review",
                rule_id=MALFORMED_RULE,
                category="intent-clarification",
                severity=FindingSeverity.HIGH,
                message=f"The agent review of the task returned a malformed result: {error}",
                evidence_refs=outcome.evidence_refs,
                recommendation="Fix the provider's clarify answer and continue the run.",
            )
            return _Items(
                [], outcome.evidence_refs, f"Agent review of the task was malformed: {error}"
            )
        dropped = 0
        if context is not None:
            items, dropped = context.converge(items)
        ref = self._record_review(execution, phase, task, outcome.invocation_id, items, dropped)
        return _Items(items, (*outcome.evidence_refs, ref))

    def _record_review(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        invocation_id: str | None,
        items: list[Item],
        dropped: int,
    ) -> str:
        results = self.results
        digest = task_digest(task)
        record: dict[str, Any] = {
            "taskDigest": digest,
            "invocationId": invocation_id,
            "questions": [
                {"category": category, "target": target, "text": text}
                for category, target, text in items
            ],
            "byCategory": {
                name: sum(1 for item in items if item[0] == name)
                for name in CLARIFY_CATEGORIES
                if any(item[0] == name for item in items)
            },
        }
        if dropped:
            # #79: questions already asked, or over the round's limit.
            record["dropped"] = dropped
        ref = results.record_json(
            execution,
            phase.phase_id,
            record,
            kind="agent-clarify-review",
            summary=f"Agent review of the task: {len(items)} question(s)",
        )
        results.s.events.append(
            execution.execution_id,
            "intent.agent-review.completed",
            {"taskDigest": digest, "questions": len(items), "evidenceRef": ref},
        )
        return ref

    # ----- the converging review (#79) -------------------------------------------------------
    def _exhausted(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        items: list[Item],
        refs: tuple[str, ...],
        context: Convergence,
        start: int,
    ) -> ReviewOutcome:
        """The agent still asks after the last round: under ``assume`` its points become the
        assumptions of a new task revision, under ``block`` they are asked again."""
        settings = context.settings
        self.results.s.events.append(
            execution.execution_id,
            "intent.ambiguity.exhausted",
            {
                "taskDigest": task_digest(task),
                "rounds": context.rounds,
                "maxRounds": settings.rounds,
                "open": len(items),
                "onExhausted": settings.exhausted,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        if settings.exhausted == "block":
            return ReviewOutcome(_numbered(start, "A1", items), refs)
        revised, added = with_assumptions(task, items, context.rounds)
        if not added:
            return ReviewOutcome((), refs)
        ref = self._store_revision(execution, phase, task, revised, added, context)
        return ReviewOutcome((), (*refs, ref), revised=revised)

    def _store_revision(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        revised: Task,
        added: list[dict[str, Any]],
        context: Convergence,
    ) -> str:
        """Store the revision with the assumptions (pinned to the run under
        ``governance.pinTaskRevision``), as evidence and on the chain."""
        results = self.results
        services = results.s
        services.state.put("task", revised.task_id, revised, project_id=revised.project_id)
        revision_ref = services.artifacts.put_json(
            revised.model_dump(mode="json"), metadata={"kind": "task-revision"}
        )
        current = task_digest(revised)
        if results.project.governance_settings.pin_task_revision:
            services.pin_task_revision(execution.execution_id, revision_ref.uri, current)
        record = {
            "previousTaskDigest": task_digest(task),
            "taskDigest": current,
            "taskRef": revision_ref.uri,
            "rounds": context.rounds,
            "maxRounds": context.settings.rounds,
            "assumptions": added,
        }
        ref = results.record_json(
            execution,
            phase.phase_id,
            record,
            kind="ambiguity-assumptions",
            summary=(
                f"{len(added)} open point(s) of the agent review recorded as assumptions after "
                f"{context.rounds} round(s)"
            ),
            supports=(task.task_id,),
        )
        # The revision's review is settled: its assumptions are its answer.
        results.set_flag_json(
            f"agentclarify:{execution.project_id}:{current}{CONVERGE_KEY}",
            {"items": [], "ref": ref},
        )
        services.events.append(
            execution.execution_id,
            "intent.assumptions.recorded",
            {**record, "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        return ref

    def _plan(self, task: Task) -> DepthPlan | None:
        """The plan of ``depth: adaptive`` from the configured rounds and questions (never
        from the settings the plan already adjusted); ``None`` without the key."""
        intake = self.results.project.intake
        settings = intake.ambiguity_settings if intake else None
        if settings is None or not settings.adaptive:
            return None
        return depth_plan(task, settings.rounds, settings.questions)

    def _language(self, task: Task) -> str:
        intake = self.results.project.intake
        return topic_language(resolve_language(task, intake.language if intake else None))

    # ----- answers ---------------------------------------------------------------------------
    def _current_answers(self, execution: Execution, task: Task) -> list[dict[str, str]]:
        """The answers that produced the current revision of the task (if it came from
        ``task clarify``)."""
        digest = task_digest(task)
        records = [
            item
            for item in self.results.s.state.list(
                "clarification", ClarificationRecord, project_id=execution.project_id
            )
            if item.task_id == task.task_id and item.task_digest == digest
        ]
        if not records:
            return []
        latest = max(records, key=lambda item: item.recorded_at)
        return [
            {"questionId": item.question_id, "question": item.question, "answer": item.answer}
            for item in latest.answers
        ]

    def dangling_references(
        self, task: Task, answers: list[dict[str, str]]
    ) -> list[tuple[str, str, str]]:
        """Questions about answers that cite a requirement id or a document that neither the
        task nor the workspace contains."""
        workspace = self.results.s.paths.workspace
        task_text = " ".join(
            [
                task.title,
                task.intent,
                *task.constraints,
                *(item.text for item in task.requirements),
                *(item.requirement_id for item in task.requirements),
                *(item.text for item in task.acceptance_criteria),
                *(item.criterion_id for item in task.acceptance_criteria),
            ]
        )
        corpus: str | None = None
        items: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for answer in answers:
            text = answer["answer"]
            _dangling_documents(workspace, answer, text, seen, items)
            corpus = _dangling_identifiers(workspace, answer, text, task_text, corpus, seen, items)
        return items


def _depth_request(
    plan: DepthPlan, context: Convergence, language: str
) -> tuple[dict[str, Any], str]:
    """The ``depth`` part of a ``clarify`` request under ``depth: adaptive`` and the line it adds
    to the instructions: the level, the plan and the fundamental topics of this round."""
    topics = plan.round_topics(context.round)
    labels = TOPIC_LABELS[language]
    request = {
        **plan.as_dict(),
        "round": context.round,
        "topics": [{"topic": topic, "label": labels[topic]} for topic in topics],
    }
    focus = (
        DEPTH_TOPICS.format(labels="; ".join(labels[topic] for topic in topics))
        if topics
        else DEPTH_DETAILS
    )
    return request, DEPTH_SUFFIX.format(
        level=plan.level,
        maxRounds=plan.rounds,
        maxQuestions=plan.questions,
        topics=focus,
    )


def _doc_references(text: str) -> list[str]:
    """The document paths ``text`` names, such as ``docs/spec.md``, in order.

    A reference starts a run of path characters (word characters, ``/``, ``.``, ``-``): its
    stem, the part before the run's first dot, is names of word characters and ``-`` joined by
    single slashes, and a known extension follows the dot, not followed by a word character.
    Each run is read once, so the time is linear in the length of ``text``.
    """
    found: list[str] = []
    for run in _PATH_RUN.finditer(text):
        stem, dot, rest = run.group().partition(".")
        if not dot or not stem or stem[0] == "/" or stem[-1] == "/" or "//" in stem:
            continue
        extension = _DOC_EXTENSION.match(rest)
        if extension is not None:
            found.append(f"{stem}.{extension.group()}")
    return found


def _dangling_documents(
    workspace: Path,
    answer: dict[str, str],
    text: str,
    seen: set[str],
    items: list[tuple[str, str, str]],
) -> None:
    """A question for every document the answer refers to that is not in the workspace."""
    for document in _doc_references(text):
        if document in seen or _document_exists(workspace, document):
            continue
        seen.add(document)
        items.append(
            (
                "consistency",
                "task",
                f"The answer to {answer['questionId']} refers to {document}, which is "
                "not in the workspace: attach the document or transcribe the part "
                "the answer relies on.",
            )
        )


def _dangling_identifiers(
    workspace: Path,
    answer: dict[str, str],
    text: str,
    task_text: str,
    corpus: str | None,
    seen: set[str],
    items: list[tuple[str, str, str]],
) -> str | None:
    """A question for every identifier the answer cites that appears neither in the task nor
    in the workspace; returns the workspace text once it was read, so it is read only once."""
    identifiers = set(_ID_REFERENCE.findall(text)) | set(_LIST_REFERENCE.findall(text))
    for identifier in sorted(identifiers):
        if identifier in seen or re.search(rf"(?<!\w){re.escape(identifier)}(?!\w)", task_text):
            continue
        if corpus is None:
            corpus = _workspace_text(workspace)
        if re.search(rf"(?<!\w){re.escape(identifier)}(?!\w)", corpus):
            continue
        seen.add(identifier)
        items.append(
            (
                "consistency",
                "task",
                f"The answer to {answer['questionId']} cites {identifier!r}, which "
                "appears neither in the task nor in the workspace: transcribe what "
                f"{identifier} says, or attach the document that defines it.",
            )
        )
    return corpus


def _document_exists(workspace: Path, document: str) -> bool:
    candidate = (workspace / document).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError:
        return False
    if candidate.is_file():
        return True
    name = Path(document).name.lower()
    return any(path.name.lower() == name for path in _text_files(workspace, _DOC_SUFFIXES))


def _text_files(workspace: Path, suffixes: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if any(part in DEFAULT_EXCLUDES for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in suffixes:
            found.append(path)
    return found


def _workspace_text(workspace: Path) -> str:
    """The text of the workspace's documents and sources, bounded, to resolve a cited id."""
    parts: list[str] = []
    total = 0
    for path in _text_files(workspace, _SEARCH_SUFFIXES):
        try:
            data = path.read_bytes()[: _MAX_SEARCH_BYTES - total]
        except OSError:
            continue
        total += len(data)
        parts.append(data.decode("utf-8", "replace"))
        if total >= _MAX_SEARCH_BYTES:
            break
    return "\n".join(parts)
