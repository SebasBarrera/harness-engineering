"""Project setup questions in INTENT (#56, rule ``P1``).

Under ``intake.projectSetup: ask`` INTENT decides deterministically whether the project is new
or existing and asks, once per project, what nothing else establishes:

* a **new** project: the testing strategy and the standards packs, unless the configuration
  sets them or a person already answered; the architecture too when ``architecture.mode`` is not
  ``agent`` (with ``agent``, the architecture call proposes options instead of a question);
* an **existing** project: only what detection could not establish: the testing strategy when
  the repository has no test, the standards when no pack's language is detected.

The answers are recorded through ``harness task clarify`` like every clarification (the person,
the question, the answer, the task digests) and, once per project, as the project setup record
that later runs read instead of asking again. Each answer also lands in the task as a
constraint, so the agent sees it."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.engineering import ARCHITECTURE_STYLES
from governed_harness.domain.models import (
    ClarificationQuestion,
    ClarificationRecord,
    Execution,
    PhaseExecution,
    Task,
    utc_now,
)
from governed_harness.intake.project_kind import detect_project_kind
from governed_harness.orchestration.engineering import SETUP_FLAG
from governed_harness.standards import BUILTIN_PACKS, detect_packs

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

TARGETS = ("project:architecture", "project:testing", "project:standards")
_STYLE_WORDS = {
    "modular monolith": "modular-monolith",
    "modular-monolith": "modular-monolith",
    "micro-services": "microservices",
    "domain-driven": "ddd",
    "ports and adapters": "hexagonal",
}


def parse_testing(answer: str) -> str | None:
    text = answer.lower()
    if re.search(r"\btdd\b|test[- ]driven|test[- ]first", text):
        return "tdd"
    if re.search(r"\bbdd\b|behaviou?r[- ]driven|gherkin|cucumber|\bbehave\b", text):
        return "bdd"
    if re.search(r"conventional|tests? after|regular|standard|unit tests?", text):
        return "conventional"
    return None


def parse_architecture(answer: str) -> str:
    text = answer.lower()
    for phrase, style in _STYLE_WORDS.items():
        if phrase in text:
            return style
    for style in ARCHITECTURE_STYLES:
        if re.search(rf"\b{re.escape(style)}\b", text):
            return style
    return "custom"


def parse_standards(answer: str, detected: list[str]) -> list[str]:
    text = answer.lower()
    named = [item for item in BUILTIN_PACKS if re.search(rf"\b{re.escape(item)}\b", text)]
    if named:
        return named
    if re.search(r"\b(default|detected|yes|ok|recommended)\b", text):
        return detected
    return []


class ProjectSetup:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def enabled(self) -> bool:
        intake = self.results.project.intake
        return bool(intake and intake.project_setup == "ask")

    def record(self, project_id: str) -> dict[str, Any]:
        return self.results.engineering.setup_record(project_id)

    def questions(
        self, execution: Execution, phase: PhaseExecution, task: Task, start: int
    ) -> tuple[ClarificationQuestion, ...]:
        if not self.enabled:
            return ()
        project = self.results.project
        workspace = self.results.s.paths.workspace
        kind = detect_project_kind(workspace)
        answered = self.record(execution.project_id)
        technologies = self.results.engineering.technologies()
        detected_packs = detect_packs(workspace, technologies)
        items: list[tuple[str, str]] = []
        architecture = project.architecture
        architecture_known = (
            bool(
                architecture
                and (architecture.style or architecture.layers or architecture.agent_enabled)
            )
            or "architecture" in answered
        )
        if kind.new and not architecture_known:
            items.append(
                (
                    "project:architecture",
                    "This is a new project. Which architecture should it follow (ddd, "
                    "hexagonal, clean, layered, modular-monolith, microservices, mvvm, mvi or "
                    "custom), and which layers may depend on which?",
                )
            )
        testing = project.testing
        testing_known = bool(testing and testing.strategy not in {None, "auto"}) or (
            "testing" in answered
        )
        if not testing_known:
            strategy = self.results.engineering.strategy(execution.project_id)
            if kind.new or strategy.strategy == "unknown":
                items.append(
                    (
                        "project:testing",
                        "Which testing strategy should this project follow: tdd (tests first, "
                        "red-green-refactor checked by the harness), bdd (criteria as Gherkin "
                        "scenarios a person approves) or conventional (tests with the code)?"
                        + (
                            f" Detected: {strategy.strategy}."
                            if strategy.strategy != "unknown"
                            else ""
                        ),
                    )
                )
        standards = project.standards
        standards_known = bool(standards and standards.packs and "auto" not in standards.packs) or (
            "standards" in answered
        )
        if not standards_known and (kind.new or not detected_packs):
            suggestion = ", ".join(detected_packs) or "none detected"
            items.append(
                (
                    "project:standards",
                    "Which coding standards packs apply (python, javascript, typescript, node, "
                    "react, angular, vue, java, kotlin, go, rust, swift, csharp, php, ruby)? "
                    f"Answer 'default' for the detected ones ({suggestion}).",
                )
            )
        self.results.s.events.append(
            execution.execution_id,
            "project.setup.assessed",
            {
                "projectKind": kind.as_dict(),
                "asked": [target for target, _text in items],
                "answered": sorted(
                    key for key in answered if key in {"architecture", "testing", "standards"}
                ),
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return tuple(
            ClarificationQuestion.model_validate(
                {
                    "questionId": f"Q-{number}",
                    "ruleId": "P1",
                    "target": target,
                    "text": text,
                    "category": "project-setup",
                }
            )
            for number, (target, text) in enumerate(items, start=start)
        )

    def store_answers(
        self, execution: Execution, record: ClarificationRecord
    ) -> dict[str, Any] | None:
        """Keep the answers to ``P1`` questions as the project setup record."""
        answers = [item for item in record.answers if item.rule_id == "P1"]
        if not answers:
            return None
        current = dict(self.record(execution.project_id))
        detected = detect_packs(
            self.results.s.paths.workspace, self.results.engineering.technologies()
        )
        for item in answers:
            part = item.target.removeprefix("project:")
            if part == "testing":
                current["testing"] = parse_testing(item.answer) or "conventional"
            elif part == "architecture":
                current["architecture"] = parse_architecture(item.answer)
            elif part == "standards":
                current["standards"] = parse_standards(item.answer, detected)
            current.setdefault("answers", {})[part] = item.answer
        current.update(
            {
                "answeredBy": record.actor.actor_id,
                "clarificationId": record.clarification_id,
                "taskId": record.task_id,
                "recordedAt": utc_now().isoformat(),
            }
        )
        self.results.set_flag_json(f"{SETUP_FLAG}:{execution.project_id}", current)
        self.results.s.events.append(
            execution.execution_id,
            "project.setup.recorded",
            {
                key: current[key]
                for key in ("testing", "architecture", "standards")
                if key in current
            },
            actor=record.actor,
        )
        return current


__all__ = ["TARGETS", "ProjectSetup", "parse_architecture", "parse_standards", "parse_testing"]
