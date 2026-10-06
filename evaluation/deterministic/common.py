"""Shared helpers of the deterministic evaluation suites (2.0.0): no model is ever called.

Every suite takes the harness executable as a parameter (``--harness``, else ``$HARNESS_BIN``,
else ``harness`` on ``PATH``) so the same scripts run on the worktree code now and on the
``v2.0.0`` wheel later; ``environment()`` records its version and, when ``--wheel`` (or
``$HARNESS_WHEEL``) names the wheel it was installed from, the wheel's SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
REPO = EVALUATION.parent
RESULTS = EVALUATION / "results-2.0.0" / "deterministic"
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GIT = [
    "git",
    "-c",
    "user.name=eval",
    "-c",
    "user.email=eval@example.invalid",
    "-c",
    "commit.gpgsign=false",
]


def add_common_arguments(parser: Any) -> None:
    parser.add_argument(
        "--harness",
        default=os.environ.get("HARNESS_BIN") or shutil.which("harness") or "harness",
        help="harness executable (default: $HARNESS_BIN, else harness on PATH)",
    )
    parser.add_argument(
        "--wheel",
        default=os.environ.get("HARNESS_WHEEL", ""),
        help="the wheel the harness was installed from (its SHA-256 is recorded)",
    )


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    """The harness's canonical JSON digest (``governed_harness.evidence.hashing.sha256_json``)."""
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def harness_version(harness: str) -> str:
    proc = subprocess.run([harness, "--version"], capture_output=True, text=True, check=False)
    return (proc.stdout or proc.stderr).strip()


def evaluation_code_digest() -> str:
    """Digest of the evaluation scripts of this directory, as earlier evaluations recorded."""
    digest = hashlib.sha256()
    for path in sorted([*HERE.glob("*.py"), *(EVALUATION / "corpus").rglob("*")]):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(EVALUATION).as_posix().encode())
            digest.update(path.read_bytes())
    for name in ("fault_probes.py", "fault_provider.py"):
        path = EVALUATION / name
        digest.update(name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "src",
            "evaluation/deterministic",
            "evaluation/corpus",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return proc.stdout.strip() + ("+dirty" if dirty else "")


def tool_version(command: Sequence[str]) -> str:
    try:
        proc = subprocess.run(
            list(command), capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return (
        (proc.stdout or proc.stderr).strip().splitlines()[0]
        if (proc.stdout or proc.stderr).strip()
        else ""
    )


def environment(
    harness: str, wheel: str = "", extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "recordedAt": now(),
        "harnessExecutable": harness,
        "harnessVersion": harness_version(harness),
        "wheel": Path(wheel).name if wheel else None,
        "wheelSha256": sha256_file(Path(wheel)) if wheel and Path(wheel).is_file() else None,
        "repositoryHead": git_head(),
        "evaluationCodeDigest": evaluation_code_digest(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "git": tool_version(["git", "--version"]),
        "modelCalls": 0,
    }
    record.update(extra or {})
    return record


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path}", flush=True)


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"wrote {path} ({len(records)} records)", flush=True)


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        [*GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def init_repo(root: Path, message: str = "baseline") -> str:
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD").strip()


class Harness:
    """Runs one harness executable with a fixed environment and records every command."""

    def __init__(self, executable: str, env: dict[str, str]) -> None:
        self.executable = executable
        self.env = env
        self.steps: list[dict[str, Any]] = []

    def run(
        self, cwd: Path, *args: str, label: str = "", timeout: float | None = None
    ) -> tuple[int, Any, float]:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [self.executable, *args],
                cwd=cwd,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as expired:
            code = -9
            stdout = (
                expired.stdout.decode()
                if isinstance(expired.stdout, bytes)
                else (expired.stdout or "")
            )
            stderr = "TIMEOUT"
        seconds = time.monotonic() - started
        payload: Any = {"stdout": stdout[-800:], "stderr": stderr[-800:]}
        for stream in (stdout, stderr):
            try:
                payload = json.loads(stream)
                break
            except (json.JSONDecodeError, TypeError):
                continue
        self.steps.append(
            {"step": label or " ".join(args[:2]), "exitCode": code, "seconds": round(seconds, 3)}
        )
        return code, payload, seconds


def isolated_env(path_dirs: Sequence[Path | str], state_root: Path) -> dict[str, str]:
    """The environment of the harness: a minimal PATH, no global Git configuration, and the run
    registry and chain anchors kept under ``state_root`` (never the user's data directory)."""
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "anchors").mkdir(exist_ok=True)
    (state_root / "state").mkdir(exist_ok=True)
    return {
        "PATH": ":".join([*(str(p) for p in path_dirs), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        "HOME": os.environ["HOME"],
        "LANG": "en_US.UTF-8",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "HARNESS_ANCHOR_DIR": str(state_root / "anchors"),
        "HARNESS_STATE_DIR": str(state_root / "state"),
        **GIT_ENV,
    }


def state_database(h: Harness, workspace: Path) -> Path:
    """The run registry database (``runtime.stateDir: auto`` keeps it outside the workspace)."""
    code, validated, _ = h.run(
        workspace, "config", "validate", "--path", ".", label="config validate"
    )
    try:
        return Path(validated["ladder"]["state"]["database"])
    except (KeyError, TypeError):
        return workspace / ".harness" / "state.db"


def query(database: Path, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(database)
    try:
        return list(connection.execute(sql, tuple(params)))
    finally:
        connection.close()


SIMULATED_PERSON = "human.reviewer-simulated"


def simulated_person(
    h: Harness,
    ws: Path,
    run_id: str,
    task_id: str,
    scratch: Path,
    waits: list[str],
    code: int,
    max_rounds: int = 6,
) -> int:
    """The declared simulated person (``human.reviewer-simulated``, a simulation stated in every
    README) answers the waits before DECISION when ``run start``/``run continue`` exits with 6:
    INTENT questions (answered "keep the behaviour the task describes"), acceptance tests, a plan
    approval and an unavailable preflight (continued uncertified). Any other stop is returned as
    it is. Each wait is appended to ``waits`` as ``PHASE:kind``."""
    for _ in range(max_rounds):
        if code != 6:
            return code
        _, status, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status")
        execution = status.get("execution", {}) if isinstance(status, dict) else {}
        phase, state = execution.get("currentPhase"), execution.get("status")
        _, inbox, _ = h.run(ws, "inbox", "--path", ".", "--json", label="inbox")
        pending = (
            [item for item in inbox if item.get("executionId") == run_id]
            if isinstance(inbox, list)
            else []
        )
        kind = str(pending[0].get("kind")) if pending else ""
        if not kind and state == "BLOCKED" and phase == "PLANNING":
            # A plan waiting for approval is not listed by harness inbox (observed on 2.0.0).
            _, shown, _ = h.run(
                ws, "plan", "show", "--path", ".", "--run", run_id, label="plan show"
            )
            approval = shown.get("approval") if isinstance(shown, dict) else None
            if isinstance(approval, dict) and approval.get("status") == "PENDING":
                kind = "plan"
        if not kind and state == "BLOCKED" and phase == "SPECIFICATION":
            kind = "acceptance"
        questions: list[dict[str, Any]] = []
        if state == "BLOCKED" and phase == "INTENT":
            _, listed, _ = h.run(
                ws, "task", "questions", "--path", ".", "--task", task_id, label="task questions"
            )
            open_request = listed.get("openRequest") if isinstance(listed, dict) else None
            questions = list((open_request or {}).get("questions") or [])
            kind = "clarification" if questions else ""
        if not kind:
            return code
        waits.append(f"{phase}:{kind}")
        if kind == "clarification":
            answers = {
                "answers": {
                    q[
                        "questionId"
                    ]: "Keep the behaviour the task describes; nothing else is in scope."
                    for q in questions
                    if q.get("questionId")
                }
            }
            waits.append("questions:" + ",".join(str(q.get("ruleId")) for q in questions))
            answers_path = scratch / f"answers-{len(waits)}.yaml"
            answers_path.write_text(yaml.safe_dump(answers))
            h.run(
                ws,
                "task",
                "clarify",
                "--path",
                ".",
                "--task",
                task_id,
                "--file",
                str(answers_path),
                "--actor",
                SIMULATED_PERSON,
                label="task clarify",
            )
            code, _, _ = h.run(
                ws, "run", "continue", "--path", ".", "--run", run_id, label="run continue"
            )
        elif kind == "acceptance":
            _, shown, _ = h.run(
                ws, "acceptance", "show", "--path", ".", "--run", run_id, label="acceptance show"
            )
            digest = shown.get("digest") if isinstance(shown, dict) else ""
            code, _, _ = h.run(
                ws,
                "acceptance",
                "decide",
                "--path",
                ".",
                "--run",
                run_id,
                "--decision",
                "APPROVE",
                "--digest",
                str(digest),
                "--actor",
                SIMULATED_PERSON,
                "--rationale",
                "simulated approval",
                label="acceptance decide",
            )
        elif kind == "plan":
            _, shown, _ = h.run(
                ws, "plan", "show", "--path", ".", "--run", run_id, label="plan show"
            )
            digest = (
                ((shown.get("approval") or {}).get("digest") or shown.get("digest"))
                if isinstance(shown, dict)
                else ""
            )
            code, _, _ = h.run(
                ws,
                "plan",
                "decide",
                "--path",
                ".",
                "--run",
                run_id,
                "--decision",
                "APPROVE",
                "--digest",
                str(digest),
                "--actor",
                SIMULATED_PERSON,
                "--rationale",
                "simulated approval",
                label="plan decide",
            )
        elif kind in {"preflight", "verification"}:
            code, _, _ = h.run(
                ws,
                "verification",
                "decide",
                "--path",
                ".",
                "--run",
                run_id,
                "--continue-uncertified",
                "--actor",
                SIMULATED_PERSON,
                "--rationale",
                "simulated: continue uncertified",
                label="verification decide",
            )
        else:
            return code
    return code
