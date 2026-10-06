"""The ``session`` provider: the agent session that drives the harness implements (#56).

In embedded mode an agent (Claude Code, Codex) drives the flow from its own session through the
harness's MCP server or skill. The harness must not start a second agent to implement the task:
the session itself edits the workspace. ``run start --provider session`` therefore runs INTENT
to PLANNING and then waits in IMPLEMENTATION (``BLOCKED``) until the workspace differs from the
baseline DISCOVERY recorded; ``run continue`` after the session's edits records them as the
candidate ChangeSet, attributed to ``agent.session``, and VERIFICATION, the review, the gate and
the human decision run as for any provider. Read-only calls (clarify, review, plan, acceptance,
architecture) go to the project's ``agentProvider`` (or their own configured provider), never
to the session, so the reviewer is not the author."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from governed_harness.agents.base import AgentCallResult, AgentContext, AgentExecutionResult
from governed_harness.agents.requests import CallKind
from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Actor, AgentInvocation, Plan, Task
from governed_harness.evidence.hashing import sha256_json

SESSION_PROVIDER = "session"


class SessionAgentProvider:
    provider_id = SESSION_PROVIDER

    def __init__(self, changed_paths: Callable[[], list[str]]) -> None:
        self._changed_paths = changed_paths

    def capabilities(self) -> tuple[str, ...]:
        return ("embedded", "session")

    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult:
        actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.session", version="1")
        started = datetime.now(UTC)
        changed = self._changed_paths()
        if changed:
            status = ResultStatus.PASSED
            summary = f"The agent session changed {len(changed)} file(s) of the ChangeSet scope"
        else:
            status = ResultStatus.BLOCKED
            summary = (
                "Waiting for the agent session to implement the task: edit the workspace, then "
                "run harness run continue (MCP tool harness_run_continue)"
            )
        output_ref = context.artifact_store.put_json(
            {"provider": SESSION_PROVIDER, "changedPaths": changed[:200], "status": status.value},
            metadata={"kind": "agent-output", "provider": SESSION_PROVIDER},
        )
        invocation = AgentInvocation(
            invocation_id=new_id("agentinv"),
            execution_id=context.execution_id,
            phase_id=PhaseId.IMPLEMENTATION,
            actor=actor,
            provider=SESSION_PROVIDER,
            model=None,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=status,
            prompt_digest=sha256_json({"task": task.model_dump(mode="json"), "session": True}),
            context_manifest_ref=context.context_manifest_ref,
            output_ref=output_ref.uri,
        )
        return AgentExecutionResult(status, summary, invocation, (), output_ref.uri)

    def call(
        self,
        kind: CallKind,
        request: dict[str, Any],
        context: AgentContext,
        *,
        phase_id: PhaseId,
    ) -> AgentCallResult:
        raise RuntimeError(
            f"the session provider does not answer {kind} calls; they go to agentProvider"
        )


__all__ = ["SESSION_PROVIDER", "SessionAgentProvider"]
