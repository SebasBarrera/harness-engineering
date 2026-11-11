"""SQLite persistence (U1, U2, V1).

Layout (schema version 1):

- ``schema_version(version)``: exactly one row.
- ``meta(name, data)``: scalar and list parts of the state (clock, counters, timers, audit).
- ``records(collection, key, data)``: one row per entity of every keyed collection, JSON.
- ``credentials(account_id, salt, digest, iterations)``: PBKDF2 material as BLOBs.

Plain passwords, card numbers and CVCs never reach the state, so they never reach the file.
``save`` rewrites the whole state in one transaction.
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
from contextlib import closing
from typing import Any

from ..core.security import PasswordHash
from ..domain.state import State
from . import codec

SCHEMA_VERSION = 1

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS records ("
    " collection TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (collection, key))",
    "CREATE TABLE IF NOT EXISTS credentials ("
    " account_id TEXT PRIMARY KEY, salt BLOB NOT NULL, digest BLOB NOT NULL, iterations INTEGER NOT NULL)",
)
# Keyed collections stored one row per entry; the rest of the state goes to ``meta``.
_SEPARATE = ("credentials",)


def _keyed_collections() -> list[str]:
    names = []
    for field in dataclasses.fields(State):
        if field.name in _SEPARATE:
            continue
        default = field.default_factory() if field.default_factory is not dataclasses.MISSING else None
        if isinstance(default, dict):
            names.append(field.name)
    return names


_COLLECTIONS = _keyed_collections()
_META = [field.name for field in dataclasses.fields(State) if field.name not in _COLLECTIONS + list(_SEPARATE)]


class SqliteStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def create(self, state: State) -> None:
        """Start a new database at ``path`` (an existing platform there is replaced)."""
        with closing(self._connect()) as conn, conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        self.save(state)

    def save(self, state: State) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM meta")
            conn.execute("DELETE FROM records")
            conn.execute("DELETE FROM credentials")
            conn.executemany(
                "INSERT INTO meta (name, data) VALUES (?, ?)",
                [(name, codec.dumps(getattr(state, name))) for name in _META],
            )
            rows: list[tuple[str, str, str]] = []
            for name in _COLLECTIONS:
                for key, value in getattr(state, name).items():
                    rows.append((name, codec.dumps(key), codec.dumps(value)))
            conn.executemany("INSERT INTO records (collection, key, data) VALUES (?, ?, ?)", rows)
            conn.executemany(
                "INSERT INTO credentials (account_id, salt, digest, iterations) VALUES (?, ?, ?, ?)",
                [(key, cred.salt, cred.digest, cred.iterations) for key, cred in state.credentials.items()],
            )

    def load(self) -> State:
        if not os.path.isfile(self.path):
            raise ValueError(f"no database at {self.path}")
        try:
            with closing(self._connect()) as conn:
                versions = conn.execute("SELECT version FROM schema_version").fetchall()
                if len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
                    raise ValueError(f"unsupported schema version {versions!r}")
                meta = dict(conn.execute("SELECT name, data FROM meta").fetchall())
                records = conn.execute("SELECT collection, key, data FROM records ORDER BY rowid").fetchall()
                credentials = conn.execute("SELECT account_id, salt, digest, iterations FROM credentials").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"not a platform database: {exc}") from exc
        values: dict[str, Any] = {name: codec.loads(meta[name]) for name in _META if name in meta}
        if "now" not in values:
            raise ValueError("the database has no saved state")
        for name in _COLLECTIONS:
            values[name] = {}
        for collection, key, data in records:
            if collection in values:
                values[collection][codec.loads(key)] = codec.loads(data)
        values["credentials"] = {
            account_id: PasswordHash(bytes(salt), bytes(digest), iterations)
            for account_id, salt, digest, iterations in credentials
        }
        return State(**values)
