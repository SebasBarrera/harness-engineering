"""One harness process at a time per workspace (``governance.workspaceLease``).

Two ``run start`` on one workspace interfered (each one's agent overwrote the other's files and
both runs failed), and a harness killed during IMPLEMENTATION left a phase ``RUNNING`` that
``run continue`` implemented again on top. The lease is a file, ``.harness/lease.json``, created
exclusively by the process that executes phases and refreshed by a heartbeat while it runs. A
second process finds it and stops with a clear error, unless the holder is gone: its process no
longer exists on this host, or (on another host, or where liveness cannot be checked) its
heartbeat is older than ``STALE_SECONDS``.

The module also keeps the process groups the governed runner starts, so that a harness that is
interrupted terminates them and a later recovery can terminate the ones a killed harness left.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from governed_harness.domain.errors import PolicyViolationError

LEASE_FILE = "lease.json"
HEARTBEAT_SECONDS = 10.0
STALE_SECONDS = 60.0


class WorkspaceBusyError(PolicyViolationError):
    """Another harness process holds the workspace lease; exit code 5."""


@dataclass(frozen=True)
class LeaseHolder:
    token: str
    pid: int
    host: str
    command: str
    run_id: str | None
    acquired_at: str
    heartbeat_at: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LeaseHolder:
        return cls(
            token=str(value.get("token", "")),
            pid=int(value.get("pid", 0)),
            host=str(value.get("host", "")),
            command=str(value.get("command", "")),
            run_id=value.get("runId"),
            acquired_at=str(value.get("acquiredAt", "")),
            heartbeat_at=float(value.get("heartbeatAt", 0.0)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "pid": self.pid,
            "host": self.host,
            "command": self.command,
            "runId": self.run_id,
            "acquiredAt": self.acquired_at,
            "heartbeatAt": self.heartbeat_at,
        }

    def describe(self) -> str:
        run = f"run {self.run_id}" if self.run_id else "a harness command"
        return f"{run} ({self.command}, pid {self.pid} on {self.host}, since {self.acquired_at})"


def process_alive(pid: int) -> bool | None:
    """Whether a process exists on this host; ``None`` where it cannot be checked safely."""
    if os.name == "nt" or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WorkspaceLease:
    def __init__(self, harness_dir: Path, command: str) -> None:
        self.path = harness_dir / LEASE_FILE
        self.command = command
        self.token = uuid.uuid4().hex
        self.host = socket.gethostname()
        self.run_id: str | None = None
        self.recovered: LeaseHolder | None = None
        """The stale holder this lease replaced, if any."""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ----- acquisition --------------------------------------------------------------
    def acquire(self) -> WorkspaceLease:
        for _ in range(3):
            if self._create():
                self._start_heartbeat()
                return self
            holder = self.holder()
            if holder is None:
                continue  # removed meanwhile, or unreadable: try again
            if not self.is_stale(holder):
                raise WorkspaceBusyError(
                    f"the workspace is in use by {holder.describe()}; wait for it to finish or "
                    f"cancel it (harness run cancel). A holder that no longer runs is taken over "
                    f"after its heartbeat is {int(STALE_SECONDS)} s old"
                )
            self.recovered = holder
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        raise WorkspaceBusyError(f"could not acquire the workspace lease {self.path}")

    def holder(self) -> LeaseHolder | None:
        try:
            return LeaseHolder.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            # A lease that cannot be read is treated as held by nobody alive: its heartbeat
            # cannot be refreshed either.
            return LeaseHolder("", 0, "", "unreadable lease", None, "", 0.0)

    def is_stale(self, holder: LeaseHolder) -> bool:
        age = time.time() - holder.heartbeat_at
        if holder.host == self.host and holder.pid != os.getpid():
            alive = process_alive(holder.pid)
            if alive is not None:
                return not alive
        return age > STALE_SECONDS

    def bind(self, run_id: str) -> None:
        """Name the run the lease protects (known after ``run start`` created it)."""
        self.run_id = run_id
        self._write()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=HEARTBEAT_SECONDS)
        with self._lock:
            holder = self.holder()
            if holder is not None and holder.token == self.token:
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()

    # ----- internals ----------------------------------------------------------------
    def _record(self) -> LeaseHolder:
        return LeaseHolder(
            token=self.token,
            pid=os.getpid(),
            host=self.host,
            command=self.command,
            run_id=self.run_id,
            acquired_at=datetime.now(UTC).isoformat(),
            heartbeat_at=time.time(),
        )

    def _create(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(self._record().as_dict(), handle)
        return True

    def _write(self) -> None:
        with self._lock:
            current = self.holder()
            if current is None or current.token != self.token:
                return  # taken over: never overwrite another holder
            record = self._record()
            record = LeaseHolder(
                record.token,
                record.pid,
                record.host,
                record.command,
                record.run_id,
                current.acquired_at,
                record.heartbeat_at,
            )
            temporary = self.path.with_name(f"{LEASE_FILE}.{self.token}")
            temporary.write_text(json.dumps(record.as_dict()), encoding="utf-8")
            os.replace(temporary, self.path)

    def _start_heartbeat(self) -> None:
        def beat() -> None:
            while not self._stop.wait(HEARTBEAT_SECONDS):
                with contextlib.suppress(OSError):
                    self._write()

        self._thread = threading.Thread(target=beat, name="harness-lease", daemon=True)
        self._thread.start()


@contextlib.contextmanager
def interruptible() -> Iterator[None]:
    """Turn ``SIGTERM`` into ``SystemExit`` while a run executes (main thread only), so the
    governed runner terminates the process groups it started and the interrupted phase is
    recorded, instead of the harness dying with its agent still running."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def terminate_process_group(pgid: int, *, grace_seconds: float = 1.0) -> bool:
    """Terminate a process group a killed harness left behind (``SIGTERM``, then ``SIGKILL``).
    Returns whether a group existed. POSIX only."""
    if os.name == "nt" or pgid <= 1 or pgid == os.getpgid(0):
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)
    return True
