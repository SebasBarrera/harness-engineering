from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.events import EventChainError, SQLiteEventStore


def test_chain_verification_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    store = SQLiteEventStore(path)
    store.append("run_1", "run.started", {"a": 1})
    store.append("run_1", "phase.started", {"b": 2})
    store.connection.execute("UPDATE events SET payload_json='{}' WHERE execution_sequence=1")
    store.connection.commit()
    with pytest.raises(EventChainError):
        store.verify_chain("run_1")
    store.close()


def test_chains_are_independent_per_execution(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")
    one = store.append("run_1", "run.started", {})
    two = store.append("run_2", "run.started", {})
    assert one.sequence == two.sequence == 1
    assert one.previous_digest is None and two.previous_digest is None
    store.close()
