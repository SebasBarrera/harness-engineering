#!/usr/bin/env python3
"""Harness command provider that delegates every agent call to Claude Code (protocol 1.0 and 1.1).

Protocol (see docs/guides/external-agents.md and docs/guides/agent-results.md): one JSON request on
stdin, one JSON object on stdout. Every call runs the Claude Code command line of the direct
condition (``agentlib._command``: same tools, permission mode, budget, time limit and strict MCP
configuration); only the prompt, and the tools of a read-only call, depend on the request:

* ``implement`` (``kind`` absent or ``implement``): the prompt of the direct condition
  (``agentlib.build_prompt`` of the task) followed by what the harness adds to the request, rendered
  with the harness's own helpers (the feedback of a correction attempt, the gate contract,
  permissions, context files, lessons, frozen acceptance tests, budget, locations, the quoted
  repository content and the self-report request). The agent edits the workspace; the harness then
  computes the ChangeSet, verifies, reviews and gates it.
* the read-only kinds of 1.1 (``clarify``, ``review`` and the reviewers of the review panel,
  ``plan``, ``acceptance``, ``locate``, ``architecture``): the harness's own rendering of the request
  (its instructions, then the request as JSON); the ``result`` object is read from the last JSON
  object with a ``result`` key in the answer, as the built-in ``claude-code`` adapter does. These
  calls run with the read-only tools of that adapter (``Read``, ``Grep``, ``Glob``; no accepted
  edits, no shell; #92): with the implement tools Haiku wrote the acceptance tests to disk instead
  of returning them, the harness undid the write and blocked SPECIFICATION (2.0.0 pilot).

The answer carries the usage Claude Code reports (tokens with their cache part, cost), as the
built-in adapter reports it. Model: ``--model`` for every call; with ``--honor-routing`` (the anchored
condition) the model and effort the router put in ``routing`` replace it for that call. Every call
leaves a usage record ``call-N.json`` (with ``kind``, model, effort and what routing asked for), the
request and the prompt next to the workspace (``../agent-calls``, or ``--calls-dir``). Without a
``feedback`` key in the request, the reviewer's feedback of the 0.9.0 decision rule is read from
``../feedback.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentlib  # noqa: E402

READ_ONLY_KINDS = {"clarify", "review", "plan", "acceptance", "locate", "architecture"}


def harness_sections(request: dict[str, Any]) -> list[str]:
    """What the harness adds to an implement request, rendered as the built-in adapter does."""
    from governed_harness.agents import native

    lines: list[str] = []
    if request.get("feedback"):
        lines += ["", *native._feedback_lines(request["feedback"])]
    extras = native.implement_extras_lines(request)
    if extras:
        lines += ["", *extras]
    check = check_instruction(request.get("gate"))
    if check:
        lines += ["", check]
    for key, title in (("locations", "Dónde intervenir"), ("attachments", "Adjuntos")):
        if request.get(key):
            lines += [
                "",
                f"## {title}",
                "```json",
                json.dumps(request[key], indent=2, sort_keys=True),
                "```",
            ]
    untrusted = request.get("untrustedContent")
    if isinstance(untrusted, dict):
        from governed_harness.capabilities.repository import quoted_lines

        lines += ["", *quoted_lines(untrusted)]
    if request.get("selfReport"):
        from governed_harness.agents.self_report import PROMPT_INSTRUCTIONS

        lines += ["", "## Self-report", PROMPT_INSTRUCTIONS]
    return lines


def check_instruction(gate: Any) -> str:
    """What the implement prompt asks the agent to run before it finishes, from the gate contract
    (amendment of 2026-11-11): ``gate.checkCommand`` (``harness check``) when the agent may run it,
    else the validator commands of ``gate.checkCommands``; empty without a gate contract (the
    ``harness-core`` condition: there the base prompt's validators are the check)."""
    if not isinstance(gate, dict):
        return ""
    command = gate.get("checkCommand")
    if isinstance(command, list) and command:
        return (
            "Antes de terminar, ejecuta desde el directorio actual (sin `cd`) el comando de "
            f"`gate.checkCommand`: `{' '.join(str(part) for part in command)}`, y corrige lo que "
            "reporte hasta que termine con código 0."
        )
    commands = [
        " ".join(str(part) for part in item["command"])
        for item in gate.get("checkCommands") or []
        if isinstance(item, dict) and item.get("command")
    ]
    if commands:
        return (
            "Antes de terminar, ejecuta desde el directorio actual (sin `cd`) los validadores de "
            "`gate.checkCommands`: " + ", ".join(f"`{c}`" for c in commands) + "."
        )
    return ""


def implement_prompt(request: dict[str, Any], run_dir: Path) -> str:
    feedback = None
    if not request.get("feedback"):
        feedback_file = run_dir / "feedback.md"
        feedback = feedback_file.read_text(encoding="utf-8") if feedback_file.exists() else None
    prompt = agentlib.build_prompt(request["task"], feedback)
    extra = harness_sections(request)
    return prompt + ("\n".join(extra).strip("\n") + "\n" if extra else "")


def call_prompt(request: dict[str, Any]) -> str:
    from governed_harness.agents.native import render_call_prompt

    return render_call_prompt(request)


def usage_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """Claude Code's usage in protocol form: input tokens with their cache part, output, cost."""
    parts = [record.get(k) for k in ("inputTokens", "cacheCreationTokens", "cacheReadTokens")]
    usage: dict[str, Any] = {}
    if any(isinstance(v, int) for v in parts):
        usage["inputTokens"] = sum(v for v in parts if isinstance(v, int))
        usage["cacheTokens"] = sum(v for v in parts[1:] if isinstance(v, int))
    if isinstance(parts[2], int):
        # Prompt-cache reads, reported apart so the per-call token budget does not count them
        # (agent protocol `cacheReadTokens`, harness 2.0.0).
        usage["cacheReadTokens"] = parts[2]
    if isinstance(record.get("outputTokens"), int):
        usage["outputTokens"] = record["outputTokens"]
    cost = record.get("costUsd")
    if isinstance(cost, int | float) and not isinstance(cost, bool) and cost >= 0:
        usage["costUsd"] = cost
    return usage or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--budget", default=None)
    parser.add_argument("--resumable", action="store_true")
    parser.add_argument(
        "--honor-routing",
        action="store_true",
        help="use the model and effort of the request's routing (anchored condition)",
    )
    parser.add_argument("--calls-dir", type=Path, default=None)
    parser.add_argument(
        "--claude-bin", default=None, help="the CLI to run (the dry runs use fake_claude.py)"
    )
    args = parser.parse_args()
    if args.claude_bin:
        agentlib.CLAUDE_BIN = args.claude_bin
    request = json.load(sys.stdin)
    workspace = Path.cwd()
    run_dir = workspace.parent
    calls = args.calls_dir or run_dir / "agent-calls"
    log = agentlib.allocate_call(calls)
    stem = log.stem
    kind = str(request.get("kind") or "implement")
    routing = request.get("routing") if isinstance(request.get("routing"), dict) else {}
    model, effort = args.model, None
    if args.honor_routing and routing.get("model"):
        model = str(routing["model"])
        effort = str(routing["effort"]) if routing.get("effort") else None
    read_only = kind in READ_ONLY_KINDS
    prompt = call_prompt(request) if read_only else implement_prompt(request, run_dir)
    (calls / f"{stem}.request.json").write_text(json.dumps(request, indent=1), encoding="utf-8")
    (calls / f"{stem}.prompt.txt").write_text(prompt, encoding="utf-8")
    record = agentlib.run_claude(
        prompt,
        workspace,
        model,
        log,
        **({"timeout_seconds": args.timeout} if args.timeout else {}),
        **({"max_budget_usd": args.budget} if args.budget else {}),
        **({"resumable": True} if args.resumable else {}),
        effort=effort,
        read_only=read_only,
    )
    answer: dict[str, Any] = {}
    result = None
    if read_only and not record["isError"]:
        from governed_harness.agents.native import extract_call_result

        result = extract_call_result(record.get("resultText", ""))
    if record["isError"]:
        status = "FAILED"
        summary = f"Claude Code ({model}) failed: {record['terminalReason']}"
    elif read_only and not isinstance(result, dict):
        status = "FAILED"
        summary = f"Claude Code ({model}) gave no result object for the {kind} request"
    else:
        status = "PASSED"
        summary = f"Claude Code ({model}) finished: {record['terminalReason']}"
    answer = {"status": status, "summary": summary}
    if read_only and status == "PASSED":
        answer["result"] = result
    usage = usage_of(record)
    if usage:
        answer["usage"] = usage
    if request.get("selfReport") and not read_only and status == "PASSED":
        from governed_harness.agents.self_report import extract_from_text

        report = extract_from_text(record.get("resultText", ""))
        if report is not None:
            answer["selfReport"] = report
    # The usage record of the evaluation keeps what the call was and what the router asked for.
    record.update(
        {
            "kind": kind,
            "readOnly": read_only,
            "phase": request.get("phase"),
            "reviewer": (request.get("reviewer") or {}).get("id")
            if isinstance(request.get("reviewer"), dict)
            else None,
            "routing": routing or None,
            "configuredModel": args.model,
            "answerStatus": status,
            "promptChars": len(prompt),
        }
    )
    log.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
