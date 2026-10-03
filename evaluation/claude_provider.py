#!/usr/bin/env python3
"""Harness command provider that delegates IMPLEMENTATION to Claude Code.

Protocol (see docs/guides/external-agents.md): one JSON request on stdin, one JSON object on
stdout. The agent edits the workspace directly; the harness then computes the ChangeSet, runs the
validators, the independent review and the gate. Usage records are written next to the workspace
(``../agent-calls``) and a reviewer's feedback, when present, is read from ``../feedback.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentlib import build_prompt, run_claude  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--budget", default=None)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    workspace = Path.cwd()
    run_dir = workspace.parent
    feedback_file = run_dir / "feedback.md"
    feedback = feedback_file.read_text(encoding="utf-8") if feedback_file.exists() else None
    calls = run_dir / "agent-calls"
    calls.mkdir(exist_ok=True)
    number = len(list(calls.glob("call-*.json"))) + 1
    record = run_claude(
        build_prompt(request["task"], feedback),
        workspace,
        args.model,
        calls / f"call-{number}.json",
        **({"timeout_seconds": args.timeout} if args.timeout else {}),
        **({"max_budget_usd": args.budget} if args.budget else {}),
    )
    status = "FAILED" if record["isError"] else "PASSED"
    summary = f"Claude Code ({args.model}) finished: {record['terminalReason']}"
    print(json.dumps({"status": status, "summary": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
