from pathlib import Path

from governed_harness.events.sqlite_store import SQLiteEventStore


def test_event_chain(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")
    first = store.append("run_001", "run.started", {"task": "task_001"})
    second = store.append("run_001", "phase.started", {"phase": "INTENT"})
    assert second.previous_digest == first.event_digest
    assert [event.sequence for event in store.list("run_001")] == [1, 2]
    store.close()
