"""Built-in adapters and the agent-results protocol: read-only request kinds rendered as a
prompt, the result read back from the CLI's text, and the router's model and effort as CLI
options (#37, #44)."""

from __future__ import annotations

import json
from typing import Any, cast

from governed_harness.agents.command import CommandAgentConfiguration
from governed_harness.agents.native import (
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
    # Without isolation the base arguments are unchanged.
    argv, _, _ = claude.process_input(REQUEST, cast(Any, None))
    assert "acceptEdits" in argv
    assert "--allowedTools" not in argv
