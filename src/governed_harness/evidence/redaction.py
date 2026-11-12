from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

PRIVATE_KEY = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    rb"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
PRIVATE_KEY_MARK = b"<REDACTED_PRIVATE_KEY>"


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
            # The value starts after the blanks; when no such value ends its line, it is the
            # last blank before a line end.
            re.compile(
                rb"(?im)^(authorization\s*:(?:\s*(?=\S[^\r\n]*$)|\s*(?=[^\S\r\n]$)))"
                rb"(\S[^\r\n]*|[^\S\r\n])$"
            ),
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
            re.compile(rb"\b(?:gh[pousr]_\w{20,}|github_pat_\w{20,})\b"),
            b"<REDACTED_GITHUB_TOKEN>",
        ),
        (
            "aws_access_key",
            re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
            b"<REDACTED_AWS_KEY>",
        ),
        ("private_key", PRIVATE_KEY, PRIVATE_KEY_MARK),
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

    def redact(
        self, data: bytes, extra_literals: Iterable[bytes] = (), *, code: bool = False
    ) -> RedactionResult:
        """``code``: ``data`` is source code (a diff), where an unquoted value after ``token``,
        ``password`` or ``secret`` is an annotation or an expression (``password: str``,
        ``token = self._token``), not a secret; only a quoted literal is redacted there (F4).
        Everywhere else (tool output, logs, ``.env`` text) unquoted values still are."""
        result = data
        applied: list[str] = []
        for rule_id, pattern, replacement in self._RULES:
            if code and rule_id == "generic_secret_assignment":
                pattern = QUOTED_SECRET_ASSIGNMENT
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

    def redact_json(self, value: Any) -> tuple[Any, tuple[str, ...]]:
        """``value`` with every string in it (object keys included) redacted on its own, and
        the rules applied. The serialized form of a JSON document escapes quotes and
        backslashes, so a rule run over it can cut an escape or span two strings and leave a
        document that no longer parses; redacting the decoded strings cannot. A value of a type
        JSON does not know is redacted in its ``str`` form, the form ``default=str`` stores."""
        applied: list[str] = []
        return self._redact_json_value(value, applied), tuple(applied)

    def _redact_json_value(self, item: Any, applied: list[str]) -> Any:
        if isinstance(item, str):
            return self._redact_json_text(item, applied)
        if isinstance(item, dict):
            return {
                (self._redact_json_text(key, applied) if isinstance(key, str) else key): (
                    self._redact_json_value(entry, applied)
                )
                for key, entry in item.items()
            }
        if isinstance(item, list | tuple):
            return [self._redact_json_value(entry, applied) for entry in item]
        if item is None or isinstance(item, bool | int | float):
            return item
        return self._redact_json_text(str(item), applied)

    def _redact_json_text(self, item: str, applied: list[str]) -> str:
        result = self.redact(item.encode("utf-8", "surrogatepass"))
        if not result.redacted:
            return item
        applied.extend(rule for rule in result.rule_ids if rule not in applied)
        try:
            return result.data.decode("utf-8", "surrogatepass")
        except UnicodeDecodeError:
            return result.data.decode("utf-8", "replace")

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
        # The configured values first: they are exact, the extended rules are patterns.
        for literal in self.literals:
            if literal in data:
                data = data.replace(literal, b"<REDACTED_ENV>")
                if "provider_environment" not in applied:
                    applied.append("provider_environment")
        if self.extended:
            for rule_id, pattern, replacement in EXTENDED_RULES:
                data, count = pattern.subn(replacement, data)
                if count:
                    applied.append(rule_id)
        return data, applied


QUOTED_SECRET_ASSIGNMENT = re.compile(
    rb"(?i)\b(api[_-]?key|token|secret|password|passwd|access[_-]?token)\b"
    rb"(\s*[:=]\s*)"
    rb"(\"[^\"]{4,}\"|'[^']{4,}')"
)
"""``generic_secret_assignment`` in code: the quoted values only."""

MIN_LITERAL_BYTES = 8
"""Environment values shorter than this are not redacted as literals."""

_API_KEY_MARK = b"<REDACTED_API_KEY>"

EXTENDED_RULES: tuple[tuple[str, re.Pattern[bytes], bytes], ...] = (
    # runtime.extendedRedaction (since 2.0)
    (
        "anthropic_api_key",
        re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{16,}"),
        _API_KEY_MARK,
    ),
    (
        "openai_api_key",
        re.compile(rb"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
        _API_KEY_MARK,
    ),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"), _API_KEY_MARK),
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
