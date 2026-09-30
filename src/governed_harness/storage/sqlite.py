from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel

from governed_harness.domain.errors import NotFoundError

T = TypeVar("T", bound=BaseModel)


class SQLiteStateStore:
    """Typed record storage. Events remain the audit authority; records are query projections."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._lock = threading.RLock()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                record_type TEXT NOT NULL,
                record_id TEXT NOT NULL,
                execution_id TEXT,
                project_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(record_type, record_id)
            );
            CREATE INDEX IF NOT EXISTS records_execution_idx
                ON records(record_type, execution_id, updated_at);
            CREATE INDEX IF NOT EXISTS records_project_idx
                ON records(record_type, project_id, updated_at);
            CREATE TABLE IF NOT EXISTS flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def put(
        self,
        record_type: str,
        record_id: str,
        value: BaseModel | dict[str, Any],
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        payload = value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value
        now = datetime.now(UTC).isoformat()
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO records(record_type,record_id,execution_id,project_id,created_at,updated_at,payload_json)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(record_type,record_id) DO UPDATE SET
                    execution_id=excluded.execution_id,
                    project_id=excluded.project_id,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record_type,
                    record_id,
                    execution_id,
                    project_id,
                    now,
                    now,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
                ),
            )

    def get_dict(self, record_type: str, record_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload_json FROM records WHERE record_type=? AND record_id=?",
            (record_type, record_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"{record_type} not found: {record_id}")
        return json.loads(row["payload_json"])

    def get(self, record_type: str, record_id: str, model: type[T]) -> T:
        return model.model_validate(self.get_dict(record_type, record_id))

    def list_dicts(
        self,
        record_type: str,
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["record_type=?"]
        params: list[Any] = [record_type]
        if execution_id is not None:
            clauses.append("execution_id=?")
            params.append(execution_id)
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        direction = "DESC" if newest_first else "ASC"
        # Only code-defined literals are interpolated (the clause list and the direction);
        # every value is bound as a parameter, so Bandit B608 is a false positive here.
        rows = self.connection.execute(
            f"SELECT payload_json FROM records WHERE {' AND '.join(clauses)} ORDER BY updated_at {direction}",  # nosec B608
            tuple(params),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def list(
        self,
        record_type: str,
        model: type[T],
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
        newest_first: bool = False,
    ) -> list[T]:
        return [
            model.model_validate(item)
            for item in self.list_dicts(
                record_type,
                execution_id=execution_id,
                project_id=project_id,
                newest_first=newest_first,
            )
        ]

    def delete(self, record_type: str, record_id: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM records WHERE record_type=? AND record_id=?", (record_type, record_id)
            )

    def set_flag(self, key: str, value: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO flags(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, now),
            )

    def get_flag(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
