from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from governed_harness.capabilities.authorizer import (
    CapabilityAuthorizer,
    contained_path,
)
from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import Actor, CapabilityGrant
from governed_harness.runtime.cancellation import CancellationToken


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    allowed_environment: tuple[str, ...] = ()
    max_output_bytes: int = 1_000_000
    stdin: bytes | None = None


@dataclass(frozen=True)
class ProcessResult:
    status: ResultStatus
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    cancelled: bool
    duration_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False


_READ_CHUNK_BYTES = 65536
_POLL_SECONDS = 0.05
_READER_JOIN_SECONDS = 5.0


class _BoundedReader(threading.Thread):
    """Drains a pipe, keeping only the first ``limit`` bytes."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._chunks: list[bytes] = []
        self._kept = 0
        self.truncated = False

    def run(self) -> None:
        try:
            descriptor = self._stream.fileno()
            while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
                room = self._limit - self._kept
                if room > 0:
                    self._chunks.append(chunk[:room])
                    self._kept += min(len(chunk), room)
                if len(chunk) > max(room, 0):
                    self.truncated = True
        except (OSError, ValueError):
            pass  # the pipe was closed while reading; keep what was read
        finally:
            self._stream.close()

    def data(self) -> bytes:
        return b"".join(self._chunks)


def _write_stdin(stream: IO[bytes], data: bytes) -> None:
    # A child that exits without reading its input closes the pipe; that is not an error here.
    with contextlib.suppress(OSError, ValueError):
        stream.write(data)
    with contextlib.suppress(OSError):
        stream.close()


class SafeProcessRunner:
    def __init__(
        self, workspace_root: Path, authorizer: CapabilityAuthorizer | None = None
    ) -> None:
        self.workspace_root = workspace_root.resolve(strict=True)
        self.authorizer = authorizer or CapabilityAuthorizer()

    def run(
        self,
        spec: CommandSpec,
        *,
        actor: Actor,
        grants: list[CapabilityGrant],
        extra_env: Mapping[str, str] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        if not spec.argv or any("\x00" in part for part in spec.argv):
            raise ValueError("argv must be a non-empty, NUL-free vector")
        self.authorizer.authorize_command(actor=actor, argv=spec.argv, grants=grants)
        cwd = contained_path(self.workspace_root, spec.cwd)
        environment = self._environment(spec, extra_env)
        start = time.perf_counter()
        process = subprocess.Popen(
            list(spec.argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE if spec.stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name != "nt",
        )
        assert process.stdout is not None and process.stderr is not None
        # Read both streams while the process runs and keep at most max_output_bytes of each;
        # the rest is drained and discarded, so memory stays bounded whatever the output size.
        readers = [
            _BoundedReader(process.stdout, spec.max_output_bytes),
            _BoundedReader(process.stderr, spec.max_output_bytes),
        ]
        for reader in readers:
            reader.start()
        writer = None
        if spec.stdin is not None:
            assert process.stdin is not None
            writer = threading.Thread(
                target=_write_stdin, args=(process.stdin, spec.stdin), daemon=True
            )
            writer.start()
        timed_out = False
        cancelled = False
        deadline = start + spec.timeout_seconds
        while True:
            # Joining the readers returns as soon as the child closes its output (normally when
            # it exits), without the growing sleep intervals of Popen.wait(timeout=...).
            for reader in readers:
                reader.join(timeout=_POLL_SECONDS)
            if not any(reader.is_alive() for reader in readers):
                try:
                    process.wait(timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass  # the streams are closed but the process still runs
            if cancellation and cancellation.cancelled:
                cancelled = True
                self._terminate(process)
                break
            if time.perf_counter() >= deadline:
                timed_out = True
                self._terminate(process)
                break
        process.wait()
        if writer is not None:
            writer.join(timeout=_READER_JOIN_SECONDS)
        for reader in readers:
            reader.join(timeout=_READER_JOIN_SECONDS)
        duration_ms = max(0, round((time.perf_counter() - start) * 1000))
        stdout, stderr = readers[0].data(), readers[1].data()
        stdout_truncated, stderr_truncated = readers[0].truncated, readers[1].truncated
        if cancelled:
            status = ResultStatus.CANCELLED
        elif timed_out:
            status = ResultStatus.TIMED_OUT
        elif process.returncode == 0:
            status = ResultStatus.PASSED
        else:
            status = ResultStatus.FAILED
        return ProcessResult(
            status=status,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=cancelled,
            duration_ms=duration_ms,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _environment(spec: CommandSpec, extra_env: Mapping[str, str] | None) -> dict[str, str]:
        environment = {
            name: os.environ[name] for name in spec.allowed_environment if name in os.environ
        }
        # Preserve only minimal executable lookup and locale by default.
        for name in ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TEMP", "LANG", "LC_ALL"):
            if name in os.environ:
                environment.setdefault(name, os.environ[name])
        if extra_env:
            unknown = set(extra_env) - set(spec.allowed_environment)
            if unknown:
                raise ValueError(f"environment variables not authorized: {sorted(unknown)}")
            environment.update(extra_env)
        return environment
