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
from collections.abc import Iterable
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
_COVERAGE = re.compile(
    r"\bcoverage\b[^.\n]{0,60}?(\d{1,3}(?:\.\d+)?)\s*%|(\d{1,3}(?:\.\d+)?)\s*%\s*(?:test\s+|line\s+|code\s+)?coverage\b",
    re.IGNORECASE,
)
_TESTS_REQUIRED = re.compile(
    r"\b(tests?\s+must\s+pass|run\s+(?:the\s+|all\s+)?tests\s+before|all\s+tests\s+(?:must\s+)?pass)",
    re.IGNORECASE,
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
    pinned = workspace / ".python-version"
    if pinned.is_file():
        value = pinned.read_text(encoding="utf-8", errors="replace").strip().split()[0:1]
        if value and re.match(r"\d", value[0]):
            found.append(("python", Statement(".python-version", 1, value[0], value[0])))
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        except tomllib.TOMLDecodeError:
            data = {}
        requires = str((data.get("project") or {}).get("requires-python") or "")
        match = re.search(r"(\d+(?:\.\d+){0,2})", requires)
        if match:
            found.append(
                (
                    "python",
                    Statement("pyproject.toml", 1, f"requires-python {requires}", match.group(1)),
                )
            )
    nvmrc = workspace / ".nvmrc"
    if nvmrc.is_file():
        match = re.search(
            r"(\d+(?:\.\d+){0,2})", nvmrc.read_text(encoding="utf-8", errors="replace")
        )
        if match:
            found.append(("node", Statement(".nvmrc", 1, match.group(0), match.group(1))))
    package = workspace / "package.json"
    if package.is_file():
        try:
            engines = (json.loads(package.read_text(encoding="utf-8")) or {}).get("engines") or {}
        except ValueError:
            engines = {}
        node = str(engines.get("node") or "") if isinstance(engines, dict) else ""
        match = re.search(r"(\d+(?:\.\d+){0,2})", node)
        if match:
            found.append(
                ("node", Statement("package.json", 1, f"engines.node {node}", match.group(1)))
            )
    gomod = workspace / "go.mod"
    if gomod.is_file():
        match = re.search(
            r"^go\s+(\d+(?:\.\d+){0,2})",
            gomod.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        if match:
            found.append(("go", Statement("go.mod", 1, match.group(0), match.group(1))))
    return found


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
    if source in {".python-version", "pyproject.toml", ".nvmrc", "package.json", "go.mod"}:
        return HARNESS_SOURCE
    return source


def lint_instructions(
    workspace: Path,
    files: Iterable[str],
    precedence: list[str],
    *,
    coverage_threshold: float | None = None,
) -> dict[str, Any]:
    """The lint report: the files read, the precedence and every issue."""
    texts = instruction_files(workspace, files)
    issues: list[LintIssue] = []
    versions = repository_versions(workspace)
    for source, text in texts:
        versions.extend(_versions(source, text))
    by_tool: dict[str, list[Statement]] = {}
    for tool, statement in versions:
        by_tool.setdefault(tool, []).append(statement)
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
    coverage: list[Statement] = []
    if coverage_threshold is not None:
        coverage.append(
            Statement(
                HARNESS_SOURCE,
                0,
                "verification.testQuality.diffCoverage",
                f"{coverage_threshold:g}",
            )
        )
    for source, text in texts:
        for number, line in _lines(text):
            for match in _COVERAGE.finditer(line):
                value = match.group(1) or match.group(2)
                coverage.append(Statement(source, number, line.strip()[:200], f"{float(value):g}"))
    if len({item.value for item in coverage}) > 1:
        issues.append(
            LintIssue(
                "coverage",
                "MEDIUM",
                "coverage thresholds disagree: "
                + ", ".join(sorted({item.value + "%" for item in coverage})),
                tuple(coverage),
                _winner((item.source for item in coverage), precedence),
            )
        )
    required: list[Statement] = []
    skipped: list[Statement] = []
    for source, text in texts:
        for number, line in _lines(text):
            skip = _TESTS_SKIPPED.search(line)
            if skip is not None and not _NEGATION.search(line[: skip.start()]):
                skipped.append(Statement(source, number, line.strip()[:200], "skip tests"))
            elif _TESTS_REQUIRED.search(line) or skip is not None:
                required.append(Statement(source, number, line.strip()[:200], "tests must pass"))
    if skipped:
        test_statements = (
            Statement(HARNESS_SOURCE, 0, "mandatory test validators", "tests must pass"),
            *required,
            *skipped,
        )
        issues.append(
            LintIssue(
                "tests",
                "HIGH",
                "an instruction file tells the agent to skip the tests the gate runs",
                test_statements,
                _winner((item.source for item in test_statements), precedence),
            )
        )
    for source, text in texts:
        for number, line in _lines(text):
            for name, pattern, why in _FORBIDDEN:
                flag = pattern.search(line)
                if flag is None:
                    continue
                before = line[: flag.start()]
                if _NEGATION.search(before):
                    continue  # "never use --no-verify" agrees with the harness
                issues.append(
                    LintIssue(
                        "forbidden-flag",
                        "HIGH",
                        f"{source}:{number} tells the agent to use {name}, which {why}; the "
                        "harness never does",
                        (Statement(source, number, line.strip()[:200], name),),
                        HARNESS_SOURCE,
                    )
                )
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
