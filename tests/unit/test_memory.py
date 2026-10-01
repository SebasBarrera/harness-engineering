from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from governed_harness.domain.enums import ActorType, MemoryLevel
from governed_harness.domain.models import Actor, MemoryRecord, Provenance
from governed_harness.memory import MemoryStore, context_manifest
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


def record(memory_id: str, **fields: object) -> MemoryRecord:
    defaults: dict[str, object] = {
        "project_id": "project_1",
        "level": MemoryLevel.PROJECT,
        "key": memory_id,
        "value": {"text": memory_id},
        "provenance": provenance(),
    }
    return MemoryRecord(memory_id=memory_id, **{**defaults, **fields})  # type: ignore[arg-type]


def test_selection_records_why_a_candidate_did_not_enter_the_context(tmp_path: Path) -> None:
    """RD-05: a record that does not enter the context leaves a trace with its reason."""
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    now = datetime.now(UTC)
    store.put(record("mem_active", approved=True))
    store.put(record("mem_expired", approved=True, valid_until=now - timedelta(seconds=1)))
    store.put(record("mem_proposal"))
    store.put(record("mem_old", approved=True))
    store.put(record("mem_new", approved=True, supersedes="mem_old"))
    selection = store.select(project_id="project_1", task_id=None, execution_id=None)
    assert [item.memory_id for item in selection.records] == ["mem_active", "mem_new"]
    reasons = {item.memory_id: (item.reason, item.superseded_by) for item in selection.exclusions}
    assert reasons == {
        "mem_expired": ("expired", None),
        "mem_proposal": ("unapproved", None),
        "mem_old": ("superseded", "mem_new"),
    }
    state.close()


def test_an_unapproved_record_does_not_supersede_an_approved_one(tmp_path: Path) -> None:
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    store.put(record("mem_rule", approved=True))
    store.put(record("mem_proposal", supersedes="mem_rule"))
    selection = store.select(project_id="project_1", task_id=None, execution_id=None)
    assert [item.memory_id for item in selection.records] == ["mem_rule"]
    assert [(item.memory_id, item.reason) for item in selection.exclusions] == [
        ("mem_proposal", "unapproved")
    ]
    state.close()


def test_an_expired_approved_record_still_supersedes_its_target(tmp_path: Path) -> None:
    """An invalidation is an approved record that is already expired: it removes its target
    from every context and does not enter one itself."""
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    now = datetime.now(UTC)
    store.put(record("mem_rule", approved=True))
    store.put(record("mem_tombstone", approved=True, supersedes="mem_rule", valid_until=now))
    selection = store.select(project_id="project_1", task_id=None, execution_id=None)
    assert selection.records == ()
    assert {item.memory_id: item.reason for item in selection.exclusions} == {
        "mem_rule": "superseded",
        "mem_tombstone": "expired",
    }
    state.close()


def test_records_beyond_the_limit_are_reported_as_excluded(tmp_path: Path) -> None:
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    for index in range(3):
        store.put(record(f"mem_{index}", approved=True))
    selection = store.select(project_id="project_1", task_id=None, execution_id=None, max_records=2)
    assert [item.memory_id for item in selection.records] == ["mem_0", "mem_1"]
    assert [(item.memory_id, item.reason) for item in selection.exclusions] == [("mem_2", "limit")]
    state.close()


def test_manifest_lists_exclusions_and_withholds_sensitive_values(tmp_path: Path) -> None:
    state = SQLiteStateStore(tmp_path / "state.db")
    store = MemoryStore(state)
    store.put(record("mem_public", approved=True))
    store.put(record("mem_secret", approved=True, sensitive=True))
    store.put(record("mem_proposal"))
    selection = store.select(project_id="project_1", task_id=None, execution_id=None)
    manifest = context_manifest(selection.records, selection.exclusions)
    by_id = {item["memoryId"]: item for item in manifest["records"]}  # type: ignore[union-attr]
    assert by_id["mem_public"]["value"] == {"text": "mem_public"}
    assert "sensitive" not in by_id["mem_public"]
    assert by_id["mem_secret"]["value"] is None
    assert by_id["mem_secret"]["sensitive"] is True
    assert manifest["excluded"] == [
        {
            "memoryId": "mem_proposal",
            "level": MemoryLevel.PROJECT,
            "key": "mem_proposal",
            "reason": "unapproved",
        }
    ]
    assert str(manifest["digest"]).startswith("sha256:")
    state.close()
