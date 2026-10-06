"""Detector of weakened controls in a ChangeSet diff.

An agent asked to make a build green can do so by removing what makes it red: deleting tests,
dropping assertions, skipping cases, silencing linters, lowering coverage thresholds or turning a
failing command into a successful one. None of that is visible in a passing test run, so this
check reads the diff itself. It is a heuristic over added and removed lines (the diff carries no
context lines), tuned to point a reviewer at the line, not to prove intent.

Documentation files (``.md``, ``.rst``, ``.adoc``) are skipped by the line-pattern rules: prose
that *mentions* ``|| true`` or ``# noqa`` weakens nothing."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Literal

from governed_harness.domain.enums import FindingSeverity

from .model import DiffFile, DiffLine, Issue, is_test_path

CATEGORY = "weakened-controls"
_QUOTE_CHARS = 160
_DOC_SUFFIXES = (".md", ".rst", ".adoc")

_RECOMMENDATIONS = {
    "weakened.test-deleted": "Restore the test or justify its removal in the ChangeSet rationale.",
    "weakened.assert-removed": "Keep the assertions; a test without them cannot fail.",
    "weakened.skip-added": "Fix the failing case instead of skipping it, or record an approved exception.",
    "weakened.suppression-added": "Fix the reported problem instead of suppressing the tool.",
    "weakened.threshold-changed": "Keep the quality threshold or strictness; lowering it needs a human decision.",
    "weakened.broad-except": "Catch the specific exception and handle or re-raise it.",
    "weakened.error-to-success": "Let the failure propagate; do not convert an error into success.",
}

# ----- tests ---------------------------------------------------------------------------------
_PY_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w*)\s*\(")
_JS_TEST = re.compile(r"(?<![\w.])(?:it|test)\s*\(\s*([\"'`])(.+?)\1")
_ASSERTION = re.compile(
    r"\bassert\s|\bassert\(|self\.assert|assert_called|pytest\.raises|\bexpect\("
)
_SKIP = re.compile(
    r"pytest\.mark\.skip\b|pytest\.mark\.skipif\b|pytest\.mark\.xfail\b|pytest\.skip\("
    r"|@unittest\.skip|unittest\.expectedFailure"
    r"|(?<![\w.])(?:it|test|describe)\.skip\(|(?<![\w.])xit\(|(?<![\w.])xdescribe\("
)
_SUPPRESSION = re.compile(
    r"#\s*noqa\b|#\s*nosec\b|#\s*type:\s*ignore\b|#\s*pragma:\s*no\s+cover\b"
    r"|eslint-disable|@ts-ignore\b|@ts-expect-error\b|#\s*pylint:\s*disable\b"
)

# ----- configuration -------------------------------------------------------------------------
_CONFIG_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        ".coveragerc",
        "mypy.ini",
        "ruff.toml",
        ".ruff.toml",
        "pytest.ini",
        "package.json",
    }
)
_THRESHOLD = re.compile(
    r"(?P<key>--cov-fail-under|fail[_-]under|minimumPercent|coverageThreshold|threshold"
    r"|max[_-]complexity|branches|lines|statements|functions)(?![\w-])"
    r"[\"']?\s*[:=]?\s*[\"']?(?P<number>\d+(?:\.\d+)?)"
)
_STRICT = re.compile(r"[\"']?\bstrict[\"']?\s*[:=]\s*true\b|--strict(?:-[a-z]+)*\b", re.IGNORECASE)
_CHECK_TOOL = re.compile(r"\b(pytest|ruff|mypy|eslint|tsc|bandit)\b|\bnpm\s+(?:run\s+)?test\b")
_NOT_A_RUN = re.compile(
    r"^\s*(?:-\s*)?(?:name|uses)\s*:|^\s*#|^\s*(?:echo|cat|printf)\b|\binstall\b|\bnpm\s+ci\b"
)

# ----- error handling ------------------------------------------------------------------------
_EXCEPT = re.compile(r"^\s*except\s*(?::|\(?\s*(?:Exception|BaseException)\b[^:]*:)(?P<rest>.*)$")
_SWALLOW = re.compile(r"^(?:pass|\.\.\.|continue|return|return\s+None)\s*(?:#.*)?$")
_ERROR_TO_SUCCESS = re.compile(
    r"\|\|\s*true\b|\|\|\s*exit\s+0\b|continue-on-error:\s*true\b|--exit-zero\b"
    r"|\bcheck\s*=\s*False\b|\bignore_errors\s*[=:]\s*true\b|\breturncode\s*=(?!=)\s*0\b",
    re.IGNORECASE,
)
_EXIT_ZERO = re.compile(r"(?<![\w.])(?:sys\.)?exit\(\s*0\s*\)")


def _quote(text: str) -> str:
    value = text.strip()
    return value if len(value) <= _QUOTE_CHARS else value[: _QUOTE_CHARS - 1] + "…"


def _issue(
    rule: str, severity: FindingSeverity, message: str, path: str, line: int | None
) -> Issue:
    return Issue(
        rule_id=rule,
        severity=severity,
        message=message,
        path=path,
        line=line,
        category=CATEGORY,
        recommendation=_RECOMMENDATIONS[rule],
    )


def _is_doc(path: str) -> bool:
    return path.lower().endswith(_DOC_SUFFIXES)


def _is_config(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    return (
        name in _CONFIG_NAMES
        or name.startswith((".eslintrc", "jest.config."))
        or name.endswith((".yml", ".yaml"))
    )


def _is_workflow(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return ".github/workflows/" in normalized or normalized.endswith(".gitlab-ci.yml")


def _test_names(lines: Iterable[DiffLine]) -> list[tuple[str, DiffLine]]:
    names: list[tuple[str, DiffLine]] = []
    for line in lines:
        python = _PY_TEST_DEF.match(line.text)
        if python:
            names.append((python.group(1), line))
            continue
        javascript = _JS_TEST.search(line.text)
        if javascript:
            names.append((javascript.group(2), line))
    return names


def _deleted_tests(file: DiffFile) -> list[Issue]:
    path = file.path
    if not is_test_path(path):
        return []
    if file.is_deleted:
        return [
            _issue(
                "weakened.test-deleted",
                FindingSeverity.HIGH,
                f"Test file {path} was deleted.",
                path,
                None,
            )
        ]
    added = {name for name, _ in _test_names(file.added)}
    return [
        _issue(
            "weakened.test-deleted",
            FindingSeverity.HIGH,
            f"Test {name!r} was removed and not re-added: {_quote(line.text)!r}",
            path,
            line.number,
        )
        for name, line in _test_names(file.removed)
        if name not in added
    ]


def _is_comment(text: str) -> bool:
    return text.lstrip().startswith(("#", "//"))


def _removed_assertions(file: DiffFile) -> list[Issue]:
    if file.is_deleted or not is_test_path(file.path):
        return []
    removed = [
        line for line in file.removed if not _is_comment(line.text) and _ASSERTION.search(line.text)
    ]
    added = [
        line for line in file.added if not _is_comment(line.text) and _ASSERTION.search(line.text)
    ]
    if len(removed) <= len(added):
        return []
    first = removed[0]
    return [
        _issue(
            "weakened.assert-removed",
            FindingSeverity.HIGH,
            f"{len(removed)} assertion line(s) removed and {len(added)} added in {file.path}; "
            f"first removed: {_quote(first.text)!r}",
            file.path,
            first.number,
        )
    ]


def _pattern_lines(
    file: DiffFile, pattern: re.Pattern[str], rule: str, severity: FindingSeverity, what: str
) -> list[Issue]:
    return [
        _issue(rule, severity, f"{what}: {_quote(line.text)!r}", file.path, line.number)
        for line in file.added
        if pattern.search(line.text)
    ]


def _threshold_key(raw: str) -> str:
    key = raw.lower().replace("_", "-")
    return "fail-under" if key == "--cov-fail-under" else key


def _thresholds(lines: Iterable[DiffLine]) -> list[tuple[str, float, DiffLine]]:
    found: list[tuple[str, float, DiffLine]] = []
    for line in lines:
        if _is_comment(line.text):
            continue
        for match in _THRESHOLD.finditer(line.text):
            found.append((_threshold_key(match.group("key")), float(match.group("number")), line))
    return found


def _threshold_changes(file: DiffFile) -> list[Issue]:
    path = file.path
    if not _is_config(path):
        return []
    issues: list[Issue] = []
    added_by_key: dict[str, list[float]] = {}
    for key, number, _ in _thresholds(file.added):
        added_by_key.setdefault(key, []).append(number)
    seen: dict[str, int] = {}
    for key, number, line in _thresholds(file.removed):
        position = seen.get(key, 0)
        seen[key] = position + 1
        counterparts = added_by_key.get(key, [])
        if position >= len(counterparts):
            reason = f"threshold {key!r} ({number:g}) removed without replacement"
        elif counterparts[position] < number:
            reason = f"threshold {key!r} lowered from {number:g} to {counterparts[position]:g}"
        else:
            continue
        issues.append(
            _issue(
                "weakened.threshold-changed",
                FindingSeverity.HIGH,
                f"{reason}: {_quote(line.text)!r}",
                path,
                line.number,
            )
        )
    added_strict = {
        match.group(0).lower().replace(" ", "")
        for line in file.added
        for match in _STRICT.finditer(line.text)
    }
    for line in file.removed:
        match = _STRICT.search(line.text)
        if match and match.group(0).lower().replace(" ", "") not in added_strict:
            issues.append(
                _issue(
                    "weakened.threshold-changed",
                    FindingSeverity.HIGH,
                    f"strict mode removed: {_quote(line.text)!r}",
                    path,
                    line.number,
                )
            )
    if _is_workflow(path):
        added_tools = {
            _tool(match) for line in file.added for match in _CHECK_TOOL.finditer(line.text)
        }
        for line in file.removed:
            if _NOT_A_RUN.search(line.text):
                continue
            tools = {_tool(match) for match in _CHECK_TOOL.finditer(line.text)}
            missing = sorted(tools - added_tools)
            if missing:
                issues.append(
                    _issue(
                        "weakened.threshold-changed",
                        FindingSeverity.HIGH,
                        f"workflow no longer runs {', '.join(missing)}: {_quote(line.text)!r}",
                        path,
                        line.number,
                    )
                )
    return issues


def _tool(match: re.Match[str]) -> str:
    return match.group(1) or "npm test"


def _contiguous(lines: Sequence[DiffLine]) -> Iterable[tuple[DiffLine, DiffLine | None]]:
    """Each added line with the next added line when it directly follows it in the new file
    (the same hunk), else ``None``."""
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else None
        yield line, following if following and following.number == line.number + 1 else None


def _broad_excepts(file: DiffFile) -> list[Issue]:
    issues: list[Issue] = []
    for line, following in _contiguous(file.added):
        match = _EXCEPT.match(line.text)
        if not match:
            continue
        rest = match.group("rest").strip()
        if rest and not rest.startswith("#"):
            swallowed = _SWALLOW.match(rest) is not None
        else:
            swallowed = following is not None and _SWALLOW.match(following.text.strip()) is not None
        if swallowed:
            issues.append(
                _issue(
                    "weakened.broad-except",
                    FindingSeverity.MEDIUM,
                    f"broad exception handler swallows errors: {_quote(line.text)!r}",
                    file.path,
                    line.number,
                )
            )
    return issues


def _error_to_success(file: DiffFile) -> list[Issue]:
    issues: list[Issue] = []
    previous_except: int | None = None
    for line in file.added:
        exits_after_except = previous_except is not None and line.number == previous_except + 1
        # A comment that explains a flag is not the flag; ``rmtree(..., ignore_errors=True)``
        # is cleanup, not a check whose failure is hidden.
        if _is_comment(line.text) or "rmtree(" in line.text:
            previous_except = None
            continue
        if _ERROR_TO_SUCCESS.search(line.text) or (
            exits_after_except and _EXIT_ZERO.search(line.text)
        ):
            issues.append(
                _issue(
                    "weakened.error-to-success",
                    FindingSeverity.HIGH,
                    f"an error is turned into success: {_quote(line.text)!r}",
                    file.path,
                    line.number,
                )
            )
        previous_except = line.number if re.match(r"^\s*except\b", line.text) else None
    return issues


def check_weakened_controls(
    diff: Sequence[DiffFile], policy: Literal["enforce", "warn"]
) -> list[Issue]:
    """Issues for every weakened control the diff introduces, in diff order.

    Under ``warn`` every issue is LOW: the evidence is still recorded, but it does not block."""
    issues: list[Issue] = []
    for file in diff:
        issues.extend(_deleted_tests(file))
        issues.extend(_removed_assertions(file))
        if not _is_doc(file.path):
            issues.extend(
                _pattern_lines(
                    file,
                    _SKIP,
                    "weakened.skip-added",
                    FindingSeverity.HIGH,
                    "test skip or expected-failure marker added",
                )
            )
            issues.extend(
                _pattern_lines(
                    file,
                    _SUPPRESSION,
                    "weakened.suppression-added",
                    FindingSeverity.MEDIUM,
                    "tool suppression added",
                )
            )
        issues.extend(_threshold_changes(file))
        if not _is_doc(file.path):
            issues.extend(_broad_excepts(file))
            issues.extend(_error_to_success(file))
    if policy == "warn":
        return [replace(issue, severity=FindingSeverity.LOW) for issue in issues]
    return issues
