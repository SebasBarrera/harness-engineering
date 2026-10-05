"""The agent's structured self-report (``provenance.selfReport``, since 1.1).

A command provider answers with an optional ``selfReport`` object next to ``status``; a built-in
adapter asks the agent to end its answer with a fenced JSON block holding
``{"harnessSelfReport": {...}}``. Either way the report is read leniently: what can be read is
kept, what cannot is listed in ``problems``. It is never a protocol error and never a check: the
run does not depend on it."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from governed_harness.domain.models import SelfReportItem

SELF_REPORT_FIELDS = (
    "assumptions",
    "alternativesDiscarded",
    "lowConfidenceAreas",
    "unrequestedChanges",
)
MAX_ITEMS = 50
MAX_TEXT = 1000

REQUEST_BLOCK: dict[str, Any] = {
    "requested": True,
    "fields": list(SELF_REPORT_FIELDS),
    "note": (
        "Optional. Answer with selfReport: {assumptions: [text], alternativesDiscarded: [text], "
        "lowConfidenceAreas: [{path, description}], unrequestedChanges: [{path, description}]}. "
        "It is recorded as reported by the agent, not as verification."
    ),
}
"""What the request of a command provider carries under ``selfReport``."""

PROMPT_INSTRUCTIONS = (
    "When you finish, end your answer with a fenced JSON code block (```json) containing one "
    'object {"harnessSelfReport": {"assumptions": [...], "alternativesDiscarded": [...], '
    '"lowConfidenceAreas": [{"path": "...", "description": "..."}], '
    '"unrequestedChanges": [{"path": "...", "description": "..."}]}}. List the assumptions you '
    "made, the alternatives you discarded, the places where you are not confident, and every "
    "change you made that the task did not ask for. Use empty lists when there is nothing to "
    "report. The harness records it as your report; it does not replace verification."
)

_FENCED_JSON = re.compile(r"```(?:json)?\s*\n(?P<body>\{.*?\})\s*\n```", re.DOTALL)


@dataclass(frozen=True)
class ParsedSelfReport:
    assumptions: tuple[str, ...]
    alternatives_discarded: tuple[str, ...]
    low_confidence_areas: tuple[SelfReportItem, ...]
    unrequested_changes: tuple[SelfReportItem, ...]
    problems: tuple[str, ...]

    def paths(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.path
                for item in (*self.low_confidence_areas, *self.unrequested_changes)
                if item.path
            )
        )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:MAX_TEXT] or None


def _texts(raw: Any, name: str, problems: list[str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"{name} is not a list")
        return ()
    values = []
    for item in raw[:MAX_ITEMS]:
        text = _text(item)
        if text is None:
            problems.append(f"{name} has an entry that is not text")
            continue
        values.append(text)
    if len(raw) > MAX_ITEMS:
        problems.append(f"{name}: {len(raw) - MAX_ITEMS} entries beyond {MAX_ITEMS} were dropped")
    return tuple(values)


def _items(raw: Any, name: str, problems: list[str]) -> tuple[SelfReportItem, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"{name} is not a list")
        return ()
    items = []
    for entry in raw[:MAX_ITEMS]:
        if isinstance(entry, str):
            text = _text(entry)
            if text:
                items.append(SelfReportItem(description=text))
            continue
        if isinstance(entry, dict):
            description = _text(entry.get("description"))
            path = entry.get("path")
            path_text = path.strip()[:MAX_TEXT] if isinstance(path, str) and path.strip() else None
            if description is None and path_text is not None:
                description = "(no description)"
            if description is not None:
                items.append(SelfReportItem(path=path_text, description=description))
                continue
        problems.append(f"{name} has an entry without a description")
    if len(raw) > MAX_ITEMS:
        problems.append(f"{name}: {len(raw) - MAX_ITEMS} entries beyond {MAX_ITEMS} were dropped")
    return tuple(items)


def parse_self_report(raw: Any) -> ParsedSelfReport | None:
    """The report in ``raw`` (an object with the four fields), ``None`` when there is none."""
    if raw is None:
        return None
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ParsedSelfReport((), (), (), (), ("the self-report is not an object",))
    unknown = sorted(set(raw) - set(SELF_REPORT_FIELDS))
    if unknown:
        problems.append(f"unknown field(s) ignored: {', '.join(unknown)}")
    return ParsedSelfReport(
        assumptions=_texts(raw.get("assumptions"), "assumptions", problems),
        alternatives_discarded=_texts(
            raw.get("alternativesDiscarded"), "alternativesDiscarded", problems
        ),
        low_confidence_areas=_items(raw.get("lowConfidenceAreas"), "lowConfidenceAreas", problems),
        unrequested_changes=_items(raw.get("unrequestedChanges"), "unrequestedChanges", problems),
        problems=tuple(problems),
    )


def extract_from_text(text: str) -> Any:
    """The ``harnessSelfReport`` object of the last fenced JSON block of an agent's answer that
    has one, or ``None``."""
    found: Any = None
    for match in _FENCED_JSON.finditer(text):
        try:
            value = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "harnessSelfReport" in value:
            found = value["harnessSelfReport"]
    return found
