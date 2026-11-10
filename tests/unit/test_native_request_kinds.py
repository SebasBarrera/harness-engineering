"""Built-in adapters and the agent-results protocol: read-only request kinds rendered as a
prompt, the result read back from the CLI's text, and the router's model and effort as CLI
options (#37, #44)."""

from __future__ import annotations

import json
from typing import Any, cast

from governed_harness.agents.command import CommandAgentConfiguration
from governed_harness.agents.native import (
    CLAUDE_WRITE_TOOLS,
    extract_call_result,
    implement_extras_lines,
    native_provider,
    render_call_prompt,
)
from governed_harness.domain.enums import ResultStatus
from governed_harness.runtime.process_runner import ProcessResult

REQUEST: dict[str, Any] = {
    "schemaVersion": "1.1",
    "kind": "clarify",
    "readOnly": True,
    "instructions": "Review the task below before any work starts.",
    "task": {"task_id": "t", "title": "Discount"},
    "routing": {"model": "claude-sonnet-5-5", "effort": "high"},
}


def provider(kind: str) -> Any:
    return native_provider(
        kind, CommandAgentConfiguration(provider_id="agent", argv_prefix=(kind,))
    )


def test_a_read_only_request_becomes_a_prompt_with_the_request_as_json() -> None:
    prompt = render_call_prompt(REQUEST)
    assert prompt.startswith("Review the task below")
    payload = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["task"]["task_id"] == "t" and "instructions" not in payload


def test_the_router_choice_reaches_the_cli_options() -> None:
    claude = provider("claude-code")
    argv, stdin, _ = claude.process_input(REQUEST, cast(Any, None))
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--effort") + 1] == "high"
    assert stdin is not None and b"Review the task below" in stdin
    codex = provider("codex")
    argv, _, _ = codex.process_input(REQUEST, cast(Any, None))
    assert 'model_reasoning_effort="high"' in argv


def test_the_result_is_read_from_the_agent_text() -> None:
    answer = 'Here it is:\n{"status": "PASSED", "result": {"questions": [{"text": "Why?"}]}}'
    stdout = json.dumps({"type": "result", "result": answer, "is_error": False})
    claude = provider("claude-code")
    process = ProcessResult(ResultStatus.PASSED, 0, stdout.encode(), b"", False, False, 1)
    assert claude.extract_result(process) == {"questions": [{"text": "Why?"}]}
    assert extract_call_result("no json here") is None


def test_implement_extras_are_listed_for_the_agent() -> None:
    lines = implement_extras_lines({"gate": {"checkCommand": ["harness", "check"]}})
    assert lines[0].startswith("## What the harness will check")
    assert implement_extras_lines({"task": {}}) == []


def test_a_reviewer_runs_isolated_on_the_built_in_adapters() -> None:
    # #57: a reviewer of the review panel is read-only, limited to its tools and to the MCP
    # servers of the allowlist; the MCP definitions never reach the recorded argv or prompt.
    request = {
        **REQUEST,
        "kind": "review",
        "isolation": {
            "readOnly": True,
            "tools": ["Read", "Grep"],
            "mcpServers": ["docs"],
            "mcpConfig": {"docs": {"command": "docs-server", "env": {"LEVEL": "debug"}}},
        },
    }
    claude = provider("claude-code")
    argv, stdin, recorded = claude.process_input(request, cast(Any, None))
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep"
    assert "--strict-mcp-config" in argv
    assert "acceptEdits" not in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["docs"]
    assert recorded[recorded.index("--mcp-config") + 1] == "<mcp-config>"
    assert stdin is not None
    assert b"docs-server" not in stdin
    codex = provider("codex")
    argv, _, _ = codex.process_input(request, cast(Any, None))
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--full-auto" not in argv


IMPLEMENT: dict[str, Any] = {
    "schemaVersion": "1.1",
    "kind": "implement",
    "task": {"task_id": "t", "title": "Discount", "intent": "Add a discount"},
}
PANEL: dict[str, Any] = {
    **REQUEST,
    "kind": "review",
    "isolation": {"readOnly": True, "tools": ["Read", "Write", "Bash(rm:*)", "Grep"]},
}


def argv_of(kind: str, request: dict[str, Any]) -> tuple[str, ...]:
    argv, _, _ = provider(kind).process_input(request, cast(Any, None))
    return cast(tuple[str, ...], argv)


def denied_tools(argv: tuple[str, ...]) -> set[str]:
    start = argv.index("--disallowedTools") + 1
    return set(argv[start : start + len(CLAUDE_WRITE_TOOLS)])


def test_claude_code_gets_read_only_tools_for_a_read_only_call() -> None:
    # #92: the pilot's clarify call ran with --permission-mode acceptEdits and wrote a file.
    argv = argv_of("claude-code", REQUEST)
    assert "acceptEdits" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "default"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert denied_tools(argv) == {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}


def test_claude_code_drops_the_write_tools_of_a_reviewer() -> None:
    argv = argv_of("claude-code", PANEL)
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep"
    assert denied_tools(argv) == set(CLAUDE_WRITE_TOOLS)


def test_claude_code_names_the_denied_tools_once_with_the_command_policy() -> None:
    argv = argv_of("claude-code", {**REQUEST, "commandPolicy": {"destructive": "deny"}})
    assert argv.count("--disallowedTools") == 1
    assert "Bash" in argv


def test_codex_runs_a_read_only_call_in_its_read_only_sandbox() -> None:
    argv = argv_of("codex", REQUEST)
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--full-auto" not in argv


def test_gemini_cli_does_not_approve_edits_in_a_read_only_call() -> None:
    argv = argv_of("gemini-cli", REQUEST)
    assert argv[argv.index("--approval-mode") + 1] == "default"
    panel = argv_of("gemini-cli", PANEL)
    assert panel[panel.index("--approval-mode") + 1] == "default"


def test_aider_answers_a_read_only_call_without_writing() -> None:
    argv = argv_of("aider", REQUEST)
    assert argv[argv.index("--chat-mode") + 1] == "ask"
    assert "--dry-run" in argv
    assert "--no-suggest-shell-commands" in argv
    assert "--dry-run" in argv_of("aider", PANEL)


def test_an_implement_call_keeps_the_write_arguments() -> None:
    assert "acceptEdits" in argv_of("claude-code", IMPLEMENT)
    assert "--full-auto" in argv_of("codex", IMPLEMENT)
    gemini = argv_of("gemini-cli", IMPLEMENT)
    assert gemini[gemini.index("--approval-mode") + 1] == "auto_edit"
    assert "--dry-run" not in argv_of("aider", IMPLEMENT)
