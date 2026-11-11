"""Which findings an exception in force covers (``review.exceptions``)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from governed_harness.domain.models import ExceptionRecord, ExceptionScope, Finding
from governed_harness.reporting.fingerprint import finding_fingerprint

EXCEPTION_REASON_PREFIX = "EXCEPTION_APPLIED_"


def scope_matches(scope: ExceptionScope, finding: Finding) -> bool:
    path = finding.location.path if finding.location else None
    if scope.rule_id != finding.rule_id:
        return False
    if scope.path is not None and scope.path != path:
        return False
    return scope.fingerprint is None or scope.fingerprint == finding_fingerprint(finding)


@dataclass
class ExceptionApplication:
    kept: list[Finding] = field(default_factory=list)
    excepted: list[Finding] = field(default_factory=list)
    used: list[ExceptionRecord] = field(default_factory=list)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(f"{EXCEPTION_REASON_PREFIX}{item.exception_id}" for item in self.used)


def apply_exceptions(
    findings: Iterable[Finding], exceptions: Iterable[ExceptionRecord], now: datetime
) -> ExceptionApplication:
    """Split ``findings`` into those that still count and those covered by an exception in
    force at ``now``."""
    active = [item for item in exceptions if item.active_at(now)]
    result = ExceptionApplication()
    used: dict[str, ExceptionRecord] = {}
    for finding in findings:
        record = next(
            (item for item in active if any(scope_matches(s, finding) for s in item.scope)),
            None,
        )
        if record is None:
            result.kept.append(finding)
        else:
            result.excepted.append(finding)
            used.setdefault(record.exception_id, record)
    result.used = list(used.values())
    return result


def exception_ids(reason_codes: Iterable[str]) -> list[str]:
    return [
        code.removeprefix(EXCEPTION_REASON_PREFIX)
        for code in reason_codes
        if code.startswith(EXCEPTION_REASON_PREFIX)
    ]
