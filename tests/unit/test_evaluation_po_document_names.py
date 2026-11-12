"""The simulated product owner never leaves a document name in its answers (block B, 2026-11-12).

In the poor and casual levels the workspace has no SPEC.md. When the product owner's answer cited
it, the harness asked again every round (rule A2, a dangling document) and the person ran out of
rounds in INTENT. The product owner now asks for the content instead of the name, and replaces a
name that remains."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import fake_claude  # noqa: E402
import product_owner  # noqa: E402

from governed_harness.orchestration import intent_review  # noqa: E402

PROMPT = "Eres el product owner.\n\nTarea:\ntitle: T\n\nPreguntas:\n- Q-1: ¿Qué?\n"


def _reply(cites: bool) -> dict[str, Any]:
    """The fake product owner's reply, citing SPEC.md in its answer when ``cites`` is true."""
    text = fake_claude._product_owner(PROMPT) or ""
    if cites:
        text = text.replace("devuelve el resultado", "devuelve, según SPEC.md, el resultado")
    return {"resultText": text}


def _owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list[bool]) -> Any:
    calls = iter(replies)
    monkeypatch.setattr(
        product_owner, "run_claude", lambda *a, **k: _reply(next(calls)), raising=True
    )
    monkeypatch.setattr(product_owner, "allocate_call", lambda d: d / "call-1", raising=True)
    (tmp_path / "ws").mkdir()
    return product_owner.ProductOwner("producto", "m", tmp_path)


def _asked_again(answers: dict[str, Any]) -> list[str]:
    """What rule A2 of the harness would ask about these answers in a workspace without SPEC.md."""
    return [
        name
        for text in product_owner._strings(answers)
        for name in intent_review._doc_references(text)
    ]


def test_the_prompt_forbids_citing_documents() -> None:
    assert "no cites documentos por su nombre" in product_owner.PO_PROMPT


def test_the_unfixed_reply_is_the_a2_loop(tmp_path: Path) -> None:
    # The citation the old product owner left in every round: the harness's own reader finds it.
    cited = product_owner.parse_answers(_reply(True)["resultText"], ["Q-1"])
    assert _asked_again(cited) == ["SPEC.md"]


def test_a_reader_matching_the_harness() -> None:
    for text in ("ver SPEC.md y docs/spec.md.", "README.MD, notes.txt", "src/a.py y v1.5 y 3.14"):
        assert product_owner.document_references(text) == intent_review._doc_references(text)


def test_a_citation_is_retried_and_the_clean_answer_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(tmp_path, monkeypatch, [True, False])
    answers = owner({"title": "T"}, {"questions": [{"questionId": "Q-1", "text": "¿Qué?"}]})
    assert _asked_again(answers) == []
    assert "scrubbedDocuments" not in owner.rounds[-1]


def test_a_citation_that_remains_is_replaced_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(tmp_path, monkeypatch, [True, True])
    answers = owner({"title": "T"}, {"questions": [{"questionId": "Q-1", "text": "¿Qué?"}]})
    assert _asked_again(answers) == []
    assert product_owner.DOCUMENT_STAND_IN in answers["answers"]["Q-1"]
    assert owner.rounds[-1]["scrubbedDocuments"] == ["SPEC.md"]


def test_a_document_the_workspace_holds_is_not_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(tmp_path, monkeypatch, [True])
    (tmp_path / "ws" / "SPEC.md").write_text("x", encoding="utf-8")
    answers = owner({"title": "T"}, {"questions": [{"questionId": "Q-1", "text": "¿Qué?"}]})
    assert "SPEC.md" in answers["answers"]["Q-1"]
