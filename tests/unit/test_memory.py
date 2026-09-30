from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from governed_harness.domain.enums import ActorType, MemoryLevel
from governed_harness.domain.models import Actor, MemoryRecord, Provenance
from governed_harness.memory import MemoryStore
from governed_harness.storage import SQLiteStateStore


def provenance() -> Provenance:
    return Provenance(
        actor=Actor(actor_type=ActorType.HUMAN, actor_id="human.owner"), core_version="test"
    )


def test_context_selection_is_level_ordered_and_scoped(tmp_path: Path) -> None:
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    records = [
        MemoryRecord(
            memory_id="mem_task",
            project_id="project_1",
            task_id="task_1",
            level=MemoryLevel.TASK,
            key="task",
            value={},
            provenance=provenance(),
        ),
        MemoryRecord(
            memory_id="mem_project",
            project_id="project_1",
            level=MemoryLevel.PROJECT,
            key="project",
            value={},
            provenance=provenance(),
            approved=True,
        ),
        MemoryRecord(
            memory_id="mem_norm",
            project_id="project_1",
            level=MemoryLevel.NORMATIVE,
            key="norm",
            value={},
            provenance=provenance(),
            approved=True,
        ),
        MemoryRecord(
            memory_id="mem_other",
            project_id="project_1",
            task_id="task_2",
            level=MemoryLevel.TASK,
            key="other",
            value={},
            provenance=provenance(),
        ),
    ]
    for record in records:
        store.put(record)
    selected = store.select_context(project_id="project_1", task_id="task_1", execution_id="run_1")
    assert [item.memory_id for item in selected] == ["mem_norm", "mem_project", "mem_task"]
    state.close()


def test_expired_memory_is_ignored(tmp_path: Path) -> None:
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    store.put(
        MemoryRecord(
            memory_id="mem_expired",
            project_id="project_1",
            level=MemoryLevel.PROJECT,
            key="expired",
            value={},
            provenance=provenance(),
            approved=True,
            valid_until=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    assert not store.select_context(project_id="project_1", task_id=None, execution_id=None)
    state.close()
