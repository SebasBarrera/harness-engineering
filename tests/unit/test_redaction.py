from __future__ import annotations

import json

import pytest

from governed_harness.evidence.redaction import SecretRedactor


def test_redacts_authorization_header() -> None:
    result = SecretRedactor().redact(b"Authorization: Bearer abcdef123456\nbody")
    assert b"abcdef123456" not in result.data
    assert result.redacted


@pytest.mark.parametrize("length", [32, 36])
def test_redacts_github_token(length: int) -> None:
    # Built at runtime so that no token-shaped literal lives in the repository and secret
    # scanners cannot flag this fixture. 36 characters is the length of a real classic token.
    token = b"ghp_" + (b"abcdefghijklmnopqrstuvwxyz0123456789" * 2)[:length]
    result = SecretRedactor().redact(token)
    assert token not in result.data
    assert result.redacted


def test_non_secret_is_unchanged() -> None:
    data = b"ordinary validation output"
    result = SecretRedactor().redact(data)
    assert result.data == data
    assert not result.redacted


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        ('secret = os.environ.get("ALERTS_PASSWORD") (risk factor authentication)', True),
        ("token=abcd1234 z", True),
        # A quoted value escaped inside a JSON string is left as in 1.0.0: the stored task of a
        # patch task carries it, and redacting it would change the code the run applies.
        ('password = "supersecret123"\n_ = password', False),
    ],
)
def test_redacting_a_json_string_keeps_the_json_valid(text: str, redacted: bool) -> None:
    # #157: the provider feedback of the H45 reference solution was redacted into invalid JSON
    # (the value was cut after the backslash of an escaped quote).
    data = json.dumps({"message": text}).encode("utf-8")
    result = SecretRedactor().redact(data)
    message = json.loads(result.data)["message"]
    assert result.redacted is redacted
    assert ("<REDACTED>" in message) is redacted
    assert "abcd1234" not in message
    assert SecretRedactor().redact(result.data).data == result.data
