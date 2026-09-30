from __future__ import annotations

import pytest

from governed_harness.benchmark import run_benchmarks, run_scenario_benchmarks


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
