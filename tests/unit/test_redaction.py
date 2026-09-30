from __future__ import annotations

from governed_harness.evidence.redaction import SecretRedactor


def test_redacts_authorization_header() -> None:
    result = SecretRedactor().redact(b"Authorization: Bearer abcdef123456\nbody")
    assert b"abcdef123456" not in result.data
    assert result.redacted


def test_redacts_github_token() -> None:
    token = b"ghp_abcdefghijklmnopqrstuvwxyz123456"
    result = SecretRedactor().redact(token)
    assert token not in result.data


def test_non_secret_is_unchanged() -> None:
    data = b"ordinary validation output"
    result = SecretRedactor().redact(data)
    assert result.data == data
    assert not result.redacted
