#!/usr/bin/env python3
"""Minimal provider-neutral command agent used for integration examples.

It reads the harness JSON request on stdin, applies only the structured patch list in
the task, and returns one JSON result line. It is intentionally deterministic and is
not a model provider.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    request = json.load(sys.stdin)
    for patch in request["task"]["implementation"]["patches"]:
        path = Path(patch["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = patch.get("content") or ""
        operation = patch["operation"]
        if operation == "replace":
            path.write_text(content, encoding="utf-8")
        elif operation == "append":
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            print(
                json.dumps(
                    {"status": "BLOCKED", "summary": f"Unsupported operation: {operation}"}
                )
            )
            return 0
    print(json.dumps({"status": "PASSED", "summary": "Structured patches applied"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
