"""Repetibilidad de la evaluación (``evaluation/analysis/repeatability.py``), registros sintéticos."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ANALYSIS = Path(__file__).resolve().parents[2] / "evaluation" / "analysis"
sys.path.insert(0, str(ANALYSIS))

import repeatability as rep  # noqa: E402


def record(
    passed: int, files: int = 2, out: int = 0, added: int = 10, removed: int = 1, **extra: Any
) -> dict[str, Any]:
    return {
        "scenario": "brownfield",
        "prompt": "full",
        "condition": "direct",
        "model": "m",
        "measures": {
            "hidden": {"passed": passed},
            "change": {
                "files": files,
                "outOfScopeFiles": out,
                "addedLines": added,
                "removedLines": removed,
            },
        },
        **extra,
    }


def test_min_max_range_and_exclusions() -> None:
    records = [
        record(20, files=2, added=10),
        record(24, files=4, out=1, added=30),
        record(1, invalid="usage-limit (1 call(s))"),
        record(2, harness={"outcome": "rejected"}),
        record(24, harness={"outcome": "approved"}, added=10),
    ]
    cell = rep.analyze(records)["cells"][0]
    assert (cell["runs"], cell["counted"], cell["rejected"], cell["invalid"]) == (5, 3, 1, 1)
    assert cell["metrics"]["hiddenPassed"] == {"min": 20, "max": 24, "range": 4}
    assert cell["metrics"]["files"] == {"min": 2, "max": 4, "range": 2}
    assert cell["metrics"]["outOfScopeFiles"] == {"min": 0, "max": 1, "range": 1}
    assert cell["metrics"]["changedLines"] == {"min": 11, "max": 31, "range": 20}


def test_cells_are_separate_and_missing_measures_have_no_data() -> None:
    other = record(5)
    other["condition"] = "harness"
    empty = {"scenario": "s", "prompt": "p", "condition": "c", "model": "m"}
    cells = rep.analyze([record(5), other, empty])["cells"]
    assert len(cells) == 3
    assert cells[-1]["metrics"]["hiddenPassed"] is None


def test_files_markdown_and_invalid_file(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text(json.dumps(record(24)) + chr(10), encoding="utf-8")
    (tmp_path / "a.invalid.jsonl").write_text(json.dumps(record(3)) + chr(10), encoding="utf-8")
    result = rep.analyze(rep.read_records([tmp_path]))
    assert result["excluded"] == {"rejected": 0, "invalid": 1}
    assert "24 / 24 (0)" in rep.render_markdown(result)
    out = tmp_path / "out"
    assert rep.main(["--in", str(tmp_path), "--out", str(out)]) == 0
    assert (out / "repetibilidad.json").exists() and (out / "repetibilidad.md").exists()
