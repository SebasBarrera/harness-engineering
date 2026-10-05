"""Kinds of agent request and the instructions the harness renders for each (#37).

Before 1.1 the provider protocol had one request: implement the task. The agent-results
settings add three more kinds that ask a provider for structured judgement the deterministic
rules cannot give:

* ``clarify`` (INTENT): ambiguity and completeness questions about the task;
* ``review`` (INDEPENDENT_REVIEW): findings of a second reviewer on the ChangeSet;
* ``plan`` (PLANNING): ordered sub-tasks that partition the task's requirements;
* ``acceptance`` (SPECIFICATION): acceptance tests written from the criteria by a separate
  call, returned as files that the harness writes and freezes once a person approves them;
* ``locate`` (INTENT, since #55): where to intervene, as ``path:line`` with evidence, for a
  task the router classifies M or L; ambiguous places come back as questions.

These kinds are read-only by contract: the request says so (``readOnly: true``) and the harness
compares the workspace before and after the call; a provider that changed it gets a HIGH
finding, its changes are undone and its answer is discarded. The agent proposes; only a person
answers clarification questions and approves a plan.

The response of every kind is the JSON object of the 1.0 protocol (``status``, ``summary``,
optional ``usage``); the non-implement kinds add ``result`` with their structured answer."""

from __future__ import annotations

from typing import Any, Final, Literal

CallKind = Literal["implement", "clarify", "review", "plan", "acceptance", "locate"]

CALL_KINDS: Final[tuple[CallKind, ...]] = (
    "implement",
    "clarify",
    "review",
    "plan",
    "acceptance",
    "locate",
)
READ_ONLY_KINDS: Final[frozenset[str]] = frozenset(
    {"clarify", "review", "plan", "acceptance", "locate"}
)
REQUEST_SCHEMA_VERSION = "1.1"
"""Version of a request that carries ``kind`` and ``instructions``; an implement request sent
without any agent-results key keeps the 1.0 form (and its prompt digest)."""

CLARIFY_CATEGORIES: Final[tuple[str, ...]] = (
    "ambiguity",
    "contradiction",
    "boundaries",
    "errors",
    "missing-flows",
    "actors-and-roles",
    "data-and-states",
    "edge-cases",
    "non-functional",
    "integrations",
    "acceptance-evidence",
    "out-of-scope",
    "consistency",
)
"""Categories of an agent's clarification questions (#37: ambiguity and completeness)."""

REVIEW_SEVERITIES: Final[tuple[str, ...]] = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")

_READ_ONLY = (
    "This request is read-only: do not create, modify or delete any file and do not run "
    "commands that change the workspace. The harness compares the workspace before and after "
    "the call and discards the answer of a call that changed it."
)

_JSON = (
    "Print exactly one JSON object on standard output: "
    '{{"status": "PASSED", "summary": "<one sentence>", "result": RESULT}}. '
    'Use "status": "BLOCKED" with a summary when you cannot answer.'
)

INSTRUCTIONS: Final[dict[CallKind, str]] = {
    "implement": (
        "Implement the task in the workspace at {workspace}: satisfy every requirement and "
        "acceptance criterion and respect every constraint. Work only inside the workspace. "
        "The gate below lists the validators and review rules the harness will run on your "
        "ChangeSet; run the check command before you finish and fix what it reports. Do not "
        "weaken tests, thresholds or checks to make them pass. Report honestly: print "
        '{{"status": "PASSED"|"FAILED"|"BLOCKED", "summary": "<what you did>"}}.'
    ),
    "clarify": (
        "Review the task below before any work starts. Read the task and the files it "
        "references in the workspace at {workspace}. Ask concrete, answerable questions, each "
        "in one category of: {categories}. Look for ambiguities, contradictions, undefined "
        "boundaries and error precedences, and for completeness: missing features or flows the "
        "intent implies, actors and roles, data and states, edge cases and error handling, "
        "non-functional requirements (performance, security, persistence, accessibility, "
        "observability), integrations and constraints, the acceptance evidence of each "
        "requirement, and what is explicitly out of scope. Do not repeat the questions listed "
        "in deterministicQuestions. When answers are listed in clarifications, check each one "
        "against the documents it references and against the other answers, and ask about any "
        "contradiction or wrong example (category consistency). Ask nothing when the task is "
        "complete and unambiguous. "
        + _READ_ONLY
        + " "
        + _JSON.replace(
            "RESULT",
            '{{"questions": [{{"category": "<category>", "target": "task" or a criterion id, '
            '"text": "<question>"}}]}}',
        )
    ),
    "review": (
        "You are an independent reviewer. Compare the ChangeSet (the unified diff below, applied "
        "to the workspace at {workspace}) with the task's requirements, acceptance criteria and "
        "constraints. Report defects that tests, linters and type checkers do not see: wrong "
        "formulas or rules, broken public signatures, unsafe deserialization, stored card data "
        "or secrets, missing error handling, requirements not implemented. Each finding needs a "
        "severity (INFO, LOW, MEDIUM, HIGH, CRITICAL), a short rule id, the file and line, a "
        "message and the evidence (the code or requirement it is about). HIGH and CRITICAL "
        "block the delivery: use them only for defects you can show. "
        + _READ_ONLY
        + " "
        + _JSON.replace(
            "RESULT",
            '{{"findings": [{{"severity": "HIGH", "rule": "<rule>", "path": "<file>", '
            '"line": 1, "message": "<message>", "evidence": "<evidence>"}}]}}',
        )
    ),
    "plan": (
        "Split the task below into ordered sub-tasks that can each be implemented and verified "
        "on their own, in the workspace at {workspace}. The sub-tasks must partition the "
        "requirements: every requirement id appears in exactly one sub-task. Give each "
        "sub-task a title, its requirement ids, the acceptance criterion ids it satisfies "
        "and the constraints that apply to it; cross-cutting constraints are added to every "
        "sub-task by the harness. At most {maxSubtasks} sub-tasks. "
        + _READ_ONLY
        + " "
        + _JSON.replace(
            "RESULT",
            '{{"subtasks": [{{"title": "<title>", "requirements": ["<id>"], '
            '"criteria": ["<id>"], "constraints": ["<text>"]}}]}}',
        )
    ),
    "acceptance": (
        "Write acceptance tests for the task below from its acceptance criteria and "
        "requirements only, before any implementation exists, for the workspace at "
        "{workspace}. Use pytest; put every file under {directory}/ with a name that starts "
        "with test_; name each test after the criterion or requirement id it checks. Test the "
        "observable behaviour the criteria describe, including at least one end-to-end flow; "
        "do not test implementation details. The tests must fail on the current workspace and "
        "pass once the task is implemented. Return the files instead of writing them: the "
        "harness writes them after a person approves them, and then freezes them. "
        + _READ_ONLY
        + " "
        + _JSON.replace(
            "RESULT",
            '{{"tests": [{{"path": "{directory}/test_<name>.py", "content": "<file>"}}]}}',
        )
    ),
    "locate": (
        "Find where the task below must be implemented in the workspace at {workspace}, before "
        "anyone changes it. Read the files the task references and search the code for its "
        "requirements and criteria. Return at most {maxLocations} locations, most relevant "
        "first, each with the file, the line, the evidence (the code or text that shows it is "
        "the place) and why it must change. When the task could be implemented in clearly "
        "different places and the task does not say which, ask instead of guessing. "
        + _READ_ONLY
        + " "
        + _JSON.replace(
            "RESULT",
            '{{"locations": [{{"path": "<file>", "line": 1, "evidence": "<code>", '
            '"reason": "<why>"}}], "questions": [{{"text": "<question>"}}]}}',
        )
    ),
}


def render_instructions(kind: CallKind, *, workspace: str, **values: Any) -> str:
    """The instructions of a request kind with its placeholders filled."""
    fields: dict[str, Any] = {
        "workspace": workspace,
        "categories": ", ".join(CLARIFY_CATEGORIES),
        "maxSubtasks": 12,
        "directory": "tests/acceptance",
        "maxLocations": 20,
    }
    fields.update(values)
    return INSTRUCTIONS[kind].format(**fields)


def phase_of(kind: CallKind) -> str:
    """The phase a call kind belongs to."""
    return {
        "implement": "IMPLEMENTATION",
        "clarify": "INTENT",
        "review": "INDEPENDENT_REVIEW",
        "plan": "PLANNING",
        "acceptance": "SPECIFICATION",
        "locate": "INTENT",
    }[kind]
