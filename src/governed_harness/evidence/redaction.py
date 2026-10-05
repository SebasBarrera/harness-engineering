from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    data: bytes
    redacted: bool
    rule_ids: tuple[str, ...]


class SecretRedactor:
    """Best-effort pre-persistence redaction; it is not a substitute for secret isolation."""

    _RULES: tuple[tuple[str, re.Pattern[bytes], bytes], ...] = (
        (
            "authorization_header",
            re.compile(rb"(?im)^(authorization\s*:\s*)([^\r\n]+)$"),
            rb"\1<REDACTED>",
        ),
        (
            "generic_secret_assignment",
            re.compile(
                rb"(?i)\b(api[_-]?key|token|secret|password|passwd|access[_-]?token)\b"
                rb"(\s*[:=]\s*)"
                rb"([^\s,;\}\]\"']{4,}|\"[^\"]{4,}\"|'[^']{4,}')"
            ),
            rb"\1\2<REDACTED>",
        ),
        (
            "github_token",
            re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
            b"<REDACTED_GITHUB_TOKEN>",
        ),
        (
            "aws_access_key",
            re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
            b"<REDACTED_AWS_KEY>",
        ),
        (
            "private_key",
            re.compile(
                rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
                rb"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                re.DOTALL,
            ),
            b"<REDACTED_PRIVATE_KEY>",
        ),
    )

    def __init__(self, *, literals: Iterable[bytes] = (), extended: bool = False) -> None:
        # Values configured for the whole store (the environment a provider receives through
        # passEnv or fromEnv); short values are left alone so that "1" or "true" does not
        # erase every occurrence in an output.
        self.literals = tuple(
            sorted(
                {item for item in literals if len(item) >= MIN_LITERAL_BYTES},
                key=lambda item: (-len(item), item),
            )
        )
        self.extended = extended

    @property
    def configured(self) -> bool:
        """Whether redaction beyond the 1.0.0 rules is configured."""
        return bool(self.literals) or self.extended

    def redact(self, data: bytes, extra_literals: Iterable[bytes] = ()) -> RedactionResult:
        result = data
        applied: list[str] = []
        for rule_id, pattern, replacement in self._RULES:
            result, count = pattern.subn(replacement, result)
            if count:
                applied.append(rule_id)
        configured, configured_rules = self._configured(result)
        result = configured
        applied.extend(configured_rules)
        for index, literal in enumerate(extra_literals):
            if literal and literal in result:
                result = result.replace(literal, b"<REDACTED_LITERAL>")
                applied.append(f"literal_{index}")
        return RedactionResult(result, bool(applied), tuple(applied))

    def redact_configured_text(self, text: str) -> str:
        """Apply only the redaction configured for the store (provider environment values
        and, with ``runtime.extendedRedaction``, the extended rules) to a text that is not an
        artifact, such as an agent's summary recorded in an event."""
        if not self.configured:
            return text
        data, _ = self._configured(text.encode("utf-8"))
        return data.decode("utf-8", "replace")

    def _configured(self, data: bytes) -> tuple[bytes, list[str]]:
        applied: list[str] = []
        if self.extended:
            for rule_id, pattern, replacement in EXTENDED_RULES:
                data, count = pattern.subn(replacement, data)
                if count:
                    applied.append(rule_id)
        for literal in self.literals:
            if literal in data:
                data = data.replace(literal, b"<REDACTED_ENV>")
                if "provider_environment" not in applied:
                    applied.append("provider_environment")
        return data, applied


MIN_LITERAL_BYTES = 8
"""Environment values shorter than this are not redacted as literals."""

EXTENDED_RULES: tuple[tuple[str, re.Pattern[bytes], bytes], ...] = (
    # runtime.extendedRedaction (since 1.1)
    (
        "anthropic_api_key",
        re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{16,}"),
        b"<REDACTED_API_KEY>",
    ),
    (
        "openai_api_key",
        re.compile(rb"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
        b"<REDACTED_API_KEY>",
    ),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"), b"<REDACTED_API_KEY>"),
    ("slack_token", re.compile(rb"\bxox[abposr]-[A-Za-z0-9-]{10,}"), b"<REDACTED_SLACK_TOKEN>"),
    (
        "json_web_token",
        re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        b"<REDACTED_JWT>",
    ),
    (
        "url_credentials",
        re.compile(rb"(\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://[^:/\s@]{1,256}:)[^@\s/]{1,256}@"),
        rb"\1<REDACTED>@",
    ),
)
