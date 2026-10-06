from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governed_harness.agents.base import AgentCallResult, AgentContext, AgentExecutionResult
from governed_harness.agents.environment import ProviderEnvironment
from governed_harness.agents.requests import CallKind
from governed_harness.agents.self_report import REQUEST_BLOCK
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
from governed_harness.runtime.process_runner import CommandSpec, ProcessResult


@dataclass(frozen=True)
class CommandAgentConfiguration:
    provider_id: str
    argv_prefix: tuple[str, ...]
    model: str | None = None
    sandbox_prefix: tuple[str, ...] = ()
    """Write-confinement wrapper (``runtime.agentSandbox: enforce``); empty runs unconfined."""
    environment: ProviderEnvironment | None = None
    """``passEnv`` and ``env`` of the provider (since 2.0); ``None`` passes only the runner's
    minimal environment, as in 1.0.0."""
    self_report: bool = False
    """``provenance.selfReport``: ask for and read the agent's self-report (since 2.0)."""
    extra_args: tuple[str, ...] = ()
    """``args`` of a built-in adapter."""


@dataclass(frozen=True)
class ProviderAnswer:
    """What a provider's process said, read by the protocol of its kind."""

    status: ResultStatus
    summary: str
    usage: dict[str, Any] | None = None
    usage_limitations: tuple[str, ...] = ()
    session_id: str | None = None
    self_report: Any = None
    result: Any = None
    """The structured ``result`` of a ``clarify``, ``acceptance``, ``plan`` or ``review``
    answer (since 2.0)."""


class CommandAgentProvider:
    """Provider-neutral adapter for a locally installed agent CLI.

    The CLI must read a JSON request from stdin and return JSON on stdout. It remains
    constrained by the same process capability grant as any other tool and, when the
    configuration carries a sandbox prefix, runs inside that write confinement.
    """

    stdout_media_type = "application/json"

    def __init__(self, configuration: CommandAgentConfiguration) -> None:
        self.configuration = configuration
        self.provider_id = configuration.provider_id

    def capabilities(self) -> tuple[str, ...]:
        return ("external_cli", "structured_json")

    # ----- hooks of the built-in adapters ------------------------------------------------
    def build_request(self, task: Task, plan: Plan, context: AgentContext) -> dict[str, object]:
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
        if self.configuration.self_report:
            request["selfReport"] = dict(REQUEST_BLOCK)
        # The agent-results settings (gate contract, permissions, context manifest, lessons,
        # budget, routing) add their keys, and the kind and instructions with them; without
        # any of them the request keeps the 1.0 form.
        extra = context.request_extra
        if extra:
            request.update(extra)
        return request

    def process_input(
        self, request: dict[str, object], context: AgentContext
    ) -> tuple[tuple[str, ...], bytes | None, tuple[str, ...]]:
        """The argv, the standard input and the argv recorded as evidence."""
        request_bytes = json.dumps(request, sort_keys=True).encode("utf-8")
        return self.configuration.argv_prefix, request_bytes, self.configuration.argv_prefix

    def read_answer(self, result: ProcessResult) -> ProviderAnswer:
        """Read the JSON protocol answer of a process that exited with 0; raise on a protocol
        error."""
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or response.get("status") not in {
            "PASSED",
            "FAILED",
            "BLOCKED",
        }:
            raise ValueError("agent response does not satisfy the minimal protocol")
        return ProviderAnswer(
            status=ResultStatus(response["status"]),
            summary=str(response.get("summary", f"Agent CLI exited with {result.exit_code}")),
            usage=response.get("usage"),
            self_report=response.get("selfReport") if self.configuration.self_report else None,
            result=response.get("result"),
        )

    def failure_summary(self, result: ProcessResult) -> str:
        return f"Agent CLI exited with {result.exit_code}"

    def extract_result(self, result: ProcessResult) -> Any:
        """The ``result`` object of a read-only call when the answer did not carry it in the
        protocol field (built-in adapters read it from the agent's text)."""
        return None

    # ----- the invocations -----------------------------------------------------------------
    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult:
        request = self.build_request(task, plan, context)
        execution, _ = self._invoke(
            request,
            context,
            phase_id=PhaseId.IMPLEMENTATION,
            call_kind="implement" if context.request_extra else None,
        )
        return execution

    def call(
        self,
        kind: CallKind,
        request: dict[str, Any],
        context: AgentContext,
        *,
        phase_id: PhaseId,
    ) -> AgentCallResult:
        """Send a read-only ``clarify``, ``acceptance``, ``plan`` or ``review`` request. A
        passing answer must carry a ``result`` object; otherwise it is a protocol error."""
        execution, result = self._invoke(
            dict(request), context, phase_id=phase_id, call_kind=kind, require_result=True
        )
        return AgentCallResult(execution, result)

    def _invoke(
        self,
        request: dict[str, object],
        context: AgentContext,
        *,
        phase_id: PhaseId,
        call_kind: CallKind | None,
        require_result: bool = False,
    ) -> tuple[AgentExecutionResult, dict[str, Any] | None]:
        actor = context.provenance.actor
        if actor.actor_type is not ActorType.AGENT or actor.actor_id != f"agent.{self.provider_id}":
            actor = Actor(
                actor_type=ActorType.AGENT,
                actor_id=f"agent.{self.provider_id}",
                version="1",
            )
        started = datetime.now(UTC)
        prompt_digest = sha256_json(request)
        argv, stdin, recorded_argv = self.process_input(request, context)
        environment = self.configuration.environment
        result = context.process_runner.run(
            CommandSpec(
                argv=argv,
                cwd=Path(context.workspace),
                timeout_seconds=context.timeout_seconds,
                allowed_environment=environment.allowed if environment else (),
                max_output_bytes=context.max_output_bytes,
                stdin=stdin,
                sandbox_prefix=self.configuration.sandbox_prefix,
            ),
            actor=actor,
            grants=context.grants,
            extra_env=(environment.values or None) if environment else None,
            cancellation=context.cancellation,
        )
        stdout_ref = context.artifact_store.put(result.stdout, media_type=self.stdout_media_type)
        stderr_ref = context.artifact_store.put(result.stderr, media_type="text/plain")
        tool = ToolInvocation(
            invocation_id=new_id("tool"),
            execution_id=context.execution_id,
            phase_id=phase_id,
            actor=actor,
            tool_id="agent.cli",
            argv=recorded_argv,
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
        summary = self.failure_summary(result)
        invocation_id = new_id("agentinv")
        usage: ResourceUsage | None = None
        usage_ref: str | None = None
        session_id: str | None = None
        self_report: Any = None
        structured: dict[str, Any] | None = None
        if result.status is ResultStatus.PASSED:
            try:
                answer = self.read_answer(result)
                if answer.usage is not None:
                    usage = _reported_usage(
                        answer.usage,
                        execution_id=context.execution_id,
                        invocation_id=invocation_id,
                        wall_time_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
                        limitations=answer.usage_limitations,
                    )
                    usage_ref = context.artifact_store.put_json(
                        usage.model_dump(mode="json", by_alias=True),
                        metadata={"kind": "resource-usage", "provider": self.provider_id},
                    ).uri
                status = answer.status
                summary = answer.summary
                session_id = answer.session_id
                self_report = answer.self_report
                if require_result and status is ResultStatus.PASSED:
                    value = (
                        answer.result if answer.result is not None else self.extract_result(result)
                    )
                    if not isinstance(value, dict):
                        raise ValueError(
                            f"a {call_kind} response that passes needs a 'result' object"
                        )
                    structured = value
            except Exception as exc:
                usage, usage_ref, structured = None, None, None
                status = ResultStatus.ERROR
                error = HarnessErrorRecord(
                    error_id=new_id("err"),
                    kind=ErrorKind.PROTOCOL_ERROR,
                    message=str(exc),
                    actor=actor,
                )
                summary = str(exc)
        redactor = context.artifact_store.redactor
        if redactor.configured:
            summary = redactor.redact_configured_text(summary)
            if error is not None:
                error = error.model_copy(
                    update={"message": redactor.redact_configured_text(error.message)}
                )
        routing = request_routing(request)
        invocation = AgentInvocation(
            invocation_id=invocation_id,
            execution_id=context.execution_id,
            phase_id=phase_id,
            actor=actor,
            provider=self.provider_id,
            model=routing.get("model") or self.configuration.model,
            session_id=session_id,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=status,
            prompt_digest=prompt_digest,
            context_manifest_ref=context.context_manifest_ref,
            tool_invocation_ids=(tool.invocation_id,),
            usage_ref=usage_ref,
            output_ref=stdout_ref.uri,
            error=error,
            call_kind=call_kind,
            effort=routing.get("effort"),
        )
        return (
            AgentExecutionResult(
                status, summary, invocation, (tool,), stdout_ref.uri, usage, self_report
            ),
            structured,
        )


def request_routing(request: dict[str, object]) -> dict[str, str]:
    """The model and effort a request carries under ``agentRouting`` or a call's own setting
    (``routing``); empty without them."""
    routing = request.get("routing")
    if not isinstance(routing, dict):
        return {}
    return {
        name: str(routing[name]) for name in ("model", "effort") if routing.get(name) is not None
    }


_USAGE_FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "reasoningTokens": "reasoning_tokens",
    "costUsd": "cost_usd",
    "cacheTokens": "cache_tokens",
}


def _reported_usage(
    raw: Any,
    *,
    execution_id: str,
    invocation_id: str,
    wall_time_ms: int,
    limitations: tuple[str, ...] = (),
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
        limitations=("Reported by the agent provider; the harness does not measure it.",)
        + limitations,
        **values,  # type: ignore[arg-type]
    )
