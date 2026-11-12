from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_harness.evidence.artifact_store import LocalArtifactStore
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


CODE = [
    b"def check(token: bytes) -> bool:",
    b"def login(user: str, password: str) -> bool:",
    b"def f(token: int) -> None:",
    b"token = self._token",
    b"password = form.password",
    b"token = await client.fetch()",
    b"token = serializer.dumps(payload)",
    b'password = os.environ["ALERTS_PASSWORD"]',
    b"function f(token: string): boolean {",
]


@pytest.mark.parametrize("code", CODE)
def test_code_keeps_its_unquoted_values(code: bytes) -> None:
    # F4: an annotation or an expression after `token`/`password` was replaced by <REDACTED>,
    # which corrupted the stored diff and the quarantined contents run continue puts back.
    result = SecretRedactor().redact(code, code=True)
    assert result.data == code
    assert not result.redacted


@pytest.mark.parametrize(
    ("data", "secret"),
    [(b'password = "supersecret123"', b"supersecret123"), (b"token = 'abcd-1234'", b"abcd-1234")],
)
def test_code_still_loses_its_quoted_secrets(data: bytes, secret: bytes) -> None:
    result = SecretRedactor().redact(data, code=True)
    assert secret not in result.data
    assert result.rule_ids == ("generic_secret_assignment",)


@pytest.mark.parametrize(
    ("data", "secret"),
    [
        (b"password=hunter22", b"hunter22"),
        (b"API_KEY=abc123def456\n", b"abc123def456"),
        (b"password: s3cr3t!x", b"s3cr3t!x"),
    ],
)
def test_tool_output_still_loses_its_unquoted_secrets(data: bytes, secret: bytes) -> None:
    result = SecretRedactor().redact(data)
    assert secret not in result.data
    assert result.rule_ids == ("generic_secret_assignment",)


def test_a_diff_artifact_is_redacted_as_code(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    diff = b"+def login(user: str, password: str) -> bool:\n+    token = self._token\n"
    ref = store.put(diff, media_type="text/x-diff")
    assert store.get(ref) == diff
    assert not ref.redacted
    output = store.put(b"password=hunter22\n", media_type="text/plain")
    assert b"hunter22" not in store.get(output)
