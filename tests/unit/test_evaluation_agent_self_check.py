"""The agent of the evaluation can check its own change, the same way in every condition.

Amendment of 2026-11-11 to the pre-registration: in the 2.0.0 pilots Claude Code denied the
agent's Ruff and Mypy calls (and every ``cd`` into the workspace), so the agent could not verify
its change. ``evaluation/agentlib.py`` now allows the project's validators and ``harness check``
in the implement calls of every condition, the prompt says the agent is already in the workspace,
every call runs with the 5-minute prompt-cache TTL, and the governed conditions with a gate
contract grant ``harness check``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import agentlib  # noqa: E402
import claude_provider  # noqa: E402
import conditions  # noqa: E402

from governed_harness.capabilities.denial import command_in_scope  # noqa: E402

TASK = {"title": "T", "intent": "Haz algo.", "requirements": [{"text": "r"}]}


def _implement_tools(read_only: bool = False) -> list[str]:
    command = agentlib._command("p", "m", "5", ["--no-session-persistence"], read_only=read_only)
    start = command.index("--allowedTools") + 1
    end = next(i for i in range(start, len(command)) if command[i].startswith("--"))
    return command[start:end]


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(python -m ruff check:*)",
        "Bash(python -m mypy:*)",
        "Bash(python -m pytest:*)",
        "Bash(harness check:*)",
    ],
)
def test_implement_calls_allow_the_validators_and_harness_check(rule: str) -> None:
    assert rule in _implement_tools()


def test_no_open_shell_or_destructive_rule_is_allowed() -> None:
    tools = _implement_tools()
    bash = [rule for rule in tools if rule.startswith("Bash")]
    assert "Bash" not in tools
    for rule in bash:
        assert rule.endswith(":*)")
        prefix = rule[len("Bash(") : -len(":*)")]
        assert prefix not in {"python", "python3", "harness", "rm", "git", "pip"}
        assert not prefix.startswith(("rm", "git", "pip", "python -c"))


def test_read_only_calls_keep_no_shell() -> None:
    command = agentlib._command("p", "m", "5", [], read_only=True)
    assert not any(part.startswith("Bash(") for part in _implement_tools(read_only=True))
    assert "Bash" in command[command.index("--disallowedTools") + 1 :]


def test_the_prompt_says_no_cd_and_names_the_validators_in_every_condition() -> None:
    prompt = agentlib.build_prompt(TASK)
    assert "sin\n`cd`" in prompt or "sin `cd`" in prompt
    prefixes = [r[len("Bash(") : -len(":*)")] for r in _implement_tools() if r.startswith("Bash(")]
    for command in agentlib.VALIDATOR_COMMANDS:
        assert f"`{command}`" in prompt
        # Every command the prompt names is one Claude Code lets the agent run.
        assert any(command.startswith(f"{prefix} ") for prefix in prefixes)


def test_prompt_cache_ttl_is_five_minutes_in_the_agent_environment() -> None:
    env = agentlib.agent_environment()
    assert env["FORCE_PROMPT_CACHING_5M"] == "1"
    assert env["CLAUDE_CODE_PROMPT_CACHE_TTL"] == "5m"
    assert env["CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"] == "5m"


def test_the_callers_state_directory_reaches_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentlib, "EXTRA_AGENT_ENV", {"HARNESS_STATE_DIR": "/run/state"})
    env = agentlib.agent_environment()
    assert env["HARNESS_STATE_DIR"] == "/run/state"
    assert env["FORCE_PROMPT_CACHING_5M"] == "1"


def test_the_governed_prompt_asks_for_harness_check() -> None:
    command = ["harness", "check", "--path", "/ws", "--run", "run_1"]
    request = {"task": TASK, "gate": {"checkCommand": command, "validators": []}}
    text = "\n".join(claude_provider.harness_sections(request))
    assert "`harness check --path /ws --run run_1`" in text
    assert "sin `cd`" in text
    assert command_in_scope(command, conditions.CHECK_GRANT["scope"][0])


def test_without_harness_check_the_prompt_asks_for_the_granted_validators() -> None:
    gate = {"checkCommands": [{"id": "python.ruff", "command": ["python", "-m", "ruff", "check"]}]}
    assert "`python -m ruff check`" in claude_provider.check_instruction(gate)
    assert claude_provider.check_instruction(None) == ""
    assert claude_provider.check_instruction({"checkCommands": []}) == ""


def _config(gate_contract: bool) -> dict:
    return {
        "capabilities": {"default": "deny", "extend": []},
        "runtime": {"gateContract": gate_contract},
    }


def test_the_gate_contract_conditions_grant_harness_check(tmp_path: Path) -> None:
    config = conditions.apply_provider(
        _config(True),
        condition="harness",
        model="m",
        code=tmp_path,
        run_dir=tmp_path,
        provider="claude",
    )
    assert conditions.CHECK_GRANT in config["capabilities"]["extend"]
    assert not command_in_scope(["harness", "run"], conditions.CHECK_GRANT["scope"][0])


def test_without_a_gate_contract_no_check_grant_is_added(tmp_path: Path) -> None:
    config = conditions.apply_provider(
        {"capabilities": {"default": "deny", "grants": []}, "runtime": {}},
        condition="harness-core",
        model="m",
        code=tmp_path,
        run_dir=tmp_path,
        provider="claude",
    )
    assert conditions.CHECK_GRANT not in (config["capabilities"].get("extend") or [])
    assert conditions.CHECK_GRANT not in config["capabilities"]["grants"]


def test_environment_json_records_what_every_agent_call_gets() -> None:
    import environment

    calls = environment.agent_calls()
    assert calls["promptCache"] == agentlib.PROMPT_CACHE_ENV
    assert "Bash(harness check:*)" in calls["allowedTools"]
    assert calls["validatorCommands"] == list(agentlib.VALIDATOR_COMMANDS)
