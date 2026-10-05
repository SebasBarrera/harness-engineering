"""Deterministic parsers of validator output (``verification.outputParsers``).

A failing command validator produced a single finding without a location; the reviewer had to
open the stored output to see which test or which line failed. These parsers read the output
the tools already print and return one issue per reported problem with path, line and the
tool's rule id. Supported formats, recognized by content:

* SARIF 2.1.0 (any tool that emits it), ESLint JSON (``-f json``), Ruff JSON
  (``--output-format json``) and JUnit XML (printed, or written with ``--junitxml``);
* text: Ruff (concise and full formats), Mypy, TypeScript ``tsc`` and the pytest failure
  summary (``FAILED``/``ERROR`` lines, with the line taken from the traceback).

Parsing never runs a command and never changes a validator's status; a format it does not
recognize yields no issue. XML is parsed without DTDs or entity expansion."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree  # nosec B405 - DTDs and entities are refused below
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

Level = Literal["error", "warning", "note"]

MAX_ISSUES = 200
"""Issues kept per validator run; the rest are counted, not recorded."""
_MESSAGE_CHARS = 1000


@dataclass(frozen=True)
class ParsedIssue:
    rule: str
    message: str
    level: Level = "error"
    path: str | None = None
    line: int | None = None
    end_line: int | None = None
    tool: str = ""


def _relative(path: str | None, workspace: Path) -> str | None:
    if not path:
        return None
    path = path.removeprefix("file://")
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(workspace.resolve()).as_posix()
        except (ValueError, OSError):
            return candidate.as_posix()
    return PurePosixPath(path.replace("\\", "/")).as_posix().removeprefix("./")


def _line(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _message(text: Any) -> str:
    value = " ".join(str(text or "").split())
    return value[:_MESSAGE_CHARS] or "no message"


# ----- JSON formats -----------------------------------------------------------------------
_SARIF_LEVELS: dict[str, Level] = {"error": "error", "warning": "warning", "note": "note"}


def parse_sarif(data: dict[str, Any], workspace: Path) -> list[ParsedIssue]:
    issues: list[ParsedIssue] = []
    for run in data.get("runs") or []:
        tool = str(((run.get("tool") or {}).get("driver") or {}).get("name") or "sarif")
        for result in run.get("results") or []:
            location = ((result.get("locations") or [{}])[0] or {}).get("physicalLocation") or {}
            region = location.get("region") or {}
            issues.append(
                ParsedIssue(
                    rule=str(result.get("ruleId") or "result"),
                    message=_message((result.get("message") or {}).get("text")),
                    level=_SARIF_LEVELS.get(str(result.get("level") or "warning"), "warning"),
                    path=_relative((location.get("artifactLocation") or {}).get("uri"), workspace),
                    line=_line(region.get("startLine")),
                    end_line=_line(region.get("endLine")),
                    tool=tool,
                )
            )
    return issues


def parse_eslint_json(data: list[dict[str, Any]], workspace: Path) -> list[ParsedIssue]:
    issues: list[ParsedIssue] = []
    for item in data:
        for message in item.get("messages") or []:
            issues.append(
                ParsedIssue(
                    rule=str(message.get("ruleId") or "parse-error"),
                    message=_message(message.get("message")),
                    level="error" if message.get("severity") == 2 else "warning",
                    path=_relative(item.get("filePath"), workspace),
                    line=_line(message.get("line")),
                    end_line=_line(message.get("endLine")),
                    tool="eslint",
                )
            )
    return issues


def parse_ruff_json(data: list[dict[str, Any]], workspace: Path) -> list[ParsedIssue]:
    return [
        ParsedIssue(
            rule=str(item.get("code") or "syntax-error"),
            message=_message(item.get("message")),
            path=_relative(item.get("filename"), workspace),
            line=_line((item.get("location") or {}).get("row")),
            end_line=_line((item.get("end_location") or {}).get("row")),
            tool="ruff",
        )
        for item in data
    ]


def _parse_json(text: str, workspace: Path) -> list[ParsedIssue] | None:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    if isinstance(data, dict) and "runs" in data and str(data.get("version", "")).startswith("2."):
        return parse_sarif(data, workspace)
    if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        if all("filePath" in item and "messages" in item for item in data):
            return parse_eslint_json(data, workspace)
        if all("code" in item and "filename" in item for item in data):
            return parse_ruff_json(data, workspace)
    if isinstance(data, list) and not data:
        return []
    return None


# ----- JUnit XML --------------------------------------------------------------------------
def parse_junit(text: str, workspace: Path) -> list[ParsedIssue]:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return []
    try:
        root = ElementTree.fromstring(text)  # nosec B314 - DTDs and entities refused above
    except ElementTree.ParseError:
        return []
    issues: list[ParsedIssue] = []
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            name = case.get("name") or "test"
            classname = case.get("classname") or ""
            identifier = f"{classname}::{name}" if classname else name
            text_lines = (node.text or "").strip().splitlines()
            message = node.get("message") or (text_lines[0] if text_lines else kind)
            issues.append(
                ParsedIssue(
                    rule="test-failed" if kind == "failure" else "test-error",
                    message=_message(f"{identifier} {kind}: {_message(message)}"),
                    path=_relative(case.get("file"), workspace),
                    line=_line(case.get("line")),
                    tool="junit",
                )
            )
    return issues


# ----- text formats -----------------------------------------------------------------------
_RUFF_CONCISE = re.compile(
    r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+): (?P<rule>[A-Z]+\d+) (?:\[\*\] )?(?P<msg>.+)$"
)
_RUFF_FULL_HEAD = re.compile(r"^(?P<rule>[A-Z]+\d+) (?:\[\*\] )?(?P<msg>.+)$")
_RUFF_FULL_ARROW = re.compile(r"^\s*-->\s*(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+)\s*$")
_MYPY = re.compile(
    r"^(?P<path>[^\s:][^:]*\.pyi?):(?P<line>\d+)(?::\d+)?: (?P<level>error|warning|note): "
    r"(?P<msg>.+?)(?:\s+\[(?P<code>[a-z0-9-]+)\])?$"
)
_TSC_PAREN = re.compile(
    r"^(?P<path>[^\s(][^(]*)\((?P<line>\d+),\d+\): (?P<level>error|warning) (?P<code>TS\d+): (?P<msg>.+)$"
)
_TSC_PRETTY = re.compile(
    r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):\d+ - (?P<level>error|warning) (?P<code>TS\d+): (?P<msg>.+)$"
)
_PYTEST_SUMMARY = re.compile(r"^(?P<kind>FAILED|ERROR) (?P<node>\S+)(?: - (?P<msg>.*))?$")
_PYTEST_SECTION = re.compile(r"^_{3,} (?:ERROR collecting )?(?P<name>.+?) _{3,}$")


def parse_ruff_text(lines: list[str]) -> list[ParsedIssue]:
    issues: list[ParsedIssue] = []
    pending: tuple[str, str] | None = None
    for line in lines:
        if match := _RUFF_CONCISE.match(line):
            issues.append(
                ParsedIssue(
                    rule=match["rule"],
                    message=_message(match["msg"]),
                    path=match["path"],
                    line=int(match["line"]),
                    tool="ruff",
                )
            )
            pending = None
        elif match := _RUFF_FULL_HEAD.match(line):
            pending = (match["rule"], match["msg"])
        elif pending and (match := _RUFF_FULL_ARROW.match(line)):
            issues.append(
                ParsedIssue(
                    rule=pending[0],
                    message=_message(pending[1]),
                    path=match["path"],
                    line=int(match["line"]),
                    tool="ruff",
                )
            )
            pending = None
    return issues


def parse_mypy_text(lines: list[str]) -> list[ParsedIssue]:
    issues = []
    for line in lines:
        if match := _MYPY.match(line):
            if match["level"] == "note":
                continue
            issues.append(
                ParsedIssue(
                    rule=match["code"] or "error",
                    message=_message(match["msg"]),
                    level="error" if match["level"] == "error" else "warning",
                    path=match["path"],
                    line=int(match["line"]),
                    tool="mypy",
                )
            )
    return issues


def parse_tsc_text(lines: list[str]) -> list[ParsedIssue]:
    issues = []
    for line in lines:
        match = _TSC_PAREN.match(line) or _TSC_PRETTY.match(line)
        if match:
            issues.append(
                ParsedIssue(
                    rule=match["code"],
                    message=_message(match["msg"]),
                    level="error" if match["level"] == "error" else "warning",
                    path=match["path"].strip(),
                    line=int(match["line"]),
                    tool="tsc",
                )
            )
    return issues


def parse_pytest_text(lines: list[str]) -> list[ParsedIssue]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines:
        if match := _PYTEST_SECTION.match(line):
            current = sections.setdefault(match["name"], [])
        elif current is not None:
            current.append(line)
    issues = []
    for line in lines:
        match = _PYTEST_SUMMARY.match(line)
        if not match:
            continue
        node = match["node"]
        path = node.split("::", 1)[0]
        if not path.endswith(".py"):
            continue
        name = node.split("::")[-1] if "::" in node else path
        section = (
            sections.get(name) or sections.get(node.split("::", 1)[-1].replace("::", ".")) or []
        )
        frames = [
            int(found.group(1))
            for text in section
            if (found := re.match(rf"^{re.escape(path)}:(\d+): ", text))
        ]
        kind = "test-failed" if match["kind"] == "FAILED" else "test-error"
        issues.append(
            ParsedIssue(
                rule=kind,
                message=_message(
                    f"{node} {match['kind'].lower()}: {match['msg'] or ''}".rstrip(": ")
                ),
                path=path,
                line=frames[-1] if frames else None,
                tool="pytest",
            )
        )
    return issues


def parse_output(
    stdout: str, stderr: str, workspace: Path, reports: tuple[str, ...] = ()
) -> list[ParsedIssue]:
    """Every issue the output of one validator run reports, in order, at most ``MAX_ISSUES``
    (callers count the rest)."""
    issues: list[ParsedIssue] = []
    for text in (*reports, stdout):
        parsed = _parse_json(text, workspace)
        if parsed is not None:
            issues.extend(parsed)
            continue
        if "<testsuite" in text:
            issues.extend(parse_junit(text, workspace))
    if not issues:
        lines = [line.rstrip() for line in (stdout + "\n" + stderr).splitlines()]
        for parser in (parse_ruff_text, parse_mypy_text, parse_tsc_text, parse_pytest_text):
            issues.extend(parser(lines))
    return [
        ParsedIssue(
            rule=item.rule,
            message=item.message,
            level=item.level,
            path=_relative(item.path, workspace),
            line=item.line,
            end_line=item.end_line,
            tool=item.tool,
        )
        for item in issues
    ]


def report_files(argv: tuple[str, ...], workspace: Path) -> list[Path]:
    """JUnit or SARIF files a validator command writes (``--junitxml=``, ``--junit-xml``,
    ``--output-file``/``-o`` with a .sarif/.xml/.json name), resolved inside the workspace."""
    found: list[Path] = []
    for index, item in enumerate(argv):
        value: str | None = None
        for flag in ("--junitxml=", "--junit-xml=", "--output-file=", "--output="):
            if item.startswith(flag):
                value = item[len(flag) :]
        if item in {"--junitxml", "--junit-xml", "--output-file", "-o"} and index + 1 < len(argv):
            value = argv[index + 1]
        if value and value.endswith((".xml", ".sarif", ".json")):
            path = (workspace / value).resolve()
            try:
                path.relative_to(workspace.resolve())
            except ValueError:
                continue
            found.append(path)
    return found
