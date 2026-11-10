#!/usr/bin/env python3
"""Fuzz the validator output parsers (validators.parsers.parse_output).

A validator's output is written by a tool the agent can influence. The parsers promise that a
format they do not recognize yields no issue and that they never raise; every issue keeps a
known level, a line of at least 1 and at most MAX_ISSUES issues come back."""

from __future__ import annotations

import sys
from pathlib import Path

import atheris

with atheris.instrument_imports():
    from governed_harness.validators.parsers import MAX_ISSUES, parse_output

WORKSPACE = Path("/fuzz-workspace")
PARSERS = ("auto", "sarif", "junit", "ruff", "mypy", "eslint", "tsc", "pytest", "checkstyle")


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    parser = PARSERS[fdp.ConsumeIntInRange(0, len(PARSERS) - 1)]
    stderr = fdp.ConsumeUnicodeNoSurrogates(256)
    stdout = fdp.ConsumeUnicodeNoSurrogates(fdp.remaining_bytes())
    issues = parse_output(stdout, stderr, WORKSPACE, parser=parser)
    assert len(issues) <= MAX_ISSUES
    for issue in issues:
        assert issue.level in ("error", "warning", "note"), issue
        assert issue.line is None or issue.line >= 1, issue


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
