from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess  # nosec B404 - the governed runner: resolved executable, no shell
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Protocol

from governed_harness.capabilities.authorizer import (
    CapabilityAuthorizer,
    CapabilityDenied,
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
    sandbox_prefix: tuple[str, ...] = ()
    """Confinement wrapper (``runtime.agentSandbox``) placed before ``argv`` after authorization:
    the capability grant applies to the command itself, the wrapper is the harness's own."""


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
    executable: str | None = None
    """The program that was started: the absolute path a bare command name resolved to on
    Windows, the name as written elsewhere."""


def resolve_executable(argv0: str, *, exclude: Iterable[Path] = ()) -> str:
    """On Windows, resolve a bare command name through PATH and PATHEXT (``npm`` is
    ``npm.cmd``), which CreateProcess does not do; elsewhere return it unchanged. Authorization
    always applies to the name as written in the command.

    The search never looks in the current directory, in a relative PATH entry or in an
    ``exclude`` directory (the workspace): ``shutil.which`` on Windows looks in the current
    directory first, so a ``python.bat`` written by the agent in the workspace ran instead of
    Python (F12). A name with a directory part (``./gradlew``) is taken as written. A bare name
    found nowhere raises ``FileNotFoundError`` (what starting a missing program raises) instead
    of letting CreateProcess search the current directory."""
    if sys.platform != "win32":
        return argv0
    if os.path.dirname(argv0):
        return shutil.which(argv0) or argv0
    for directory in _search_path(exclude):
        found = shutil.which(os.path.join(directory, argv0))
        if found:
            return found
    raise FileNotFoundError(
        f"{argv0} was not found on PATH (the working directory is not searched)"
    )


def find_executable(argv0: str) -> str | None:
    """The program ``argv0`` starts, as :func:`resolve_executable` finds it on Windows (never
    in the current directory) and ``shutil.which`` elsewhere; ``None`` when there is none."""
    if sys.platform != "win32":
        return shutil.which(argv0)
    try:
        found = resolve_executable(argv0)
    except FileNotFoundError:
        return None
    return found if os.path.dirname(argv0) == "" or os.path.exists(found) else None


def _search_path(exclude: Iterable[Path]) -> list[str]:
    """The absolute PATH entries, without the current directory and the excluded ones."""
    skipped = {_normalized(Path.cwd()), *(_normalized(item) for item in exclude)}
    entries: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry) or _normalized(Path(entry)) in skipped:
            continue
        entries.append(entry)
    return entries


def _normalized(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    return os.path.normcase(str(resolved))


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


class ProcessObserver(Protocol):
    """Told about every process the runner starts and finishes (``governance.workspaceLease``
    records the process groups so that a recovery can terminate what a killed harness left)."""

    def started(self, pid: int, argv: tuple[str, ...]) -> None:
        """Record that process ``pid`` started with ``argv``."""

    def finished(self, pid: int) -> None:
        """Record that process ``pid`` finished."""


class SafeProcessRunner:
    def __init__(
        self,
        workspace_root: Path,
        authorizer: CapabilityAuthorizer | None = None,
        *,
        observer: ProcessObserver | None = None,
        terminate_on_exit: bool = False,
    ) -> None:
        """``terminate_on_exit``: when the harness itself is interrupted while it waits
        (``KeyboardInterrupt``, or ``SystemExit`` from a ``SIGTERM`` handler), terminate the
        child's process group before the exception propagates."""
        self.workspace_root = workspace_root.resolve(strict=True)
        self.authorizer = authorizer or CapabilityAuthorizer()
        self.observer = observer
        self.terminate_on_exit = terminate_on_exit

    def _authorize(self, spec: CommandSpec, actor: Actor, grants: list[CapabilityGrant]) -> Path:
        """Check the command against the grants and the policies before it starts; returns the
        working directory, contained in the workspace."""
        if not spec.argv or any("\x00" in part for part in (*spec.argv, *spec.sandbox_prefix)):
            raise ValueError("argv must be a non-empty, NUL-free vector")
        try:
            self.authorizer.authorize_command(actor=actor, argv=spec.argv, grants=grants)
        except CapabilityDenied as error:
            from governed_harness.capabilities.phase import current_policy

            policy = current_policy()
            if policy is not None:
                # governance.phaseCapabilities (#87): an agent's refused command is a finding
                # and blocks the phase.
                policy.refuse(actor, spec.argv, error)
            raise
        cwd = contained_path(self.workspace_root, spec.cwd)
        from governed_harness.capabilities.repository import current_destructive

        destructive = current_destructive()
        if destructive is not None:
            # governance.applyRepositoryPolicies, destructiveActionsDefault: deny (#5).
            destructive.check(spec.argv, cwd, actor, grants)
        return cwd

    def run(
        self,
        spec: CommandSpec,
        *,
        actor: Actor,
        grants: list[CapabilityGrant],
        extra_env: Mapping[str, str] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        cwd = self._authorize(spec, actor, grants)
        environment = self._environment(spec, extra_env)
        executable = resolve_executable(spec.argv[0], exclude=(self.workspace_root, cwd))
        start = time.perf_counter()
        process = subprocess.Popen(  # nosec B603 - executable resolved and checked by policy, shell=False
            [*spec.sandbox_prefix, executable, *spec.argv[1:]],
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE if spec.stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=sys.platform != "win32",
        )
        if self.observer is not None:
            self.observer.started(process.pid, spec.argv)
        try:
            return replace(self._wait(process, spec, start, cancellation), executable=executable)
        except BaseException:
            if self.terminate_on_exit:
                self._terminate(process)
                if sys.platform != "win32":
                    # The leader may be gone while its children still run in its group.
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_READER_JOIN_SECONDS)
            raise
        finally:
            if self.observer is not None and process.poll() is not None:
                self.observer.finished(process.pid)

    def _wait(
        self,
        process: subprocess.Popen[bytes],
        spec: CommandSpec,
        start: float,
        cancellation: CancellationToken | None,
    ) -> ProcessResult:
        if process.stdout is None or process.stderr is None:  # not reached: both are pipes
            raise RuntimeError("the process was started without its output pipes")
        # Read both streams while the process runs and keep at most max_output_bytes of each;
        # the rest is drained and discarded, so memory stays bounded whatever the output size.
        readers = [
            _BoundedReader(process.stdout, spec.max_output_bytes),
            _BoundedReader(process.stderr, spec.max_output_bytes),
        ]
        for reader in readers:
            reader.start()
        writer = self._start_writer(process, spec)
        deadline = start + spec.timeout_seconds
        timed_out, cancelled = self._poll(process, readers, cancellation, deadline)
        process.wait()
        if writer is not None:
            writer.join(timeout=_READER_JOIN_SECONDS)
        for reader in readers:
            reader.join(timeout=_READER_JOIN_SECONDS)
        duration_ms = max(0, round((time.perf_counter() - start) * 1000))
        stdout, stderr = readers[0].data(), readers[1].data()
        stdout_truncated, stderr_truncated = readers[0].truncated, readers[1].truncated
        return ProcessResult(
            status=_result_status(cancelled, timed_out, process.returncode),
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
    def _start_writer(
        process: subprocess.Popen[bytes], spec: CommandSpec
    ) -> threading.Thread | None:
        """Start the thread that writes ``spec.stdin``; ``None`` without input."""
        if spec.stdin is None:
            return None
        if process.stdin is None:  # not reached: stdin is a pipe when there is input
            raise RuntimeError("the process was started without its input pipe")
        writer = threading.Thread(
            target=_write_stdin, args=(process.stdin, spec.stdin), daemon=True
        )
        writer.start()
        return writer

    def _poll(
        self,
        process: subprocess.Popen[bytes],
        readers: list[_BoundedReader],
        cancellation: CancellationToken | None,
        deadline: float,
    ) -> tuple[bool, bool]:
        """Wait until the process exits, is cancelled or reaches the deadline; whether it timed
        out and whether it was cancelled."""
        while True:
            # Joining the readers returns as soon as the child closes its output (normally when
            # it exits), without the growing sleep intervals of Popen.wait(timeout=...).
            for reader in readers:
                reader.join(timeout=_POLL_SECONDS)
            if not any(reader.is_alive() for reader in readers) and _exited(process):
                return False, False
            if cancellation and cancellation.cancelled:
                self._terminate(process)
                return False, True
            if time.perf_counter() >= deadline:
                self._terminate(process)
                return True, False

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            # Still running after SIGTERM: kill it; a process group that is already gone is fine.
            with contextlib.suppress(ProcessLookupError):
                if sys.platform == "win32":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)

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


def _exited(process: subprocess.Popen[bytes]) -> bool:
    """Whether the process exits within a poll interval once its streams are closed."""
    try:
        process.wait(timeout=_POLL_SECONDS)
    except subprocess.TimeoutExpired:
        return False  # the streams are closed but the process still runs
    return True


def _result_status(cancelled: bool, timed_out: bool, returncode: int) -> ResultStatus:
    if cancelled:
        return ResultStatus.CANCELLED
    if timed_out:
        return ResultStatus.TIMED_OUT
    if returncode == 0:
        return ResultStatus.PASSED
    return ResultStatus.FAILED
