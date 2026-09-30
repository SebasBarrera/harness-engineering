from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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


class SafeProcessRunner:
    def __init__(self, workspace_root: Path, authorizer: CapabilityAuthorizer | None = None) -> None:
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
        input_data = spec.stdin
        stdout = b""
        stderr = b""
        timed_out = False
        cancelled = False
        deadline = start + spec.timeout_seconds
        while True:
            try:
                stdout, stderr = process.communicate(input=input_data, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                input_data = None
                if cancellation and cancellation.cancelled:
                    cancelled = True
                    self._terminate(process)
                    stdout, stderr = process.communicate()
                    break
                if time.perf_counter() >= deadline:
                    timed_out = True
                    self._terminate(process)
                    stdout, stderr = process.communicate()
                    break
        duration_ms = max(0, round((time.perf_counter() - start) * 1000))
        stdout_truncated = len(stdout) > spec.max_output_bytes
        stderr_truncated = len(stderr) > spec.max_output_bytes
        stdout = stdout[: spec.max_output_bytes]
        stderr = stderr[: spec.max_output_bytes]
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
        environment = {name: os.environ[name] for name in spec.allowed_environment if name in os.environ}
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
