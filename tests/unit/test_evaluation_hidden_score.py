"""The fixed denominator and the discriminating hidden tests (``evaluation/measure.py``).

Amendment of 2026-11-12 to the pre-registration: a collection error (junit ``tests: 1, errors:
1``) scores 0 of the scenario's nominal number of hidden tests, and the tests the untouched
baseline already passes are reported apart from the discriminating ones."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import measure  # noqa: E402
import report  # noqa: E402


def _junit(path: Path, cases: dict[str, str]) -> Path:
    body = "".join(
        f'<testcase name="{name}">{"" if outcome == "passed" else f"<{outcome}/>"}</testcase>'
        for name, outcome in cases.items()
    )
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")
    return path


def test_a_collection_error_scores_zero_of_the_nominal(tmp_path: Path) -> None:
    junit = _junit(tmp_path / "h.xml", {"test_hidden_alerts": "error"})
    hidden = {"tests": 1, "passed": 0, "errors": 1, "failures": 0}
    hidden.update(measure.hidden_score("security", hidden, junit))
    assert hidden["nominal"] == 14
    assert hidden["collectionError"] is True
    assert hidden["discriminating"] == {"total": 14, "passed": 0}
    record = {"measures": {"hidden": hidden}}
    assert not report.hidden_ok(record)
    assert report.hidden_total(hidden) == 14


def test_brownfield_discriminating_tests_leave_out_what_the_baseline_passes(
    tmp_path: Path,
) -> None:
    names = sorted(measure.HIDDEN_DISCRIMINATING["brownfield"] or [])
    cases = {name: "failure" for name in names}
    cases[names[0]] = "passed"
    cases.update({f"test_baseline_{i}": "passed" for i in range(18)})
    junit = _junit(tmp_path / "h.xml", cases)
    hidden = {"tests": 24, "passed": 19, "errors": 0, "failures": 5}
    score = measure.hidden_score("brownfield", hidden, junit)
    assert score == {
        "nominal": 24,
        "collectionError": False,
        "discriminating": {"total": 6, "passed": 1},
        "baselinePassing": 18,
    }


@pytest.mark.parametrize(
    ("scenario", "package"),
    [("greenfield", "greenfield-shipping"), ("security", "greenfield-alerts")],
)
def test_the_nominal_counts_are_what_the_hidden_files_collect(scenario: str, package: str) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            str(EVALUATION / measure.HIDDEN[scenario]),
        ],
        capture_output=True,
        text=True,
        cwd=EVALUATION,
        env={**os.environ, "PYTHONPATH": str(EVALUATION / "reference" / package)},
        check=False,
    )
    assert f": {measure.HIDDEN_NOMINAL[scenario]}" in proc.stdout, proc.stdout + proc.stderr
