"""Rides runs without a turn cap in every condition; every other condition keeps it (amendment of
2026-11-12, plan D5).

The direct call of rides passes ``max_turns=0``; its governed conditions are configured by
``conditions.write_config`` with ``turn_cap=False``, so the provider gets ``--no-max-turns`` and
ignores ``EVAL_MAX_TURNS``. The calls run the dry-run stand-in ``evaluation/fake_claude.py``: no
model is called."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))
sys.path.insert(0, str(EVALUATION / "rides"))

import agentlib  # noqa: E402
import claude_provider  # noqa: E402
import conditions  # noqa: E402
import environment  # noqa: E402
import fake_claude  # noqa: E402
import run_rides  # noqa: E402

TASK = {"title": "T", "intent": "Haz algo.", "requirements": [{"text": "r"}]}


def _command(monkeypatch: pytest.MonkeyPatch, cap: str | None, **kwargs: Any) -> list[str]:
    if cap is None:
        monkeypatch.delenv("EVAL_MAX_TURNS", raising=False)
    else:
        monkeypatch.setenv("EVAL_MAX_TURNS", cap)
    return list(
        conditions.provider_command(
            Path("code"), "m", honor_routing=False, claude_bin=None, **kwargs
        )
    )


def test_rides_declares_no_cap_and_the_harness_configuration() -> None:
    assert run_rides.MAX_TURNS == 0
    assert run_rides.GOVERNED_CONDITION == "harness"
    assert run_rides.PROVIDER_ARGS == ("--timeout", "14400", "--budget", "200")


@pytest.mark.parametrize("cap", [None, "25"])
def test_the_rides_provider_command_has_no_cap_whatever_eval_max_turns_says(
    monkeypatch: pytest.MonkeyPatch, cap: str | None
) -> None:
    command = _command(monkeypatch, cap, turn_cap=False, extra_args=run_rides.PROVIDER_ARGS)
    assert "--max-turns" not in command
    assert command[-1] == "--no-max-turns"
    assert command[3:8] == ["m", "--timeout", "14400", "--budget", "200"]


def test_the_other_conditions_keep_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "--no-max-turns" not in _command(monkeypatch, None)
    assert _command(monkeypatch, "25")[-2:] == ["--max-turns", "25"]


def test_write_config_grants_exactly_the_rides_command(tmp_path: Path) -> None:
    config: dict[str, Any] = {"runtime": {"gateContract": True}}
    conditions.apply_provider(
        config,
        condition="harness",
        model="m",
        code=Path("code"),
        run_dir=tmp_path,
        provider="claude",
        resumable=True,
        turn_cap=False,
        extra_args=run_rides.PROVIDER_ARGS,
    )
    command = config["agentProviders"]["claude"]["command"]
    assert "--no-max-turns" in command and "--resumable" in command
    assert conditions.provider_grant(command) in config["capabilities"]["extend"]
    assert conditions.CHECK_GRANT in config["capabilities"]["extend"]
    assert config["runtime"]["commandTimeoutSeconds"] == 7 * 24 * 3600
    assert config["agentRouting"]["mode"] == "fixed"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(agentlib, "CLAUDE_BIN", str(agentlib.FAKE_CLAUDE))
    ws = tmp_path / "run" / "ws"
    ws.mkdir(parents=True)
    return ws


def _provider(ws: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> dict[str, Any]:
    request = {
        "kind": "implement",
        "task": {**TASK, "task_id": "task_a", "created_at": "2026-11-12T00:00:00Z"},
    }
    argv = ["claude_provider.py", "--model", "m", "--claude-bin", str(agentlib.FAKE_CLAUDE)]
    monkeypatch.setattr(sys, "argv", [*argv, *extra])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.chdir(ws)
    assert claude_provider.main() == 0
    calls = agentlib.call_records(ws.parent / "agent-calls")
    return dict(json.loads(calls[-1].read_text(encoding="utf-8")))


def test_the_provider_without_a_cap_records_max_turns_null(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace.parent / "fake-mode").write_text("turn-limit", encoding="utf-8")
    record = _provider(workspace, monkeypatch, "--max-turns", "9", "--no-max-turns")
    assert record["maxTurns"] is None
    # The turn-limit mode of the stand-in stops only a call that carries --max-turns.
    assert record["hitTurnLimit"] is False
    capped = _provider(workspace, monkeypatch, "--max-turns", "9")
    assert capped["maxTurns"] == 9 and capped["hitTurnLimit"] is True
    capsys.readouterr()


def test_the_direct_rides_call_is_resumable_and_uncapped(workspace: Path) -> None:
    log = workspace.parent / "agent-calls" / "call-1.json"
    record = agentlib.run_claude(
        "Implementa.", workspace, "m", log, resumable=True, max_turns=run_rides.MAX_TURNS
    )
    assert record["maxTurns"] is None
    assert json.loads(log.read_text(encoding="utf-8"))["maxTurns"] is None
    default = agentlib.run_claude("Implementa.", workspace, "m", log.with_name("call-2.json"))
    assert default["maxTurns"] == agentlib.MAX_TURNS


def test_the_stand_in_recognizes_the_rides_fixture(tmp_path: Path) -> None:
    for item in (EVALUATION / "rides" / "fixture").iterdir():
        (tmp_path / item.name).write_bytes(item.read_bytes())
    assert fake_claude._scenario(tmp_path) == "rides"
    assert "rides" in fake_claude._implement(tmp_path, "Implementa.")
    assert (tmp_path / "src" / "rides" / "__init__.py").is_file()
    assert "import rides" in (tmp_path / "tests" / "test_fake_dry_run.py").read_text("utf-8")


def test_the_environment_record_of_rides_declares_no_cap() -> None:
    rides = environment.agent_calls("rides")
    assert rides["maxTurns"] is None
    assert str(rides["maxTurnsSource"]).startswith("none: rides")
    for block in ("", "A", "B", "T1"):
        assert environment.agent_calls(block)["maxTurns"] == agentlib.MAX_TURNS
