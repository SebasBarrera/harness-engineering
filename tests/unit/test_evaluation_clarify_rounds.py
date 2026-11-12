"""The simulated person's cap of clarification rounds follows the project (amendment of 2026-11-12).

With ``intake.ambiguityReview.depth: adaptive`` the harness may ask up to
``max(maxRounds, depth.MAX_ROUNDS)`` agent rounds, and the project-setup and deterministic rounds
come on top: the person must not stop before the harness does. The fake product owner of the dry
runs adds its criterion once, so it never creates the duplicate that rule C3 asks about again."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import fake_claude  # noqa: E402
import person  # noqa: E402

from governed_harness.intake import depth  # noqa: E402


def _project(tmp_path: Path, config: Any) -> Path:
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "project.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return tmp_path


def test_the_cap_reads_the_harness_ceiling() -> None:
    assert person.ADAPTIVE_MAX_ROUNDS == depth.MAX_ROUNDS


@pytest.mark.parametrize(
    ("review", "cap"),
    [
        ({"maxRounds": 3, "depth": "adaptive"}, depth.MAX_ROUNDS + person.EXTRA_CLARIFY_ROUNDS),
        ({"maxRounds": 10, "depth": "adaptive"}, 10 + person.EXTRA_CLARIFY_ROUNDS),
        ({"maxRounds": 3, "depth": "fixed"}, person.MAX_CLARIFY_ROUNDS),
        ({"maxRounds": 5}, 5 + person.EXTRA_CLARIFY_ROUNDS),
        ({"depth": "adaptive"}, depth.MAX_ROUNDS + person.EXTRA_CLARIFY_ROUNDS),
    ],
)
def test_the_cap_is_above_every_round_the_harness_may_ask(
    tmp_path: Path, review: dict[str, Any], cap: int
) -> None:
    workspace = _project(tmp_path, {"intake": {"ambiguityReview": review}})
    assert person.clarify_round_cap(workspace) == cap
    rounds = review.get("maxRounds", 3)
    if review.get("depth") == "adaptive":
        rounds = max(rounds, depth.MAX_ROUNDS)
    assert person.clarify_round_cap(workspace) > rounds


def test_without_the_key_the_cap_is_the_old_guard(tmp_path: Path) -> None:
    assert person.clarify_round_cap(tmp_path) == person.MAX_CLARIFY_ROUNDS == 6
    assert person.clarify_round_cap(_project(tmp_path, {"configVersion": "1.0"})) == 6


def _po_prompt(task: str) -> str:
    return f"Eres el product owner.\n\nTarea:\n{task}\n\nPreguntas:\n- Q-1: ¿Qué?\n"


def test_the_fake_product_owner_adds_its_criterion_once() -> None:
    first = fake_claude._product_owner(_po_prompt("title: T\n")) or ""
    assert "addCriteria" in first and "Q-1" in first
    criterion = "Ejecutar python -m pytest -q termina con código 0."
    again = fake_claude._product_owner(_po_prompt(f"acceptanceCriteria:\n- {criterion}\n")) or ""
    assert "addCriteria" not in again and "Q-1" in again
