#!/usr/bin/env python3
"""Harness command provider that delegates IMPLEMENTATION to Codex CLI.

Same protocol and files as claude_provider.py: one JSON request on stdin, one JSON object on
stdout, usage records in ``../agent-calls`` and reviewer feedback from ``../feedback.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentlib import build_prompt, run_codex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    workspace = Path.cwd()
    run_dir = workspace.parent
    feedback_file = run_dir / "feedback.md"
    feedback = feedback_file.read_text(encoding="utf-8") if feedback_file.exists() else None
    calls = run_dir / "agent-calls"
    calls.mkdir(exist_ok=True)
    number = len(list(calls.glob("call-*.json"))) + 1
    record = run_codex(
        build_prompt(request["task"], feedback),
        workspace,
        args.model,
        args.effort,
        calls / f"call-{number}.json",
    )
    status = "FAILED" if record["isError"] else "PASSED"
    print(json.dumps({"status": status, "summary": f"Codex ({args.model}, {args.effort}) finished: {record['terminalReason']}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
