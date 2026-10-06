"""``harness config lint``: contradictions between the harness configuration and the agent
instruction files of a repository (#55, item 14).

Agents read instruction files (``AGENTS.md``, ``CLAUDE.md``, ``.cursorrules``, ``.cursor/rules``,
``.github/copilot-instructions.md``); the harness reads ``.harness/project.yaml``. When they
disagree, an agent follows one and the gate enforces the other. The lint reads them all,
deterministically (fixed patterns, no language model), and reports:

* ``tool-version``: the same tool with different versions (``Python 3.11`` in one file,
  ``python 3.12`` in another or in the repository's own version files);
* ``coverage``: different coverage thresholds (a file against another or against the
  harness's ``diffCoverage``);
* ``tests``: one source says the tests must pass or run, another says to skip them;
* ``forbidden-flag``: an instruction to bypass a control: ``--no-verify``, a forced push,
  ``git add -A`` or ``git add .``, ``|| true``, ``--exit-zero``, ``continue-on-error``,
  ``HUSKY=0`` or ``SKIP=`` for hooks.

Each conflict names the source that wins by the declared precedence (``instructions.precedence``,
first wins; ``harness`` is the project configuration)."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HARNESS_SOURCE = "harness"
MAX_FILE_BYTES = 1_000_000

_TOOLS = {
    "python": "python",
    "node": "node",
    "node.js": "node",
    "nodejs": "node",
    "java": "java",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "ruby": "ruby",
    "swift": "swift",
    "kotlin": "kotlin",
    "typescript": "typescript",
}
_VERSION = re.compile(
    r"\b(python|node(?:\.js|js)?|java|golang|go|rust|ruby|swift|kotlin|typescript)"
    r"\s*(?:version\s*)?(?:>=|==|~=|\^|v)?\s*(\d+(?:\.\d+){0,2})\b",
    re.IGNORECASE,
)
# A threshold after the word (``coverage of at least 80%``) or before it (``80% line
# coverage``); _coverage_values scans with both as one alternation would, the first first.
_COVERAGE_AFTER = re.compile(r"\bcoverage\b[^.\n]{0,60}?(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_COVERAGE_BEFORE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:test\s+|line\s+|code\s+)?coverage\b", re.IGNORECASE
)
_TESTS_REQUIRED = (
    re.compile(r"\btests?\s+must\s+pass", re.IGNORECASE),
    re.compile(r"\brun\s+(?:the\s+|all\s+)?tests\s+before", re.IGNORECASE),
    re.compile(r"\ball\s+tests\s+(?:must\s+)?pass", re.IGNORECASE),
)
_TESTS_SKIPPED = re.compile(
    r"\b(skip(?:ping)?\s+(?:the\s+)?tests|do\s+not\s+run\s+(?:the\s+)?tests|don't\s+run\s+(?:the\s+)?tests|"
    r"no\s+need\s+to\s+run\s+(?:the\s+)?tests)",
    re.IGNORECASE,
)
_FORBIDDEN: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("--no-verify", re.compile(r"--no-verify\b"), "bypasses the Git hooks"),
    (
        "push --force",
        re.compile(r"\bgit\s+push\b[^\n]*\s(?:--force(?!-with-lease)\b|-f\b)"),
        "rewrites published history",
    ),
    (
        "git add -A",
        re.compile(r"\bgit\s+add\s+(?:-A\b|--all\b|\.(?:\s|$))"),
        "stages files beyond the change",
    ),
    ("|| true", re.compile(r"\|\|\s*true\b"), "turns a failing command into success"),
    ("--exit-zero", re.compile(r"--exit-zero\b"), "turns a failing check into success"),
    ("continue-on-error", re.compile(r"\bcontinue-on-error\b"), "ignores a failing CI step"),
    ("HUSKY=0", re.compile(r"\bHUSKY=0\b"), "disables the Git hooks"),
    ("SKIP=", re.compile(r"\bSKIP=[A-Za-z0-9_,-]+"), "skips pre-commit hooks"),
)
_NEGATION = re.compile(r"\b(never|do\s+not|don't|must\s+not|avoid|forbidden|no)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Statement:
    source: str
    line: int
    text: str
    value: str


@dataclass(frozen=True)
class LintIssue:
    kind: str
    severity: str
    message: str
    statements: tuple[Statement, ...]
    winner: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "winner": self.winner,
            "statements": [
                {"source": item.source, "line": item.line, "value": item.value, "text": item.text}
                for item in self.statements
            ],
        }


def instruction_files(workspace: Path, configured: Iterable[str]) -> list[tuple[str, str]]:
    """(source name, text) of each instruction file that exists; a directory (``.cursor/rules``)
    gives one entry per file in it."""
    found: list[tuple[str, str]] = []
    for item in configured:
        target = workspace / item
        paths = sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else [target]
        for path in paths:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            name = path.relative_to(workspace).as_posix()
            found.append((name, path.read_text(encoding="utf-8", errors="replace")))
    return found


def _lines(text: str) -> Iterable[tuple[int, str]]:
    yield from enumerate(text.splitlines(), start=1)


def _versions(source: str, text: str) -> list[tuple[str, Statement]]:
    found: list[tuple[str, Statement]] = []
    for number, line in _lines(text):
        for match in _VERSION.finditer(line):
            name = match.group(1).lower()
            tool = _TOOLS.get(name, name)
            found.append((tool, Statement(source, number, line.strip()[:200], match.group(2))))
    return found


def repository_versions(workspace: Path) -> list[tuple[str, Statement]]:
    """Versions the repository itself pins: ``.python-version``, ``requires-python``,
    ``.nvmrc``, ``engines.node``, the ``go`` directive of ``go.mod``."""
    found: list[tuple[str, Statement]] = []
    for name, reader in _VERSION_READERS:
        path = workspace / name
        if path.is_file():
            found.extend(reader(path))
    return found


_PYTHON_VERSION_FILE = ".python-version"
_PYPROJECT = "pyproject.toml"
_NVMRC = ".nvmrc"
_PACKAGE_JSON = "package.json"
_GO_MOD = "go.mod"
_VERSION_NUMBER = re.compile(r"(\d+(?:\.\d+){0,2})")
_GO_DIRECTIVE = re.compile(r"^go\s+(\d+(?:\.\d+){0,2})", re.MULTILINE)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _python_version_file(path: Path) -> list[tuple[str, Statement]]:
    value = _read(path).strip().split()[0:1]
    if value and re.match(r"\d", value[0]):
        return [("python", Statement(_PYTHON_VERSION_FILE, 1, value[0], value[0]))]
    return []


def _requires_python(path: Path) -> list[tuple[str, Statement]]:
    try:
        data = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError:
        data = {}
    requires = str((data.get("project") or {}).get("requires-python") or "")
    match = _VERSION_NUMBER.search(requires)
    if match:
        return [("python", Statement(_PYPROJECT, 1, f"requires-python {requires}", match[1]))]
    return []


def _nvmrc(path: Path) -> list[tuple[str, Statement]]:
    match = _VERSION_NUMBER.search(_read(path))
    if match:
        return [("node", Statement(_NVMRC, 1, match[0], match[1]))]
    return []


def _engines_node(path: Path) -> list[tuple[str, Statement]]:
    try:
        engines = (json.loads(path.read_text(encoding="utf-8")) or {}).get("engines") or {}
    except ValueError:
        engines = {}
    node = str(engines.get("node") or "") if isinstance(engines, dict) else ""
    match = _VERSION_NUMBER.search(node)
    if match:
        return [("node", Statement(_PACKAGE_JSON, 1, f"engines.node {node}", match[1]))]
    return []


def _go_directive(path: Path) -> list[tuple[str, Statement]]:
    match = _GO_DIRECTIVE.search(_read(path))
    if match:
        return [("go", Statement(_GO_MOD, 1, match[0], match[1]))]
    return []


_VERSION_READERS: tuple[tuple[str, Callable[[Path], list[tuple[str, Statement]]]], ...] = (
    (_PYTHON_VERSION_FILE, _python_version_file),
    (_PYPROJECT, _requires_python),
    (_NVMRC, _nvmrc),
    (_PACKAGE_JSON, _engines_node),
    (_GO_MOD, _go_directive),
)
"""The repository's own version files, in the order they are read."""


def _compatible(left: str, right: str) -> bool:
    """``3`` and ``3.12`` agree (one is a prefix of the other); ``3.11`` and ``3.12`` do not."""
    a, b = left.split("."), right.split(".")
    width = min(len(a), len(b))
    return a[:width] == b[:width]


def _winner(sources: Iterable[str], precedence: list[str]) -> str | None:
    ranked = [
        (
            precedence.index(_base(source)) if _base(source) in precedence else len(precedence),
            source,
        )
        for source in sources
    ]
    return min(ranked)[1] if ranked else None


def _base(source: str) -> str:
    if source.startswith(".cursor/rules/"):
        return ".cursor/rules"
    if source in {name for name, _reader in _VERSION_READERS}:
        return HARNESS_SOURCE
    return source


def _version_issues(
    versions: list[tuple[str, Statement]], precedence: list[str]
) -> list[LintIssue]:
    """One issue per tool whose versions are not prefixes of each other."""
    by_tool: dict[str, list[Statement]] = {}
    for tool, statement in versions:
        by_tool.setdefault(tool, []).append(statement)
    issues: list[LintIssue] = []
    for tool, statements in sorted(by_tool.items()):
        values = sorted({item.value for item in statements})
        conflict = any(
            not _compatible(left, right) for left in values for right in values if left < right
        )
        if conflict:
            winner = _winner((item.source for item in statements), precedence)
            issues.append(
                LintIssue(
                    "tool-version",
                    "HIGH",
                    f"{tool} versions disagree: {', '.join(values)}",
                    tuple(statements),
                    winner,
                )
            )
    return issues


def _coverage_values(line: str) -> Iterator[str]:
    """The thresholds of a line, leftmost first; at the same position the threshold after the
    word wins, as in one alternation of both patterns."""
    position = 0
    while True:
        after = _COVERAGE_AFTER.search(line, position)
        before = _COVERAGE_BEFORE.search(line, position)
        found = after if before is None or (after and after.start() <= before.start()) else before
        if found is None:
            return
        yield found[1]
        position = found.end()


def _coverage_issues(
    texts: list[tuple[str, str]], threshold: float | None, precedence: list[str]
) -> list[LintIssue]:
    coverage: list[Statement] = []
    if threshold is not None:
        coverage.append(
            Statement(HARNESS_SOURCE, 0, "verification.testQuality.diffCoverage", f"{threshold:g}")
        )
    for source, text in texts:
        for number, line in _lines(text):
            coverage.extend(
                Statement(source, number, line.strip()[:200], f"{float(value):g}")
                for value in _coverage_values(line)
            )
    if len({item.value for item in coverage}) <= 1:
        return []
    return [
        LintIssue(
            "coverage",
            "MEDIUM",
            "coverage thresholds disagree: "
            + ", ".join(sorted({item.value + "%" for item in coverage})),
            tuple(coverage),
            _winner((item.source for item in coverage), precedence),
        )
    ]


def _test_issues(texts: list[tuple[str, str]], precedence: list[str]) -> list[LintIssue]:
    """An issue when a file says to skip the tests (not "never skip the tests")."""
    required: list[Statement] = []
    skipped: list[Statement] = []
    for source, text in texts:
        for number, line in _lines(text):
            skip = _TESTS_SKIPPED.search(line)
            if skip is not None and not _NEGATION.search(line[: skip.start()]):
                skipped.append(Statement(source, number, line.strip()[:200], "skip tests"))
            elif skip is not None or any(item.search(line) for item in _TESTS_REQUIRED):
                required.append(Statement(source, number, line.strip()[:200], "tests must pass"))
    if not skipped:
        return []
    test_statements = (
        Statement(HARNESS_SOURCE, 0, "mandatory test validators", "tests must pass"),
        *required,
        *skipped,
    )
    return [
        LintIssue(
            "tests",
            "HIGH",
            "an instruction file tells the agent to skip the tests the gate runs",
            test_statements,
            _winner((item.source for item in test_statements), precedence),
        )
    ]


def _forbidden_flag_issues(texts: list[tuple[str, str]]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for source, text in texts:
        for number, line in _lines(text):
            issues.extend(
                LintIssue(
                    "forbidden-flag",
                    "HIGH",
                    f"{source}:{number} tells the agent to use {name}, which {why}; the "
                    "harness never does",
                    (Statement(source, number, line.strip()[:200], name),),
                    HARNESS_SOURCE,
                )
                for name, why in _forbidden_flags(line)
            )
    return issues


def _forbidden_flags(line: str) -> Iterator[tuple[str, str]]:
    """``(flag, why)`` of each control the line tells the agent to bypass; "never use
    --no-verify" agrees with the harness."""
    for name, pattern, why in _FORBIDDEN:
        flag = pattern.search(line)
        if flag is not None and not _NEGATION.search(line[: flag.start()]):
            yield name, why


def lint_instructions(
    workspace: Path,
    files: Iterable[str],
    precedence: list[str],
    *,
    coverage_threshold: float | None = None,
) -> dict[str, Any]:
    """The lint report: the files read, the precedence and every issue."""
    texts = instruction_files(workspace, files)
    versions = repository_versions(workspace)
    for source, text in texts:
        versions.extend(_versions(source, text))
    issues = [
        *_version_issues(versions, precedence),
        *_coverage_issues(texts, coverage_threshold, precedence),
        *_test_issues(texts, precedence),
        *_forbidden_flag_issues(texts),
    ]
    return {
        "status": "FAILED" if issues else "PASSED",
        "files": [source for source, _ in texts],
        "precedence": precedence,
        "issues": [item.as_dict() for item in issues],
    }


__all__ = [
    "HARNESS_SOURCE",
    "LintIssue",
    "Statement",
    "instruction_files",
    "lint_instructions",
    "repository_versions",
]
