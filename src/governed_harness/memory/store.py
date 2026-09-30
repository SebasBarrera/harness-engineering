from __future__ import annotations

from datetime import UTC, datetime

from governed_harness.domain.enums import MemoryLevel
from governed_harness.domain.models import MemoryRecord
from governed_harness.storage.sqlite import SQLiteStateStore


class MemoryStore:
    def __init__(self, state: SQLiteStateStore) -> None:
        self.state = state

    def put(self, record: MemoryRecord) -> None:
        self.state.put(
            "memory",
            record.memory_id,
            record,
            execution_id=record.execution_id,
            project_id=record.project_id,
        )

    def list_project(self, project_id: str) -> list[MemoryRecord]:
        return self.state.list("memory", MemoryRecord, project_id=project_id)

    def select_context(
        self,
        *,
        project_id: str,
        task_id: str | None,
        execution_id: str | None,
        max_records: int = 50,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        instant = now or datetime.now(UTC)
        records = self.list_project(project_id)
        eligible: list[MemoryRecord] = []
        for record in records:
            if record.valid_until and record.valid_until <= instant:
                continue
            if record.supersedes and any(item.memory_id == record.supersedes for item in eligible):
                eligible = [item for item in eligible if item.memory_id != record.supersedes]
            if record.level in {MemoryLevel.NORMATIVE, MemoryLevel.PROJECT, MemoryLevel.RETROSPECTIVE}:
                if not record.approved:
                    continue
            elif record.level is MemoryLevel.TASK and record.task_id != task_id:
                continue
            elif record.level is MemoryLevel.EPHEMERAL and record.execution_id != execution_id:
                continue
            eligible.append(record)
        order = {
            MemoryLevel.NORMATIVE: 0,
            MemoryLevel.PROJECT: 1,
            MemoryLevel.TASK: 2,
            MemoryLevel.RETROSPECTIVE: 3,
            MemoryLevel.EPHEMERAL: 4,
        }
        eligible.sort(key=lambda item: (order[item.level], item.key, item.created_at, item.memory_id))
        return tuple(eligible[:max_records])
