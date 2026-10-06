#!/usr/bin/env python3
"""Record the environment of an evaluation block in ``environment.json`` (no model call).

Usage: python environment.py <out.json> [--wheel PATH] [--code DIR] [--block NAME] [--claude-bin BIN]

Records the harness executable (``EVAL_HARNESS``, default ``harness``) and its version, the SHA-256
of the wheel it was installed from, the Claude Code version, the Python and tool versions of the
interpreter running it (pytest, coverage, ruff, bandit, mypy), a digest of the evaluation code
copy, the operating system and the start time. Paths of the machine are not recorded. Existing
entries of the file are kept under ``blocks``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_digest(root: Path) -> str:
    """Digest of every file of the evaluation code copy (sorted paths and contents)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


def version(command: list[str]) -> str | None:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return (proc.stdout.strip() or proc.stderr.strip()) or None


def package(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--wheel", type=Path, default=None)
    parser.add_argument("--code", type=Path, default=None)
    parser.add_argument("--block", default="")
    parser.add_argument("--claude-bin", default=os.environ.get("EVAL_CLAUDE_BIN", "claude"))
    args = parser.parse_args()
    harness = shlex.split(os.environ.get("EVAL_HARNESS", "harness"))
    entry = {
        "recordedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "harnessVersion": version([*harness, "--version"]),
        "harnessPackage": package("governed-agent-harness"),
        "wheel": {"name": args.wheel.name, "sha256": sha256_file(args.wheel)}
        if args.wheel
        else None,
        "claudeCode": version([args.claude_bin, "--version"]),
        "python": platform.python_version(),
        "tools": {
            name: package(name)
            for name in ("pytest", "coverage", "ruff", "bandit", "mypy", "freezegun")
        },
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "codeDigest": code_digest(args.code) if args.code else None,
    }
    data = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    data.setdefault("blocks", {})[args.block or "default"] = entry
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {json.dumps(entry)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
