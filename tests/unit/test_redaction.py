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


def test_redact_json_redacts_a_quoted_secret_in_a_decoded_string() -> None:
    # #178: the text redaction of the serialized JSON cannot see this value (its quotes are
    # escaped there); the decoded string shows it.
    value = {"content": 'password = "supersecret123"\n_ = password\n', "line": 'a\\nb\t"c"'}
    serialized = json.dumps(value).encode("utf-8")
    assert b"supersecret123" in SecretRedactor().redact(serialized).data
    redacted, rule_ids = SecretRedactor().redact_json(value)
    assert redacted == {"content": "password = <REDACTED>\n_ = password\n", "line": value["line"]}
    assert rule_ids == ("generic_secret_assignment",)
    again, _ = SecretRedactor().redact_json(redacted)
    assert again == redacted
