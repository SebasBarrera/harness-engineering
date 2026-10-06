"""Shared agent invocation for both evaluation conditions.

The same prompt builder, tool set, permission mode and command line are used whether Claude Code
runs on its own (baseline) or as the command provider of the harness, so that the only difference
between conditions is the governance around the invocation.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

CLAUDE_BIN = os.environ.get("EVAL_CLAUDE_BIN", "claude")
# The deterministic stand-in for the Claude Code CLI used by the dry runs (no model call).
FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
# Every real call runs only on this Claude account (the evaluation's own subscription), with no API key
# and no configuration-directory override; anything else aborts before the CLI starts.
EXPECTED_ACCOUNT = os.environ.get("EVAL_CLAUDE_ACCOUNT", "js.barrerap@gmail.com")
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
# A resumable call that stops on the account usage limit (or an overloaded API) waits and resumes the
# same session; the wait is recorded apart from the agent's working time.
LIMIT_TEXT = re.compile(
    r"usage limit|session limit|weekly limit|limit reached|hit your (\w+ )?limit|limit will reset"
    r"|resets? (at |in )?\d|rate.?limit|overloaded"
    r"|\b(429|529)\b",
    re.IGNORECASE,
)
# Failures of the environment, not of the agent: the machine slept, the request timed out or the
# connection dropped. A resumable call waits briefly and resumes the session.
TRANSIENT_TEXT = re.compile(
    r"went to sleep|request timed out|timed out|connection error|connection reset|econnreset|"
    r"socket hang up|network error|fetch failed|api error: 5\d\d|internal server error",
    re.IGNORECASE,
)
TRANSIENT_WAIT_SECONDS = 60
TRANSIENT_MAX_RETRIES = 20
LIMIT_RESET_EPOCH = re.compile(r"\|(\d{10})\b")
LIMIT_POLL_SECONDS = 600
CONTINUE_PROMPT = (
    "The previous session was interrupted (usage limit or a failure of the machine or the network) "
    "before the task was finished. Continue the same task from where you left off; do not start over."
)


GENERATED_ID = re.compile(r"^(req|ac)_[0-9a-f]{32}$")
ID_KEYS = ("requirementId", "requirement_id", "criterionId", "criterion_id")


def _items(task: dict[str, Any], *keys: str) -> list[str]:
    """The texts of a task list, prefixed with the id the task file gave the item (``req_spec:``).

    Since the 2.0.0 evaluation the ids are shown in every condition: the harness's requirement
    traceability names requirements by these ids, and the direct condition must get the same text.
    Ids the harness generated (``req_<32 hex>``, for answers to clarification questions) are left out.
    """
    for key in keys:
        if key in task and task[key]:
            rendered = []
            for item in task[key]:
                if not isinstance(item, dict):
                    rendered.append(str(item))
                    continue
                ident = next((str(item[k]) for k in ID_KEYS if item.get(k)), "")
                prefix = f"{ident}: " if ident and not GENERATED_ID.match(ident) else ""
                rendered.append(prefix + str(item["text"]))
            return rendered
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


CALL_RECORD = re.compile(r"^call-(\d+)\.json$")


def call_records(directory: Path) -> list[Path]:
    """The usage records ``call-N.json`` of a directory, in call order (requests, prompts and stderr
    files next to them are left out)."""
    if not directory.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in directory.iterdir() if (m := CALL_RECORD.match(p.name))]
    return [p for _, p in sorted(found)]


def allocate_call(directory: Path) -> Path:
    """Reserve the next ``call-N.json`` atomically: the reviewers of the review panel may call the
    provider in parallel, and two calls must never share a record."""
    directory.mkdir(parents=True, exist_ok=True)
    number = len(call_records(directory)) + 1
    while True:
        path = directory / f"call-{number}.json"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            number += 1
            continue
        os.write(fd, b"{}")
        os.close(fd)
        return path


def load_calls(directory: Path) -> list[dict[str, Any]]:
    """The completed usage records of a directory (a reserved, never written record is skipped)."""
    records = []
    for path in call_records(directory):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(value, dict) and "wallSeconds" in value:
            records.append(value)
    return records


RUN_ID = re.compile(r"\brun_[0-9a-f]{32}\b")
TEMP_PATH = re.compile(r"(?:/private)?/(?:tmp|var/folders)/[^\s\"'\\]*")


def anonymize(text: str, *roots: Path) -> str:
    """A record line without local paths or run identifiers: every root (the run directory, the
    evaluation code) becomes ``<run>``/``<code>``, the home directory ``~`` and a run id ``run``."""
    for label, root in zip(("<run>", "<code>", "<work>"), roots, strict=False):
        for form in {str(root), str(root.resolve())}:
            text = text.replace(form, label)
    home = os.environ.get("HOME", "")
    if home:
        text = text.replace(home, "~")
    # Temporary directories (and a call record's truncated command) keep no machine path either.
    text = TEMP_PATH.sub("<tmp>", text)
    return RUN_ID.sub("run", text)


class AccountError(RuntimeError):
    """The Claude CLI is not on the evaluation account, or an override is set."""


def is_fake(binary: str | None = None) -> bool:
    """True when the CLI is the deterministic stand-in of the dry runs (no model call)."""
    return Path(binary or CLAUDE_BIN).name == FAKE_CLAUDE.name


def ensure_account() -> str:
    """Abort unless ~/.claude.json is logged in to EXPECTED_ACCOUNT and no override is set.

    Checked before every real call (not cached: the login could change during a long matrix).
    """
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"):
        if os.environ.get(name):
            raise AccountError(f"{name} is set: the evaluation runs only on the subscription login")
    path = Path(os.environ.get("HOME", pwd.getpwuid(os.getuid()).pw_dir)) / ".claude.json"
    try:
        account = (json.loads(path.read_text(encoding="utf-8")).get("oauthAccount") or {}).get(
            "emailAddress", ""
        )
    except (OSError, ValueError) as error:
        raise AccountError(f"cannot read {path}: {error}") from error
    if account != EXPECTED_ACCOUNT:
        raise AccountError(f"the Claude CLI is logged in as {account!r}, not {EXPECTED_ACCOUNT!r}")
    return account


def _command(
    prompt: str, model: str, max_budget_usd: str, session: list[str], effort: str | None = None
) -> list[str]:
    return [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--model",
        model,
        # Only the routed calls of the tiered condition carry an effort; every other call keeps the
        # command line of the direct condition.
        *(["--effort", effort] if effort else []),
        "--output-format",
        "json",
        "--safe-mode",
        "--strict-mcp-config",
        *session,
        "--tools",
        TOOLS,
        "--allowedTools",
        *ALLOWED_TOOLS,
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        max_budget_usd,
    ]


def run_claude(
    prompt: str,
    cwd: Path,
    model: str,
    log_path: Path,
    timeout_seconds: int = AGENT_TIMEOUT_SECONDS,
    max_budget_usd: str = MAX_BUDGET_USD,
    resumable: bool = False,
    resume_session: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """Run Claude Code non-interactively and return the normalized usage record.

    The large-project scenario raises the time and budget limits and makes the call resumable; every
    other scenario uses the defaults (one call, no session kept). ``effort`` is passed only for the
    calls the tiered router sends with an effort.
    """
    if resumable:
        return _run_resumable(
            prompt, cwd, model, log_path, timeout_seconds, max_budget_usd, resume_session, effort
        )
    command = _command(prompt, model, max_budget_usd, ["--no-session-persistence"], effort)
    record, stderr, _ = _invoke(command, cwd, model, timeout_seconds)
    record["effort"] = effort
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    (log_path.with_suffix(".stderr.txt")).write_text(stderr[-20000:], encoding="utf-8")
    return record


def _transient(record: dict[str, Any], result: dict[str, Any], stderr: str) -> bool:
    """True when the call failed because of the environment (sleep, timeout, network), not the agent."""
    if record["timedOut"] or (result and not result.get("is_error")):
        return False
    text = " ".join([str(result.get("result", "")), stderr[-4000:]])
    return bool(TRANSIENT_TEXT.search(text))


LIMIT_EXIT_CODE = 75
"""Exit code of a runner whose run met the account's usage limit: the run is not a result and is
written to ``<out>.invalid.jsonl``; the matrix waits and runs the cell again."""


def hit_limit(record: dict[str, Any]) -> bool:
    """A usage record of a call that ended on the account's usage limit (or an overloaded API)."""
    if not record.get("isError") or record.get("timedOut"):
        return False
    text = " ".join(str(record.get(k) or "") for k in ("summary", "resultText", "terminalReason"))
    return bool(LIMIT_TEXT.search(text))


def _limited(record: dict[str, Any], result: dict[str, Any], stderr: str) -> bool:
    """True when the call stopped on the usage limit or an overloaded API, not on the agent's own error."""
    if record["timedOut"] or (result and not result.get("is_error")):
        return False
    if str(result.get("subtype", "")).startswith("error_max"):
        return False
    text = " ".join(
        [str(result.get("result", "")), str(result.get("api_error_status", "")), stderr[-4000:]]
    )
    return bool(LIMIT_TEXT.search(text))


def _run_resumable(
    prompt: str,
    cwd: Path,
    model: str,
    log_path: Path,
    timeout_seconds: int,
    max_budget_usd: str,
    resume_session: str | None,
    effort: str | None = None,
) -> dict[str, Any]:
    """One call that survives usage limits and restarts of the runner.

    The session id is chosen here and written to ``<log>.inflight`` before every segment, so a runner
    that is restarted can resume the same session. A segment stopped by the usage limit waits until the
    limit resets and resumes the session; the usage of every segment is summed, and the wait is kept
    apart (``pausedSeconds``) from the working time (``wallSeconds``).
    """
    inflight = log_path.with_name(log_path.name + ".inflight")
    state: dict[str, Any] = {
        "sessionId": resume_session,
        "started": resume_session is not None,
        "segments": [],
        "pausedSeconds": 0.0,
    }
    if resume_session and inflight.exists():
        state = json.loads(inflight.read_text(encoding="utf-8"))
        state["started"] = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_tail = ""
    while True:
        if not state["started"]:
            state["sessionId"] = str(uuid.uuid4())
            command = _command(
                prompt, model, max_budget_usd, ["--session-id", state["sessionId"]], effort
            )
        else:
            command = _command(
                CONTINUE_PROMPT, model, max_budget_usd, ["--resume", state["sessionId"]], effort
            )
        inflight.write_text(json.dumps(state, indent=1), encoding="utf-8")
        active = sum(seg["wallSeconds"] for seg in state["segments"])
        record, stderr, result = _invoke(
            command, cwd, model, max(int(timeout_seconds - active), 60)
        )
        stderr_tail = (stderr_tail + stderr)[-20000:]
        record["resumed"] = state["started"]
        state["segments"].append(record)
        state["started"] = state["started"] or bool(record["numTurns"]) or bool(record["costUsd"])
        if (
            _transient(record, result, stderr)
            and state.get("transientRetries", 0) < TRANSIENT_MAX_RETRIES
        ):
            state["transientRetries"] = state.get("transientRetries", 0) + 1
            inflight.write_text(json.dumps(state, indent=1), encoding="utf-8")
            time.sleep(TRANSIENT_WAIT_SECONDS)
            state["pausedSeconds"] += TRANSIENT_WAIT_SECONDS
            continue
        if not _limited(record, result, stderr):
            break
        match = LIMIT_RESET_EPOCH.search(str(result.get("result", "")))
        wait = max(int(match.group(1)) - time.time() + 60, 60) if match else LIMIT_POLL_SECONDS
        state["limitPauses"] = state.get("limitPauses", 0) + 1
        inflight.write_text(json.dumps(state, indent=1), encoding="utf-8")
        time.sleep(wait)
        state["pausedSeconds"] += wait
    segments = state["segments"]
    last = segments[-1]
    # On --resume, Claude Code reports total_cost_usd, duration_api_ms and modelUsage for the whole session
    # (they are restored from it), while usage tokens and num_turns cover only that invocation. So the
    # session totals come from the latest segment that reports them, tokens are summed, and probes that
    # only met the limit again (no output) add no turns.
    cumulative = max(segments, key=lambda seg: seg["costUsd"] or 0)

    def total(key: str) -> Any:
        values = [seg[key] for seg in segments if seg[key] is not None]
        return sum(values) if values else None

    record = {
        **last,
        "costUsd": cumulative["costUsd"],
        "durationApiMs": cumulative["durationApiMs"],
        "modelUsage": cumulative["modelUsage"],
        "inputTokens": total("inputTokens"),
        "outputTokens": total("outputTokens"),
        "cacheReadTokens": total("cacheReadTokens"),
        "cacheCreationTokens": total("cacheCreationTokens"),
        "numTurns": sum(
            seg["numTurns"] or 0
            for seg in segments
            if not seg["resumed"] or (seg["outputTokens"] or 0) > 0
        ),
        "wallSeconds": round(sum(seg["wallSeconds"] for seg in segments), 3),
        "permissionDenials": sum(seg["permissionDenials"] for seg in segments),
        "deniedTools": [tool for seg in segments for tool in seg["deniedTools"]],
        "sessionId": state["sessionId"],
        "segments": len(segments),
        "segmentLog": [
            {
                k: seg[k]
                for k in (
                    "resumed",
                    "exitCode",
                    "isError",
                    "numTurns",
                    "costUsd",
                    "outputTokens",
                    "wallSeconds",
                )
            }
            | {"summary": seg["summary"][:200]}
            for seg in segments
        ],
        "limitPauses": state.get("limitPauses", 0),
        "transientRetries": state.get("transientRetries", 0),
        "pausedSeconds": round(state["pausedSeconds"], 3),
        "effort": effort,
    }
    log_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    (log_path.with_suffix(".stderr.txt")).write_text(stderr_tail, encoding="utf-8")
    inflight.unlink(missing_ok=True)
    return record


def _invoke(
    command: list[str], cwd: Path, model: str, timeout_seconds: int
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Run one Claude Code process; return its normalized record, its stderr and the raw result."""
    if not is_fake(command[0]):
        ensure_account()
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=agent_environment(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
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
        "resultText": str(result.get("result", "")),
    }
    return record, stderr, result


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
    usage = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
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
