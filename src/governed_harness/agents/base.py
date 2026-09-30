from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import AgentInvocation, Plan, Task, ToolInvocation


@dataclass(frozen=True)
class AgentExecutionResult:
    status: ResultStatus
    summary: str
    invocation: AgentInvocation
    tool_invocations: tuple[ToolInvocation, ...] = ()
    output_ref: str | None = None


class AgentProvider(Protocol):
    provider_id: str

    def capabilities(self) -> tuple[str, ...]: ...

    def implement(self, task: Task, plan: Plan, context: object) -> AgentExecutionResult: ...
