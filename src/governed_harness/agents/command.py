from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from governed_harness.agents.base import AgentContext, AgentExecutionResult
from governed_harness.domain.enums import ActorType, ErrorKind, PhaseId, ResultStatus
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    AgentInvocation,
    HarnessErrorRecord,
    Plan,
    Task,
    ToolInvocation,
)
from governed_harness.evidence.hashing import sha256_json
from governed_harness.runtime.process_runner import CommandSpec


@dataclass(frozen=True)
class CommandAgentConfiguration:
    provider_id: str
    argv_prefix: tuple[str, ...]
    model: str | None = None


class CommandAgentProvider:
    """Provider-neutral adapter for a locally installed agent CLI.

    The CLI must read a JSON request from stdin and return JSON on stdout. It remains
    constrained by the same process capability grant as any other tool.
    """

    def __init__(self, configuration: CommandAgentConfiguration) -> None:
        self.configuration = configuration
        self.provider_id = configuration.provider_id

    def capabilities(self) -> tuple[str, ...]:
        return ("external_cli", "structured_json")

    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult:
        actor = context.provenance.actor
        if actor.actor_type is not ActorType.AGENT or actor.actor_id != f"agent.{self.provider_id}":
            actor = Actor(
                actor_type=ActorType.AGENT,
                actor_id=f"agent.{self.provider_id}",
                version="1",
            )
        started = datetime.now(UTC)
        request = {
            "schemaVersion": "1.0",
            "task": task.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        }
        request_bytes = json.dumps(request, sort_keys=True).encode("utf-8")
        prompt_digest = sha256_json(request)
        result = context.process_runner.run(
            CommandSpec(
                argv=self.configuration.argv_prefix,
                cwd=Path(context.workspace),
                timeout_seconds=context.timeout_seconds,
                max_output_bytes=context.max_output_bytes,
                stdin=request_bytes,
            ),
            actor=actor,
            grants=context.grants,
            cancellation=context.cancellation,
        )
        stdout_ref = context.artifact_store.put(result.stdout, media_type="application/json")
        stderr_ref = context.artifact_store.put(result.stderr, media_type="text/plain")
        tool = ToolInvocation(
            invocation_id=new_id("tool"),
            execution_id=context.execution_id,
            phase_id=PhaseId.IMPLEMENTATION,
            actor=actor,
            tool_id="agent.cli",
            argv=self.configuration.argv_prefix,
            cwd=".",
            started_at=started,
            finished_at=datetime.now(UTC),
            status=result.status,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            stdout_ref=stdout_ref.uri,
            stderr_ref=stderr_ref.uri,
            provenance=context.provenance.model_copy(update={"actor": actor}),
        )
        error = None
        status = result.status
        summary = f"Agent CLI exited with {result.exit_code}"
        if result.status is ResultStatus.PASSED:
            try:
                response = json.loads(result.stdout)
                if not isinstance(response, dict) or response.get("status") not in {
                    "PASSED",
                    "FAILED",
                    "BLOCKED",
                }:
                    raise ValueError("agent response does not satisfy the minimal protocol")
                status = ResultStatus(response["status"])
                summary = str(response.get("summary", summary))
            except Exception as exc:
                status = ResultStatus.ERROR
                error = HarnessErrorRecord(
                    error_id=new_id("err"),
                    kind=ErrorKind.PROTOCOL_ERROR,
                    message=str(exc),
                    actor=actor,
                )
                summary = str(exc)
        invocation = AgentInvocation(
            invocation_id=new_id("agentinv"),
            execution_id=context.execution_id,
            phase_id=PhaseId.IMPLEMENTATION,
            actor=actor,
            provider=self.provider_id,
            model=self.configuration.model,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=status,
            prompt_digest=prompt_digest,
            tool_invocation_ids=(tool.invocation_id,),
            output_ref=stdout_ref.uri,
            error=error,
        )
        return AgentExecutionResult(status, summary, invocation, (tool,), stdout_ref.uri)
