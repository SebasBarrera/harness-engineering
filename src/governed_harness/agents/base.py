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
    self_report: Any = None
    """The raw self-report the agent gave (``provenance.selfReport``), read by the engine."""
    request_bytes: int | None = None
    """Size of the request the invocation's prompt digest covers (JSON with sorted keys, UTF-8
    bytes): what the agent contract records as the bytes sent. ``None``: not reported."""


class AgentContext(Protocol):
    """Read-only execution context the engine hands to every agent provider."""

    @property
    def execution_id(self) -> str:
        """The identifier of the execution the agent works for."""

    @property
    def workspace(self) -> Any:
        """The workspace the agent works in."""

    @property
    def grants(self) -> list[CapabilityGrant]:
        """The capability grants the agent holds for this execution."""

    @property
    def artifact_store(self) -> LocalArtifactStore:
        """The store where the agent records its artifacts."""

    @property
    def process_runner(self) -> SafeProcessRunner:
        """The runner that executes the commands the agent is allowed to launch."""

    @property
    def patch_applier(self) -> PatchApplier:
        """The applier that writes the changes of the agent into the workspace."""

    @property
    def provenance(self) -> Provenance:
        """The provenance recorded for everything the agent produces."""

    @property
    def timeout_seconds(self) -> int:
        """The time limit of the call, in seconds."""

    @property
    def max_output_bytes(self) -> int:
        """The largest output the call may produce, in bytes."""

    @property
    def cancellation(self) -> CancellationToken:
        """The token that tells the agent the call was cancelled."""

    @property
    def context_manifest_ref(self) -> str | None:
        """The reference of the context manifest of the call, or ``None``."""

    @property
    def memory_context(self) -> dict[str, Any] | None:
        """The project memory the call may read, or ``None``."""

    @property
    def feedback(self) -> dict[str, Any] | None:
        """The feedback of the previous attempt, or ``None`` on the first one."""

    @property
    def request_extra(self) -> dict[str, Any] | None:
        """Keys the agent-results settings add to the request (gate contract, permissions,
        context manifest, lessons, budget, routing); ``None`` keeps the 1.0 request."""


@dataclass(frozen=True)
class AgentCallResult:
    """The outcome of a ``clarify``, ``review`` or ``plan`` call: the 1.0 result fields and
    the structured ``result`` object of the response (``None`` when the call did not pass or
    the response carried none)."""

    execution: AgentExecutionResult
    response: dict[str, Any] | None = None


class AgentProvider(Protocol):
    provider_id: str

    def capabilities(self) -> tuple[str, ...]:
        """The capabilities the provider declares."""

    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult:
        """Implement the plan of the task in the workspace and return the outcome."""

    def call(
        self,
        kind: CallKind,
        request: dict[str, Any],
        context: AgentContext,
        *,
        phase_id: PhaseId,
    ) -> AgentCallResult:
        """Make a ``clarify``, ``review`` or ``plan`` call and return its outcome."""
