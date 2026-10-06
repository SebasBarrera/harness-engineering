from __future__ import annotations

import os
import platform
import shutil
import statistics
import subprocess  # nosec B404 - fixed git, node and fixture argv, no shell
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, ResultStatus
from governed_harness.runtime import resolve_executable

# The fixture repository must not inherit the user's Git configuration: global commit signing or
# a global core.hooksPath would make the baseline commit fail or prompt.
_GIT_ISOLATION = ("-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}")


def _git_init(root: Path) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*args: str) -> None:
        subprocess.run(["git", *_GIT_ISOLATION, *args], cwd=root, check=True, env=env)  # nosec B603 B607 - git from PATH, fixed fixture argv, no shell

    git("init", "-q")
    git("config", "user.email", "benchmark@example.invalid")
    git("config", "user.name", "Benchmark Fixture")
    git("add", ".")
    git("commit", "-qm", "baseline")


def _python_fixture(root: Path) -> tuple[Path, tuple[str, ...]]:
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text(
        "from .pricing import apply_discount\n", encoding="utf-8"
    )
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_pricing.py").write_text(
        "from sample import apply_discount\n\n"
        "def test_below_threshold() -> None:\n"
        "    assert apply_discount(99, 100, 0.1) == 99\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n\n"
        "[project]\nname='benchmark-python-fixture'\nversion='0.1.0'\n\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\npythonpath=['src']\n",
        encoding="utf-8",
    )
    task = root / "task.yaml"
    task.write_text(
        "title: Implement threshold discount\n"
        "intent: Apply a discount at or above the threshold.\n"
        "acceptanceCriteria:\n"
        "  - A subtotal of 100 with a ten percent rate returns 90.\n"
        "  - A subtotal below the threshold is unchanged.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/sample/pricing.py\n"
        "      operation: replace\n"
        "      content: |\n"
        "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
        "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        def test_at_threshold() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    _git_init(root)
    return task, (sys.executable, "-m", "pytest", "-q")


def _node_fixture(root: Path) -> tuple[Path, tuple[str, ...]]:
    (root / "src").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "src" / "resolve-config.js").write_text(
        "export function resolveConfig(defaults, repository, task) {\n"
        "  return { ...defaults, ...task, ...repository };\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "test" / "resolve-config.test.js").write_text(
        "import assert from 'node:assert/strict';\n"
        "import test from 'node:test';\n"
        "import { resolveConfig } from '../src/resolve-config.js';\n\n"
        "test('repository overrides defaults', () => {\n"
        "  assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {}), {mode:'strict'});\n"
        "});\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        "{\n"
        '  "name": "benchmark-node-fixture",\n'
        '  "version": "0.1.0",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "test": "node --test",\n'
        '    "lint": "node --check src/resolve-config.js",\n'
        '    "typecheck": "node --check src/resolve-config.js"\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    task = root / "task.yaml"
    task.write_text(
        "title: Correct configuration precedence\n"
        "intent: Task values must override repository values, which override defaults.\n"
        "acceptanceCriteria:\n"
        "  - Task values have the highest precedence.\n"
        "  - Existing repository precedence remains covered.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/resolve-config.js\n"
        "      operation: replace\n"
        "      content: |\n"
        "        export function resolveConfig(defaults, repository, task) {\n"
        "          return { ...defaults, ...repository, ...task };\n"
        "        }\n"
        "    - path: test/resolve-config.test.js\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        test('task overrides repository', () => {\n"
        "          assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {mode:'task'}), {mode:'task'});\n"
        "        });\n",
        encoding="utf-8",
    )
    _git_init(root)
    return task, ("npm", "test", "--silent")


def _apply_direct_change(stack: str, root: Path) -> None:
    if stack == "python":
        (root / "src" / "sample" / "pricing.py").write_text(
            "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
            "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n",
            encoding="utf-8",
        )
        with (root / "tests" / "test_pricing.py").open("a", encoding="utf-8") as handle:
            handle.write(
                "\n\ndef test_at_threshold() -> None:\n"
                "    assert apply_discount(100, 100, 0.1) == 90\n"
            )
    else:
        (root / "src" / "resolve-config.js").write_text(
            "export function resolveConfig(defaults, repository, task) {\n"
            "  return { ...defaults, ...repository, ...task };\n"
            "}\n",
            encoding="utf-8",
        )
        with (root / "test" / "resolve-config.test.js").open("a", encoding="utf-8") as handle:
            handle.write(
                "\n\ntest('task overrides repository', () => {\n"
                "  assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {mode:'task'}), {mode:'task'});\n"
                "});\n"
            )


def _summarize(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "iterations": float(len(samples)),
        "meanMs": statistics.fmean(samples),
        "medianMs": statistics.median(samples),
        "p95Ms": p95,
        "minMs": min(samples),
        "maxMs": max(samples),
    }


def _run_direct(
    stack: str, fixture: Callable[[Path], tuple[Path, tuple[str, ...]]]
) -> tuple[float, dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix=f"harness-direct-{stack}-") as directory:
        root = Path(directory)
        _, command = fixture(root)
        start = time.perf_counter_ns()
        _apply_direct_change(stack, root)
        argv = [resolve_executable(command[0]), *command[1:]]
        result = subprocess.run(argv, cwd=root, capture_output=True, check=False)  # nosec B603 - the fixture's own resolved command, no shell
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        if result.returncode != 0:
            raise RuntimeError(
                f"direct {stack} fixture failed: {result.stderr.decode('utf-8', 'replace')[:500]}"
            )
        return elapsed_ms, {"exitCode": result.returncode}


def _run_governed(
    stack: str, fixture: Callable[[Path], tuple[Path, tuple[str, ...]]]
) -> tuple[float, dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix=f"harness-governed-{stack}-") as directory:
        root = Path(directory)
        task_file, _ = fixture(root)
        app = HarnessApplication()
        start = time.perf_counter_ns()
        app.init(root)
        task = app.create_task(root, task_file)
        pending = app.start_run(root, task.task_id)
        if pending.status is not ResultStatus.BLOCKED or not pending.change_set_digest:
            raise RuntimeError(
                f"governed {stack} fixture did not reach the human gate: {pending.status}"
            )
        _, final = app.decide_gate(
            root,
            execution_id=pending.execution_id,
            decision=DecisionKind.APPROVE,
            change_set_digest=pending.change_set_digest,
            actor_id="human.benchmark",
            rationale="Programmatic benchmark approval after all deterministic checks passed",
        )
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        if final.status is not ResultStatus.PASSED:
            raise RuntimeError(f"governed {stack} fixture did not close: {final.status}")
        status = app.status(root, final.execution_id)
        return elapsed_ms, {
            "executionId": final.execution_id,
            "events": status["eventCount"],
            "validations": status["validationSummary"]["total"],
            "findings": status["findings"]["total"],
            "eventChainValid": status["eventChainValid"],
        }


def run_scenario_benchmarks(*, iterations: int = 3) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    scenarios: dict[str, object] = {}
    for stack, fixture, available in (
        ("python", _python_fixture, True),
        (
            "node",
            _node_fixture,
            shutil.which("node") is not None and shutil.which("npm") is not None,
        ),
    ):
        if not available:
            scenarios[stack] = {"status": "NOT_AVAILABLE", "reason": "required runtime not found"}
            continue
        direct_samples: list[float] = []
        governed_samples: list[float] = []
        last_details: dict[str, object] = {}
        for _ in range(iterations):
            direct_ms, _ = _run_direct(stack, fixture)
            governed_ms, last_details = _run_governed(stack, fixture)
            direct_samples.append(direct_ms)
            governed_samples.append(governed_ms)
        direct = _summarize(direct_samples)
        governed = _summarize(governed_samples)
        overhead = governed["medianMs"] - direct["medianMs"]
        scenarios[stack] = {
            "status": "PASSED",
            "directImplementationAndTests": direct,
            "governedEndToEnd": governed,
            "governanceOverhead": {
                "absoluteMedianMs": overhead,
                "relativeMedianPercent": (
                    (governed["medianMs"] / direct["medianMs"] - 1) * 100
                    if direct["medianMs"]
                    else None
                ),
            },
            "lastRun": last_details,
        }
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "node": subprocess.run(  # nosec B603 B607 - node --version from PATH, no shell
                ["node", "--version"], capture_output=True, text=True
            ).stdout.strip()
            if shutil.which("node")
            else None,
            "platform": platform.platform(),
        },
        "methodology": {
            "scope": "Synthetic local fixtures; not a productivity or quality claim.",
            "directPath": "Apply the same deterministic patch and execute the mandatory test command.",
            "governedPath": "Initialize, persist task, execute all normative phases, validate, review, evaluate the gate, apply a programmatic benchmark approval, close, and project status.",
            "humanWait": "Excluded: the benchmark approval is immediate and programmatic.",
            "network": "No network access required.",
        },
        "scenarios": scenarios,
    }
