"""environment.json records the Claude Code version at the start and the end of a block."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import environment  # noqa: E402


@pytest.mark.parametrize(("end", "changed"), [("1.0.0", False), ("1.0.1", True)])
def test_finish_block_flags_a_version_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end: str, changed: bool
) -> None:
    out = tmp_path / "environment.json"
    out.write_text(
        json.dumps({"blocks": {"B": {"claudeCodeAtStart": "1.0.0", "results": "kept"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(environment, "version", lambda command: end)
    assert environment.finish_block(out, "B", "claude") == 0
    entry = json.loads(out.read_text(encoding="utf-8"))["blocks"]["B"]
    assert entry["claudeCodeAtEnd"] == end
    assert entry["claudeCodeChanged"] is changed
    assert entry["results"] == "kept"
