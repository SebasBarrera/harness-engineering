"""Checks of test quality: tests that cannot fail, interface methods no test calls, and changed
lines no test executes.

A green suite only means something when its tests assert, reach the code under change and
exercise the contract the task declared. These checks read sources, a diff and the JSON report of
``coverage json``; they never run the tests themselves."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from governed_harness.domain.enums import FindingSeverity

from .model import DiffFile, Issue, is_test_path

CATEGORY = "tests"
_MAX_LISTED_LINES = 15
_ASSERTING_CALLS = frozenset(
    {"pytest.fail", "pytest.raises", "pytest.warns", "raises", "warns", "self.fail"}
)


# ----- assertion-free tests ------------------------------------------------------------------
def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _asserts(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the test body can fail on purpose: an ``assert``, an ``assert*`` call (unittest,
    mock), or a ``pytest.raises``/``pytest.warns``/``pytest.fail`` call or context."""
    for node in ast.walk(function):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            if "assert" in _call_name(node).lower():
                return True
            if _dotted(node.func) in _ASSERTING_CALLS:
                return True
    return False


def _tests(body: Sequence[ast.stmt]) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            yield node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            yield from _tests(node.body)


def assertion_free_tests(files: Mapping[str, str], *, severity: FindingSeverity) -> list[Issue]:
    """One issue per ``test_*`` function or ``Test*`` method that asserts nothing.

    Unparsable test files yield an INFO ``tests.unparsable`` issue: the suite would fail to
    collect them anyway, and a check must not crash on agent output."""
    issues: list[Issue] = []
    for path in sorted(files):
        if not path.endswith(".py") or not is_test_path(path):
            continue
        try:
            tree = ast.parse(files[path], filename=path)
        except (SyntaxError, ValueError) as error:
            issues.append(
                Issue(
                    rule_id="tests.unparsable",
                    severity=FindingSeverity.INFO,
                    message=f"{path} could not be parsed: {error.__class__.__name__}",
                    path=path,
                    line=getattr(error, "lineno", None),
                    category=CATEGORY,
                )
            )
            continue
        for function in _tests(tree.body):
            if not _asserts(function):
                issues.append(
                    Issue(
                        rule_id="tests.assertion-free",
                        severity=severity,
                        message=f"Test {function.name!r} has no assertion; it can only fail by "
                        "raising.",
                        path=path,
                        line=function.lineno,
                        category=CATEGORY,
                        recommendation="Assert on the observable result of the behavior under "
                        "test.",
                    )
                )
    return issues


# ----- interface methods ---------------------------------------------------------------------
def untested_interface_methods(
    names: Sequence[str], test_sources: Mapping[str, str], *, severity: FindingSeverity
) -> list[Issue]:
    """One issue per declared interface method whose name no test calls or reads.

    The match is lexical (``advance(`` or ``.advance``): it proves a test mentions the method,
    not that it covers it, which is why it complements changed-line coverage."""
    issues: list[Issue] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        last = name.rsplit(".", 1)[-1]
        if not last:
            continue
        escaped = re.escape(last)
        pattern = re.compile(rf"(?<![\w.]){escaped}\s*\(|\.{escaped}\b")
        if any(pattern.search(source) for source in test_sources.values()):
            continue
        issues.append(
            Issue(
                rule_id="tests.interface-untested",
                severity=severity,
                message=f"Interface method {name!r} is not called by any test.",
                category=CATEGORY,
                recommendation=f"Add a test that exercises {name}.",
            )
        )
    return issues


# ----- changed-line coverage -----------------------------------------------------------------
def _coverage_entry(path: str, files: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key, entry in files.items():
        normalized = str(key).replace("\\", "/").removeprefix("./")
        if (normalized == path or normalized.endswith("/" + path)) and isinstance(entry, Mapping):
            return entry
    return None


def _line_numbers(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, int) and not isinstance(item, bool)}


def _code(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("#")


def changed_line_coverage(
    diff: Sequence[DiffFile],
    coverage: Mapping[str, Any],
    *,
    minimum_percent: float,
    severity: FindingSeverity,
) -> tuple[float | None, list[Issue]]:
    """The percentage of executable added lines (non-test ``.py`` files) that the tests ran,
    and the issues when it is below ``minimum_percent``.

    Whole-project coverage hides an untested change behind well-tested old code; measuring only
    the changed lines does not. A changed file absent from the report was never imported by the
    tests, which is reported on its own whatever the percentage."""
    files = coverage.get("files")
    measured: Mapping[str, Any] = files if isinstance(files, Mapping) else {}
    executed_total = missing_total = 0
    uncovered: list[tuple[str, list[int]]] = []
    issues: list[Issue] = []
    for file in diff:
        path = (file.new_path or "").replace("\\", "/").removeprefix("./")
        if file.is_deleted or not path.endswith(".py") or is_test_path(path):
            continue
        changed = {line.number for line in file.added if _code(line.text)}
        if not changed:
            continue
        entry = _coverage_entry(path, measured)
        if entry is None:
            issues.append(
                Issue(
                    rule_id="tests.changed-file-not-executed",
                    severity=severity,
                    message=f"{path} changed ({len(changed)} line(s)) but the tests never "
                    "executed it.",
                    path=path,
                    category=CATEGORY,
                    recommendation="Add tests that import and exercise the changed module.",
                )
            )
            continue
        executed = changed & _line_numbers(entry.get("executed_lines"))
        missing = changed & _line_numbers(entry.get("missing_lines"))
        executed_total += len(executed)
        missing_total += len(missing)
        if missing:
            uncovered.append((path, sorted(missing)))
    total = executed_total + missing_total
    percent = executed_total / total * 100 if total else None
    if percent is not None and percent < minimum_percent:
        for path, lines in uncovered:
            listed = ", ".join(str(number) for number in lines[:_MAX_LISTED_LINES])
            more = ", …" if len(lines) > _MAX_LISTED_LINES else ""
            issues.append(
                Issue(
                    rule_id="tests.changed-lines-uncovered",
                    severity=severity,
                    message=f"{len(lines)} changed line(s) of {path} are not executed by the "
                    f"tests (lines {listed}{more}); changed-line coverage {percent:.1f}% is "
                    f"below {minimum_percent:g}%.",
                    path=path,
                    line=lines[0],
                    category=CATEGORY,
                    recommendation="Add tests that reach the listed changed lines.",
                )
            )
    return percent, issues


# ----- pytest node ids -----------------------------------------------------------------------
_SUMMARY_ID = re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+)", re.MULTILINE)
_VERBOSE_ID = re.compile(r"^(\S+::\S+)\s+(?:FAILED|ERROR)\b", re.MULTILINE)


def pytest_node_ids(text: str) -> list[str]:
    """The failing node ids (``path::name``) of a pytest output, in order and without repeats,
    from the short summary (``FAILED path::name - ...``) and from verbose result lines
    (``path::name FAILED``), so two runs can be compared test by test."""
    found: list[tuple[int, str]] = [
        (match.start(), match.group(1)) for match in _SUMMARY_ID.finditer(text)
    ]
    found.extend((match.start(), match.group(1)) for match in _VERBOSE_ID.finditer(text))
    ids: list[str] = []
    for _, node_id in sorted(found):
        if node_id not in ids:
            ids.append(node_id)
    return ids
