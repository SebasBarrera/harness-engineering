from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

from governed_harness.capabilities import CapabilityDenied, grants_from_rules
from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.enums import ActorType, ResultStatus
from governed_harness.domain.models import Actor
from governed_harness.runtime import CommandSpec, SafeProcessRunner


def authorized(tmp_path: Path):
    actor = Actor(actor_type=ActorType.TOOL, actor_id="tool.process")
    grants = grants_from_rules(
        "run_1",
        actor,
        (CapabilityRule(capability="process.execute", scope=(sys.executable,)),),
        lifetime=timedelta(minutes=1),
    )
    return actor, grants, SafeProcessRunner(tmp_path)


def test_process_pass(tmp_path: Path) -> None:
    actor, grants, runner = authorized(tmp_path)
    result = runner.run(
        CommandSpec(argv=(sys.executable, "-c", "print('ok')"), cwd=tmp_path, timeout_seconds=5),
        actor=actor,
        grants=grants,
    )
    assert result.status is ResultStatus.PASSED
    assert result.stdout.strip() == b"ok"


def test_process_failure_is_structured(tmp_path: Path) -> None:
    actor, grants, runner = authorized(tmp_path)
    result = runner.run(
        CommandSpec(
            argv=(sys.executable, "-c", "raise SystemExit(7)"), cwd=tmp_path, timeout_seconds=5
        ),
        actor=actor,
        grants=grants,
    )
    assert result.status is ResultStatus.FAILED
    assert result.exit_code == 7


def test_process_timeout(tmp_path: Path) -> None:
    actor, grants, runner = authorized(tmp_path)
    result = runner.run(
        CommandSpec(
            argv=(sys.executable, "-c", "import time; time.sleep(2)"),
            cwd=tmp_path,
            timeout_seconds=0.1,
        ),
        actor=actor,
        grants=grants,
    )
    assert result.status is ResultStatus.TIMED_OUT
    assert result.timed_out


def test_output_is_truncated(tmp_path: Path) -> None:
    actor, grants, runner = authorized(tmp_path)
    result = runner.run(
        CommandSpec(
            argv=(sys.executable, "-c", "print('x'*1000)"),
            cwd=tmp_path,
            timeout_seconds=5,
            max_output_bytes=20,
        ),
        actor=actor,
        grants=grants,
    )
    assert len(result.stdout) == 20
    assert result.stdout_truncated


def test_unauthorized_command_is_not_spawned(tmp_path: Path) -> None:
    actor, grants, runner = authorized(tmp_path)
    with pytest.raises(CapabilityDenied):
        runner.run(
            CommandSpec(argv=("git", "status"), cwd=tmp_path, timeout_seconds=5),
            actor=actor,
            grants=grants,
        )


def test_output_bound_is_applied_while_reading(tmp_path: Path) -> None:
    """The runner must not buffer the whole output before truncating it (#9)."""
    import tracemalloc

    actor, grants, runner = authorized(tmp_path)
    produce_32_mib = (
        "import sys; chunk = b'x' * 65536; [sys.stdout.buffer.write(chunk) for _ in range(512)]"
    )
    tracemalloc.start()
    try:
        result = runner.run(
            CommandSpec(
                argv=(sys.executable, "-c", produce_32_mib),
                cwd=tmp_path,
                timeout_seconds=30,
                max_output_bytes=1024,
            ),
            actor=actor,
            grants=grants,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.status is ResultStatus.PASSED
    assert result.stdout == b"x" * 1024
    assert result.stdout_truncated is True
    assert peak < 4 * 1024 * 1024, f"peak traced memory {peak} bytes for a 1 KiB bound"


WRAPPER = (
    "import subprocess, sys\n"
    "sys.stderr.write('wrapped\\n')\n"
    "raise SystemExit(subprocess.call(sys.argv[1:]))\n"
)


def test_sandbox_prefix_wraps_the_authorized_command(tmp_path: Path) -> None:
    """The confinement wrapper runs the command; authorization applies to the command itself,
    so the wrapper needs no grant of its own."""
    actor, grants, runner = authorized(tmp_path)
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    result = runner.run(
        CommandSpec(
            argv=(sys.executable, "-c", "print('inner')"),
            cwd=tmp_path,
            timeout_seconds=10,
            sandbox_prefix=(sys.executable, str(wrapper)),
        ),
        actor=actor,
        grants=grants,
    )
    assert result.status is ResultStatus.PASSED
    assert result.stdout.strip() == b"inner"
    assert result.stderr.strip() == b"wrapped"


def test_sandbox_prefix_does_not_authorize_the_command(tmp_path: Path) -> None:
    actor, grants, runner = authorized(tmp_path)
    with pytest.raises(CapabilityDenied):
        runner.run(
            CommandSpec(
                argv=("not-granted",),
                cwd=tmp_path,
                timeout_seconds=5,
                sandbox_prefix=(sys.executable,),
            ),
            actor=actor,
            grants=grants,
        )
