from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.models import Actor, AgentInvocation, Provenance, ToolInvocation
from governed_harness.events import SQLiteEventStore
from governed_harness.storage import SQLiteStateStore
from governed_harness.telemetry import MetricsProjector


def test_metrics_use_persisted_invocation_times(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    state = SQLiteStateStore(database)
    events = SQLiteEventStore(database)
    start = datetime.now(UTC)
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.test")
    state.put(
        "agent_invocation",
        "agentinv_001",
        AgentInvocation(
            invocation_id="agentinv_001",
            execution_id="run_001",
            phase_id=PhaseId.IMPLEMENTATION,
            actor=actor,
            provider="test",
            started_at=start,
            finished_at=start + timedelta(milliseconds=50),
            status=ResultStatus.PASSED,
            prompt_digest="sha256:" + "a" * 64,
        ),
        execution_id="run_001",
    )
    events.append("run_001", "run.created", {})
    events.append("run_001", "run.closed", {})
    projected = MetricsProjector(state).project("run_001", events.list("run_001"))
    assert projected["agent.invocations"].value == 1
    assert projected["duration.agent_ms"].value == 50
    assert projected["tokens.input"].value is None
    events.close()
    state.close()
