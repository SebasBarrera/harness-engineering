from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from governed_harness.domain.models import HARNESS_ACTOR, Actor
from governed_harness.evidence.hashing import sha256_json


@dataclass(frozen=True)
class StoredEvent:
    event_id: str
    sequence: int
    execution_id: str
    event_type: str
    occurred_at: str
    actor: dict[str, Any]
    payload: dict[str, Any]
    previous_digest: str | None
    event_digest: str
    phase_execution_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    redaction_version: str = "1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "eventId": self.event_id,
            "executionId": self.execution_id,
            "phaseExecutionId": self.phase_execution_id,
            "causationId": self.causation_id,
            "correlationId": self.correlation_id,
            "sequence": self.sequence,
            "eventType": self.event_type,
            "occurredAt": self.occurred_at,
            "actor": self.actor,
            "payload": self.payload,
            "previousEventDigest": self.previous_digest,
            "eventDigest": self.event_digest,
            "redactionVersion": self.redaction_version,
        }


class EventChainError(ValueError):
    pass


@dataclass(frozen=True)
class ChainCheck:
    """The result of walking the event chain of one run."""

    valid: bool
    event_count: int
    head_digest: str | None
    """Digest of the last event that verified (the head of the chain when ``valid``)."""
    head_sequence: int | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "eventCount": self.event_count,
            "headSequence": self.head_sequence,
            "headDigest": self.head_digest,
            "error": self.error,
        }


class SQLiteEventStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._lock = threading.RLock()
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              global_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              execution_id TEXT NOT NULL,
              execution_sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              actor_json TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              phase_execution_id TEXT,
              causation_id TEXT,
              correlation_id TEXT,
              previous_digest TEXT,
              event_digest TEXT NOT NULL UNIQUE,
              redaction_version TEXT NOT NULL,
              UNIQUE(execution_id, execution_sequence)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS events_execution_idx ON events(execution_id, execution_sequence)"
        )
        self.connection.commit()

    def append(
        self,
        execution_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: Actor = HARNESS_ACTOR,
        event_id: str | None = None,
        phase_execution_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        redaction_version: str = "1",
    ) -> StoredEvent:
        from governed_harness.domain.ids import new_id

        event_id = event_id or new_id("evt")
        occurred_at = datetime.now(UTC).isoformat()
        actor_payload = actor.model_dump(mode="json", by_alias=True)
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT execution_sequence,event_digest FROM events WHERE execution_id=? "
                "ORDER BY execution_sequence DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
            sequence = int(row["execution_sequence"]) + 1 if row else 1
            previous = row["event_digest"] if row else None
            envelope = {
                "schemaVersion": "1.0",
                "eventId": event_id,
                "executionId": execution_id,
                "phaseExecutionId": phase_execution_id,
                "causationId": causation_id,
                "correlationId": correlation_id,
                "sequence": sequence,
                "eventType": event_type,
                "occurredAt": occurred_at,
                "actor": actor_payload,
                "payload": payload,
                "previousEventDigest": previous,
                "redactionVersion": redaction_version,
            }
            digest = sha256_json(envelope)
            self.connection.execute(
                """
                INSERT INTO events(
                    event_id,execution_id,execution_sequence,event_type,occurred_at,
                    actor_json,payload_json,phase_execution_id,causation_id,correlation_id,
                    previous_digest,event_digest,redaction_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    execution_id,
                    sequence,
                    event_type,
                    occurred_at,
                    json.dumps(actor_payload, sort_keys=True),
                    json.dumps(payload, sort_keys=True, default=str),
                    phase_execution_id,
                    causation_id,
                    correlation_id,
                    previous,
                    digest,
                    redaction_version,
                ),
            )
        return StoredEvent(
            event_id,
            sequence,
            execution_id,
            event_type,
            occurred_at,
            actor_payload,
            payload,
            previous,
            digest,
            phase_execution_id,
            causation_id,
            correlation_id,
            redaction_version,
        )

    def list(self, execution_id: str) -> list[StoredEvent]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE execution_id=? ORDER BY execution_sequence",
            (execution_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_all(self, *, limit: int | None = None) -> builtins.list[StoredEvent]:
        query = "SELECT * FROM events ORDER BY global_sequence"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        return [self._from_row(row) for row in self.connection.execute(query, params).fetchall()]

    def execution_ids(self) -> builtins.list[str]:
        """Every run that has events, in the order of its first event."""
        rows = self.connection.execute(
            "SELECT execution_id FROM events GROUP BY execution_id ORDER BY MIN(global_sequence)"
        ).fetchall()
        return [str(row["execution_id"]) for row in rows]

    def delete_execution(self, execution_id: str) -> int:
        """Delete every event of a run (``retention.eventDays``); returns how many."""
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM events WHERE execution_id=?", (execution_id,)
            )
        return int(cursor.rowcount)

    def count(self, execution_id: str | None = None) -> int:
        if execution_id is None:
            row = self.connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM events WHERE execution_id=?", (execution_id,)
            ).fetchone()
        return int(row["count"])

    def verify_chain(self, execution_id: str) -> bool:
        """``True`` when the chain of the run is intact; raises :class:`EventChainError` at the
        first broken link. :meth:`check_chain` reports the same check without raising."""
        check = self.check_chain(execution_id)
        if not check.valid:
            raise EventChainError(check.error or "event chain is invalid")
        return True

    def check_chain(self, execution_id: str) -> ChainCheck:
        """Walk the chain of a run: sequence without gaps, each event linked to the digest of the
        one before it and each digest recomputed from its envelope. A malformed row (a payload
        that is not JSON) is a broken link, not an exception."""
        previous: str | None = None
        expected_sequence = 1
        try:
            events = self.list(execution_id)
        except (ValueError, TypeError) as unreadable:
            return ChainCheck(False, 0, None, None, f"unreadable event: {unreadable}")
        for event in events:
            error: str | None = None
            if event.sequence != expected_sequence:
                error = f"event sequence gap: expected {expected_sequence}, got {event.sequence}"
            elif event.previous_digest != previous:
                error = f"previous digest mismatch at event {event.event_id}"
            else:
                envelope = event.as_dict()
                envelope.pop("eventDigest")
                if sha256_json(envelope) != event.event_digest:
                    error = f"event digest mismatch at event {event.event_id}"
            if error is not None:
                return ChainCheck(
                    False, len(events), previous, expected_sequence - 1 or None, error
                )
            previous = event.event_digest
            expected_sequence += 1
        return ChainCheck(True, len(events), previous, (expected_sequence - 1) or None, None)

    def export_jsonl(self, execution_id: str) -> bytes:
        return (
            "\n".join(
                json.dumps(event.as_dict(), sort_keys=True, ensure_ascii=False)
                for event in self.list(execution_id)
            )
            + "\n"
        ).encode("utf-8")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            event_id=row["event_id"],
            sequence=row["execution_sequence"],
            execution_id=row["execution_id"],
            event_type=row["event_type"],
            occurred_at=row["occurred_at"],
            actor=json.loads(row["actor_json"]),
            payload=json.loads(row["payload_json"]),
            previous_digest=row["previous_digest"],
            event_digest=row["event_digest"],
            phase_execution_id=row["phase_execution_id"],
            causation_id=row["causation_id"],
            correlation_id=row["correlation_id"],
            redaction_version=row["redaction_version"],
        )
