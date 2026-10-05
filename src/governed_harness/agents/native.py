"""Built-in adapters for agent CLIs (``agentProviders.<id>.kind``, since 1.1).

A command provider has to speak the harness JSON protocol, so every agent needed a wrapper
written by the user. A built-in adapter runs the agent CLI itself in its documented
non-interactive mode, renders the request (task, plan, governed memory, the feedback of a
correction attempt and, with ``provenance.selfReport``, the self-report instructions) as a
prompt, and reads the CLI's own output: the status, a summary, the session id and, when the CLI
reports them, tokens and cost as ``REPORTED`` usage.

The adapters run through the same governed process runner as any provider: the executable needs
a ``process.execute`` grant, the call runs under ``runtime.agentSandbox`` and receives only the
environment the provider declares (``passEnv``, ``env``). The command lines below follow each
CLI's documentation; they are covered by tests against fake CLIs that print the documented
output formats, not against the live agents.

=========== ============ ===================================================================
kind        executable   arguments (before ``args``)
=========== ============ ===================================================================
claude-code ``claude``   ``-p --output-format json --permission-mode acceptEdits``
                         (``--model``); prompt on stdin
codex       ``codex``    ``exec --json --full-auto --skip-git-repo-check`` (``--model``);
                         prompt on stdin (``-``)
gemini-cli  ``gemini``   ``--output-format json --approval-mode auto_edit`` (``--model``);
                         prompt on stdin, ``--prompt`` points to it
aider       ``aider``    ``--yes-always --no-auto-commits --no-dirty-commits --no-stream
                         --no-pretty --no-gitignore --no-check-update --analytics-disable``
                         and history files in the null device (``--model``); prompt in
                         ``--message``
=========== ============ ===================================================================
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

from governed_harness.agents.base import AgentContext
from governed_harness.agents.command import (
    CommandAgentConfiguration,
    CommandAgentProvider,
    ProviderAnswer,
    request_routing,
)
from governed_harness.agents.self_report import PROMPT_INSTRUCTIONS, extract_from_text
from governed_harness.configuration.models import NATIVE_DEFAULT_COMMANDS
from governed_harness.domain.enums import ResultStatus
from governed_harness.runtime.process_runner import ProcessResult

SUMMARY_CHARS = 2000
STDERR_TAIL_CHARS = 400
PROMPT_PLACEHOLDER = "<prompt>"
"""Recorded in place of a prompt passed on the command line (aider)."""

DEFAULT_EXECUTABLES = NATIVE_DEFAULT_COMMANDS


def render_prompt(request: dict[str, Any], *, self_report: bool) -> str:
    """The request as instructions for an agent that reads text."""
    task = request["task"]
    plan = request.get("plan") or {}
    lines = [
        "You are implementing a task in the repository in the current directory, under the "
        "Governed Agent Harness. Edit the files needed to meet the acceptance criteria. Do not "
        "commit, push or create branches; the harness verifies your change and a person decides.",
        "",
        f"# Task {task['task_id']}: {task['title']}",
        "",
        str(task["intent"]).strip(),
    ]
    if task.get("requirements"):
        lines += ["", "## Requirements"]
        lines += [f"- {item['requirement_id']}: {item['text']}" for item in task["requirements"]]
    if task.get("acceptance_criteria"):
        lines += ["", "## Acceptance criteria"]
        for item in task["acceptance_criteria"]:
            hint = (
                f" (verify: {item['verification_hint']})" if item.get("verification_hint") else ""
            )
            lines.append(
                f"- {item['criterion_id']} [{item.get('priority', 'MUST')}]: {item['text']}{hint}"
            )
    if task.get("constraints"):
        lines += ["", "## Constraints"]
        lines += [f"- {item}" for item in task["constraints"]]
    owned = (task.get("metadata") or {}).get("ownedPaths")
    implementation = task.get("implementation") or {}
    if implementation.get("mode") == "patch":
        owned = owned or [patch["path"] for patch in implementation.get("patches", [])]
        lines += ["", "## Changes to apply"]
        for patch in implementation.get("patches", []):
            lines.append(f"- {patch['operation']} {patch['path']}")
            if patch.get("content") is not None:
                lines += ["```", str(patch["content"]).rstrip("\n"), "```"]
    if owned:
        lines += ["", "## Files you may change"]
        lines += [f"- {path}" for path in owned]
    if plan.get("steps"):
        lines += ["", "## Plan"]
        lines += [
            f"{index}. {step['description']}" for index, step in enumerate(plan["steps"], start=1)
        ]
    context = request.get("context")
    if context and context.get("records"):
        lines += ["", "## Project memory approved for this task"]
        for record in context["records"]:
            value = record.get("value")
            text = value.get("text") if isinstance(value, dict) else None
            lines.append(f"- {text or json.dumps(value, sort_keys=True)}")
    feedback = request.get("feedback")
    if feedback:
        lines += ["", *_feedback_lines(feedback)]
    extras = implement_extras_lines(request)
    if extras:
        lines += ["", *extras]
    if self_report:
        lines += ["", "## Self-report", PROMPT_INSTRUCTIONS]
    return "\n".join(lines).rstrip() + "\n"


def _feedback_lines(feedback: dict[str, Any]) -> list[str]:
    lines = [
        f"## Why the previous attempt was not accepted (attempt {feedback.get('attempt')}, "
        f"{feedback.get('trigger')})"
    ]
    gate = feedback.get("gate") or {}
    if gate:
        codes = ", ".join(gate.get("reasonCodes") or []) or "none"
        lines.append(f"Gate {gate.get('gateId')}: {gate.get('status')} ({codes})")
    for finding in feedback.get("findings") or []:
        location = finding.get("location") or {}
        where = location.get("path") or ""
        if where and location.get("startLine"):
            where = f"{where}:{location['startLine']}"
        lines.append(
            f"- {finding.get('severity')} {finding.get('ruleId')}"
            f"{' at ' + where if where else ''}: {finding.get('message')}"
        )
    for validator in feedback.get("validators") or []:
        lines.append(
            f"Validator {validator.get('validatorId')} {validator.get('status')} "
            f"(exit {validator.get('exitCode')})"
        )
        for stream in ("stdout", "stderr"):
            text = (validator.get(stream) or "").strip()
            if text:
                lines += [f"{stream}:", "```", text, "```"]
    decision = feedback.get("decision")
    if decision:
        lines.append(
            f"The reviewer ({decision.get('actorId')}) asked for changes: "
            f"{decision.get('rationale')}"
        )
    return lines


_CALL_KEYS_LEFT_OUT = frozenset({"instructions", "schemaVersion", "kind", "readOnly"})
_IMPLEMENT_EXTRAS = ("gate", "permissions", "contextFiles", "lessons", "acceptanceTests", "budget")


def render_call_prompt(request: dict[str, Any]) -> str:
    """A read-only request (``clarify``, ``acceptance``, ``plan``, ``review``, since 1.1) as
    text: the rendered instructions, then the request itself as JSON."""
    payload = {key: value for key, value in request.items() if key not in _CALL_KEYS_LEFT_OUT}
    return (
        f"{request.get('instructions', '')}\n\n"
        "The request, as JSON:\n\n```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n```\n\n"
        "End your answer with the JSON object described above, on its own, so the harness can "
        "read it.\n"
    )


def implement_extras_lines(request: dict[str, Any]) -> list[str]:
    """What the agent-results settings add to an implement request (gate contract,
    permissions, context manifest, lessons, frozen acceptance tests, budget), as text."""
    present = {key: request[key] for key in _IMPLEMENT_EXTRAS if request.get(key)}
    if not present:
        return []
    return [
        "## What the harness will check and what you may use",
        "```json",
        json.dumps(present, indent=2, sort_keys=True),
        "```",
    ]


def extract_call_result(text: str) -> Any:
    """The ``result`` object of a read-only call from a CLI's output: the last JSON object
    with a ``result`` key found in the output or in any text field of it."""
    candidates: list[str] = [text]
    for line in [text, *text.splitlines()]:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        candidates.extend(_strings(value))
    for candidate in reversed(candidates):
        found = _last_result_object(candidate)
        if found is not None:
            return found
    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _last_result_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    found: Any = None
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            found = value["result"]
        index = text.find("{", end)
    return found


def _tail(text: str, chars: int = STDERR_TAIL_CHARS) -> str:
    text = " ".join(text.strip().split())
    return text[-chars:]


def _summary(text: Any) -> str:
    value = str(text or "").strip()
    return value[:SUMMARY_CHARS] if value else ""


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(**values: int | float | None) -> dict[str, int | float] | None:
    kept = {name: value for name, value in values.items() if value is not None}
    return kept or None


class NativeAgentProvider(CommandAgentProvider):
    """Base of the built-in adapters: a prompt instead of the JSON request, the CLI's own
    output instead of the protocol answer."""

    kind = ""
    stdout_media_type = "text/plain"
    prompt_on_stdin = True

    def capabilities(self) -> tuple[str, ...]:
        return ("external_cli", "native_adapter", self.kind)

    def base_args(self) -> tuple[str, ...]:
        raise NotImplementedError

    def model_args(self, routing: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """``--model`` of the configured model, or of the model the router chose for the call
        (``agentRouting``, since 1.1), followed by the CLI's effort option."""
        routing = routing or {}
        model = routing.get("model") or self.configuration.model
        effort = routing.get("effort")
        return (("--model", model) if model else ()) + (self.effort_args(effort) if effort else ())

    def effort_args(self, effort: str) -> tuple[str, ...]:
        """The CLI's reasoning-effort option; a CLI without one ignores the router's effort."""
        return ()

    def prompt_args(self, prompt: str) -> tuple[str, ...]:
        return ()

    def process_input(
        self, request: dict[str, object], context: AgentContext
    ) -> tuple[tuple[str, ...], bytes | None, tuple[str, ...]]:
        kind = request.get("kind")
        if kind is not None and kind != "implement":
            prompt = render_call_prompt(request)
        else:
            prompt = render_prompt(request, self_report=self.configuration.self_report)
        head = (
            *self.configuration.argv_prefix,
            *self.base_args(),
            *self.model_args(request_routing(request)),
            *self.configuration.extra_args,
        )
        tail = self.prompt_args(prompt)
        recorded = (*head, *(PROMPT_PLACEHOLDER if item == prompt else item for item in tail))
        stdin = prompt.encode("utf-8") if self.prompt_on_stdin else None
        return (*head, *tail), stdin, recorded

    def failure_summary(self, result: ProcessResult) -> str:
        tail = _tail(result.stderr.decode("utf-8", "replace")) or _tail(
            result.stdout.decode("utf-8", "replace")
        )
        summary = f"{self.kind} exited with {result.exit_code}"
        return f"{summary}: {tail}" if tail else summary

    def _self_report_from(self, text: str) -> Any:
        return extract_from_text(text) if self.configuration.self_report else None

    def extract_result(self, result: ProcessResult) -> Any:
        return extract_call_result(result.stdout.decode("utf-8", "replace"))


class ClaudeCodeProvider(NativeAgentProvider):
    """``claude -p --output-format json``: one JSON object with ``result``, ``is_error``,
    ``session_id``, ``total_cost_usd`` and ``usage``."""

    kind = "claude-code"

    def base_args(self) -> tuple[str, ...]:
        return ("-p", "--output-format", "json", "--permission-mode", "acceptEdits")

    def effort_args(self, effort: str) -> tuple[str, ...]:
        return ("--effort", effort)

    def read_answer(self, result: ProcessResult) -> ProviderAnswer:
        value = json.loads(result.stdout)
        if isinstance(value, list):  # --verbose prints every message; the last one is the result
            results = [
                item for item in value if isinstance(item, dict) and item.get("type") == "result"
            ]
            value = results[-1] if results else None
        if not isinstance(value, dict) or ("result" not in value and "is_error" not in value):
            raise ValueError("claude output is not a result object")
        failed = bool(value.get("is_error")) or value.get("subtype", "success") != "success"
        raw_usage = value.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        inputs = [
            _int(usage.get(name))
            for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        ]
        cost = value.get("total_cost_usd")
        text = _summary(value.get("result"))
        return ProviderAnswer(
            status=ResultStatus.FAILED if failed else ResultStatus.PASSED,
            summary=text or f"claude-code {value.get('subtype', 'finished')}",
            usage=_usage(
                inputTokens=sum(item for item in inputs if item is not None)
                if any(item is not None for item in inputs)
                else None,
                outputTokens=_int(usage.get("output_tokens")),
                costUsd=cost
                if isinstance(cost, int | float) and not isinstance(cost, bool) and cost >= 0
                else None,
            ),
            usage_limitations=(
                "Claude Code: inputTokens adds input, cache-creation and cache-read tokens; "
                "costUsd is total_cost_usd.",
            ),
            session_id=value.get("session_id")
            if isinstance(value.get("session_id"), str)
            else None,
            self_report=self._self_report_from(str(value.get("result") or "")),
        )


class CodexProvider(NativeAgentProvider):
    """``codex exec --json``: one JSON event per line; ``turn.completed`` carries the usage,
    ``item.completed`` with an ``agent_message`` the answer, ``turn.failed`` or ``error`` a
    failure."""

    kind = "codex"

    def base_args(self) -> tuple[str, ...]:
        return ("exec", "--json", "--full-auto", "--skip-git-repo-check")

    def effort_args(self, effort: str) -> tuple[str, ...]:
        return ("-c", f'model_reasoning_effort="{effort}"')

    def prompt_args(self, prompt: str) -> tuple[str, ...]:
        return ("-",)

    def read_answer(self, result: ProcessResult) -> ProviderAnswer:
        events = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if not events:
            raise ValueError("codex printed no JSON events")
        totals = {"input": 0, "output": 0, "reasoning": 0}
        seen = False
        messages: list[str] = []
        failure: str | None = None
        session: str | None = None
        for event in events:
            kind = event.get("type")
            if kind == "thread.started" and isinstance(event.get("thread_id"), str):
                session = event["thread_id"]
            elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
                seen = True
                totals["input"] += _int(usage.get("input_tokens")) or 0
                totals["output"] += _int(usage.get("output_tokens")) or 0
                totals["reasoning"] += _int(usage.get("reasoning_output_tokens")) or 0
            elif kind == "item.completed" and isinstance(event.get("item"), dict):
                item = event["item"]
                if item.get("type") in {"agent_message", "assistant_message"} and isinstance(
                    item.get("text"), str
                ):
                    messages.append(item["text"])
            elif kind in {"turn.failed", "error"}:
                error = event.get("error")
                message = error.get("message") if isinstance(error, dict) else event.get("message")
                failure = str(message or kind)
        answer = messages[-1] if messages else ""
        return ProviderAnswer(
            status=ResultStatus.FAILED if failure else ResultStatus.PASSED,
            summary=_summary(failure or answer) or "codex finished",
            usage=_usage(
                inputTokens=totals["input"] if seen else None,
                outputTokens=totals["output"] if seen else None,
                reasoningTokens=totals["reasoning"] if seen and totals["reasoning"] else None,
            ),
            usage_limitations=("Codex reports tokens per turn and no cost.",),
            session_id=session,
            self_report=self._self_report_from(answer),
        )


class GeminiCliProvider(NativeAgentProvider):
    """``gemini --output-format json``: ``response``, ``stats.models.<model>.tokens``
    (``prompt``, ``candidates``, ``thoughts``) and ``error`` on failure."""

    kind = "gemini-cli"

    def base_args(self) -> tuple[str, ...]:
        return ("--output-format", "json", "--approval-mode", "auto_edit")

    def prompt_args(self, prompt: str) -> tuple[str, ...]:
        return ("--prompt", "Follow the instructions given on standard input.")

    def read_answer(self, result: ProcessResult) -> ProviderAnswer:
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not ({"response", "error"} & set(value)):
            raise ValueError("gemini output is not a response object")
        error = value.get("error")
        models = (
            ((value.get("stats") or {}).get("models") or {})
            if isinstance(value.get("stats"), dict)
            else {}
        )
        totals = {"prompt": 0, "candidates": 0, "thoughts": 0}
        seen = False
        for stats in models.values() if isinstance(models, dict) else ():
            tokens = stats.get("tokens") if isinstance(stats, dict) else None
            if not isinstance(tokens, dict):
                continue
            seen = True
            for name in totals:
                totals[name] += _int(tokens.get(name)) or 0
        response = str(value.get("response") or "")
        message = (
            error.get("message") if isinstance(error, dict) else (str(error) if error else None)
        )
        return ProviderAnswer(
            status=ResultStatus.FAILED if error else ResultStatus.PASSED,
            summary=_summary(message or response) or "gemini-cli finished",
            usage=_usage(
                inputTokens=totals["prompt"] if seen else None,
                outputTokens=totals["candidates"] if seen else None,
                reasoningTokens=totals["thoughts"] if seen and totals["thoughts"] else None,
            ),
            usage_limitations=("Gemini CLI reports tokens per model and no cost.",),
            self_report=self._self_report_from(response),
        )


_AIDER_TOKENS = re.compile(
    r"Tokens: (?P<sent>[\d.,]+)(?P<sent_k>k?) sent,(?:.*?,)? (?P<received>[\d.,]+)(?P<received_k>k?) "
    r"received\.(?: Cost: \$(?P<message>[\d.,]+) message, \$(?P<session>[\d.,]+) session\.)?"
)


def _aider_count(number: str, thousands: str) -> int:
    value = float(number.replace(",", ""))
    return round(value * 1000) if thousands else round(value)


class AiderProvider(NativeAgentProvider):
    """``aider --message``: plain text; each model call prints ``Tokens: N sent, M received.
    Cost: $x message, $y session.``"""

    kind = "aider"
    prompt_on_stdin = False

    def base_args(self) -> tuple[str, ...]:
        return (
            "--yes-always",
            "--no-auto-commits",
            "--no-dirty-commits",
            "--no-stream",
            "--no-pretty",
            "--no-gitignore",
            "--no-check-update",
            "--analytics-disable",
            "--input-history-file",
            os.devnull,
            "--chat-history-file",
            os.devnull,
        )

    def prompt_args(self, prompt: str) -> tuple[str, ...]:
        return ("--message", prompt)

    def read_answer(self, result: ProcessResult) -> ProviderAnswer:
        text = result.stdout.decode("utf-8", "replace")
        sent = received = 0
        session_cost: float | None = None
        seen = False
        rounded = False
        for match in _AIDER_TOKENS.finditer(text):
            seen = True
            rounded = rounded or bool(match.group("sent_k") or match.group("received_k"))
            sent += _aider_count(match.group("sent"), match.group("sent_k"))
            received += _aider_count(match.group("received"), match.group("received_k"))
            if match.group("session"):
                session_cost = float(match.group("session").replace(",", ""))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        limitations = ["Aider prints the session cost; tokens are summed per message."]
        if rounded:
            limitations.append("Aider prints token counts above 1,000 rounded (for example 1.2k).")
        return ProviderAnswer(
            status=ResultStatus.PASSED,
            summary=_summary(lines[-1] if lines else "") or "aider finished",
            usage=_usage(
                inputTokens=sent if seen else None,
                outputTokens=received if seen else None,
                costUsd=session_cost,
            ),
            usage_limitations=tuple(limitations),
            self_report=self._self_report_from(text),
        )


ADAPTERS: dict[str, type[NativeAgentProvider]] = {
    "claude-code": ClaudeCodeProvider,
    "codex": CodexProvider,
    "gemini-cli": GeminiCliProvider,
    "aider": AiderProvider,
}


def native_provider(kind: str, configuration: CommandAgentConfiguration) -> NativeAgentProvider:
    try:
        return ADAPTERS[kind](configuration)
    except KeyError as error:
        raise ValueError(f"unknown built-in agent adapter: {kind}") from error
