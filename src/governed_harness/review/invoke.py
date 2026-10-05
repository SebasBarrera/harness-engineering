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
from governed_harness.runtime.workspace import WorkspaceDiff


class CallingProvider(Protocol):
    def call(
        self, kind: Any, request: dict[str, Any], context: Any, *, phase_id: PhaseId
    ) -> AgentCallResult: ...


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
        if self.before is not None:
            blocked = self.before(calls)
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
            target = built[index]
            if isinstance(target, str):
                return target
            call = calls[index]
            context = SimulatedAgentContext(
                execution_id=self.execution_id,
                workspace=self.workspace,
                grants=grants[index],
                artifact_store=self.artifacts,
                process_runner=self.runner,
                patch_applier=PatchApplier(self.workspace),
                provenance=self.provenance.model_copy(update={"actor": target.actor}),
                timeout_seconds=(
                    min(call.timeout_seconds, self.default_timeout)
                    if call.timeout_seconds
                    else self.default_timeout
                ),
                max_output_bytes=self.max_output_bytes,
                cancellation=CancellationToken(self.cancelled),
            )
            request = call.request
            isolation = request.get("isolation")
            if isinstance(isolation, dict):
                # The definitions of the allowed MCP servers reach the provider process only;
                # the recorded request keeps their names.
                names = [str(item) for item in isolation.get("mcpServers") or []]
                request = {
                    **request,
                    "isolation": {
                        **isolation,
                        "mcpConfig": {
                            name: self.mcp_servers[name]
                            for name in names
                            if name in self.mcp_servers
                        },
                    },
                }
            try:
                return target.provider.call("review", request, context, phase_id=self.phase_id)
            except Exception as error:  # a provider that raises is an answer that never came
                return f"the provider raised {type(error).__name__}: {error}"

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(calls)))) as pool:
            results = list(pool.map(run, range(len(calls))))
        answers: list[ReviewerAnswer] = []
        for call, result, request_ref, target in zip(
            calls, results, request_refs, built, strict=True
        ):
            if isinstance(result, str):
                answers.append(ReviewerAnswer("ERROR", None, result, call.provider))
                continue
            if self.after is not None:
                self.after(call, result)
            refs = tuple(
                item
                for item in (
                    request_ref,
                    *(target.refs if isinstance(target, BuiltProvider) else ()),
                    result.execution.output_ref,
                )
                if item
            )
            passed = result.execution.status is ResultStatus.PASSED
            answers.append(
                ReviewerAnswer(
                    "ANSWERED" if passed and isinstance(result.response, dict) else "ERROR",
                    result.response if passed and isinstance(result.response, dict) else None,
                    result.execution.summary,
                    call.provider,
                    model=result.execution.invocation.model,
                    tokens=usage_tokens(result),
                    invocation_id=result.execution.invocation.invocation_id,
                    evidence_refs=refs,
                )
            )
        if snapshot is not None:
            diff = changes_since(self.workspace, snapshot)
            if diff.changes:
                restored, unrestorable = restore_changes(
                    self.workspace, snapshot_contents(snapshot), diff
                )
                if self.on_violation is not None:
                    self.on_violation(diff, len(restored), len(unrestorable))
                answers = [
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
        return answers


__all__ = ["BuiltProvider", "CallingProvider", "ProviderInvoker", "usage_tokens"]
