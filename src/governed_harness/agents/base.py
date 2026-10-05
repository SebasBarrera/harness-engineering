from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from governed_harness.agents.requests import CallKind
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.models import (
    AgentInvocation,
    Plan,
    ResourceUsage,
    Task,
    ToolInvocation,
)

if TYPE_CHECKING:
    from governed_harness.domain.models import CapabilityGrant, Provenance
    from governed_harness.evidence.artifact_store import LocalArtifactStore
    from governed_harness.runtime.cancellation import CancellationToken
    from governed_harness.runtime.patches import PatchApplier
    from governed_harness.runtime.process_runner import SafeProcessRunner


@dataclass(frozen=True)
class AgentExecutionResult:
    status: ResultStatus
    summary: str
    invocation: AgentInvocation
    tool_invocations: tuple[ToolInvocation, ...] = ()
    output_ref: str | None = None
    usage: ResourceUsage | None = None


class AgentContext(Protocol):
    """Read-only execution context the engine hands to every agent provider."""

    @property
    def execution_id(self) -> str: ...
    @property
    def workspace(self) -> Any: ...
    @property
    def grants(self) -> list[CapabilityGrant]: ...
    @property
    def artifact_store(self) -> LocalArtifactStore: ...
    @property
    def process_runner(self) -> SafeProcessRunner: ...
    @property
    def patch_applier(self) -> PatchApplier: ...
    @property
    def provenance(self) -> Provenance: ...
    @property
    def timeout_seconds(self) -> int: ...
    @property
    def max_output_bytes(self) -> int: ...
    @property
    def cancellation(self) -> CancellationToken: ...
    @property
    def context_manifest_ref(self) -> str | None: ...
    @property
    def memory_context(self) -> dict[str, Any] | None: ...
    @property
    def feedback(self) -> dict[str, Any] | None: ...
    @property
    def request_extra(self) -> dict[str, Any] | None:
        """Keys the agent-results settings add to the request (gate contract, permissions,
        context manifest, lessons, budget, routing); ``None`` keeps the 1.0 request."""
        ...


@dataclass(frozen=True)
class AgentCallResult:
    """The outcome of a ``clarify``, ``review`` or ``plan`` call: the 1.0 result fields and
    the structured ``result`` object of the response (``None`` when the call did not pass or
    the response carried none)."""

    execution: AgentExecutionResult
    response: dict[str, Any] | None = None


class AgentProvider(Protocol):
    provider_id: str

    def capabilities(self) -> tuple[str, ...]: ...

    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult: ...

    def call(
        self,
        kind: CallKind,
        request: dict[str, Any],
        context: AgentContext,
        *,
        phase_id: PhaseId,
    ) -> AgentCallResult: ...
