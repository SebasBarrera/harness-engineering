"""Intake: deterministic checks of a task's intent before any work starts."""

from .clarification import (
    C0_TARGETS,
    OUT_OF_SCOPE_PREFIX,
    QUESTION_TEMPLATES,
    TASK_TARGET,
    ClarificationInput,
    TaskRevision,
    assess_intent,
    no_acceptance_criteria,
    no_observable_result,
    resolve_language,
    revise_task,
    scope_without_breakdown,
    task_digest,
    unmeasured_quality,
)
from .language import QUESTION_TEMPLATES_ES, detect_language

__all__ = [
    "C0_TARGETS",
    "OUT_OF_SCOPE_PREFIX",
    "QUESTION_TEMPLATES",
    "QUESTION_TEMPLATES_ES",
    "TASK_TARGET",
    "ClarificationInput",
    "TaskRevision",
    "assess_intent",
    "detect_language",
    "no_acceptance_criteria",
    "no_observable_result",
    "resolve_language",
    "revise_task",
    "scope_without_breakdown",
    "task_digest",
    "unmeasured_quality",
]
