"""The simulated product owner survives tool-call markup after its YAML (amendment of 2026-11-13).

In run r2 of rides super harness, step S02, both replies of the product owner ended with
``</parameter>`` and ``</invoke>`` after the YAML (the first without a closing fence, the second
inside it). The parser raised a ScannerError twice and the run stopped as ``product-owner-failed``:
an apparatus defect. The two replies are kept verbatim in ``tests/fixtures``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
sys.path.insert(0, str(EVALUATION))

import agentlib  # noqa: E402
import product_owner  # noqa: E402

IDS = [f"Q-{n}" for n in range(3, 11)]
QUESTIONS = {"questions": [{"questionId": q, "text": "¿Qué?"} for q in IDS]}
S02 = [FIXTURES / f"rides-r2-s02-po-call-{n}.txt" for n in (1, 2)]


@pytest.mark.parametrize("reply", S02, ids=["call-1", "call-2"])
def test_the_recorded_reply_fails_the_strict_parser(reply: Path) -> None:
    with pytest.raises(yaml.YAMLError):
        product_owner.parse_answers(reply.read_text(encoding="utf-8"), IDS)


@pytest.mark.parametrize("reply", S02, ids=["call-1", "call-2"])
def test_the_sanitized_reply_answers_every_question(reply: Path) -> None:
    text, notes = product_owner.sanitize_reply(reply.read_text(encoding="utf-8"))
    assert notes[0] == "removed tool-call markup lines: </parameter>, </invoke>"
    assert set(product_owner.parse_answers(text, IDS)["answers"]) == set(IDS)


def test_a_valid_reply_is_not_changed() -> None:
    text = "```yaml\nanswers:\n  Q-3: |\n    <parameter> en una línea con sangría es texto.\n```"
    assert product_owner.sanitize_reply(text) == (text, [])


def _owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> Any:
    """A product owner whose calls go to fake_claude.py, which gives ``replies`` in order."""
    monkeypatch.setattr(agentlib, "CLAUDE_BIN", str(agentlib.FAKE_CLAUDE))
    queue = tmp_path / "fake-po-replies"
    queue.mkdir()
    for n, reply in enumerate(replies, 1):
        (queue / f"{n}.txt").write_text(reply, encoding="utf-8")
    (tmp_path / "ws").mkdir()
    return product_owner.ProductOwner("producto", "m", tmp_path)


def test_the_s02_reply_is_used_and_the_sanitizing_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(tmp_path, monkeypatch, [S02[0].read_text(encoding="utf-8")])
    answers = owner({"title": "T"}, QUESTIONS)
    assert set(answers["answers"]) == set(IDS)
    assert owner.rounds[-1]["sanitizedOutput"] == [
        "call-1.json: removed tool-call markup lines: </parameter>, </invoke>",
        "call-1.json: closed an unclosed ```yaml fence",
    ]
    assert len(list((tmp_path / "po-calls").glob("call-*.json"))) == 1


def test_an_unreadable_reply_is_asked_again_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(tmp_path, monkeypatch, ["no es yaml: [", "tampoco: ["])
    answers = owner({"title": "T"}, QUESTIONS)
    # The third call is the stand-in's own valid answers file.
    assert set(answers["answers"]) == set(IDS)
    assert len(list((tmp_path / "po-calls").glob("call-*.json"))) == 3
    assert "sanitizedOutput" not in owner.rounds[-1]


def test_the_last_error_is_raised_after_the_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(tmp_path, monkeypatch, ["a: [", "b: [", "c: ["])
    with pytest.raises(yaml.YAMLError):
        owner({"title": "T"}, QUESTIONS)
    assert len(list((tmp_path / "po-calls").glob("call-*.json"))) == 3
