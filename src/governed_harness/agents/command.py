from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governed_harness.agents.base import AgentContext, AgentExecutionResult
from governed_harness.domain.enums import (
    ActorType,
    ErrorKind,
    MetricQuality,
    PhaseId,
    ResultStatus,
)
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    AgentInvocation,
    HarnessErrorRecord,
    Plan,
    ResourceUsage,
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
    sandbox_prefix: tuple[str, ...] = ()
    """Write-confinement wrapper (``runtime.agentSandbox: enforce``); empty runs unconfined."""


class CommandAgentProvider:
    """Provider-neutral adapter for a locally installed agent CLI.

    The CLI must read a JSON request from stdin and return JSON on stdout. It remains
    constrained by the same process capability grant as any other tool and, when the
    configuration carries a sandbox prefix, runs inside that write confinement.
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
        request: dict[str, object] = {
            "schemaVersion": "1.0",
            "task": task.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        }
        # The governed memory selected in PLANNING reaches the agent only when there is any, so
        # a request without memory is identical to the one sent before memory was wired in.
        if context.memory_context and context.memory_context.get("records"):
            request["context"] = context.memory_context
        # Feedback exists only on a correction attempt with runtime.providerFeedback enabled, so
        # every other request keeps its previous form and prompt digest.
        if context.feedback:
            request["feedback"] = context.feedback
        request_bytes = json.dumps(request, sort_keys=True).encode("utf-8")
        prompt_digest = sha256_json(request)
        result = context.process_runner.run(
            CommandSpec(
                argv=self.configuration.argv_prefix,
                cwd=Path(context.workspace),
                timeout_seconds=context.timeout_seconds,
                max_output_bytes=context.max_output_bytes,
                stdin=request_bytes,
                sandbox_prefix=self.configuration.sandbox_prefix,
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
        invocation_id = new_id("agentinv")
        usage: ResourceUsage | None = None
        usage_ref: str | None = None
        if result.status is ResultStatus.PASSED:
            try:
                response = json.loads(result.stdout)
                if not isinstance(response, dict) or response.get("status") not in {
                    "PASSED",
                    "FAILED",
                    "BLOCKED",
                }:
                    raise ValueError("agent response does not satisfy the minimal protocol")
                if response.get("usage") is not None:
                    usage = _reported_usage(
                        response["usage"],
                        execution_id=context.execution_id,
                        invocation_id=invocation_id,
                        wall_time_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
                    )
                    usage_ref = context.artifact_store.put_json(
                        usage.model_dump(mode="json", by_alias=True),
                        metadata={"kind": "resource-usage", "provider": self.provider_id},
                    ).uri
                status = ResultStatus(response["status"])
                summary = str(response.get("summary", summary))
            except Exception as exc:
                usage, usage_ref = None, None
                status = ResultStatus.ERROR
                error = HarnessErrorRecord(
                    error_id=new_id("err"),
                    kind=ErrorKind.PROTOCOL_ERROR,
                    message=str(exc),
                    actor=actor,
                )
                summary = str(exc)
        invocation = AgentInvocation(
            invocation_id=invocation_id,
            execution_id=context.execution_id,
            phase_id=PhaseId.IMPLEMENTATION,
            actor=actor,
            provider=self.provider_id,
            model=self.configuration.model,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=status,
            prompt_digest=prompt_digest,
            context_manifest_ref=context.context_manifest_ref,
            tool_invocation_ids=(tool.invocation_id,),
            usage_ref=usage_ref,
            output_ref=stdout_ref.uri,
            error=error,
        )
        return AgentExecutionResult(status, summary, invocation, (tool,), stdout_ref.uri, usage)


_USAGE_FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "reasoningTokens": "reasoning_tokens",
    "costUsd": "cost_usd",
}


def _reported_usage(
    raw: Any, *, execution_id: str, invocation_id: str, wall_time_ms: int
) -> ResourceUsage:
    """Usage the provider reports about its own model calls. It is optional, but a malformed
    report is a protocol error: the harness records what was reported, never an estimate."""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("agent usage must be a non-empty object")
    unknown = sorted(set(raw) - set(_USAGE_FIELDS))
    if unknown:
        raise ValueError(f"unknown agent usage field(s): {', '.join(unknown)}")
    values: dict[str, int | float] = {}
    for name, field in _USAGE_FIELDS.items():
        if name not in raw:
            continue
        value = raw[name]
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        if not numeric or value < 0 or (name != "costUsd" and not isinstance(value, int)):
            raise ValueError(f"agent usage field {name} must be a non-negative number")
        values[field] = value
    return ResourceUsage(
        usage_id=new_id("usage"),
        execution_id=execution_id,
        invocation_id=invocation_id,
        wall_time_ms=wall_time_ms,
        quality=MetricQuality.REPORTED,
        limitations=("Reported by the agent provider; the harness does not measure it.",),
        **values,  # type: ignore[arg-type]
    )
