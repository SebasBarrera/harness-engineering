#!/usr/bin/env python3
"""Fuzz the secret redactor (evidence.redaction.SecretRedactor).

Agent output is redacted before it is stored. For any surrounding bytes: redaction never
raises, a planted AWS access key and a planted GitHub token never survive it, and redacting
the result again changes nothing (redaction is idempotent)."""

from __future__ import annotations

import sys

import atheris

with atheris.instrument_imports():
    from governed_harness.evidence.redaction import SecretRedactor

REDACTOR = SecretRedactor(extended=True)
UPPER = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
TOKEN = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    before = fdp.ConsumeBytes(fdp.ConsumeIntInRange(0, 512))
    after = fdp.ConsumeBytes(fdp.remaining_bytes())
    aws = b"AKIA" + bytes(UPPER[i] for i in _indexes(before, 16, len(UPPER)))
    github = b"ghp_" + bytes(TOKEN[i] for i in _indexes(after, 24, len(TOKEN)))
    payload = before + b"\n" + aws + b" " + github + b"\n" + after
    once = REDACTOR.redact(payload).data
    assert aws not in once, "an AWS access key survived redaction"
    assert github not in once, "a GitHub token survived redaction"
    assert REDACTOR.redact(once).data == once, "redaction is not idempotent"


def _indexes(seed: bytes, count: int, size: int) -> list[int]:
    """Deterministic positions derived from the fuzzed bytes (a valid key alphabet)."""
    material = (seed or b"\x00") * (count // max(len(seed), 1) + 1)
    return [material[i] % size for i in range(count)]


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
