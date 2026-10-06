"""The operational contract of a task (#55, item 7).

Before work starts, the person and the harness agree on one summary: the objective,
reproducible examples, the scope (and what is out of it), the definition of done, the
verification level required, the branch, whether to push, create a pull request and comment on
it, and the coverage threshold. Each item comes from the task's ``contract`` when it declares
it, else from the rest of the task (intent, requirements, criteria) or the project
configuration; an item nobody settled is ``missing``. The summary has a digest and is bound to
the task digest it was derived from: a revision of the task gives another summary.

Under ``intake.operationalContract: batch`` the items are added to the one clarification
request INTENT sends anyway; under ``enforce`` INTENT also asks for every missing item and
waits for a person to confirm the summary (``harness task confirm``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from governed_harness.domain.models import Task
from governed_harness.evidence.hashing import sha256_json

_ANCHOR = re.compile(
    r"\d|[`'\"]|\w+\(|\b[a-z]+_[a-z_]+\b|\b(returns?|raises?|rejects?|equals?|contains?|prints?|"
    r"exits?|responds?|lists?|stores?)\b",
    re.IGNORECASE,
)

CONTRACT_FIELDS: tuple[str, ...] = (
    "objective",
    "examples",
    "scope",
    "outOfScope",
    "definitionOfDone",
    "verificationLevel",
    "branch",
    "push",
    "createPullRequest",
    "comment",
    "coverageThreshold",
)

QUESTIONS: dict[str, str] = {
    "objective": "What is the objective of the task, in one sentence?",
    "examples": (
        "Which reproducible examples show the expected behaviour (input or action -> exact "
        "result), one per line?"
    ),
    "scope": "Which behaviours or paths are in scope, one per line?",
    "outOfScope": "What is explicitly out of scope, one per line?",
    "definitionOfDone": "What must hold for the task to be done (the definition of done)?",
    "verificationLevel": (
        "Which verification level is required (L0 static, L1 unit, L2 integration, L3 "
        "executable behaviour, L4 external environment, L5 human)?"
    ),
    "branch": "On which branch should the change be delivered?",
    "push": "May the harness push the approved change (yes or no)?",
    "createPullRequest": "Should the harness create a pull request for it (yes or no)?",
    "comment": "Should the harness comment the decision brief on the pull request (yes or no)?",
    "coverageThreshold": "Which coverage of the changed lines is required (0 to 100)?",
}


@dataclass(frozen=True)
class ContractItem:
    field: str
    value: Any
    source: str  # task, derived, project, missing
    question: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "question": self.question,
        }


@dataclass(frozen=True)
class ContractSummary:
    task_digest: str
    items: tuple[ContractItem, ...]

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(item.field for item in self.items if item.source == "missing")

    @property
    def values(self) -> dict[str, Any]:
        return {item.field: item.value for item in self.items}

    @property
    def digest(self) -> str:
        return sha256_json({"taskDigest": self.task_digest, "values": self.values})

    def as_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "taskDigest": self.task_digest,
            "items": [item.as_dict() for item in self.items],
            "missing": list(self.missing),
        }


def _item(field: str, value: Any, source: str) -> ContractItem:
    empty = value is None or value == [] or value == ""
    return ContractItem(
        field, None if empty else value, "missing" if empty else source, QUESTIONS[field]
    )


_SETTINGS = (
    ("verificationLevel", "verification_level"),
    ("branch", "branch"),
    ("push", "push"),
    ("createPullRequest", "create_pull_request"),
    ("comment", "comment"),
    ("coverageThreshold", "coverage_threshold"),
)


def _declared_value(declared: Any, name: str) -> Any:
    """A field of the task's contract declaration: an empty list is ``None``, an enum its
    value."""
    if declared is None:
        return None
    value = getattr(declared, name)
    if isinstance(value, tuple):
        return list(value) or None
    if hasattr(value, "value"):
        return value.value
    return value


def _examples_item(task: Task, examples: Any) -> ContractItem:
    if examples:
        return _item("examples", examples, "task")
    anchored = [
        item.verification_hint or item.text
        for item in task.acceptance_criteria
        if item.verification_hint or _ANCHOR.search(item.text)
    ]
    return _item("examples", anchored, "derived")


def _scope_item(task: Task, scope: Any, paths: Any) -> ContractItem:
    if scope or paths:
        return _item("scope", [*(scope or []), *(paths or [])], "task")
    derived = [item.text for item in task.requirements] or list(
        task.metadata.get("ownedPaths") or []
    )
    return _item("scope", derived, "derived")


def _out_of_scope_item(task: Task, out_of_scope: Any) -> ContractItem:
    if out_of_scope:
        return _item("outOfScope", out_of_scope, "task")
    derived_out = [
        item.removeprefix("Out of scope: ")
        for item in task.constraints
        if item.startswith("Out of scope: ")
    ]
    # Saying nothing about what is out of scope is a choice, not a gap.
    return ContractItem("outOfScope", derived_out, "derived", QUESTIONS["outOfScope"])


def _setting_item(name: str, value: Any, defaults: dict[str, Any]) -> ContractItem:
    if value is not None:
        return _item(name, value, "task")
    if defaults.get(name) is not None:
        return _item(name, defaults[name], "project")
    return _item(name, None, "missing")


def derive_contract(task: Task, task_digest: str, defaults: dict[str, Any]) -> ContractSummary:
    """The contract summary of ``task``: its declaration first, then the task, then the
    project ``defaults`` (``branch``, ``push``, ``createPullRequest``, ``comment``,
    ``coverageThreshold``, ``verificationLevel``)."""
    declared = task.contract
    objective = _declared_value(declared, "objective")
    done = _declared_value(declared, "definition_of_done")
    items = [
        _item("objective", objective, "task")
        if objective
        else _item("objective", task.intent, "derived"),
        _examples_item(task, _declared_value(declared, "examples")),
        _scope_item(
            task, _declared_value(declared, "scope"), _declared_value(declared, "scope_paths")
        ),
        _out_of_scope_item(task, _declared_value(declared, "out_of_scope")),
        _item("definitionOfDone", done, "task")
        if done
        else _item("definitionOfDone", [item.text for item in task.acceptance_criteria], "derived"),
    ]
    items.extend(
        _setting_item(name, _declared_value(declared, attribute), defaults)
        for name, attribute in _SETTINGS
    )
    return ContractSummary(task_digest, tuple(items))


__all__ = ["CONTRACT_FIELDS", "QUESTIONS", "ContractItem", "ContractSummary", "derive_contract"]
