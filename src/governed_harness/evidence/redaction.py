from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


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

    def redact(self, data: bytes, extra_literals: Iterable[bytes] = ()) -> RedactionResult:
        result = data
        applied: list[str] = []
        for rule_id, pattern, replacement in self._RULES:
            result, count = pattern.subn(replacement, result)
            if count:
                applied.append(rule_id)
        for index, literal in enumerate(extra_literals):
            if literal and literal in result:
                result = result.replace(literal, b"<REDACTED_LITERAL>")
                applied.append(f"literal_{index}")
        return RedactionResult(result, bool(applied), tuple(applied))
