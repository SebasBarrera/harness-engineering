from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from governed_harness.domain.enums import MetricQuality, ResultStatus
from governed_harness.domain.models import (
    AgentInvocation,
    ChangeSet,
    Finding,
    HumanDecision,
    PhaseExecution,
    ResourceUsage,
    ToolInvocation,
    ValidationResult,
)
from governed_harness.events.sqlite_store import StoredEvent
from governed_harness.storage.sqlite import SQLiteStateStore


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: int | float | None
    unit: str
    quality: MetricQuality
    definition: str
    source: str
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "definition": self.definition,
            "source": self.source,
            "limitations": list(self.limitations),
        }


class MetricsProjector:
    def __init__(self, state: SQLiteStateStore) -> None:
        self.state = state

    def project(self, execution_id: str, events: list[StoredEvent]) -> dict[str, MetricValue]:
        phases = self.state.list("phase", PhaseExecution, execution_id=execution_id)
        agents = self.state.list("agent_invocation", AgentInvocation, execution_id=execution_id)
        tools = self.state.list("tool_invocation", ToolInvocation, execution_id=execution_id)
        validations = self.state.list("validation", ValidationResult, execution_id=execution_id)
        changes = self.state.list("change_set", ChangeSet, execution_id=execution_id)
        decisions = self.state.list("decision", HumanDecision, execution_id=execution_id)
        usages = self.state.list("resource_usage", ResourceUsage, execution_id=execution_id)
        timestamps = [datetime.fromisoformat(event.occurred_at) for event in events]
        total_ms = (
            int((max(timestamps) - min(timestamps)).total_seconds() * 1000)
            if len(timestamps) >= 2
            else 0
        )
        phase_ms = sum(
            int((phase.finished_at - phase.started_at).total_seconds() * 1000)
            for phase in phases
            if phase.started_at and phase.finished_at
        )
        agent_ms = sum(
            int((item.finished_at - item.started_at).total_seconds() * 1000) for item in agents
        )
        tool_ms = sum(
            int((item.finished_at - item.started_at).total_seconds() * 1000) for item in tools
        )
        human_wait_ms: int | None = None
        gate_events = [event for event in events if event.event_type == "gate.evaluated"]
        decision_events = [
            event for event in events if event.event_type == "human.decision.recorded"
        ]
        if gate_events and decision_events:
            decision_time = datetime.fromisoformat(decision_events[-1].occurred_at)
            eligible_gates = [
                event
                for event in gate_events
                if datetime.fromisoformat(event.occurred_at) <= decision_time
            ]
            if eligible_gates:
                human_wait_ms = int(
                    (
                        decision_time - datetime.fromisoformat(eligible_gates[-1].occurred_at)
                    ).total_seconds()
                    * 1000
                )
        corrections = [event for event in events if event.event_type == "correction.authorized"]
        correction_cycles = len(corrections)
        verification_cycles = sum(
            1 for event in corrections if event.payload.get("trigger") == "VERIFICATION_FAILED"
        )
        transient_retries = sum(
            1 for event in events if event.event_type == "agent.invocation.retried"
        )
        unsupported_claims = sum(
            1
            for item in self.state.list("finding", Finding, execution_id=execution_id)
            if item.rule_id == "agent.unsupported-claim"
        )
        implementation_attempts = sum(
            1
            for event in events
            if event.event_type == "phase.started"
            and event.payload.get("phaseId") == "IMPLEMENTATION"
        )
        review_cycles = sum(
            1
            for event in events
            if event.event_type == "phase.started"
            and event.payload.get("phaseId") == "INDEPENDENT_REVIEW"
        )
        replans = sum(1 for event in events if event.event_type == "plan.replaced")
        failed_validations = sum(
            1 for item in validations if item.status is not ResultStatus.PASSED
        )
        files_changed = len({file.path for change in changes for file in change.files})
        input_tokens = self._sum_optional(usages, "input_tokens")
        output_tokens = self._sum_optional(usages, "output_tokens")
        reasoning_tokens = self._sum_optional(usages, "reasoning_tokens")
        cost = self._sum_optional(usages, "cost_usd")
        common = {
            "duration.total_ms": MetricValue(
                "duration.total_ms",
                total_ms,
                "ms",
                MetricQuality.OBSERVED,
                "Wall-clock time between the first and last persisted execution event.",
                "event store",
            ),
            "duration.phase_ms": MetricValue(
                "duration.phase_ms",
                phase_ms,
                "ms",
                MetricQuality.DERIVED,
                "Sum of completed phase wall-clock durations; parallel phases would be double-counted.",
                "phase records",
                ("Not equal to total wall time when phases overlap.",),
            ),
            "duration.agent_ms": MetricValue(
                "duration.agent_ms",
                agent_ms,
                "ms",
                MetricQuality.DERIVED,
                "Sum of persisted agent invocation durations.",
                "agent invocation records",
            ),
            "duration.tool_ms": MetricValue(
                "duration.tool_ms",
                tool_ms,
                "ms",
                MetricQuality.DERIVED,
                "Sum of persisted tool invocation durations.",
                "tool invocation records",
            ),
            "duration.human_wait_ms": MetricValue(
                "duration.human_wait_ms",
                human_wait_ms,
                "ms",
                MetricQuality.DERIVED if human_wait_ms is not None else MetricQuality.NOT_AVAILABLE,
                "Elapsed time from the latest gate evaluation to the latest human decision.",
                "gate and decision events",
                () if human_wait_ms is not None else ("No completed human-decision interval.",),
            ),
            "agent.invocations": MetricValue(
                "agent.invocations",
                len(agents),
                "count",
                MetricQuality.OBSERVED,
                "Number of persisted agent invocation records.",
                "agent invocation records",
            ),
            "tool.invocations": MetricValue(
                "tool.invocations",
                len(tools),
                "count",
                MetricQuality.OBSERVED,
                "Number of persisted tool invocation records.",
                "tool invocation records",
            ),
            "implementation.attempts": MetricValue(
                "implementation.attempts",
                implementation_attempts,
                "count",
                MetricQuality.DERIVED,
                "Count of IMPLEMENTATION phase-start events.",
                "event store",
            ),
            "correction.cycles": MetricValue(
                "correction.cycles",
                correction_cycles,
                "count",
                MetricQuality.OBSERVED,
                "Count of authorized transitions back to IMPLEMENTATION: REQUEST_CHANGES "
                "decisions and automatic corrections after a failed VERIFICATION.",
                "event store",
            ),
            "correction.verification_cycles": MetricValue(
                "correction.verification_cycles",
                verification_cycles,
                "count",
                MetricQuality.OBSERVED,
                "Automatic corrections after a failed VERIFICATION (runtime.verificationCorrections).",
                "event store",
            ),
            "agent.transient_retries": MetricValue(
                "agent.transient_retries",
                transient_retries,
                "count",
                MetricQuality.OBSERVED,
                "Command-provider calls repeated after a transient failure (runtime.providerRetries).",
                "event store",
            ),
            "agent.unsupported_claims": MetricValue(
                "agent.unsupported_claims",
                unsupported_claims,
                "count",
                MetricQuality.DERIVED,
                "Findings agent.unsupported-claim: the agent reported success and verification failed.",
                "finding records",
            ),
            "review.cycles": MetricValue(
                "review.cycles",
                review_cycles,
                "count",
                MetricQuality.DERIVED,
                "Count of INDEPENDENT_REVIEW phase-start events.",
                "event store",
            ),
            "replanning.count": MetricValue(
                "replanning.count",
                replans,
                "count",
                MetricQuality.OBSERVED,
                "Count of plan replacement events after the first accepted plan.",
                "event store",
            ),
            "validation.non_passed": MetricValue(
                "validation.non_passed",
                failed_validations,
                "count",
                MetricQuality.DERIVED,
                "Validation results whose normalized status is not PASSED.",
                "validation records",
            ),
            "changesets.count": MetricValue(
                "changesets.count",
                len(changes),
                "count",
                MetricQuality.OBSERVED,
                "Number of distinct persisted ChangeSet records.",
                "ChangeSet records",
            ),
            "changeset.files": MetricValue(
                "changeset.files",
                files_changed,
                "count",
                MetricQuality.DERIVED,
                "Unique paths appearing in persisted ChangeSets.",
                "ChangeSet records",
            ),
            "human.decisions": MetricValue(
                "human.decisions",
                len(decisions),
                "count",
                MetricQuality.OBSERVED,
                "Number of persisted human decisions.",
                "decision records",
            ),
            "tokens.input": self._token_metric("tokens.input", input_tokens, "input_tokens"),
            "tokens.output": self._token_metric("tokens.output", output_tokens, "output_tokens"),
            "tokens.reasoning": self._token_metric(
                "tokens.reasoning", reasoning_tokens, "reasoning_tokens"
            ),
            "cost.usd": MetricValue(
                "cost.usd",
                cost,
                "USD",
                MetricQuality.REPORTED if cost is not None else MetricQuality.NOT_AVAILABLE,
                "Sum of provider-reported costs; never inferred from tokens.",
                "resource usage records",
                () if cost is not None else ("The provider did not report cost.",),
            ),
        }
        return common

    @staticmethod
    def _sum_optional(records: list[ResourceUsage], field: str) -> int | float | None:
        values = [
            getattr(record, field) for record in records if getattr(record, field) is not None
        ]
        return sum(values) if values else None

    @staticmethod
    def _token_metric(name: str, value: int | float | None, field: str) -> MetricValue:
        return MetricValue(
            name,
            value,
            "tokens",
            MetricQuality.REPORTED if value is not None else MetricQuality.NOT_AVAILABLE,
            f"Sum of provider-reported {field}; no estimation is performed.",
            "resource usage records",
            () if value is not None else ("The provider did not expose this token category.",),
        )
