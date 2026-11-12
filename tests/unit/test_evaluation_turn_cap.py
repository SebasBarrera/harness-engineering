"""The turn cap of the implement calls and the resume of a session in a correction (amendment A3).

Every implement call of every condition carries Claude Code's ``--max-turns`` (40, or
``EVAL_MAX_TURNS``); a call that stops on it is recorded with ``hitTurnLimit``; a correction of the
governed conditions resumes the session of the previous implement call (``resumed``). The calls run
the dry-run stand-in ``evaluation/fake_claude.py``: no model is called."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import agentlib  # noqa: E402
import claude_provider  # noqa: E402
import conditions  # noqa: E402
import environment  # noqa: E402

TASK = {"title": "T", "intent": "Haz algo.", "requirements": [{"text": "r"}]}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(agentlib, "CLAUDE_BIN", str(agentlib.FAKE_CLAUDE))
    ws = tmp_path / "run" / "ws"
    ws.mkdir(parents=True)
    return ws


def _run(ws: Path, name: str, **kwargs: Any) -> dict[str, Any]:
    return agentlib.run_claude("Implementa.", ws, "m", ws.parent / "agent-calls" / name, **kwargs)


def test_the_cap_defaults_to_40_and_rejects_anything_but_a_positive_integer() -> None:
    assert agentlib.DEFAULT_MAX_TURNS == 40
    assert agentlib.max_turns_from_environment("") == 40
    assert agentlib.max_turns_from_environment("12") == 12
    for bad in ("0", "-3", "diez", "1.5"):
        with pytest.raises(ValueError, match="EVAL_MAX_TURNS"):
            agentlib.max_turns_from_environment(bad)


def test_the_command_line_carries_the_cap_only_when_given() -> None:
    capped = agentlib._command("p", "m", "5", [], max_turns=40)
    assert capped[capped.index("--max-turns") + 1] == "40"
    assert "--max-turns" not in agentlib._command("p", "m", "5", [])


def test_an_implement_call_is_capped_and_a_read_only_call_is_not(workspace: Path) -> None:
    implement = _run(workspace, "call-1.json")
    assert implement["maxTurns"] == agentlib.MAX_TURNS
    assert implement["hitTurnLimit"] is False
    assert implement["resumed"] is False
    read_only = _run(workspace, "call-2.json", read_only=True)
    assert read_only["maxTurns"] is None
    assert _run(workspace, "call-3.json", max_turns=0)["maxTurns"] is None


def test_a_call_that_reaches_the_cap_is_marked_and_is_not_a_usage_limit(workspace: Path) -> None:
    (workspace.parent / "fake-mode").write_text("turn-limit", encoding="utf-8")
    record = _run(workspace, "call-1.json", max_turns=7)
    assert record["hitTurnLimit"] is True
    assert record["isError"] is True
    assert record["subtype"] == "error_max_turns"
    assert record["terminalReason"] == "max_turns"
    assert record["numTurns"] == 7
    assert agentlib.hit_limit(record) is False
    saved = json.loads((workspace.parent / "agent-calls" / "call-1.json").read_text("utf-8"))
    assert saved["hitTurnLimit"] is True and saved["maxTurns"] == 7
    # Only the first capped call stops on the cap in that mode.
    assert _run(workspace, "call-2.json")["hitTurnLimit"] is False


def test_a_kept_session_is_resumed(workspace: Path) -> None:
    first = _run(workspace, "call-1.json", persist_session=True)
    assert first["sessionId"] and first["resumed"] is False
    second = _run(workspace, "call-2.json", previous_session=first["sessionId"])
    assert second["resumed"] is True
    assert second["sessionId"] == first["sessionId"]
    assert "resumeFallback" not in second


def test_an_unknown_session_starts_a_new_one(workspace: Path) -> None:
    record = _run(workspace, "call-1.json", previous_session="00000000-0000-4000-8000-000000000000")
    assert record["resumed"] is False
    assert record["resumeFallback"] is True
    assert record["isError"] is False
    assert record["sessionId"] != "00000000-0000-4000-8000-000000000000"


def test_a_resumable_call_resumes_the_previous_session(workspace: Path) -> None:
    first = _run(workspace, "call-1.json", resumable=True)
    assert first["maxTurns"] == agentlib.MAX_TURNS and first["resumed"] is False
    second = _run(workspace, "call-2.json", resumable=True, previous_session=first["sessionId"])
    assert second["resumed"] is True
    assert second["sessionId"] == first["sessionId"]
    unknown = _run(workspace, "call-3.json", resumable=True, previous_session="no-such-session")
    assert unknown["resumed"] is False and unknown["resumeFallback"] is True


def _provider(
    ws: Path, monkeypatch: pytest.MonkeyPatch, request: dict[str, Any], *extra: str
) -> dict[str, Any]:
    argv = ["claude_provider.py", "--model", "m", "--claude-bin", str(agentlib.FAKE_CLAUDE)]
    monkeypatch.setattr(sys, "argv", [*argv, *extra])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.chdir(ws)
    assert claude_provider.main() == 0
    calls = agentlib.call_records(ws.parent / "agent-calls")
    return dict(json.loads(calls[-1].read_text(encoding="utf-8")))


def _task(task_id: str) -> dict[str, Any]:
    return {**TASK, "task_id": task_id, "created_at": "2026-11-11T00:00:00Z"}


def _implement(task_id: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "implement", "task": _task(task_id), **extra}


def test_the_provider_resumes_the_previous_implement_call_in_a_correction(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _provider(workspace, monkeypatch, _implement("task_a"))
    assert first["kind"] == "implement" and first["resumed"] is False
    assert first["maxTurns"] == agentlib.MAX_TURNS and first["sessionId"]
    assert first["taskId"] == "task_a" and first["run"] and "resumeSkipped" not in first
    review = _provider(workspace, monkeypatch, {"kind": "review", "task": _task("task_a")})
    assert review["maxTurns"] is None and review["resumed"] is False
    correction = _provider(
        workspace,
        monkeypatch,
        _implement("task_a", feedback={"summary": "corrige"}),
        "--max-turns",
        "9",
    )
    assert correction["resumed"] is True
    assert correction["sessionId"] == first["sessionId"]
    assert correction["maxTurns"] == 9
    capsys.readouterr()


def test_two_tasks_in_one_calls_directory_do_not_share_a_session(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _provider(workspace, monkeypatch, _implement("task_a"))
    other = _provider(workspace, monkeypatch, _implement("task_b"))
    assert other["resumed"] is False
    assert other["sessionId"] != first["sessionId"]
    assert "resumeSkipped" not in other
    again = _provider(workspace, monkeypatch, _implement("task_a", feedback={"summary": "x"}))
    assert again["resumed"] is True and again["sessionId"] == first["sessionId"]
    capsys.readouterr()


def test_a_correction_after_the_turn_cap_starts_over(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace.parent / "fake-mode").write_text("turn-limit", encoding="utf-8")
    capped = _provider(workspace, monkeypatch, _implement("task_a"))
    assert capped["hitTurnLimit"] is True
    correction = _provider(workspace, monkeypatch, _implement("task_a", feedback={"summary": "x"}))
    assert correction["resumed"] is False
    assert correction["sessionId"] != capped["sessionId"]
    assert correction["resumeSkipped"] == "the previous implement call stopped on the turn cap"
    assert correction["hitTurnLimit"] is False
    capsys.readouterr()


def test_the_provider_finds_the_latest_implement_session_of_the_task_and_run(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "agent-calls"
    calls.mkdir()
    key = {"taskId": "t", "run": "r"}
    rows = [
        {"kind": "implement", "sessionId": "a", "wallSeconds": 1, **key},
        {"kind": "review", "sessionId": None, "wallSeconds": 1, **key},
        {"kind": "implement", "sessionId": "b", "wallSeconds": 1, **key},
        {"kind": "implement", "sessionId": "c", "wallSeconds": 1, "taskId": "u", "run": "r"},
        {"kind": "implement", "sessionId": "d", "wallSeconds": 1, "taskId": "t", "run": "s"},
        {"kind": "acceptance", "sessionId": None, "wallSeconds": 1, **key},
    ]
    for number, row in enumerate(rows, 1):
        (calls / f"call-{number}.json").write_text(json.dumps(row), encoding="utf-8")
    (calls / "call-7.json").write_text("{}", encoding="utf-8")  # reserved, not written yet
    assert claude_provider.previous_session(calls, key) == ("b", None)
    assert claude_provider.previous_session(calls, {"taskId": "x", "run": "r"}) == (None, None)
    assert claude_provider.previous_session(tmp_path / "none", key) == (None, None)
    (calls / "call-8.json").write_text(
        json.dumps({"kind": "implement", "sessionId": None, "wallSeconds": 1, **key}),
        encoding="utf-8",
    )
    assert claude_provider.previous_session(calls, key) == (
        None,
        "the previous implement call kept no session",
    )


def test_the_run_key_has_no_machine_path(tmp_path: Path) -> None:
    key = claude_provider.run_key({"task": _task("task_a")}, tmp_path)
    assert key["taskId"] == "task_a"
    assert str(tmp_path) not in json.dumps(key) and len(str(key["run"])) == 16
    assert claude_provider.run_key({"task": _task("task_a")}, tmp_path / "other") != key


def test_the_governed_command_line_names_the_cap_only_when_it_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def command() -> list[str]:
        return conditions.provider_command(Path("code"), "m", honor_routing=False, claude_bin=None)

    monkeypatch.delenv("EVAL_MAX_TURNS", raising=False)
    assert "--max-turns" not in command()
    monkeypatch.setenv("EVAL_MAX_TURNS", "25")
    assert command()[-2:] == ["--max-turns", "25"]


def test_the_environment_record_declares_the_cap() -> None:
    calls = environment.agent_calls()
    assert calls["maxTurns"] == agentlib.MAX_TURNS
    assert calls["correctionResumesSession"] is True
