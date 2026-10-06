"""SARIF 2.1.0 results with the tool and rule versions that produced them.

A finding is only reproducible when the record says which analyzer version and which rule
version raised it. ``governed_harness.validators.parsers.parse_sarif`` already extracts rule,
message, level, path and line; this module adds the provenance it drops, aligned result by result
with that parser's order (runs, then results)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governed_harness.validators.parsers import parse_sarif


@dataclass(frozen=True)
class SarifResult:
    rule_id: str
    level: str
    message: str
    path: str | None
    line: int | None
    tool: str
    tool_version: str | None
    rule_version: str | None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if isinstance(value, str | int | float) and not isinstance(value, bool) and str(value):
        return str(value)
    return None


def _sanitized(data: Mapping[str, Any]) -> dict[str, Any] | None:
    """The document with only well-formed runs and results, or None when it is not SARIF 2.x.

    ``parse_sarif`` trusts the shape of the document; a tool's report is agent-influenced input,
    so malformed entries are dropped here instead of raising there."""
    if not str(data.get("version", "")).startswith("2.") or not isinstance(data.get("runs"), list):
        return None
    runs: list[dict[str, Any]] = []
    for run in data["runs"]:
        if not isinstance(run, Mapping):
            continue
        tool = _mapping(run.get("tool"))
        driver = _mapping(tool.get("driver"))
        results = run.get("results")
        runs.append(
            {
                **run,
                "tool": {**tool, "driver": dict(driver)},
                "results": [
                    _sanitized_result(result)
                    for result in (results if isinstance(results, list) else [])
                    if isinstance(result, Mapping)
                ],
            }
        )
    return {**data, "runs": runs}


def _sanitized_result(result: Mapping[str, Any]) -> dict[str, Any]:
    locations = result.get("locations")
    first = locations[0] if isinstance(locations, list) and locations else {}
    physical = _mapping(_mapping(first).get("physicalLocation"))
    artifact = {
        key: value
        for key, value in _mapping(physical.get("artifactLocation")).items()
        if key != "uri" or isinstance(value, str)
    }
    return {
        **result,
        "message": dict(_mapping(result.get("message"))),
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": artifact,
                    "region": dict(_mapping(physical.get("region"))),
                }
            }
        ],
    }


def _rule_for(result: Mapping[str, Any], rules: Sequence[Any]) -> Mapping[str, Any]:
    index = result.get("ruleIndex")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(rules):
        return _mapping(rules[index])
    rule_id = result.get("ruleId")
    for rule in rules:
        if isinstance(rule, Mapping) and rule.get("id") == rule_id:
            return rule
    return {}


def read_sarif(text: str, workspace: Path) -> list[SarifResult]:
    """The results of a SARIF 2.x document, with tool name and version and rule version.

    Invalid JSON or a non-SARIF document yields ``[]``: the caller records "no parsable report",
    it does not crash. The rule version is the rule's ``properties.version`` (or
    ``properties.ruleVersion``) and falls back to the driver version."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, Mapping):
        return []
    document = _sanitized(data)
    if document is None:
        return []
    parsed = parse_sarif(document, workspace)
    results: list[SarifResult] = []
    position = 0
    for run in document["runs"]:
        driver = _mapping(run["tool"]["driver"])
        tool = str(driver.get("name") or "sarif")
        tool_version = _text(driver.get("semanticVersion")) or _text(driver.get("version"))
        rules = driver.get("rules")
        rule_list: Sequence[Any] = rules if isinstance(rules, list) else []
        for result in run["results"]:
            issue = parsed[position]
            position += 1
            rule = _rule_for(result, rule_list)
            properties = _mapping(rule.get("properties"))
            rule_version = (
                _text(properties.get("version"))
                or _text(properties.get("ruleVersion"))
                or tool_version
            )
            rule_id = issue.rule
            if not result.get("ruleId") and _text(rule.get("id")):
                rule_id = str(rule["id"])
            results.append(
                SarifResult(
                    rule_id=rule_id,
                    level=issue.level,
                    message=issue.message,
                    path=issue.path,
                    line=issue.line,
                    tool=tool,
                    tool_version=tool_version,
                    rule_version=rule_version,
                )
            )
    return results
