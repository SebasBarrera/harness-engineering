"""The findings of a run as a GitLab Code Quality report (#56).

GitLab shows a Code Quality report (``artifacts:reports:codequality`` in ``.gitlab-ci.yml``) in
the merge request widget and the diff. The format is a JSON array of issues with a description,
a check name, a fingerprint, a severity (``info``, ``minor``, ``major``, ``critical``,
``blocker``) and a location (path and begin line). The fingerprint is the finding's stable
fingerprint (``harnessFinding/v1``), the same one the SARIF report carries, so the widget
tracks one problem across pipelines. Findings without a path are reported on the repository
root, as GitLab needs a path."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from governed_harness.domain.models import Finding
from governed_harness.reporting.fingerprint import finding_fingerprint

CODE_QUALITY_FILE = "gl-code-quality-report.json"
"""The file name the CI templates attach as ``artifacts:reports:codequality``."""

_SEVERITY = {
    "INFO": "info",
    "LOW": "minor",
    "MEDIUM": "major",
    "HIGH": "critical",
    "CRITICAL": "blocker",
}


def code_quality_issues(findings: Iterable[Finding]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for finding in findings:
        location = finding.location
        path = location.path if location and location.path else "."
        line = location.start_line if location and location.start_line else 1
        issues.append(
            {
                "type": "issue",
                "check_name": finding.rule_id,
                "description": finding.message,
                "categories": [finding.category or "Style"],
                "severity": _SEVERITY.get(str(finding.severity), "info"),
                "fingerprint": finding_fingerprint(finding).removeprefix("sha256:"),
                "location": {"path": path, "lines": {"begin": line}},
            }
        )
    return issues


def render_code_quality(findings: Iterable[Finding]) -> bytes:
    return (json.dumps(code_quality_issues(findings), indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


__all__ = ["CODE_QUALITY_FILE", "code_quality_issues", "render_code_quality"]
