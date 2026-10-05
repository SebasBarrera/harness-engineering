"""Feedback for a correction attempt and the transient-failure test for command providers.

Both work on what the harness already recorded (validation results, findings and their redacted
artifacts); neither runs a check of its own."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Literal

from governed_harness.domain.enums import FindingSeverity, ResultStatus
from governed_harness.domain.models import (
    FEEDBACK_MAX_FINDINGS,
    FEEDBACK_STREAM_CHARS,
    FEEDBACK_TEXT_CHARS,
    FEEDBACK_TOTAL_CHARS,
    FeedbackDecision,
    FeedbackFinding,
    FeedbackGate,
    FeedbackValidator,
    Finding,
    ProviderFeedback,
    ValidationResult,
)
from governed_harness.evidence import LocalArtifactStore

_SEVERITY_ORDER = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.HIGH: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.LOW: 3,
    FindingSeverity.INFO: 4,
}

TRANSIENT_SCAN_BYTES = 65536
"""Bytes read from the end of each provider stream when looking for a transient cause."""


def tail(text: str, limit: int) -> tuple[str, bool]:
    """The last ``limit`` characters of ``text`` and whether anything was cut."""
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    return text[-limit:], True


def head(text: str, limit: int = FEEDBACK_TEXT_CHARS) -> str:
    """``text`` cut to ``limit`` characters, marking the cut with an ellipsis."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def failing_validations(validations: Iterable[ValidationResult]) -> list[ValidationResult]:
    """Mandatory validation results that did not pass, in their recorded order."""
    return [
        item for item in validations if item.mandatory and item.status is not ResultStatus.PASSED
    ]


def verification_reason_codes(failing: Sequence[ValidationResult]) -> tuple[str, ...]:
    """Reason codes in the form the gate engine uses: ``<validatorId>_<STATUS>``."""
    return tuple(f"{item.validator_id}_{item.status}" for item in failing)


class FeedbackBuilder:
    def __init__(self, artifacts: LocalArtifactStore) -> None:
        self.artifacts = artifacts

    def build(
        self,
        *,
        trigger: Literal["VERIFICATION_FAILED", "CHANGES_REQUESTED"],
        attempt: int,
        change_set_digest: str,
        gate: FeedbackGate,
        validations: Sequence[ValidationResult],
        findings: Sequence[Finding],
        decision: FeedbackDecision | None = None,
    ) -> ProviderFeedback:
        ordered = sorted(findings, key=lambda item: _SEVERITY_ORDER.get(item.severity, 5))
        kept = ordered[:FEEDBACK_MAX_FINDINGS]
        return ProviderFeedback(
            attempt=attempt,
            trigger=trigger,
            change_set_digest=change_set_digest,
            gate=gate,
            findings=tuple(
                FeedbackFinding(
                    rule_id=item.rule_id,
                    severity=item.severity,
                    validator_id=item.validator_id,
                    location=item.location,
                    message=head(item.message),
                )
                for item in kept
            ),
            omitted_findings=len(ordered) - len(kept),
            validators=self._validators(failing_validations(validations)),
            decision=decision,
        )

    def _validators(self, failing: Sequence[ValidationResult]) -> tuple[FeedbackValidator, ...]:
        budget = FEEDBACK_TOTAL_CHARS
        items: list[FeedbackValidator] = []
        for result in failing:
            report = self._report(result)
            streams: dict[str, tuple[str, bool]] = {}
            for name in ("stdout", "stderr"):
                text = self._text(report.get(f"{name}Ref"))
                excerpt, cut = tail(text, min(FEEDBACK_STREAM_CHARS, budget))
                budget -= len(excerpt)
                streams[name] = (excerpt, cut or bool(report.get(f"{name}Truncated")))
            exit_code = report.get("exitCode")
            items.append(
                FeedbackValidator(
                    validator_id=result.validator_id,
                    status=result.status,
                    summary=head(result.summary),
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                    stdout=streams["stdout"][0],
                    stderr=streams["stderr"][0],
                    stdout_truncated=streams["stdout"][1],
                    stderr_truncated=streams["stderr"][1],
                )
            )
        return tuple(items)

    def _report(self, result: ValidationResult) -> dict[str, object]:
        """The validation report of a command validator (its first evidence reference)."""
        try:
            value = json.loads(self.artifacts.get(result.evidence_refs[0]))
        except (OSError, ValueError, KeyError, IndexError):
            return {}
        return value if isinstance(value, dict) else {}

    def _text(self, uri: object) -> str:
        if not isinstance(uri, str):
            return ""
        try:
            return self.artifacts.get(uri).decode("utf-8", "replace")
        except (OSError, ValueError, KeyError):
            return ""


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    # A numeric code such as 429 must not match inside a longer number (a line 4290 of a log).
    body = re.escape(pattern.strip())
    prefix = r"(?<![0-9])" if pattern.strip()[:1].isdigit() else ""
    suffix = r"(?![0-9])" if pattern.strip()[-1:].isdigit() else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


def transient_cause(texts: Iterable[str], patterns: Sequence[str]) -> str | None:
    """The first configured pattern found in the provider's streams, or ``None``."""
    compiled = [(pattern, _pattern_regex(pattern)) for pattern in patterns]
    for text in texts:
        for pattern, regex in compiled:
            if regex.search(text):
                return pattern
    return None
