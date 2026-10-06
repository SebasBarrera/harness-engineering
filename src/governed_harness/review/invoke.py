"""How the panel reaches the providers: parallel, read-only, isolated (#57).

``ProviderInvoker`` answers a batch of reviewer calls. The provider processes run in parallel
(up to ``review.panel.parallel``); everything the harness records runs on the calling thread,
before and after the batch, so the record keeps one writer. Isolation of a reviewer:

* **read-only**: the request says so, the agent sandbox (when enforced) keeps the workspace
  read-only for the reviewer's process, and the workspace is compared before and after the batch:
  a batch that changed it is undone and every answer of it is discarded;
* **tools**: the request carries the reviewer's tool allowlist (``isolation.tools``); the
  built-in adapters pass it to the CLI (see ``agents.native``);
* **MCP**: only the servers of ``review.panel.mcpServers`` that the reviewer names, in a strict
  configuration; nothing else the user configured;
* **no inherited configuration**: the provider process gets only the environment the provider
  declares, and the adapters ask their CLI not to read the user's settings."""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from governed_harness.agents import AgentCallResult, SimulatedAgentContext
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.models import Actor, CapabilityGrant, Provenance
from governed_harness.evidence.artifact_store import LocalArtifactStore
from governed_harness.orchestration.workspace_ops import (
    changes_since,
    restore_changes,
    snapshot_contents,
)
from governed_harness.review.panel import ReviewerAnswer, ReviewerCall
from governed_harness.runtime import CancellationToken, PatchApplier, WorkspaceSnapshotter
from governed_harness.runtime.process_runner import SafeProcessRunner
from governed_harness.runtime.workspace import WorkspaceDiff, WorkspaceSnapshot


class CallingProvider(Protocol):
    def call(
        self, kind: Any, request: dict[str, Any], context: Any, *, phase_id: PhaseId
    ) -> AgentCallResult:
        """Send a read-only request of ``kind`` and return the provider's answer."""


@dataclass(frozen=True)
class BuiltProvider:
    provider: CallingProvider
    actor: Actor
    refs: tuple[str, ...] = ()


def usage_tokens(result: AgentCallResult) -> int | None:
    usage = result.execution.usage
    if usage is None:
        return None
    values = [usage.input_tokens, usage.output_tokens, usage.reasoning_tokens]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


class ProviderInvoker:
    def __init__(
        self,
        *,
        workspace: Path,
        artifacts: LocalArtifactStore,
        runner: SafeProcessRunner,
        provenance: Provenance,
        build: Callable[[str], BuiltProvider | str],
        grants_for: Callable[[Actor], list[CapabilityGrant]],
        execution_id: str,
        phase_id: PhaseId,
        default_timeout: int,
        max_output_bytes: int,
        cancelled: Callable[[], bool] = lambda: False,
        before: Callable[[Sequence[ReviewerCall]], str | None] | None = None,
        record_request: Callable[[ReviewerCall], str | None] | None = None,
        after: Callable[[ReviewerCall, AgentCallResult], None] | None = None,
        on_violation: Callable[[WorkspaceDiff, int, int], None] | None = None,
        guard_workspace: bool = True,
        mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.runner = runner
        self.provenance = provenance
        self.build = build
        self.grants_for = grants_for
        self.execution_id = execution_id
        self.phase_id = phase_id
        self.default_timeout = default_timeout
        self.max_output_bytes = max_output_bytes
        self.cancelled = cancelled
        self.before = before
        self.record_request = record_request
        self.after = after
        self.on_violation = on_violation
        self.guard_workspace = guard_workspace
        self.mcp_servers = mcp_servers or {}
        self._built: dict[str, BuiltProvider | str] = {}

    def _provider(self, provider_id: str) -> BuiltProvider | str:
        if provider_id not in self._built:
            self._built[provider_id] = self.build(provider_id)
        return self._built[provider_id]

    def invoke(self, calls: Sequence[ReviewerCall], workers: int) -> list[ReviewerAnswer]:
        if not calls:
            return []
        blocked = self.before(calls) if self.before is not None else None
        if blocked is not None:
            return [ReviewerAnswer("ERROR", None, blocked, call.provider) for call in calls]
        request_refs = [
            self.record_request(call) if self.record_request is not None else None for call in calls
        ]
        built = [self._provider(call.provider) for call in calls]
        # The grants are made here, on the calling thread, where the policy of the running
        # phase applies (governance.phaseCapabilities, #4); the workers only use them.
        grants = [
            self.grants_for(target.actor) if isinstance(target, BuiltProvider) else []
            for target in built
        ]
        snapshot = WorkspaceSnapshotter(self.workspace).snapshot() if self.guard_workspace else None

        def run(index: int) -> AgentCallResult | str:
            return self._call(calls[index], built[index], grants[index])

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(calls)))) as pool:
            # Each worker runs in a copy of this context: the policies of the running phase
            # (capabilities, destructive commands) reach the provider processes it starts.
            parent = contextvars.copy_context()
            results = list(pool.map(lambda index: parent.copy().run(run, index), range(len(calls))))
        answers = [
            self._answer(call, result, request_ref, target)
            for call, result, request_ref, target in zip(
                calls, results, request_refs, built, strict=True
            )
        ]
        if snapshot is not None:
            answers = self._guarded(snapshot, answers)
        return answers

    def _call(
        self, call: ReviewerCall, target: BuiltProvider | str, grants: list[CapabilityGrant]
    ) -> AgentCallResult | str:
        """One reviewer call on a worker; a provider that cannot start or raises is the reason
        its answer never came."""
        if isinstance(target, str):
            return target
        timeout = self.default_timeout
        if call.timeout_seconds:
            timeout = min(call.timeout_seconds, self.default_timeout)
        context = SimulatedAgentContext(
            execution_id=self.execution_id,
            workspace=self.workspace,
            grants=grants,
            artifact_store=self.artifacts,
            process_runner=self.runner,
            patch_applier=PatchApplier(self.workspace),
            provenance=self.provenance.model_copy(update={"actor": target.actor}),
            timeout_seconds=timeout,
            max_output_bytes=self.max_output_bytes,
            cancellation=CancellationToken(self.cancelled),
        )
        try:
            return target.provider.call(
                "review", self._with_mcp(call.request), context, phase_id=self.phase_id
            )
        except Exception as error:  # a provider that raises is an answer that never came
            return f"the provider raised {type(error).__name__}: {error}"

    def _with_mcp(self, request: dict[str, Any]) -> dict[str, Any]:
        """The definitions of the allowed MCP servers reach the provider process only; the
        recorded request keeps their names."""
        isolation = request.get("isolation")
        if not isinstance(isolation, dict):
            return request
        names = [str(item) for item in isolation.get("mcpServers") or []]
        config = {name: self.mcp_servers[name] for name in names if name in self.mcp_servers}
        return {**request, "isolation": {**isolation, "mcpConfig": config}}

    def _answer(
        self,
        call: ReviewerCall,
        result: AgentCallResult | str,
        request_ref: str | None,
        target: BuiltProvider | str,
    ) -> ReviewerAnswer:
        if isinstance(result, str):
            return ReviewerAnswer("ERROR", None, result, call.provider)
        if self.after is not None:
            self.after(call, result)
        built_refs = target.refs if isinstance(target, BuiltProvider) else ()
        refs = tuple(
            item for item in (request_ref, *built_refs, result.execution.output_ref) if item
        )
        answered = result.execution.status is ResultStatus.PASSED and isinstance(
            result.response, dict
        )
        return ReviewerAnswer(
            "ANSWERED" if answered else "ERROR",
            result.response if answered else None,
            result.execution.summary,
            call.provider,
            model=result.execution.invocation.model,
            tokens=usage_tokens(result),
            invocation_id=result.execution.invocation.invocation_id,
            evidence_refs=refs,
        )

    def _guarded(
        self, snapshot: WorkspaceSnapshot, answers: list[ReviewerAnswer]
    ) -> list[ReviewerAnswer]:
        """When a read-only call changed the workspace: restore it, report it and discard the
        batch's answers."""
        diff = changes_since(self.workspace, snapshot)
        if not diff.changes:
            return answers
        restored, unrestorable = restore_changes(self.workspace, snapshot_contents(snapshot), diff)
        if self.on_violation is not None:
            self.on_violation(diff, len(restored), len(unrestorable))
        return [
            ReviewerAnswer(
                "ERROR",
                None,
                f"a read-only review call changed {len(diff.changes)} path(s); the "
                "batch's answers were discarded",
                item.provider,
                model=item.model,
                tokens=item.tokens,
                invocation_id=item.invocation_id,
                evidence_refs=item.evidence_refs,
            )
            for item in answers
        ]


__all__ = ["BuiltProvider", "CallingProvider", "ProviderInvoker", "usage_tokens"]
