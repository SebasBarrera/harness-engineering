"""The output contract of a reviewer and the verdict the harness recomputes (#57).

A reviewer answers with one JSON object (the ``result`` of the provider protocol)::

    {"verdict": "PASS" | "PASS_WARN" | "FAIL",
     "findings": [{"file": "src/a.py", "side": "new" | "old", "line": 12,
                   "rule": "tests.mock-of-subject", "severity": "error" | "suggestion",
                   "issue": "...", "evidence": "the changed line it is about"}],
     "summary": "..."}

An answer that does not follow it is ``UNKNOWN``. The harness never takes the reviewer's verdict:

* a finding outside the reportable locations is dropped and counted;
* a finding on a rule of the catalog keeps the rule's severity at most (a ``warning`` rule
  cannot block);
* a finding on a rule outside the catalog blocks only with concrete evidence: its ``evidence``
  must quote a changed line of the file near the reported line; otherwise it is a suggestion;
* at most N findings are kept, errors first, then by rule priority, file, line and rule;
* the verdict is ``FAIL`` with any error, ``PASS_WARN`` with suggestions only, else ``PASS``."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from governed_harness.review.diff import FileChange, Locations
from governed_harness.review.rules import DEFAULT_PRIORITY, Rule

Verdict = Literal["PASS", "PASS_WARN", "FAIL", "UNKNOWN"]
FindingSeverity = Literal["error", "suggestion"]
VERDICTS: tuple[str, ...] = ("PASS", "PASS_WARN", "FAIL")
SEVERITIES: tuple[str, ...] = ("error", "suggestion")
OUT_OF_CATALOG_PRIORITY = 100
_EVIDENCE_WINDOW = 3
_MAX_TEXT = 1000
_RULE_TEXT = re.compile(r"[^a-z0-9.\-_]")


class ContractError(ValueError):
    """The answer does not follow the output contract: the reviewer's result is UNKNOWN."""


@dataclass(frozen=True)
class ReviewFinding:
    reviewer: str
    file: str
    side: str
    line: int
    rule: str
    severity: FindingSeverity
    issue: str
    evidence: str = ""
    in_catalog: bool = True
    priority: int = DEFAULT_PRIORITY
    source: str = "ai"
    """``ai``, ``tool:harness:CHECK``, ``tool:TOOL`` or ``consistency``."""
    note: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "error"

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (self.file, self.side, self.line, self.rule)

    def sort_key(self) -> tuple[Any, ...]:
        return (not self.blocking, self.priority, self.file, self.side, self.line, self.rule)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "reviewer": self.reviewer,
            "file": self.file,
            "side": self.side,
            "line": self.line,
            "rule": self.rule,
            "severity": self.severity,
            "issue": self.issue,
            "inCatalog": self.in_catalog,
            "priority": self.priority,
            "source": self.source,
        }
        if self.evidence:
            value["evidence"] = self.evidence
        if self.note:
            value["note"] = self.note
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReviewFinding:
        return cls(
            reviewer=str(value["reviewer"]),
            file=str(value["file"]),
            side=str(value["side"]),
            line=int(value["line"]),
            rule=str(value["rule"]),
            severity="error" if value["severity"] == "error" else "suggestion",
            issue=str(value["issue"]),
            evidence=str(value.get("evidence") or ""),
            in_catalog=bool(value.get("inCatalog", True)),
            priority=int(value.get("priority", DEFAULT_PRIORITY)),
            source=str(value.get("source") or "ai"),
            note=str(value.get("note") or ""),
        )


@dataclass
class Normalized:
    """A reviewer's answer after the harness applied the contract."""

    findings: list[ReviewFinding] = field(default_factory=list)
    stated_verdict: str = ""
    summary: str = ""
    dropped_outside: int = 0
    downgraded: int = 0
    truncated: int = 0
    model: str | None = None


def _text(value: Any) -> str:
    return " ".join(str(value).split())[:_MAX_TEXT]


def parse_answer(result: Any) -> tuple[str, list[dict[str, Any]], str, str | None]:
    """Check the shape of an answer; raise ``ContractError`` when it does not follow it."""
    if not isinstance(result, dict):
        raise ContractError("the answer is not a JSON object")
    verdict = result.get("verdict")
    if verdict not in VERDICTS:
        raise ContractError(f"verdict must be one of {', '.join(VERDICTS)}, got {verdict!r}")
    summary = result.get("summary")
    if not isinstance(summary, str):
        raise ContractError("summary must be a string")
    raw = result.get("findings")
    if not isinstance(raw, list):
        raise ContractError("findings must be a list")
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ContractError(f"finding {index} is not an object")
        for name in ("file", "rule", "issue"):
            if not isinstance(entry.get(name), str) or not entry[name].strip():
                raise ContractError(f"finding {index} needs a non-empty {name}")
        if entry.get("side") not in ("new", "old"):
            raise ContractError(f"finding {index}: side must be new or old")
        line = entry.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ContractError(f"finding {index}: line must be a positive integer")
        if entry.get("severity") not in SEVERITIES:
            raise ContractError(f"finding {index}: severity must be error or suggestion")
        evidence = entry.get("evidence", "")
        if evidence is not None and not isinstance(evidence, str):
            raise ContractError(f"finding {index}: evidence must be a string")
        items.append(entry)
    model = result.get("model")
    return str(verdict), items, summary, model if isinstance(model, str) and model else None


def _rule_id(value: str) -> str:
    return _RULE_TEXT.sub("-", value.strip().lower())[:80] or "finding"


def _near(changes: Mapping[str, FileChange], path: str, side: str, line: int) -> list[str]:
    item = changes.get(path)
    if item is None:
        return []
    lines = item.added if side == "new" else item.removed
    return [
        entry.text
        for entry in lines
        if abs(entry.number - line) <= _EVIDENCE_WINDOW and entry.text.strip()
    ]


def concrete(evidence: str, candidates: list[str]) -> bool:
    """Whether ``evidence`` quotes one of the changed lines (whitespace-insensitive)."""
    quoted = " ".join(evidence.split()).strip("`'\" ")
    if len(quoted) < 4:
        return False
    for text in candidates:
        line = " ".join(text.split())
        if quoted in line or (len(line) >= 4 and line in quoted):
            return True
    return False


def normalize(
    reviewer: str,
    result: Any,
    *,
    rules: Mapping[str, Rule],
    locations: Locations,
    changes: Mapping[str, FileChange],
    limit: int,
) -> Normalized:
    """Apply the contract to an answer (``rules``: the reviewer's catalog rules by id)."""
    verdict, items, summary, model = parse_answer(result)
    normalized = Normalized(stated_verdict=verdict, summary=_text(summary), model=model)
    seen: set[tuple[str, str, int, str]] = set()
    for entry in items:
        path = entry["file"].strip().replace("\\", "/").removeprefix("./")
        side, line = entry["side"], int(entry["line"])
        if not locations.allows(path, side, line):
            normalized.dropped_outside += 1
            continue
        rule_id = _rule_id(entry["rule"])
        severity: FindingSeverity = "error" if entry["severity"] == "error" else "suggestion"
        evidence = _text(entry.get("evidence") or "")
        rule = rules.get(rule_id)
        note = ""
        if rule is not None:
            if severity == "error" and not rule.blocking:
                severity, note = "suggestion", "the rule is a warning"
                normalized.downgraded += 1
            priority = rule.priority
        else:
            priority = OUT_OF_CATALOG_PRIORITY
            if severity == "error" and not concrete(evidence, _near(changes, path, side, line)):
                severity = "suggestion"
                note = "outside the catalog without concrete evidence"
                normalized.downgraded += 1
        finding = ReviewFinding(
            reviewer=reviewer,
            file=path,
            side=side,
            line=line,
            rule=rule_id,
            severity=severity,
            issue=_text(entry["issue"]),
            evidence=evidence,
            in_catalog=rule is not None,
            priority=priority,
            note=note,
        )
        if finding.key in seen:
            continue
        seen.add(finding.key)
        normalized.findings.append(finding)
    normalized.findings.sort(key=ReviewFinding.sort_key)
    if len(normalized.findings) > limit:
        normalized.truncated = len(normalized.findings) - limit
        normalized.findings = normalized.findings[:limit]
    return normalized


def verdict_of(findings: list[ReviewFinding]) -> Verdict:
    if any(item.blocking for item in findings):
        return "FAIL"
    if findings:
        return "PASS_WARN"
    return "PASS"


def contract_text(limit: int) -> str:
    """The fixed output contract every reviewer request carries."""
    return (
        "Answer with exactly one JSON object on standard output: "
        '{"status": "PASSED", "summary": "<one sentence>", "result": {"verdict": "PASS" | '
        '"PASS_WARN" | "FAIL", "findings": [{"file": "<path>", "side": "new" | "old", '
        '"line": 1, "rule": "<rule id>", "severity": "error" | "suggestion", "issue": '
        '"<what is wrong>", "evidence": "<the changed line it is about>"}], "summary": '
        '"<one sentence>"}}. Report only on the reportable locations of the request: side new '
        "is a line the change added (its number in the new file), side old a line it removed "
        "(its number in the old file); anything else is dropped. Use the rule ids listed above; "
        "a finding on another rule is an error only when its evidence quotes the changed line. "
        f"At most {limit} findings, errors first in rule priority order. Prefer false "
        "negatives: report only what you confirmed within the rule's budget. The harness "
        "recomputes the verdict from the findings. This request is read-only: do not change "
        "any file."
    )


__all__ = [
    "OUT_OF_CATALOG_PRIORITY",
    "SEVERITIES",
    "VERDICTS",
    "ContractError",
    "Normalized",
    "ReviewFinding",
    "Verdict",
    "concrete",
    "contract_text",
    "normalize",
    "parse_answer",
    "verdict_of",
]
