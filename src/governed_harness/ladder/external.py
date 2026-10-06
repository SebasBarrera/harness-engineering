"""Reading external evidence that closes a deferred verification (#55, item 2).

Three formats, recognized by content or named explicitly:

* JUnit XML: passes when it reports at least one test case and none failed or errored
  (``case`` narrows it to the test cases whose name or class name contains a text);
* SARIF 2.1.0: passes when no result has the level ``error`` (results without a level count
  as ``warning``, as the format says);
* a CI status JSON: ``state`` (or ``conclusion``) of a commit status or check run, with the
  commit it is about (``sha``, ``commit`` or ``head_sha``). ``success``/``passed`` pass;
  ``failure``, ``error``, ``cancelled``, ``timed_out`` fail; a pending status is not evidence.

XML is parsed without DTDs or entity expansion, as the output parsers do."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree  # nosec B405 - DTDs and entities are refused below
from dataclasses import dataclass
from typing import Any, Literal

EvidenceKind = Literal["junit", "sarif", "ci-status"]
_PASSING = {"success", "passed", "pass", "succeeded"}
_FAILING = {"failure", "failed", "error", "cancelled", "canceled", "timed_out", "action_required"}


class EvidenceFormatError(ValueError):
    """The file is not evidence the harness can read."""


@dataclass(frozen=True)
class EvidenceVerdict:
    kind: EvidenceKind
    passed: bool
    summary: str
    commit: str | None = None
    details: dict[str, Any] | None = None


def detect_kind(data: bytes) -> EvidenceKind:
    text = data.decode("utf-8", "replace").lstrip()
    if text.startswith("<"):
        return "junit"
    try:
        value = json.loads(text)
    except ValueError as error:
        raise EvidenceFormatError("the file is neither JUnit XML nor JSON") from error
    if isinstance(value, dict) and "runs" in value:
        return "sarif"
    if isinstance(value, dict):
        return "ci-status"
    raise EvidenceFormatError("the JSON file is neither SARIF nor a CI status object")


def _junit_root(data: bytes) -> ElementTree.Element:
    text = data.decode("utf-8", "replace")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise EvidenceFormatError("JUnit XML with a DTD or entities is refused")
    try:
        root = ElementTree.fromstring(text)  # nosec B314 - DTDs and entities refused above
    except ElementTree.ParseError as error:
        raise EvidenceFormatError(f"not well-formed XML: {error}") from error
    if root.tag not in {"testsuites", "testsuite"}:
        raise EvidenceFormatError("not a JUnit report (no testsuites or testsuite element)")
    return root


def _junit_counts(root: ElementTree.Element, case: str | None) -> tuple[int, int, int, list[str]]:
    """``(total, failed, skipped, failing ids)`` of the test cases whose id contains
    ``case``."""
    total = failed = skipped = 0
    failing: list[str] = []
    for item in root.iter("testcase"):
        name = item.get("name") or "test"
        classname = item.get("classname") or ""
        identifier = f"{classname}::{name}" if classname else name
        if case is not None and case not in identifier:
            continue
        total += 1
        if item.find("skipped") is not None:
            skipped += 1
        if item.find("failure") is not None or item.find("error") is not None:
            failed += 1
            failing.append(identifier)
    return total, failed, skipped, failing


def read_junit(data: bytes, case: str | None = None) -> EvidenceVerdict:
    total, failed, skipped, failing = _junit_counts(_junit_root(data), case)
    executed = total - skipped
    passed = executed > 0 and failed == 0
    scope = f" matching {case!r}" if case is not None else ""
    summary = (
        f"JUnit: {total} test case(s){scope}, {failed} failed or errored, {skipped} skipped"
        + ("" if executed else "; nothing ran")
    )
    return EvidenceVerdict(
        "junit",
        passed,
        summary,
        details={"total": total, "failed": failed, "skipped": skipped, "failing": failing[:20]},
    )


def _sarif_runs(data: bytes) -> list[Any]:
    try:
        value = json.loads(data.decode("utf-8", "replace"))
    except ValueError as error:
        raise EvidenceFormatError("not JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("runs"), list):
        raise EvidenceFormatError("not a SARIF log (no runs)")
    runs: list[Any] = value["runs"]
    return runs


def _sarif_levels(run: dict[str, Any]) -> tuple[int, int]:
    """``(errors, others)`` of a run's results; a result without a level is a warning."""
    levels = [
        result.get("level") or "warning"
        for result in run.get("results") or []
        if isinstance(result, dict)
    ]
    errors = levels.count("error")
    return errors, len(levels) - errors


def read_sarif(data: bytes) -> EvidenceVerdict:
    errors = warnings = 0
    tools: list[str] = []
    for run in _sarif_runs(data):
        if not isinstance(run, dict):
            continue
        driver = (run.get("tool") or {}).get("driver") or {}
        if isinstance(driver, dict) and driver.get("name"):
            tools.append(str(driver["name"]))
        run_errors, run_others = _sarif_levels(run)
        errors += run_errors
        warnings += run_others
    summary = f"SARIF ({', '.join(tools) or 'unknown tool'}): {errors} error(s), {warnings} other"
    return EvidenceVerdict(
        "sarif", errors == 0, summary, details={"errors": errors, "others": warnings}
    )


def read_ci_status(data: bytes) -> EvidenceVerdict:
    try:
        value = json.loads(data.decode("utf-8", "replace"))
    except ValueError as error:
        raise EvidenceFormatError("not JSON") from error
    if not isinstance(value, dict):
        raise EvidenceFormatError("a CI status is a JSON object")
    raw = value.get("conclusion") or value.get("state") or value.get("status")
    state = str(raw or "").strip().lower()
    commit = value.get("sha") or value.get("commit") or value.get("head_sha")
    context = value.get("context") or value.get("name") or "CI"
    if state in _PASSING:
        passed = True
    elif state in _FAILING:
        passed = False
    else:
        raise EvidenceFormatError(
            f"the CI status {state or 'without a state'!r} is not final (success or failure)"
        )
    return EvidenceVerdict(
        "ci-status",
        passed,
        f"CI status {context}: {state}",
        commit=str(commit) if commit else None,
        details={"context": str(context), "state": state, "targetUrl": value.get("target_url")},
    )


def read_evidence(data: bytes, kind: str = "auto", case: str | None = None) -> EvidenceVerdict:
    chosen = detect_kind(data) if kind == "auto" else kind
    if chosen == "junit":
        return read_junit(data, case)
    if chosen == "sarif":
        return read_sarif(data)
    if chosen == "ci-status":
        return read_ci_status(data)
    raise EvidenceFormatError(f"unknown evidence format: {kind}")


__all__ = [
    "EvidenceFormatError",
    "EvidenceKind",
    "EvidenceVerdict",
    "detect_kind",
    "read_ci_status",
    "read_evidence",
    "read_junit",
    "read_sarif",
]
