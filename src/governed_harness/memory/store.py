from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from governed_harness.domain.enums import MemoryLevel
from governed_harness.domain.models import MemoryRecord
from governed_harness.storage.sqlite import SQLiteStateStore

ExclusionReason = Literal["superseded", "expired", "unapproved", "limit"]

# Levels that only enter a context after an explicit approval.
APPROVAL_REQUIRED = frozenset(
    {MemoryLevel.NORMATIVE, MemoryLevel.PROJECT, MemoryLevel.RETROSPECTIVE}
)

_LEVEL_ORDER = {
    MemoryLevel.NORMATIVE: 0,
    MemoryLevel.PROJECT: 1,
    MemoryLevel.TASK: 2,
    MemoryLevel.RETROSPECTIVE: 3,
    MemoryLevel.EPHEMERAL: 4,
}


@dataclass(frozen=True)
class MemoryExclusion:
    """A record that was in scope for a context but did not enter it, with the reason."""

    memory_id: str
    level: MemoryLevel
    key: str
    reason: ExclusionReason
    superseded_by: str | None = None


@dataclass(frozen=True)
class ContextSelection:
    records: tuple[MemoryRecord, ...]
    exclusions: tuple[MemoryExclusion, ...]


def is_effective(record: MemoryRecord) -> bool:
    """Whether a record carries authority: approved, or of a level that needs no approval."""
    return record.approved or record.level not in APPROVAL_REQUIRED


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

    def get(self, memory_id: str) -> MemoryRecord:
        return self.state.get("memory", memory_id, MemoryRecord)

    def list_project(self, project_id: str) -> list[MemoryRecord]:
        return self.state.list("memory", MemoryRecord, project_id=project_id)

    def select(
        self,
        *,
        project_id: str,
        task_id: str | None,
        execution_id: str | None,
        max_records: int = 50,
        now: datetime | None = None,
    ) -> ContextSelection:
        """Select the records that enter a context and say why each other candidate did not.

        Task records are candidates only for their task and ephemeral records only for their
        execution; records of other tasks or executions are out of scope and are not listed. A
        record supersedes another only when it carries authority itself, so an unapproved
        proposal never displaces an approved record.
        """
        instant = now or datetime.now(UTC)
        candidates = [
            record
            for record in self.list_project(project_id)
            if not (
                record.level is MemoryLevel.TASK
                and record.task_id != task_id
                or record.level is MemoryLevel.EPHEMERAL
                and record.execution_id != execution_id
            )
        ]
        superseded_by = {
            record.supersedes: record.memory_id
            for record in candidates
            if record.supersedes and is_effective(record)
        }
        eligible: list[MemoryRecord] = []
        exclusions: list[MemoryExclusion] = []

        def exclude(record: MemoryRecord, reason: ExclusionReason) -> None:
            exclusions.append(
                MemoryExclusion(
                    memory_id=record.memory_id,
                    level=record.level,
                    key=record.key,
                    reason=reason,
                    superseded_by=superseded_by.get(record.memory_id),
                )
            )

        for record in candidates:
            if record.memory_id in superseded_by:
                exclude(record, "superseded")
            elif record.valid_until and record.valid_until <= instant:
                exclude(record, "expired")
            elif not is_effective(record):
                exclude(record, "unapproved")
            else:
                eligible.append(record)
        eligible.sort(
            key=lambda item: (_LEVEL_ORDER[item.level], item.key, item.created_at, item.memory_id)
        )
        for record in eligible[max_records:]:
            exclude(record, "limit")
        exclusions.sort(key=lambda item: (_LEVEL_ORDER[item.level], item.key, item.memory_id))
        return ContextSelection(tuple(eligible[:max_records]), tuple(exclusions))

    def select_context(
        self,
        *,
        project_id: str,
        task_id: str | None,
        execution_id: str | None,
        max_records: int = 50,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        return self.select(
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            max_records=max_records,
            now=now,
        ).records
