"""Intake: deterministic checks of a task's intent before any work starts."""

from .clarification import (
    QUESTION_TEMPLATES,
    TASK_TARGET,
    ClarificationInput,
    TaskRevision,
    assess_intent,
    no_observable_result,
    revise_task,
    scope_without_breakdown,
    task_digest,
    unmeasured_quality,
)

__all__ = [
    "QUESTION_TEMPLATES",
    "TASK_TARGET",
    "ClarificationInput",
    "TaskRevision",
    "assess_intent",
    "no_observable_result",
    "revise_task",
    "scope_without_breakdown",
    "task_digest",
    "unmeasured_quality",
]
