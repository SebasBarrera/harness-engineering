"""The conditions of tests T1 and T6 never share a cell (``evaluation/``).

T1 compares ``harness`` with and without the simulated product owner on the same casual prompt;
T6 puts ``harness-nopanel`` between ``harness-core`` and ``harness``. Each one has its own
condition name, which reaches the run directory, the record and every cell key of the reports."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))
sys.path.insert(0, str(EVALUATION / "analysis"))

import compare_implementations as cmp  # noqa: E402
import conditions  # noqa: E402
import repeatability  # noqa: E402
import report  # noqa: E402
import run_eval  # noqa: E402
import run_matrix  # noqa: E402

STAMP = "20261112T010101Z"


def test_harness_without_product_owner_is_a_governed_condition_of_its_own() -> None:
    assert "harness-nopo" in conditions.GOVERNED
    assert "harness-nopo" in conditions.NO_PRODUCT_OWNER
    assert "harness-nopo" in run_eval.CONDITION_CHOICES
    assert "harness-nopo" in report.GOVERNED_V2


@pytest.mark.parametrize(
    ("condition", "no_po", "recorded"),
    [
        ("harness", True, "harness-nopo"),
        ("harness", False, "harness"),
        ("harness-nopo", False, "harness-nopo"),
        ("baseline", False, "direct"),
        ("harness-core", True, "harness-core"),
    ],
)
def test_the_recorded_condition_is_the_same_in_run_eval_and_run_matrix(
    condition: str, no_po: bool, recorded: str
) -> None:
    assert run_eval.effective_condition(condition, no_po) == recorded
    # run_matrix skips a run already in the output file by the condition run_eval records.
    assert run_matrix.recorded_condition(condition, no_po) == recorded


def test_harness_nopo_has_the_configuration_of_harness(tmp_path: Path) -> None:
    def configured(condition: str) -> dict[str, Any]:
        return conditions.apply_provider(
            {"runtime": {}},
            condition=condition,
            model="claude-sonnet-5-5",
            code=tmp_path,
            run_dir=tmp_path / "run",
            provider="simulated",
        )

    assert configured("harness-nopo") == configured("harness")
    assert configured("harness-nopo")["agentRouting"]["mode"] == "fixed"


def test_report_cells_split_harness_with_and_without_product_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(report, "summarize_cell_v2", len)
    base = {"prompt": "casual", "model": "m", "scenario": "greenfield"}
    records = [
        {**base, "condition": "harness"},
        {**base, "condition": "harness-nopo"},
        # a record written before the condition had its own name
        {**base, "condition": "harness", "productOwnerMode": "none"},
    ]
    cells = report.summarize_v2(records)["B"]
    assert cells == {"casual|harness-nopo|m|greenfield": 2, "casual|harness|m|greenfield": 1}


def test_repeatability_cells_split_harness_with_and_without_product_owner() -> None:
    base = {"scenario": "greenfield", "prompt": "casual", "model": "m", "measures": {}}
    records = [
        {**base, "condition": "harness"},
        {**base, "condition": "harness", "productOwnerMode": "none"},
        {**base, "condition": "harness-nopo"},
    ]
    cells = {c["condition"]: c["runs"] for c in repeatability.analyze(records)["cells"]}
    assert cells == {"harness": 1, "harness-nopo": 2}


@pytest.mark.parametrize(
    ("name", "condition", "model", "prompt"),
    [
        (
            "brownfield-harness-nopanel-claude-sonnet-5-5-r1",
            "harness-nopanel",
            "claude-sonnet-5-5",
            "full",
        ),
        (
            "greenfield-casual-harness-nopo-claude-haiku-4-5-20251001-r3",
            "harness-nopo",
            "claude-haiku-4-5-20251001",
            "casual",
        ),
        (
            "greenfield-casual-harness-claude-sonnet-5-5-r2",
            "harness",
            "claude-sonnet-5-5",
            "casual",
        ),
        (
            "brownfield-harness-core-claude-sonnet-5-5-r1",
            "harness-core",
            "claude-sonnet-5-5",
            "full",
        ),
        (
            "brownfield-harness-anchored-claude-opus-5-5-r1",
            "harness-anchored",
            "claude-opus-5-5",
            "full",
        ),
        ("security-poor-direct-po-claude-sonnet-5-5-r1", "direct-po", "claude-sonnet-5-5", "poor"),
    ],
)
def test_run_names_parse_to_their_own_condition_and_model(
    name: str, condition: str, model: str, prompt: str
) -> None:
    meta = cmp.parse_run_name(f"{name}-{STAMP}")
    assert meta is not None
    assert (meta["condition"], meta["model"], meta["prompt"]) == (condition, model, prompt)
