from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from governed_harness.agents.base import AgentCallResult, AgentContext, AgentExecutionResult
from governed_harness.agents.requests import CallKind
from governed_harness.domain.enums import ActorType, ErrorKind, PhaseId, ResultStatus
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    AgentInvocation,
    CapabilityGrant,
    HarnessErrorRecord,
    Plan,
    Provenance,
    Task,
    ToolInvocation,
)
from governed_harness.evidence.artifact_store import LocalArtifactStore
from governed_harness.evidence.hashing import sha256_json
from governed_harness.runtime.cancellation import CancellationToken
from governed_harness.runtime.patches import PatchApplier
from governed_harness.runtime.process_runner import CommandSpec, SafeProcessRunner


@dataclass(frozen=True)
class SimulatedAgentContext:
    execution_id: str
    workspace: Any
    grants: list[CapabilityGrant]
    artifact_store: LocalArtifactStore
    process_runner: SafeProcessRunner
    patch_applier: PatchApplier
    provenance: Provenance
    timeout_seconds: int
    max_output_bytes: int
    cancellation: CancellationToken
    context_manifest_ref: str | None = None
    memory_context: dict[str, Any] | None = None
    feedback: dict[str, Any] | None = None
    """Serialized ``ProviderFeedback`` for a correction attempt; ``None`` on a first attempt."""
    request_extra: dict[str, Any] | None = None
    """Keys the agent-results settings add to the provider request (``None``: the 1.0 form)."""


class SimulatedAgentProvider:
    """Deterministic provider used for tests, evaluation fixtures and offline demonstrations."""

    provider_id = "simulated"

    def capabilities(self) -> tuple[str, ...]:
        return ("structured_patch", "command", "deterministic", "offline")

    def implement(self, task: Task, plan: Plan, context: AgentContext) -> AgentExecutionResult:
        actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.simulated", version="1")
        started = datetime.now(UTC)
        prompt_digest = sha256_json(
            {
                "task": task.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "provider": self.provider_id,
            }
        )
        tool_invocations: list[ToolInvocation] = []
        status = ResultStatus.PASSED
        summary = "No implementation action was requested"
        error_record: HarnessErrorRecord | None = None
        output: dict[str, Any] = {"mode": task.implementation.mode, "changedPaths": []}
        try:
            if context.cancellation.cancelled:
                status = ResultStatus.CANCELLED
                summary = "Implementation cancelled before start"
            elif task.implementation.mode == "patch":
                for patch in task.implementation.patches:
                    if context.cancellation.cancelled:
                        status = ResultStatus.CANCELLED
                        summary = "Implementation cancelled"
                        break
                    path = context.patch_applier.apply(patch, actor=actor, grants=context.grants)
                    output["changedPaths"].append(str(path.relative_to(context.workspace)))
                else:
                    summary = f"Applied {len(task.implementation.patches)} structured patch(es)"
            elif task.implementation.mode == "command":
                tool_started = datetime.now(UTC)
                result = context.process_runner.run(
                    CommandSpec(
                        argv=task.implementation.argv,
                        cwd=context.workspace / task.implementation.cwd,
                        timeout_seconds=context.timeout_seconds,
                        max_output_bytes=context.max_output_bytes,
                    ),
                    actor=actor,
                    grants=context.grants,
                    cancellation=context.cancellation,
                )
                stdout_ref = context.artifact_store.put(
                    result.stdout,
                    media_type="text/plain",
                    metadata={"stream": "stdout", "phase": "IMPLEMENTATION"},
                )
                stderr_ref = context.artifact_store.put(
                    result.stderr,
                    media_type="text/plain",
                    metadata={"stream": "stderr", "phase": "IMPLEMENTATION"},
                )
                tool_invocations.append(
                    ToolInvocation(
                        invocation_id=new_id("tool"),
                        execution_id=context.execution_id,
                        phase_id=PhaseId.IMPLEMENTATION,
                        actor=actor,
                        tool_id="process.execute",
                        argv=task.implementation.argv,
                        cwd=task.implementation.cwd,
                        started_at=tool_started,
                        finished_at=datetime.now(UTC),
                        status=result.status,
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        cancelled=result.cancelled,
                        stdout_ref=stdout_ref.uri,
                        stderr_ref=stderr_ref.uri,
                        provenance=context.provenance.model_copy(update={"actor": actor}),
                    )
                )
                status = result.status
                summary = f"Implementation command exited with {result.exit_code}"
                output.update(
                    {
                        "exitCode": result.exit_code,
                        "stdoutRef": stdout_ref.uri,
                        "stderrRef": stderr_ref.uri,
                    }
                )
        except Exception as error:  # converted into a structured provider error
            status = ResultStatus.ERROR
            summary = str(error)
            error_record = HarnessErrorRecord(
                error_id=new_id("err"),
                kind=ErrorKind.PROVIDER_ERROR,
                message=str(error),
                actor=actor,
                details={"provider": self.provider_id},
            )
        output_ref = context.artifact_store.put_json(
            output,
            metadata={"kind": "agent-output", "provider": self.provider_id},
        )
        finished = datetime.now(UTC)
        invocation = AgentInvocation(
            invocation_id=new_id("agentinv"),
            execution_id=context.execution_id,
            phase_id=PhaseId.IMPLEMENTATION,
            actor=actor,
            provider=self.provider_id,
            model="deterministic-v1",
            session_id=None,
            started_at=started,
            finished_at=finished,
            status=status,
            prompt_digest=prompt_digest,
            context_manifest_ref=context.context_manifest_ref,
            tool_invocation_ids=tuple(item.invocation_id for item in tool_invocations),
            output_ref=output_ref.uri,
            error=error_record,
        )
        return AgentExecutionResult(
            status, summary, invocation, tuple(tool_invocations), output_ref.uri
        )

    def call(
        self,
        kind: CallKind,
        request: dict[str, Any],
        context: AgentContext,
        *,
        phase_id: PhaseId,
    ) -> AgentCallResult:
        """Deterministic answers to the read-only request kinds: no clarification question, no
        review finding, and a plan that splits the requirements in order into sub-tasks of at
        most ``threshold`` requirements (the acceptance criteria go to the last one)."""
        actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.simulated", version="1")
        started = datetime.now(UTC)
        result: dict[str, Any]
        if kind == "clarify":
            result = {"questions": []}
        elif kind == "review" and "outputContract" in request:
            # A reviewer of the review panel (#57) answers with its output contract.
            result = {"verdict": "PASS", "findings": [], "summary": "Simulated review: no finding."}
        elif kind == "review":
            result = {"findings": []}
        elif kind == "acceptance":
            result = {"tests": []}
        elif kind == "locate":
            result = {"locations": [], "questions": []}
        elif kind == "architecture":
            # No layering inferred and no option proposed: nothing waits for a person (#56).
            result = (
                {"options": []}
                if request.get("mode") == "advise"
                else {
                    "style": "custom",
                    "summary": "Simulated survey: no layering was inferred.",
                    "layers": [],
                    "allow": {},
                }
            )
        elif kind == "plan":
            task = request.get("task") or {}
            requirement_ids = [
                str(item.get("requirement_id"))
                for item in task.get("requirements") or []
                if isinstance(item, dict)
            ]
            size = max(1, int(request.get("threshold") or len(requirement_ids) or 1))
            groups = [
                requirement_ids[index : index + size]
                for index in range(0, len(requirement_ids), size)
            ] or [[]]
            criteria = [
                str(item.get("criterion_id"))
                for item in task.get("acceptance_criteria") or []
                if isinstance(item, dict)
            ]
            result = {
                "subtasks": [
                    {
                        "title": f"Part {number} of {len(groups)}",
                        "requirements": group,
                        "criteria": criteria if number == len(groups) else [],
                        "constraints": [],
                    }
                    for number, group in enumerate(groups, start=1)
                ]
            }
        else:
            result = {}
        output_ref = context.artifact_store.put_json(
            {"kind": kind, "result": result},
            metadata={"kind": "agent-output", "provider": self.provider_id},
        )
        invocation = AgentInvocation(
            invocation_id=new_id("agentinv"),
            execution_id=context.execution_id,
            phase_id=phase_id,
            actor=actor,
            provider=self.provider_id,
            model="deterministic-v1",
            started_at=started,
            finished_at=datetime.now(UTC),
            status=ResultStatus.PASSED,
            prompt_digest=sha256_json(request),
            context_manifest_ref=context.context_manifest_ref,
            output_ref=output_ref.uri,
            call_kind=kind,
        )
        execution = AgentExecutionResult(
            ResultStatus.PASSED,
            f"Simulated {kind} answer",
            invocation,
            (),
            output_ref.uri,
        )
        return AgentCallResult(execution, result)
