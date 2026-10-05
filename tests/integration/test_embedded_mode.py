"""Embedded mode (#56): the MCP server over stdio and the skill files ``init`` writes. The
session drives the flow; no tool decides for a person."""

from __future__ import annotations

import io
import json
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.cli.main import app
from governed_harness.embedded import SKILL_PATHS, TOOLS, McpServer

GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
TASK = {
    "taskId": "task_embedded",
    "title": "Threshold discount",
    "intent": "Apply the configured discount at or above the threshold.",
    "acceptanceCriteria": [
        {"criterionId": "AC-1", "text": "apply_discount(100, 100, 0.1) returns 90."}
    ],
    "metadata": {"ownedPaths": ["src/sample/pricing.py"]},
}


def rpc(server: McpServer, method: str, params: dict[str, object] | None = None) -> dict:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response is not None
    return response


def call(server: McpServer, name: str, arguments: dict[str, object]) -> dict:
    result = rpc(server, "tools/call", {"name": name, "arguments": arguments})["result"]
    return result


def test_no_tool_decides_for_a_person() -> None:
    names = {tool["name"] for tool in TOOLS}
    assert "harness_run_start" in names and "harness_task_clarify" in names
    assert not any("decide" in name or "approve" in name or "raise" in name for name in names)
    for tool in TOOLS:
        assert tool["inputSchema"]["type"] == "object"


def test_session_drives_a_governed_run_over_mcp(python_workspace: Path) -> None:
    server = McpServer(python_workspace)
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["serverInfo"]["name"] == "governed-harness"
    assert init["capabilities"] == {"tools": {"listChanged": False}}
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert {item["name"] for item in rpc(server, "tools/list")["result"]["tools"]} == {
        tool["name"] for tool in TOOLS
    }
    created = call(server, "harness_task_create", {"task": TASK})
    assert created["isError"] is False
    assert created["structuredContent"]["taskId"] == "task_embedded"
    started = call(server, "harness_run_start", {"taskId": "task_embedded"})["structuredContent"]
    assert (started["currentPhase"], started["status"], started["exitCode"]) == (
        "IMPLEMENTATION",
        "BLOCKED",
        6,
    )
    (python_workspace / "src" / "sample" / "pricing.py").write_text(GOOD, encoding="utf-8")
    checked = call(server, "harness_check", {"run": started["executionId"]})
    assert checked["isError"] is False
    continued = call(server, "harness_run_continue", {"run": started["executionId"]})
    value = continued["structuredContent"]
    assert (value["currentPhase"], value["exitCode"]) == ("DECISION", 4)
    assert "harness gate decide" in value["next"]
    brief = call(server, "harness_review", {"run": started["executionId"]})
    assert brief["structuredContent"]["run"]["awaitingDecision"] is True
    missing = call(server, "harness_status", {"run": "run_does_not_exist"})
    assert missing["isError"] is True and missing["structuredContent"]["exitCode"] == 3
    unknown = rpc(server, "resources/list")
    assert unknown["error"]["code"] == -32601


def test_stdio_framing_and_cli(python_workspace: Path) -> None:
    lines = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "not json",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "harness_standards", "arguments": {"files": ["a.py"]}},
                }
            ),
        ]
    )
    output = io.StringIO()
    McpServer(python_workspace).serve(io.StringIO(lines + "\n"), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [item.get("id") for item in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700
    cards = replies[2]["result"]["structuredContent"]["selection"]["implement"]
    assert cards and cards[0]["id"].startswith("python.")
    result = CliRunner().invoke(
        app,
        ["mcp", "serve", "--path", str(python_workspace)],
        input=json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.splitlines()[0]) == {"jsonrpc": "2.0", "id": 9, "result": {}}


def test_init_writes_agent_skills(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["--json", "init", "--path", str(tmp_path), "--agent-skills", "--no-gitignore"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert [item["status"] for item in report["agentSkills"]] == ["WRITTEN", "WRITTEN"]
    for relative in SKILL_PATHS:
        text = (tmp_path / relative).read_text(encoding="utf-8")
        assert text.startswith("---\nname: harness\n")
        assert "You never decide a gate" in text
