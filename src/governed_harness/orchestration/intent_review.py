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
of the revised task also receives the answers and checks them against the documents they cite."""

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
from governed_harness.intake import task_digest
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

MALFORMED_RULE = "intake.agent-review-malformed"
MAX_AGENT_QUESTIONS = 40
_TEXT_LIMIT = 1000
_DOC_SUFFIXES = (".md", ".txt", ".rst", ".pdf", ".doc", ".docx", ".yaml", ".yml", ".json")
_DOC_REFERENCE = re.compile(
    r"(?<![\w/.-])((?:[\w-]+/)*[\w-]+\.(?:md|txt|rst|pdf|docx?|ya?ml|json))(?![\w])", re.I
)
_ID_REFERENCE = re.compile(
    r"\b(?:per|see|as in|according to|following|by|from|under|in)\s+"
    r"(?:requirements?|rules?|sections?|criteri(?:on|a)|items?\s+)?\s*"
    r"([A-Z]{1,3}-?\d{1,3}(?:\.\d+)?)\b"
)
_LIST_REFERENCE = re.compile(r"\brequirements?\s+([A-Z]{1,3}\d{0,3})\s*(?:to|-|through)\s*", re.I)
_SEARCH_SUFFIXES = (".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".py", ".ts", ".js")
_MAX_SEARCH_BYTES = 2_000_000


@dataclass(frozen=True)
class ReviewOutcome:
    questions: tuple[ClarificationQuestion, ...]
    evidence_refs: tuple[str, ...]
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
        ``agent`` false (the fast lane of #58) leaves out the agent's ambiguity review."""
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
        setup = self.results.project_setup.questions(execution, phase, task, start)
        added.extend(setup)
        start += len(setup)
        if self.enabled and agent:
            outcome = self._agent_questions(execution, phase, task, deterministic, answers, start)
            refs.extend(outcome.evidence_refs)
            if outcome.blocked is not None:
                return ReviewOutcome(tuple(added), tuple(refs), outcome.blocked)
            added.extend(outcome.questions)
        return ReviewOutcome(tuple(added), tuple(refs))

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
        results = self.results
        digest = task_digest(task)
        key = f"agentclarify:{execution.project_id}:{digest}"
        cached = results.flag_json(key)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            # Once per task revision: the stored answer is reused, no provider call.
            items = [tuple(item) for item in cached["items"]]
            results.s.events.append(
                execution.execution_id,
                "intent.agent-review.reused",
                {"taskDigest": digest, "questions": len(items), "evidenceRef": cached["ref"]},
            )
            return ReviewOutcome(
                _numbered(start, "A1", [(a, b, c) for a, b, c in items]), (cached["ref"],)
            )
        payload: dict[str, Any] = {
            "deterministicQuestions": [
                item.model_dump(mode="json", by_alias=True) for item in deterministic
            ],
            "categories": list(CLARIFY_CATEGORIES),
        }
        if answers:
            payload["clarifications"] = answers
        outcome = results.call_agent(execution, phase, "clarify", payload, task=task)
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return ReviewOutcome(
                (),
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
            return ReviewOutcome(
                (), outcome.evidence_refs, f"Agent review of the task was malformed: {error}"
            )
        record = {
            "taskDigest": digest,
            "invocationId": outcome.invocation_id,
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
        ref = results.record_json(
            execution,
            phase.phase_id,
            record,
            kind="agent-clarify-review",
            summary=f"Agent review of the task: {len(items)} question(s)",
        )
        results.set_flag_json(key, {"items": [list(item) for item in items], "ref": ref})
        results.s.events.append(
            execution.execution_id,
            "intent.agent-review.completed",
            {"taskDigest": digest, "questions": len(items), "evidenceRef": ref},
        )
        return ReviewOutcome(_numbered(start, "A1", items), (*outcome.evidence_refs, ref))

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
            for document in _DOC_REFERENCE.findall(text):
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
            identifiers = set(_ID_REFERENCE.findall(text)) | set(_LIST_REFERENCE.findall(text))
            for identifier in sorted(identifiers):
                if identifier in seen or re.search(
                    rf"(?<!\w){re.escape(identifier)}(?!\w)", task_text
                ):
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
        return items


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
