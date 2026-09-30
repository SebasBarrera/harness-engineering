from __future__ import annotations

import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.models import Actor
from governed_harness.events import SQLiteEventStore
from governed_harness.evidence import LocalArtifactStore, sha256_json
from governed_harness.gates import GateEngine, GateInput
from governed_harness.orchestration import NormativeStateMachine
from governed_harness.runtime import CommandSpec, SafeProcessRunner


def _measure(operation: Callable[[], object], iterations: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    total_ms = sum(samples)
    return {
        "iterations": float(iterations),
        "totalMs": total_ms,
        "meanMs": statistics.fmean(samples),
        "medianMs": statistics.median(samples),
        "p95Ms": p95,
        "operationsPerSecond": iterations / (total_ms / 1000) if total_ms else 0.0,
    }


def run_benchmarks(*, iterations: int = 1000) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="harness-bench-") as directory:
        root = Path(directory)
        event_store = SQLiteEventStore(root / "events.db")
        artifact_store = LocalArtifactStore(root / "artifacts")
        gate = GateEngine()
        machine = NormativeStateMachine()
        hash_payload = {"phase": "VERIFICATION", "values": list(range(100))}
        event_index = 0

        def append_event() -> object:
            nonlocal event_index
            event_index += 1
            return event_store.append("run_benchmark", "benchmark.event", {"i": event_index})

        core_iterations = max(10, iterations)
        artifact_iterations = max(10, min(iterations, 5000))
        event_iterations = max(10, min(iterations, 10000))
        results: dict[str, object] = {
            "canonicalHash": _measure(lambda: sha256_json(hash_payload), core_iterations),
            "stateTransition": _measure(
                lambda: machine.advance(PhaseId.INTENT, ResultStatus.PASSED), core_iterations
            ),
            "gateEvaluation": _measure(
                lambda: gate.evaluate([GateInput("tests", ResultStatus.PASSED)]), core_iterations
            ),
            "artifactPutDeduplicated": _measure(
                lambda: artifact_store.put(b"benchmark-evidence", redact=False), artifact_iterations
            ),
            "eventAppend": _measure(append_event, event_iterations),
        }

        actor = Actor(actor_type=ActorType.TOOL, actor_id="benchmark.runner")
        grants = grants_from_rules(
            "run_benchmark",
            actor,
            (CapabilityRule(capability="process.execute", scope=(sys.executable,)),),
            lifetime=timedelta(minutes=5),
        )
        runner = SafeProcessRunner(root)
        subprocess_iterations = max(5, min(30, iterations // 50 or 5))

        def direct_process() -> object:
            import subprocess

            return subprocess.run(
                [sys.executable, "-c", "pass"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        def governed_process() -> object:
            return runner.run(
                CommandSpec(
                    argv=(sys.executable, "-c", "pass"),
                    cwd=root,
                    timeout_seconds=10,
                ),
                actor=actor,
                grants=grants,
            )

        direct = _measure(direct_process, subprocess_iterations)
        governed = _measure(governed_process, subprocess_iterations)
        results["processDirect"] = direct
        results["processGoverned"] = governed
        results["processGovernanceOverhead"] = {
            "absoluteMedianMs": governed["medianMs"] - direct["medianMs"],
            "relativeMedianPercent": (
                (governed["medianMs"] / direct["medianMs"] - 1) * 100
                if direct["medianMs"]
                else None
            ),
            "note": "Local process-launch overhead only; excludes validators, agents and human wait.",
        }
        event_store.close()
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or "not reported",
        },
        "methodology": {
            "clock": "time.perf_counter_ns",
            "warmup": "No explicit warmup; every sample is reported in the aggregate.",
            "scope": "Microbenchmarks on this execution environment; not general performance claims.",
        },
        "benchmarks": results,
    }
