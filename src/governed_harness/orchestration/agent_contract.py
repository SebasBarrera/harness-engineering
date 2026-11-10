"""The agent contract of a run (wave 11, #97), wired into the agent calls.

With ``agentContract`` in ``project.yaml`` every agent call of a run:

* receives the minimal request of its kind (``agents/contract.py``) with the request contract
  in ``agentContract``: task, allowed context, authorized resources, constraints, token budget,
  time limit, iteration, expected response and what returns to the parent;
* counts as one iteration of its agent against one cap (default 5, at most 6): the first
  attempt, corrections, contract retries, escalations and calls cut by a limit all count;
* ends in a controlled way when it reaches the time limit (the process is stopped) or the token
  budget (checked on the usage the provider reports), with a structured result;
* returns to the parent only the bounded response, and the harness records what was sent and
  what was received (``harness agent-contract calls``).

Absent the section, nothing here runs."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Final, cast

from governed_harness.agents import AgentCallResult, AgentExecutionResult
from governed_harness.agents.contract import (
    CONTRACT_VERSION,
    REQUEST_KEY,
    AgentCallRequestContract,
    AgentCallResponse,
    AuthorizedResources,
    ExecutionConstraints,
    FinalStatus,
    Iteration,
    LimitName,
    ReturnToParent,
    SizeName,
    TaskAssignment,
    Usage,
    check_answer,
    expected_response,
    limit_summary,
    minimal_task,
    objective_of,
    request_bytes,
    request_digest,
    validate_request_contract,
)
from governed_harness.agents.requests import CallKind
from governed_harness.agents.routing import classify_size
from governed_harness.agents.script_contracts import BLACK_BOX_NOTICE, brief, registry
from governed_harness.configuration.agent_contract import AgentContractConfig
from governed_harness.domain.enums import FindingSeverity, ResultStatus
from governed_harness.domain.models import Execution, PhaseExecution, Task
from governed_harness.orchestration.agent_contract_types import CallLimits, SentInfo
from governed_harness.orchestration.engine_types import AgentCallOutcome, PhaseOutcome

ITERATION_EVENT: Final = "agent.iteration"
RECORD_EVENT: Final = "agent.call.recorded"
EXHAUSTED_EVENT: Final = "agent.iterations.exhausted"
ITERATIONS_RULE: Final = "agent.iterations.exhausted"
LIMIT_RULE: Final = "agent.limit.reached"
VIOLATION_RULE: Final = "agent.contract.violation"
DENIED_PATHS: Final[tuple[str, ...]] = (".git", ".harness")

POSSIBLE_CAUSES: Final[tuple[str, ...]] = (
    "badly structured prompt: the task text is vague, contradictory or too long",
    "wrong or insufficient context: the agent lacks the files or facts it needs",
    "excessive context: so much is sent that the agent loses the point",
    "badly delimited task: it asks for more than one thing, or its limits are not clear",
)
RECOMMENDATION: Final = (
    "Do not run the task again unchanged. Restructure the input first: rewrite the task in one "
    "clear objective, name the files it owns and the criteria that prove it, split it when it "
    "asks for more than one thing, then start a new run."
)

_FINAL = {
    ResultStatus.PASSED: "PASSED",
    ResultStatus.FAILED: "FAILED",
    ResultStatus.BLOCKED: "BLOCKED",
    ResultStatus.CANCELLED: "CANCELLED",
}
_REPORTED = frozenset({"PASSED", "FAILED", "BLOCKED"})


class AgentContractFlow:
    def __init__(self, results: Any) -> None:
        self.results = results

    @property
    def config(self) -> AgentContractConfig | None:
        config: AgentContractConfig | None = self.results.project.agent_contract
        return config

    @property
    def enabled(self) -> bool:
        config = self.config
        return config is not None and config.enabled

    # ----- iterations ----------------------------------------------------------------------
    def used(self, execution: Execution, kind: str) -> int:
        """The iterations the ``kind`` agent has begun in this run."""
        return sum(
            1
            for item in self.results.s.events.list(execution.execution_id)
            if item.event_type == ITERATION_EVENT and item.payload.get("callKind") == kind
        )

    def begin(self, execution: Execution, phase: PhaseExecution, kind: str) -> PhaseOutcome | None:
        """Count the iteration a call is about to be; ``None`` when it may go ahead, else the
        blocked outcome of an agent that used its cap."""
        config = self.config
        if config is None or not config.enabled:
            return None
        used = self.used(execution, kind)
        if used >= config.iterations:
            return self._exhausted(execution, phase, kind, used, config.iterations)
        self.results.s.events.append(
            execution.execution_id,
            ITERATION_EVENT,
            {"callKind": kind, "number": used + 1, "max": config.iterations},
            phase_execution_id=phase.phase_execution_id,
        )
        return None

    def _exhausted(
        self, execution: Execution, phase: PhaseExecution, kind: str, used: int, cap: int
    ) -> PhaseOutcome:
        message = (
            f"{ITERATIONS_RULE}: the {kind} agent used {used} of {cap} iterations in this run "
            "without finishing. The input may be badly structured (a badly structured prompt, "
            "wrong or insufficient context, excessive context or a badly delimited task). "
            f"{RECOMMENDATION}"
        )
        events = self.results.s.events.list(execution.execution_id)
        if any(
            item.event_type == EXHAUSTED_EVENT and item.payload.get("callKind") == kind
            for item in events
        ):
            return PhaseOutcome(ResultStatus.BLOCKED, message)
        history = [
            {
                key: item.payload.get(key)
                for key in ("iteration", "finalStatus", "limitReached", "violations", "summary")
            }
            for item in events
            if item.event_type == RECORD_EVENT and item.payload.get("callKind") == kind
        ]
        record = {
            "callKind": kind,
            "used": used,
            "max": cap,
            "iterations": history,
            "possibleCauses": list(POSSIBLE_CAUSES),
            "recommendation": RECOMMENDATION,
        }
        ref: str = self.results.record_json(
            execution,
            phase.phase_id,
            record,
            kind="agent-iteration-cap",
            summary=f"The {kind} agent used its {cap} iterations",
        )
        self.results.s.events.append(
            execution.execution_id,
            EXHAUSTED_EVENT,
            {**record, "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        self.results.record_finding(
            execution,
            validator_id="harness.agent-contract",
            rule_id=ITERATIONS_RULE,
            category="agent-protocol",
            severity=FindingSeverity.HIGH,
            message=message,
            evidence_refs=(ref,),
            recommendation=RECOMMENDATION,
        )
        return PhaseOutcome(ResultStatus.BLOCKED, message, (ref,))

    # ----- limits --------------------------------------------------------------------------
    def limits(self, execution: Execution, task: Task) -> CallLimits:
        """The token budget and the time limit for the size the router gives the task."""
        config = self.config or AgentContractConfig()
        policy = self.results.project.agent_routing
        size, _rule = classify_size(
            self.results.task_signals(execution, task), policy.thresholds if policy else None
        )
        tokens, seconds = config.budget_for(size)
        return CallLimits(size, tokens, seconds)

    @staticmethod
    def timeout(default: int, limits: CallLimits) -> int:
        """The time a process may run: the command timeout, or the limit of the task's size
        when that is shorter."""
        return min(default, limits.seconds)

    # ----- the request ---------------------------------------------------------------------
    def apply_read_only(
        self, execution: Execution, kind: CallKind, request: dict[str, Any], limits: CallLimits
    ) -> SentInfo:
        """Reduce the task of a read-only request to the fields its kind needs and add the
        request contract."""
        task = request.get("task")
        kept: tuple[str, ...] = ()
        omitted: tuple[str, ...] = ()
        if isinstance(task, dict):
            request["task"] = minimal_task(kind, task)
            kept = tuple(request["task"])
            omitted = tuple(name for name in task if name not in request["task"])
        info = SentInfo(kept, omitted, self.used(execution, kind))
        resources = AuthorizedResources(
            read_paths=(str(request.get("workspace", ".")),),
            write_paths=(),
            deny_paths=DENIED_PATHS,
        )
        self._attach(request, kind, limits, resources, info, read_only=True)
        return info

    def apply_implement(
        self, execution: Execution, task: Task, extra: dict[str, Any], limits: CallLimits
    ) -> None:
        """Add the request contract, the script contracts and the black-box notice to the
        extras of an implement request."""
        owned = [str(item) for item in task.metadata.get("ownedPaths") or []]
        workspace = str(extra.get("workspace", "."))
        gate = extra.get("gate") or {}
        ids = self._script_ids(gate)
        contracts = registry(self.results.gate.validators())
        briefs = [brief(contracts[item]) for item in ids if item in contracts]
        if briefs:
            extra["scriptContracts"] = briefs
        manifest = extra.get("contextFiles")
        files = manifest.get("files", []) if isinstance(manifest, dict) else []
        resources = AuthorizedResources(
            read_paths=(workspace,),
            write_paths=tuple(owned) or (workspace,),
            guidance_paths=tuple(str(item["path"]) for item in files if item.get("path")),
            deny_paths=DENIED_PATHS,
            commands=(" ".join(gate["checkCommand"]),) if "checkCommand" in gate else (),
            scripts=tuple(ids),
        )
        info = SentInfo(("task", "plan"), (), self.used(execution, "implement"))
        self._attach(extra, "implement", limits, resources, info, read_only=False, task=task)

    @staticmethod
    def _script_ids(gate: dict[str, Any]) -> list[str]:
        if "checkCommand" in gate:
            return ["harness.check"]
        return [f"validator.{item['id']}" for item in gate.get("checkCommands") or []]

    def _attach(
        self,
        request: dict[str, Any],
        kind: CallKind,
        limits: CallLimits,
        resources: AuthorizedResources,
        info: SentInfo,
        *,
        read_only: bool,
        task: Task | None = None,
    ) -> None:
        config = self.config or AgentContractConfig()
        task_view = request.get("task") if task is None else task.model_dump(mode="json")
        view = task_view if isinstance(task_view, dict) else {}
        keys = sorted({*request, REQUEST_KEY, *(() if read_only else ("task", "plan"))})
        contract = AgentCallRequestContract(
            call_kind=kind,
            task=TaskAssignment(
                task_id=str(view.get("task_id", "")) or "-",
                call_kind=kind,
                objective=objective_of(view),
                fields=info.kept,
            ),
            allowed_context=tuple(keys),
            resources=resources,
            constraints=ExecutionConstraints(
                read_only=read_only, workspace=str(request.get("workspace", "."))
            ),
            token_budget=limits.tokens,
            time_limit_seconds=limits.seconds,
            size_class=cast(SizeName, limits.size),
            iteration=Iteration(number=max(1, info.iteration), max=config.iterations),
            expected_response=expected_response(kind, config),
            return_to_parent=ReturnToParent(),
        )
        request[REQUEST_KEY] = contract.model_dump(mode="json", by_alias=True)

    @staticmethod
    def black_box_notice() -> str:
        return BLACK_BOX_NOTICE

    # ----- the response --------------------------------------------------------------------
    def judge_limit(
        self, answer: AgentExecutionResult, limits: CallLimits
    ) -> tuple[str, str] | None:
        """The limit a finished call reached, as ``(name, description)``: its process was
        stopped at the time limit, or the usage the provider reported is above the token
        budget. ``None``: neither."""
        timed_out = answer.status is ResultStatus.TIMED_OUT or any(
            item.timed_out for item in answer.tool_invocations
        )
        if timed_out:
            return "time", f"the call was stopped at its time limit ({limits.seconds} s)"
        usage = answer.usage
        used = (
            (usage.input_tokens or 0) + (usage.output_tokens or 0) + (usage.reasoning_tokens or 0)
            if usage is not None
            else 0
        )
        if used > limits.tokens:
            return "tokens", f"the call used {used} tokens of a budget of {limits.tokens}"
        return None

    def settle(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        outcome: AgentCallOutcome,
        answer: AgentCallResult,
        *,
        request: dict[str, Any],
        request_ref: str,
        limits: CallLimits,
        sent: SentInfo,
    ) -> AgentCallOutcome:
        """Judge a finished read-only call against the contract (limit reached, response out
        of bounds), record what was sent and received, and return the outcome the parent sees."""
        config = self.config or AgentContractConfig()
        final, limit, violations, outcome = self._judge(
            kind, outcome, answer, limits, config, sent.iteration
        )
        summary, cut = limit_summary(outcome.summary, config.summary_chars)
        outcome = replace(outcome, summary=summary)
        response = self._response(
            kind, outcome, answer.execution, final, sent.iteration, limit, violations
        )
        self._record(
            execution,
            phase,
            kind,
            response,
            sent=sent,
            limits=limits,
            digest=request_digest(request),
            size=request_bytes(request),
            request_ref=request_ref,
            keys=sorted(request),
            answer=answer.execution,
            truncated=cut,
        )
        return outcome

    def _judge(
        self,
        kind: CallKind,
        outcome: AgentCallOutcome,
        answer: AgentCallResult,
        limits: CallLimits,
        config: AgentContractConfig,
        iteration: int,
    ) -> tuple[str, str | None, tuple[str, ...], AgentCallOutcome]:
        reached = self.judge_limit(answer.execution, limits)
        if reached is not None:
            name, text = reached
            message = f"{LIMIT_RULE}: {text}; the call ended and counts as iteration {iteration}"
            return (
                "LIMIT_REACHED",
                name,
                (),
                replace(outcome, status=ResultStatus.BLOCKED, summary=message, result=None),
            )
        problems = (
            check_answer(kind, outcome.result, config)
            if outcome.status is ResultStatus.PASSED
            else []
        )
        if problems:
            message = f"{VIOLATION_RULE}: {'; '.join(problems)}"
            return (
                "CONTRACT_VIOLATION",
                None,
                tuple(problems),
                replace(
                    outcome,
                    status=ResultStatus.ERROR,
                    summary=message,
                    result=None,
                    protocol_error=message,
                ),
            )
        return _FINAL.get(outcome.status, "ERROR"), None, (), outcome

    def _response(
        self,
        kind: CallKind,
        outcome: AgentCallOutcome,
        answer: AgentExecutionResult,
        final: str,
        iteration: int,
        limit: str | None,
        violations: tuple[str, ...],
    ) -> AgentCallResponse:
        usage = answer.usage
        reported = outcome.status.value if outcome.status.value in _REPORTED else None
        return AgentCallResponse(
            call_kind=kind,
            invocation_id=outcome.invocation_id,
            iteration=max(1, iteration),
            status=cast(Any, reported),
            final_status=cast(FinalStatus, final),
            summary=outcome.summary,
            result=outcome.result if final == "PASSED" else None,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
            )
            if usage is not None
            else None,
            limit_reached=cast(LimitName | None, limit),
            violations=violations,
        )

    def _record(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        response: AgentCallResponse,
        *,
        sent: SentInfo,
        limits: CallLimits,
        digest: str,
        size: int | None,
        request_ref: str | None,
        keys: list[str],
        answer: AgentExecutionResult,
        truncated: bool,
    ) -> str:
        received = response.model_dump(mode="json", by_alias=True)
        record = {
            "contractVersion": CONTRACT_VERSION,
            "callKind": kind,
            "iteration": sent.iteration,
            "provider": answer.invocation.provider,
            "invocationId": answer.invocation.invocation_id,
            "size": limits.size,
            "tokenBudget": limits.tokens,
            "timeLimitSeconds": limits.seconds,
            "sent": {
                "requestRef": request_ref,
                "digest": digest,
                "bytes": size,
                "topLevelKeys": keys,
                "taskFields": list(sent.kept),
                "taskFieldsOmitted": list(sent.omitted),
            },
            "received": received,
            "receivedBytes": len(json.dumps(received, sort_keys=True).encode("utf-8")),
            "summaryTruncated": truncated,
            "raw": {
                "outputRef": answer.output_ref,
                "note": "The provider's full output, kept for diagnosis; the parent does not "
                "receive it.",
            },
        }
        ref: str = self.results.record_json(
            execution,
            phase.phase_id,
            record,
            kind="agent-call-record",
            summary=f"{kind} call {sent.iteration}: sent and received ({response.final_status})",
        )
        self.results.s.events.append(
            execution.execution_id,
            RECORD_EVENT,
            {
                "callKind": kind,
                "iteration": sent.iteration,
                "finalStatus": response.final_status,
                "limitReached": response.limit_reached,
                "violations": list(response.violations),
                "summary": response.summary[:200],
                "invocationId": record["invocationId"],
                "provider": record["provider"],
                "size": limits.size,
                "sentBytes": size,
                "receivedBytes": record["receivedBytes"],
                "evidenceRef": ref,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        if response.final_status in {"LIMIT_REACHED", "CONTRACT_VIOLATION"}:
            self._finding(execution, kind, response, ref)
        return ref

    def _finding(
        self, execution: Execution, kind: str, response: AgentCallResponse, ref: str
    ) -> None:
        rule = LIMIT_RULE if response.final_status == "LIMIT_REACHED" else VIOLATION_RULE
        self.results.record_finding(
            execution,
            validator_id="harness.agent-contract",
            rule_id=rule,
            category="agent-protocol",
            severity=FindingSeverity.HIGH
            if response.final_status == "LIMIT_REACHED"
            else FindingSeverity.MEDIUM,
            message=response.summary[:1000],
            evidence_refs=(ref,),
            recommendation="Narrow the task or raise the limit of its size (agentContract) "
            "and run again; the provider's output is kept as evidence.",
        )

    # ----- implement -----------------------------------------------------------------------
    def settle_implement(
        self,
        execution: Execution,
        phase: PhaseExecution,
        answer: AgentExecutionResult,
        contract: dict[str, Any],
    ) -> PhaseOutcome | None:
        """Record an implement call and end it in a controlled way when it reached its time
        limit or its token budget; ``None`` otherwise (the phase goes on as before)."""
        config = self.config or AgentContractConfig()
        validated = validate_request_contract(contract)
        limits = CallLimits(
            validated.size_class, validated.token_budget, validated.time_limit_seconds
        )
        iteration = validated.iteration.number
        reached = self.judge_limit(answer, limits)
        status = ResultStatus.BLOCKED if reached is not None else answer.status
        final = "LIMIT_REACHED" if reached is not None else _FINAL.get(status, "ERROR")
        message = (
            f"{LIMIT_RULE}: {reached[1]}; the call ended and counts as iteration {iteration}"
            if reached is not None
            else answer.summary
        )
        summary, cut = limit_summary(message, config.summary_chars)
        response = AgentCallResponse(
            call_kind="implement",
            invocation_id=answer.invocation.invocation_id,
            iteration=iteration,
            status=cast(Any, status.value if status.value in _REPORTED else None),
            final_status=cast(FinalStatus, final),
            summary=summary,
            limit_reached=cast(LimitName | None, reached[0] if reached is not None else None),
        )
        ref = self._record(
            execution,
            phase,
            "implement",
            response,
            sent=SentInfo(validated.task.fields, (), iteration),
            limits=limits,
            digest=answer.invocation.prompt_digest,
            size=None,
            request_ref=None,
            keys=list(validated.allowed_context),
            answer=answer,
            truncated=cut,
        )
        if reached is None:
            return None
        return PhaseOutcome(ResultStatus.BLOCKED, summary, (ref,))
