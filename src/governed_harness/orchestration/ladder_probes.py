"""Running behaviour probes (#55, items 3 and 4).

A probe is run once per variant, without a shell, with the harness's process runner (so the
capability grants apply: a project probe's executable is granted by the configuration, a task
probe's executable must already be granted) and an environment of its ``passEnv`` names plus the
variant's own variables. The output is evaluated by ``ladder.probes``. A probe runs on the
workspace after the change (VERIFICATION) or on a scratch copy of the baseline (preflight in
PLANNING); either way an executable that is missing, refused or too slow makes the probe
``UNAVAILABLE``, never ``PASSED``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor, Execution, ProbeDefinition
from governed_harness.ladder.probes import ProbeEvaluation, VariantRun, evaluate, fill
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.runtime.process_runner import CommandSpec

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import EngineHost

PROBE_ACTOR_PREFIX = "validator.probe."
_STREAM_CHARS = 2_000


@dataclass(frozen=True)
class ProbeRun:
    """A probe's evaluation and the evidence of its variants (stored output)."""

    evaluation: ProbeEvaluation
    variants: tuple[dict[str, Any], ...]
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.evaluation.as_dict(),
            "variantRuns": list(self.variants),
            "durationMs": self.duration_ms,
        }


def probe_actor(probe: ProbeDefinition) -> Actor:
    return Actor(
        actor_type=ActorType.TOOL, actor_id=f"{PROBE_ACTOR_PREFIX}{probe.probe_id}", version="1"
    )


def run_probe(
    engine: EngineHost,
    execution: Execution,
    probe: ProbeDefinition,
    root: Path,
    runner: SafeProcessRunner,
) -> ProbeRun:
    """Run every variant of ``probe`` in ``root`` (the workspace or a scratch copy)."""
    services = engine.s
    actor = probe_actor(probe)
    grants = grants_from_rules(
        execution.execution_id, actor, services.resolved.effective_capabilities
    )
    cancellation = CancellationToken(lambda: engine.is_cancelled(execution.execution_id))
    runs: list[VariantRun] = []
    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    for variant in probe.expanded_variants():
        argv = tuple(fill(item, variant.values) for item in probe.command)
        cwd = root / fill(probe.cwd, variant.values)
        record: dict[str, Any] = {"variant": variant.name, "argv": list(argv)}
        try:
            result = runner.run(
                CommandSpec(
                    argv=argv,
                    cwd=cwd,
                    timeout_seconds=float(engine._bounded_timeout(probe.timeout_seconds)),
                    allowed_environment=(*probe.pass_env, *variant.env),
                    max_output_bytes=services.resolved.project.runtime.max_output_bytes,
                ),
                actor=actor,
                grants=grants,
                extra_env=variant.env or None,
                cancellation=cancellation,
            )
        except FileNotFoundError:
            problem = f"{argv[0]} not found"
            runs.append(VariantRun(variant.name, False, None, "", problem=problem))
            records.append({**record, "ran": False, "problem": problem})
            continue
        except Exception as error:  # noqa: BLE001  # a refused or failed start is "unavailable"
            problem = f"{type(error).__name__}: {error}"
            runs.append(VariantRun(variant.name, False, None, "", problem=problem))
            records.append({**record, "ran": False, "problem": problem})
            continue
        stdout = result.stdout.decode("utf-8", "replace")
        stderr = result.stderr.decode("utf-8", "replace")
        ran = not result.timed_out and not result.cancelled
        stopped = "timed out" if result.timed_out else "cancelled" if result.cancelled else None
        runs.append(VariantRun(variant.name, ran, result.exit_code, stdout, stderr, stopped))
        stdout_ref = services.artifacts.put(
            result.stdout,
            media_type="text/plain",
            metadata={"stream": "stdout", "probe": probe.probe_id},
        )
        stderr_ref = services.artifacts.put(
            result.stderr,
            media_type="text/plain",
            metadata={"stream": "stderr", "probe": probe.probe_id},
        )
        records.append(
            {
                **record,
                "ran": ran,
                "exitCode": result.exit_code,
                "problem": stopped,
                "stdoutRef": stdout_ref.uri,
                "stderrRef": stderr_ref.uri,
                "stdoutHead": stdout[:_STREAM_CHARS],
            }
        )
    duration = max(0, round((time.perf_counter() - started) * 1000))
    return ProbeRun(evaluate(probe, runs), tuple(records), duration)


__all__ = ["PROBE_ACTOR_PREFIX", "ProbeRun", "probe_actor", "run_probe"]
