from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governed_harness.benchmark import run_benchmarks, run_scenario_benchmarks
from governed_harness.benchmark.scenarios import _git_init


@pytest.mark.performance
def test_microbenchmark_result_is_structured() -> None:
    result = run_benchmarks(iterations=10)
    assert result["schemaVersion"] == "1.0"
    assert result["benchmarks"]["canonicalHash"]["iterations"] >= 10
    overhead = result["benchmarks"]["processGovernanceOverhead"]["absoluteMedianMs"]
    assert isinstance(overhead, float)


@pytest.mark.performance
def test_python_scenario_benchmark_completes() -> None:
    result = run_scenario_benchmarks(iterations=1)
    python = result["scenarios"]["python"]
    assert python["status"] == "PASSED"
    assert python["lastRun"]["eventChainValid"] is True


@pytest.mark.performance
def test_scenario_fixture_ignores_the_users_git_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global Git configuration that signs commits must not break the fixture repository."""
    config = tmp_path / "gitconfig"
    config.write_text(
        "[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n", encoding="utf-8"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "file.txt").write_text("content\n", encoding="utf-8")
    _git_init(root)
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True, check=True
    )
    assert log.stdout.strip().endswith("baseline")
