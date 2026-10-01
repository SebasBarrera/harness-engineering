"""Shared agent invocation for both evaluation conditions.

The same prompt builder, tool set, permission mode and command line are used whether Claude Code
runs on its own (baseline) or as the command provider of the harness, so that the only difference
between conditions is the governance around the invocation.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import time
from pathlib import Path
from typing import Any

CLAUDE_BIN = os.environ.get("EVAL_CLAUDE_BIN", "claude")
TOOLS = "Read,Edit,Write,Glob,Grep,Bash"
ALLOWED_TOOLS = [
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Bash(python -m pytest:*)",
    "Bash(python3 -m pytest:*)",
    "Bash(pytest:*)",
    "Bash(ls:*)",
    "Bash(cd:*)",
]
AGENT_TIMEOUT_SECONDS = 1500
MAX_BUDGET_USD = "5"


def _items(task: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        if key in task and task[key]:
            return [item["text"] if isinstance(item, dict) else str(item) for item in task[key]]
    return []


def build_prompt(task: dict[str, Any], feedback: str | None = None) -> str:
    """Render a task (camelCase task file or snake_case harness request) as the agent prompt."""
    lines = [
        "You are working in the Git repository in the current directory.",
        "",
        f"Task: {task['title']}",
        "",
        str(task["intent"]).strip(),
    ]
    sections = (
        ("Requirements", _items(task, "requirements")),
        ("Acceptance criteria", _items(task, "acceptanceCriteria", "acceptance_criteria")),
        ("Constraints", _items(task, "constraints")),
    )
    for title, items in sections:
        if items:
            lines += ["", f"{title}:", *[f"- {item}" for item in items]]
    lines += [
        "",
        "Work only inside this directory. Do not commit, do not create branches and do not install",
        "packages. You may run the test suite with `python -m pytest -q`. When you are done, reply",
        "with a short summary of the change.",
    ]
    if feedback:
        lines += [
            "",
            "A previous attempt was returned for changes by the reviewer. Evidence:",
            feedback.strip(),
            "Address these points.",
        ]
    return "\n".join(lines) + "\n"


def agent_environment() -> dict[str, str]:
    """Minimal environment for the agent CLI: identity, locale and the evaluation PATH."""
    user = pwd.getpwuid(os.getuid()).pw_name
    env = {
        "HOME": os.environ.get("HOME", pwd.getpwuid(os.getuid()).pw_dir),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "USER": user,
        "LOGNAME": user,
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    if os.environ.get("TMPDIR"):
        env["TMPDIR"] = os.environ["TMPDIR"]
    return env


def run_claude(prompt: str, cwd: Path, model: str, log_path: Path) -> dict[str, Any]:
    """Run Claude Code non-interactively and return the normalized usage record."""
    command = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--tools",
        TOOLS,
        "--allowedTools",
        *ALLOWED_TOOLS,
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        MAX_BUDGET_USD,
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=agent_environment(),
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        exit_code, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        exit_code, timed_out = -1, True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    wall = time.monotonic() - started
    result: dict[str, Any] = {}
    try:
        payload = json.loads(stdout)
        result = payload[-1] if isinstance(payload, list) else payload
    except (json.JSONDecodeError, IndexError):
        pass
    usage = result.get("usage") or {}
    record = {
        "model": model,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "isError": bool(result.get("is_error", True)) or exit_code != 0,
        "terminalReason": result.get("terminal_reason"),
        "numTurns": result.get("num_turns"),
        "wallSeconds": round(wall, 3),
        "durationApiMs": result.get("duration_api_ms"),
        "costUsd": result.get("total_cost_usd"),
        "inputTokens": usage.get("input_tokens"),
        "outputTokens": usage.get("output_tokens"),
        "cacheReadTokens": usage.get("cache_read_input_tokens"),
        "cacheCreationTokens": usage.get("cache_creation_input_tokens"),
        "permissionDenials": len(result.get("permission_denials") or []),
        "deniedTools": [
            f"{item.get('tool_name')}: {str((item.get('tool_input') or {}).get('command', ''))[:60]}"
            for item in (result.get("permission_denials") or [])
        ],
        "modelUsage": {
            name: {k: v for k, v in data.items() if k != "contextWindow"}
            for name, data in (result.get("modelUsage") or {}).items()
        },
        "summary": str(result.get("result", ""))[:2000],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    (log_path.with_suffix(".stderr.txt")).write_text(stderr[-20000:], encoding="utf-8")
    return record


CODEX_BIN = os.environ.get("EVAL_CODEX_BIN", "codex")


def run_codex(prompt: str, cwd: Path, model: str, effort: str, log_path: Path) -> dict[str, Any]:
    """Run Codex CLI non-interactively and return a record shaped like run_claude's.

    Same prompt, minimal environment and time limit as the Claude backend. The sandbox limits
    writes to the working directory without network access, and no session is persisted.
    """
    command = [
        CODEX_BIN,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--model",
        model,
        "--json",
        prompt,
    ]
    env = agent_environment()
    tmp = cwd.parent / "tmp"
    tmp.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmp)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        exit_code, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        exit_code, timed_out = -1, True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    wall = time.monotonic() - started
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    turns, commands, failed, last_message, errors = 0, 0, False, "", []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = str(event.get("type", ""))
        if kind == "turn.completed":
            turns += 1
            for key in usage:
                usage[key] += int((event.get("usage") or {}).get(key) or 0)
        elif kind in {"turn.failed", "error"}:
            failed = True
            errors.append(json.dumps(event)[:300])
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "command_execution":
                commands += 1
            elif item.get("type") in {"agent_message", "assistant_message"}:
                last_message = str(item.get("text", ""))
    record = {
        "model": model,
        "effort": effort,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "isError": failed or exit_code != 0 or timed_out,
        "terminalReason": "completed" if not (failed or timed_out or exit_code) else "error",
        "numTurns": turns,
        "commands": commands,
        "wallSeconds": round(wall, 3),
        "durationApiMs": None,
        "costUsd": None,
        "inputTokens": usage["input_tokens"],
        "outputTokens": usage["output_tokens"],
        "cacheReadTokens": usage["cached_input_tokens"],
        "cacheCreationTokens": 0,
        "reasoningTokens": usage["reasoning_output_tokens"],
        "permissionDenials": 0,
        "deniedTools": [],
        "errors": errors,
        "summary": last_message[:2000],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    (log_path.with_suffix(".stderr.txt")).write_text(stderr[-20000:], encoding="utf-8")
    (log_path.with_suffix(".events.jsonl")).write_text(stdout[-2000000:], encoding="utf-8")
    return record
