"""The versioned JSON contract between a parent and an agent call (wave 11, #97).

A *request contract* travels in the ``agentContract`` key of every request of a run that sets
``agentContract`` in ``project.yaml``. It states, in one place: the assigned task, the allowed
context, the authorized files and resources, the execution constraints, the token budget, the
time limit, the iteration number, the expected response and the minimal result that goes back to
the parent. A *response contract* is what the parent receives: the final status, a bounded
summary, the minimal ``result`` and, when a limit or the contract itself stopped the call, which
one. Both are validated; a violation fails closed with a structured result.

The module is pure: it reads no file and calls no agent."""

from __future__ import annotations

import json
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from governed_harness.agents.requests import CallKind
from governed_harness.configuration.agent_contract import (
    MAX_ITERATIONS_CEILING,
    AgentContractConfig,
)
from governed_harness.evidence.hashing import sha256_json

CONTRACT_VERSION: Final = "1.0"
REQUEST_KEY: Final = "agentContract"
"""The key of a request that carries the contract."""

SizeName = Literal["S", "M", "L"]
AgentStatus = Literal["PASSED", "FAILED", "BLOCKED"]
FinalStatus = Literal[
    "PASSED",
    "FAILED",
    "BLOCKED",
    "ERROR",
    "CANCELLED",
    "LIMIT_REACHED",
    "CONTRACT_VIOLATION",
    "ITERATIONS_EXHAUSTED",
]
LimitName = Literal["tokens", "time"]

TASK_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "clarify": (
        "task_id",
        "title",
        "intent",
        "constraints",
        "requirements",
        "acceptance_criteria",
        "contract",
    ),
    "plan": ("task_id", "title", "intent", "constraints", "requirements", "acceptance_criteria"),
    "acceptance": ("task_id", "title", "intent", "requirements", "acceptance_criteria"),
    "locate": ("task_id", "title", "intent", "requirements", "acceptance_criteria"),
    "architecture": ("task_id", "title", "intent", "constraints", "requirements"),
    "review": ("task_id", "title", "constraints", "requirements", "acceptance_criteria"),
}
"""The fields of the task each read-only call kind needs; the others are not sent. An
``implement`` request keeps the whole task, which the agent implements."""

RESULT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "implement": (),
    "clarify": ("questions",),
    "review": ("findings",),
    "plan": ("subtasks",),
    "acceptance": ("tests",),
    "locate": ("locations", "questions"),
    "architecture": ("style", "summary", "layers", "allow", "options"),
}
"""The keys the ``result`` object of each call kind may carry."""

FILE_CONTENT_KEYS: Final[frozenset[str]] = frozenset(
    {"content", "contents", "filecontent", "filecontents", "source", "sourcecode", "body"}
)
"""Keys under which a ``result`` would carry the content of a file; only ``acceptance`` returns
files (the tests it writes), every other kind returns a conclusion."""

OBJECTIVE_CHARS: Final = 500
RETURN_FIELDS: Final[tuple[str, ...]] = ("finalStatus", "summary", "result", "limitReached")
RETURN_FORBIDDEN: Final[tuple[str, ...]] = (
    "the request or its context",
    "the content of files when a conclusion is required",
    "reasoning or internal notes",
    "information the parent does not need for its next decision",
)


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, frozen=True, alias_generator=to_camel
    )


class TaskAssignment(_Contract):
    """The task the call assigns: its id, the kind of call, a short objective and the task
    fields the request carries (the others were left out)."""

    task_id: str = Field(min_length=1)
    call_kind: CallKind
    objective: str = Field(max_length=OBJECTIVE_CHARS)
    fields: tuple[str, ...]


class AuthorizedResources(_Contract):
    """What the call may read, write and run. Context files remain guidance: reads are not
    blocked, writes are confined as the agent sandbox does."""

    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    guidance_paths: tuple[str, ...] = ()
    """The context files the request lists: guidance, never a restriction on reads."""
    deny_paths: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    """Ids of the scripts and commands registered as black boxes (``harness agent-contract
    scripts``); the call uses their contract, not their source."""


class ExecutionConstraints(_Contract):
    read_only: bool
    workspace: str
    scripts_are_black_boxes: bool = True
    context_files_are_guidance: bool = True


class Iteration(_Contract):
    """The call's place in the cap of iterations of the agent in this run."""

    number: int = Field(ge=1)
    max: int = Field(ge=1, le=MAX_ITERATIONS_CEILING)

    @model_validator(mode="after")
    def _within_cap(self) -> Iteration:
        if self.number > self.max:
            raise ValueError(f"iteration {self.number} is above the cap of {self.max}")
        return self


class ExpectedResponseLimits(_Contract):
    summary_chars: int = Field(ge=1)
    result_bytes: int = Field(ge=1)
    field_chars: int = Field(ge=1)


class ExpectedResponse(_Contract):
    """The answer the call must give: the 1.0 protocol object, the keys of its ``result`` and
    the caps per field."""

    protocol: Literal["1.0"] = "1.0"
    result_keys: tuple[str, ...]
    limits: ExpectedResponseLimits
    file_contents_allowed: bool


class ReturnToParent(_Contract):
    """The minimal result that reaches the parent and what never does."""

    fields: tuple[str, ...] = RETURN_FIELDS
    never: tuple[str, ...] = RETURN_FORBIDDEN


class AgentCallRequestContract(_Contract):
    """The request contract (schema ``agent-call-request.schema.json``)."""

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    call_kind: CallKind
    task: TaskAssignment
    allowed_context: tuple[str, ...] = Field(min_length=1)
    """The top-level keys of the request, all of them: what the call receives."""
    resources: AuthorizedResources
    constraints: ExecutionConstraints
    token_budget: int = Field(ge=1)
    time_limit_seconds: int = Field(ge=1)
    size_class: SizeName
    iteration: Iteration
    expected_response: ExpectedResponse
    return_to_parent: ReturnToParent

    @model_validator(mode="after")
    def _consistent(self) -> AgentCallRequestContract:
        if self.task.call_kind != self.call_kind:
            raise ValueError("the task assignment and the contract name different call kinds")
        if self.constraints.read_only and self.resources.write_paths:
            raise ValueError("a read-only call cannot be authorized to write")
        return self


class Usage(_Contract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class AgentCallResponse(_Contract):
    """What the parent receives from a call (schema ``agent-call-response.schema.json``)."""

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    call_kind: CallKind
    invocation_id: str | None = None
    iteration: int = Field(ge=1)
    status: AgentStatus | None = None
    """The status the agent reported (``None``: it reported none, for example on a timeout)."""
    final_status: FinalStatus
    summary: str = Field(max_length=5000)
    result: dict[str, Any] | None = None
    usage: Usage | None = None
    limit_reached: LimitName | None = None
    violations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> AgentCallResponse:
        if self.final_status == "LIMIT_REACHED" and self.limit_reached is None:
            raise ValueError("finalStatus LIMIT_REACHED needs limitReached")
        if self.final_status != "PASSED" and self.result is not None:
            raise ValueError("only a passed call returns a result")
        if self.final_status == "CONTRACT_VIOLATION" and not self.violations:
            raise ValueError("finalStatus CONTRACT_VIOLATION needs violations")
        return self


# ----- the minimal request -------------------------------------------------------------------
def minimal_task(kind: str, task: dict[str, Any]) -> dict[str, Any]:
    """The task as a call of ``kind`` receives it: only the fields the kind needs. ``implement``
    (and a kind without a list) keeps the whole task."""
    fields = TASK_FIELDS.get(kind)
    if fields is None:
        return task
    return {name: task[name] for name in fields if name in task}


def request_digest(request: dict[str, Any]) -> str:
    """The digest of the request the provider received (the same one as its prompt digest)."""
    return sha256_json(request)


def request_bytes(request: dict[str, Any]) -> int:
    return len(json.dumps(request, sort_keys=True).encode("utf-8"))


def objective_of(task: dict[str, Any]) -> str:
    """A short objective for the assignment: the title, then the start of the intent."""
    text = f"{task.get('title', '')}. {task.get('intent', '')}".strip()
    return text[:OBJECTIVE_CHARS]


def expected_response(kind: str, config: AgentContractConfig) -> ExpectedResponse:
    return ExpectedResponse(
        result_keys=RESULT_KEYS.get(kind, ()),
        limits=ExpectedResponseLimits(
            summary_chars=config.summary_chars,
            result_bytes=config.result_bytes(kind),
            field_chars=config.field_chars,
        ),
        file_contents_allowed=kind == "acceptance",
    )


def validate_request_contract(value: object) -> AgentCallRequestContract:
    """The request contract or ``ValueError`` listing every violation."""
    return _validated(AgentCallRequestContract, value)


def validate_response_contract(value: object) -> AgentCallResponse:
    """The response the parent receives or ``ValueError`` listing every violation."""
    return _validated(AgentCallResponse, value)


def _validated[M: BaseModel](model: type[M], value: object) -> M:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
            for item in error.errors()
        ]
        raise ValueError("; ".join(problems)) from error


# ----- the response limits -------------------------------------------------------------------
def limit_summary(summary: str, chars: int) -> tuple[str, bool]:
    """The summary cut to ``chars`` characters and whether it was cut."""
    if len(summary) <= chars:
        return summary, False
    return summary[: max(0, chars - 1)] + "…", True


def check_answer(
    kind: str, result: dict[str, Any] | None, config: AgentContractConfig
) -> list[str]:
    """What a passing answer's ``result`` does against the contract of its kind: unknown keys,
    a size above the cap, a text field above its cap, file contents where only a conclusion is
    due. Empty: the answer keeps the contract."""
    if result is None:
        return []
    problems: list[str] = []
    allowed = RESULT_KEYS.get(kind, ())
    unknown = sorted(set(result) - set(allowed))
    if unknown:
        problems.append(f"result carries keys outside the {kind} contract: {', '.join(unknown)}")
    size = len(json.dumps(result, sort_keys=True).encode("utf-8"))
    if size > config.result_bytes(kind):
        problems.append(f"result is {size} bytes, above the cap of {config.result_bytes(kind)}")
    problems.extend(_field_problems(kind, result, config.field_chars))
    return problems[:20]


def _field_problems(kind: str, value: Any, field_chars: int, path: str = "result") -> list[str]:
    problems: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}.{key}"
            if kind != "acceptance" and str(key).lower() in FILE_CONTENT_KEYS:
                problems.append(f"{where} carries file content; a {kind} call returns a conclusion")
                continue
            problems.extend(_field_problems(kind, item, field_chars, where))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            problems.extend(_field_problems(kind, item, field_chars, f"{path}[{index}]"))
    elif isinstance(value, str) and len(value) > field_chars and not _is_test_file(kind, path):
        problems.append(f"{path} has {len(value)} characters, above the cap of {field_chars}")
    return problems


def _is_test_file(kind: str, path: str) -> bool:
    """Only the content of a test an ``acceptance`` call writes may exceed a text-field cap."""
    return kind == "acceptance" and path.endswith(".content")
