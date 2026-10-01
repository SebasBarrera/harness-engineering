from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import AgentInvocation, Plan, Task, ToolInvocation

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


class AgentProvider(Protocol):
    provider_id: str

    def capabilities(self) -> tuple[str, ...]: ...

    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult: ...
