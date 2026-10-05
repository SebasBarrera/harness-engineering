"""``harness mcp serve``: the harness as a Model Context Protocol server over stdio (#56).

Embedded mode: an agent drives the governed flow from its own session (Claude Code, Codex or
any MCP client) while the harness keeps enforcing the gates, the digests and the decisions.
The server speaks JSON-RPC 2.0, one message per line on standard input and output
(``initialize``, ``ping``, ``tools/list``, ``tools/call``) and exposes read and governed
actions only:

=============================  =======================================================
Tool                           What it does
=============================  =======================================================
``harness_project``            What the harness detects (new or existing, packs, ...)
``harness_status``             The status projection of a run
``harness_inbox``              Runs that wait for a person
``harness_task_create``        Create a task from a task document
``harness_task_questions``     The open clarification questions of a task
``harness_task_clarify``       Record the person's answers, relayed by the session
``harness_run_start``          Start a run (default provider ``session``)
``harness_run_continue``       Continue a run (after the session's edits, for example)
``harness_check``              The gate's validators and checks, nothing recorded
``harness_review``             The decision brief of a run
``harness_standards``          The standards cards for some files
=============================  =======================================================

Human decisions are **not** exposed: no tool decides a gate, acceptance tests, a plan, the
architecture, a budget raise, an exception or a memory record. A tool result says what a person
must run (``next``), and the person runs it in a terminal. A clarification answer is recorded
with the person's identity and marked as relayed by an agent session."""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import yaml

from governed_harness import __version__
from governed_harness.application import HarnessApplication
from governed_harness.domain.errors import HarnessError, NotFoundError

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "governed-harness"
HUMAN_ONLY = (
    "harness gate decide",
    "harness acceptance decide",
    "harness plan decide",
    "harness architecture decide",
    "harness budget raise",
    "harness memory approve",
)

INSTRUCTIONS = (
    "Governed Agent Harness. Drive a governed change: harness_task_create, harness_run_start "
    "(provider session: you implement in this session), then edit the workspace and call "
    "harness_run_continue; use harness_check before you continue and harness_status to read "
    "findings. Clarification questions are answered by the person: ask them and relay their "
    "exact words with harness_task_clarify. Never decide for a person: gate, acceptance, plan "
    "and architecture decisions are made by a person in a terminal."
)


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_RUN = {"type": "string", "description": "Run id, a unique prefix or latest"}
TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "harness_project",
        "description": "What the harness detects about the project: new or existing, standards "
        "packs, testing strategy, architecture and forge. Reads only.",
        "inputSchema": _schema({}),
    },
    {
        "name": "harness_status",
        "description": "The status of a run: phases, validations, findings, gate, decision.",
        "inputSchema": _schema({"run": _RUN}),
    },
    {
        "name": "harness_inbox",
        "description": "Runs that wait for a person (a decision or clarification answers).",
        "inputSchema": _schema({}),
    },
    {
        "name": "harness_task_create",
        "description": "Create (or replace) a task from a task document: title, intent, "
        "requirements, acceptanceCriteria, constraints. Use implementation mode none: you "
        "implement in this session.",
        "inputSchema": _schema(
            {"task": {"type": "object", "description": "The task document (task.yaml fields)"}},
            ("task",),
        ),
    },
    {
        "name": "harness_task_questions",
        "description": "The open clarification questions of a task, for the person to answer.",
        "inputSchema": _schema({"taskId": {"type": "string"}}, ("taskId",)),
    },
    {
        "name": "harness_task_clarify",
        "description": "Record the person's answers to the clarification questions (question id "
        "to the person's exact words). Recorded with the person's identity, marked as relayed "
        "by an agent session. Do not answer for the person.",
        "inputSchema": _schema(
            {
                "taskId": {"type": "string"},
                "answers": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            ("taskId", "answers"),
        ),
    },
    {
        "name": "harness_run_start",
        "description": "Start a governed run of a task. The default provider session waits in "
        "IMPLEMENTATION for your edits; harness_run_continue then verifies them.",
        "inputSchema": _schema(
            {"taskId": {"type": "string"}, "provider": {"type": "string", "default": "session"}},
            ("taskId",),
        ),
    },
    {
        "name": "harness_run_continue",
        "description": "Continue a run: after your edits, after a person's decision or answers.",
        "inputSchema": _schema({"run": _RUN}, ("run",)),
    },
    {
        "name": "harness_check",
        "description": "Run the gate's validators and diff checks on the workspace without "
        "recording anything.",
        "inputSchema": _schema({"run": _RUN}),
    },
    {
        "name": "harness_review",
        "description": "The decision brief of a run: what was asked and changed, the gate, the "
        "risks and what a person must decide.",
        "inputSchema": _schema({"run": _RUN}),
    },
    {
        "name": "harness_standards",
        "description": "The standards cards that apply to some files (follow them).",
        "inputSchema": _schema(
            {"files": {"type": "array", "items": {"type": "string"}}}, ("files",)
        ),
    },
)
"""The tools of the server; a tool that decides for a person does not exist here."""


def exit_code(execution: dict[str, Any]) -> int:
    """The exit code ``harness run start`` would give for this execution."""
    status, phase = execution.get("status"), execution.get("currentPhase")
    if status == "PASSED":
        return 0
    if status == "CANCELLED":
        return 130
    if status == "BLOCKED" and phase == "DECISION":
        return 4
    if status == "ERROR":
        return 1
    return 6


class McpServer:
    def __init__(self, workspace: Path, application: HarnessApplication | None = None) -> None:
        self.workspace = workspace
        self.application = application or HarnessApplication()
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "harness_project": lambda args: self.application.project(self.workspace),
            "harness_status": lambda args: self.application.status(
                self.workspace, args.get("run") or "latest"
            ),
            "harness_inbox": lambda args: self.application.inbox(self.workspace),
            "harness_task_create": self._task_create,
            "harness_task_questions": lambda args: self.application.list_clarifications(
                self.workspace, str(args["taskId"])
            ),
            "harness_task_clarify": self._task_clarify,
            "harness_run_start": self._run_start,
            "harness_run_continue": self._run_continue,
            "harness_check": self._check,
            "harness_review": lambda args: self.application.review(
                self.workspace, args.get("run") or "latest"
            ),
            "harness_standards": lambda args: self.application.standards(
                self.workspace, files=tuple(str(item) for item in args.get("files") or [])
            ),
        }

    # ----- tools -------------------------------------------------------------------------------
    def _task_create(self, args: dict[str, Any]) -> Any:
        task = args.get("task")
        if not isinstance(task, dict):
            raise ValueError("task must be an object")
        with tempfile.TemporaryDirectory(prefix="harness-mcp-") as scratch:
            source = Path(scratch) / "task.yaml"
            source.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
            created = self.application.create_task(self.workspace, source)
        return created.model_dump(mode="json", by_alias=True)

    def _task_clarify(self, args: dict[str, Any]) -> Any:
        answers = args.get("answers")
        if not isinstance(answers, dict) or not answers:
            raise ValueError("answers must map question ids to the person's answers")
        with tempfile.TemporaryDirectory(prefix="harness-mcp-") as scratch:
            source = Path(scratch) / "answers.yaml"
            source.write_text(
                yaml.safe_dump({"answers": {str(k): str(v) for k, v in answers.items()}}),
                encoding="utf-8",
            )
            return self.application.clarify_task(
                self.workspace, task_id=str(args["taskId"]), answers_file=source, relayed=True
            )

    def _check(self, args: dict[str, Any]) -> Any:
        """The run's check state when the gate contract wrote one, else the validators only."""
        run = args.get("run")
        try:
            return self.application.check(self.workspace, run)
        except NotFoundError:
            return self.application.check(self.workspace, None)

    def _execution(self, execution: Any) -> dict[str, Any]:
        value: dict[str, Any] = execution.model_dump(mode="json", by_alias=True)
        value["exitCode"] = exit_code(value)
        if value.get("currentPhase") == "DECISION" and value["exitCode"] == 4:
            value["next"] = (
                f"A person decides in a terminal: harness review --run {value['executionId']} "
                f"then harness gate decide --run {value['executionId']}"
            )
        return value

    def _run_start(self, args: dict[str, Any]) -> Any:
        provider = str(args.get("provider") or "session")
        return self._execution(
            self.application.start_run(self.workspace, str(args["taskId"]), provider)
        )

    def _run_continue(self, args: dict[str, Any]) -> Any:
        return self._execution(self.application.continue_run(self.workspace, str(args["run"])))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "content": [{"type": "text", "text": f"unknown tool {name}"}],
                "isError": True,
            }
        try:
            value = handler(arguments)
        except HarnessError as error:
            payload = {"status": "ERROR", "error": str(error), "exitCode": error.exit_code}
            return {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload,
                "isError": True,
            }
        except (ValueError, KeyError, TypeError, OSError) as error:
            payload = {"status": "ERROR", "error": str(error)}
            return {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload,
                "isError": True,
            }
        plain = json.loads(json.dumps(_plain(value), default=str))
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": json.dumps(plain, ensure_ascii=False)}],
            "isError": False,
        }
        if isinstance(plain, dict):
            result["structuredContent"] = plain
        return result

    # ----- JSON-RPC ------------------------------------------------------------------------------
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        identifier = message.get("id")
        params = message.get("params") or {}
        if identifier is None:
            return None  # a notification (initialized, cancelled): nothing to answer
        if method == "initialize":
            requested = str(params.get("protocolVersion") or PROTOCOL_VERSIONS[0])
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            result: Any = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": list(TOOLS)}
        elif method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _error(identifier, -32602, "arguments must be an object")
            result = self.call_tool(name, arguments)
        else:
            return _error(identifier, -32601, f"method not found: {method}")
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    def serve(self, reader: IO[str], writer: IO[str]) -> int:
        for line in reader:
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                response: dict[str, Any] | None = _error(None, -32700, "parse error")
            else:
                if isinstance(message, list):
                    replies = [
                        reply
                        for item in message
                        if isinstance(item, dict) and (reply := self.handle(item)) is not None
                    ]
                    if replies:
                        writer.write(json.dumps(replies) + "\n")
                        writer.flush()
                    continue
                response = (
                    self.handle(message)
                    if isinstance(message, dict)
                    else _error(None, -32600, "invalid request")
                )
            if response is not None:
                writer.write(json.dumps(response) + "\n")
                writer.flush()
        return 0


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def serve_stdio(workspace: Path) -> int:
    return McpServer(workspace).serve(sys.stdin, sys.stdout)


__all__ = ["HUMAN_ONLY", "INSTRUCTIONS", "TOOLS", "McpServer", "exit_code", "serve_stdio"]
