"""Stable identity of a finding across attempts and runs.

The fingerprint ignores the finding id, the run, the line number and the time: it is the rule,
the validator, the path and the message with whitespace collapsed and digits that look like
line or column numbers removed. The same problem reported again in a later attempt or run gets
the same fingerprint; a different message or file gets another one. It identifies findings in
the review delta, in exception scopes and as SARIF ``partialFingerprints``."""

from __future__ import annotations

import hashlib
import re

from governed_harness.domain.models import Finding

FINGERPRINT_VERSION = "harnessFinding/v1"

_POSITIONS = re.compile(r"(?<=[:(,])\d+|\bline \d+|\bcolumn \d+", re.IGNORECASE)


def normalized_message(message: str) -> str:
    return " ".join(_POSITIONS.sub("#", message).split())


def finding_fingerprint(finding: Finding) -> str:
    path = finding.location.path if finding.location and finding.location.path else ""
    material = "\x1f".join(
        (finding.rule_id, finding.validator_id, path, normalized_message(finding.message))
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
