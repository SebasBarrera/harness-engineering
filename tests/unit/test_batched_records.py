"""The batched writes of VERIFICATION (2.0.1): ``append_many`` writes the chain one ``append`` per
event writes (same envelope, sequence and digest links) in one transaction, and ``put_many``
stores what ``put`` stores."""

from __future__ import annotations

from pathlib import Path

from governed_harness.domain.models import HARNESS_ACTOR
from governed_harness.events.sqlite_store import SQLiteEventStore
from governed_harness.storage.sqlite import SQLiteStateStore


def test_append_many_writes_the_chain_one_append_per_event_writes(tmp_path: Path) -> None:
    events = [("finding.recorded", {"n": index}, HARNESS_ACTOR) for index in range(5)]
    with SQLiteEventStore(tmp_path / "one.db") as one, SQLiteEventStore(tmp_path / "b.db") as many:
        one.append("run_1", "phase.started", {"p": 1})
        many.append("run_1", "phase.started", {"p": 1})
        for event_type, payload, actor in events:
            one.append("run_1", event_type, payload, actor=actor)
        stored = many.append_many("run_1", events)
        assert [item.sequence for item in stored] == [2, 3, 4, 5, 6]
        assert many.verify_chain("run_1") and one.verify_chain("run_1")
        single, batched = one.list("run_1"), many.list("run_1")
        assert len(single) == len(batched) == 6
        for left, right in zip(single, batched, strict=True):
            a, b = left.as_dict(), right.as_dict()
            for name in ("eventId", "occurredAt", "eventDigest", "previousEventDigest"):
                a.pop(name), b.pop(name)
            assert a == b
        assert many.append_many("run_1", []) == []


def test_put_many_stores_what_put_stores(tmp_path: Path) -> None:
    with SQLiteStateStore(tmp_path / "state.db") as state:
        state.put_many(
            [("finding", "f1", {"a": 1}), ("finding", "f2", {"a": 2})],
            execution_id="run_1",
            project_id="p",
        )
        assert state.get_dict("finding", "f2") == {"a": 2}
        assert len(state.list_dicts("finding", execution_id="run_1")) == 2
