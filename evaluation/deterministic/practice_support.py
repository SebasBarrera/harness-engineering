"""Shared pieces of the practice and architecture suites H27, H28, H31 and H32 (no model).

Each case runs on a fresh governed workspace: a baseline the suite builds and commits,
``harness init``, the condition's configuration (``suite_support.configure``: the fixture
provider ``practice_agent.py`` and the grant of exactly its command), and the fixture's
specification. The command line is ``resource_support.main``, the one every suite shares.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common import git, init_repo, state_database
from resource_support import Utf8Harness, Workspace
from suite_support import HERE, bin_dir, commit_init, configure, portable_env

AGENT = HERE / "practice_agent.py"


def write_files(root: Path, files: dict[str, str]) -> None:
    """Write UTF-8 files with LF line endings on every platform."""
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))


def commit(root: Path, message: str) -> None:
    if git(root, "status", "--porcelain").strip():
        git(root, "add", "-A")
        git(root, "commit", "-qm", message)


def workspace(
    run_dir: Path,
    args: argparse.Namespace,
    build: Callable[[Path], None],
    spec: dict[str, Any],
    *,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> Workspace:
    """``build`` the baseline and commit it, ``harness init``, the condition's configuration
    (``mutate`` over the ``init`` file) and the fixture's specification."""
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    build(ws)
    if not (ws / ".git").exists():
        init_repo(ws)
    else:
        commit(ws, "baseline")
    h = Utf8Harness(
        args.harness, portable_env([bin_dir(args.project_venv)], run_dir / "harness-state")
    )
    h.run(ws, "init", "--path", ".", label="init")
    commit_init(ws)
    spec_path = run_dir / "fixture-spec.json"
    command = ["python", str(AGENT), "--spec", str(spec_path)]
    config, deviations = configure(ws, command, mutate=mutate)
    item = Workspace(
        run_dir, ws, h, config, deviations, spec_path, run_dir / "calls.jsonl", command, Path()
    )
    item.write_spec(spec)
    item.database = state_database(h, ws)
    return item


def cancel(item: Workspace, run_id: str) -> int:
    code, _, _ = item.h.run(
        item.ws, "run", "cancel", "--path", ".", "--run", run_id, label="run cancel"
    )
    return code


def records_of(database: Path, run_id: str, record_type: str) -> list[dict[str, Any]]:
    """The payloads of the run's records of one type (``agent_invocation``, ...)."""
    if not run_id or not database.exists():
        return []
    connection = sqlite3.connect(database)
    try:
        rows = list(
            connection.execute(
                "SELECT payload_json FROM records WHERE record_type=? AND execution_id=?",
                (record_type, run_id),
            )
        )
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    payloads = []
    for (payload,) in rows:
        try:
            payloads.append(json.loads(payload))
        except ValueError:
            continue
    return payloads


def flag(database: Path, key: str) -> Any:
    """A JSON flag of the state database (``acceptance:<run>``, ...); ``None`` when absent."""
    if not database.exists():
        return None
    connection = sqlite3.connect(database)
    try:
        row = connection.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return row[0]


__all__ = ["AGENT", "cancel", "commit", "flag", "records_of", "workspace", "write_files"]
