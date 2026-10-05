"""Risk factors of a ChangeSet diff.

The harness raises the review depth of a change by what it touches, not by what the agent says it
touched. These signals are lexical heuristics over added and removed lines: each one names the
factor, the file and the line so a reviewer can confirm or dismiss it. They are not findings by
themselves; the caller decides what a factor implies (a stricter gate, a second reviewer).

Documentation files (``.md``, ``.rst``, ``.adoc``) do not raise the ``authentication`` factor: a
README that mentions tokens changes no access control."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from .model import DiffFile, DiffLine, is_test_path

FACTORS = (
    "newDependency",
    "authentication",
    "destructiveMigration",
    "publicContract",
    "network",
    "floatMoney",
    "sensitiveLogging",
    "deletedWithoutTests",
)
_DELETED_LINES_THRESHOLD = 20
_DOC_SUFFIXES = (".md", ".rst", ".adoc")
_SOURCE_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".rs",
    ".cs",
    ".php",
    ".swift",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".scala",
    ".sql",
)


@dataclass(frozen=True)
class RiskSignal:
    factor: str
    message: str
    path: str | None = None
    line: int | None = None


def _normalized(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _code(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith(("#", "//", "--"))


def _quote(text: str) -> str:
    value = text.strip()
    return value if len(value) <= 120 else value[:119] + "…"


# ----- newDependency -------------------------------------------------------------------------
_REQUIREMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?"
_PYPROJECT_REQUIREMENT = re.compile(
    rf"^\s*[\"']{_REQUIREMENT}\s*(?:[<>=!~]=?|===|;|@)[^\"']*[\"']\s*,?\s*(?:#.*)?$"
)
_PYPROJECT_INLINE = re.compile(
    rf"^\s*dependencies\s*=\s*\[\s*[\"']{_REQUIREMENT}"
    rf"|^\s*[\w-]+\s*=\s*\[\s*[\"']{_REQUIREMENT}\s*(?:[<>=!~]=?|;|@)"
)
_KEY_VERSION = re.compile(r"^\s*[\"']?(?P<name>[\w.@/-]+)[\"']?\s*[=:]\s*(?P<value>.+?)\s*,?\s*$")
_VERSION_VALUE = re.compile(
    r"^(?:[\"'](?:[\^~<>=*]|\d|latest\b|workspace:|file:|git\+|npm:)"
    r"|\{[^}]*\b(?:version|git|path|url)\s*=)"
)
_NOT_A_DEPENDENCY = frozenset(
    {
        "version",
        "requires-python",
        "python_requires",
        "python",
        "python_version",
        "python_full_version",
        "target-version",
        "minversion",
        "node",
        "npm",
        "yarn",
        "pnpm",
        "url",
        "verify_ssl",
        "name",
    }
)
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?[\w.-]+(?:/[\w.~-]+)+\s+v\d")


def _dependency_line(name: str, line: DiffLine) -> bool:
    text = line.text
    if not _code(text):
        return False
    if name == "pyproject.toml":
        if _PYPROJECT_REQUIREMENT.match(text) or _PYPROJECT_INLINE.match(text):
            return not text.lstrip().startswith(("requires", "build-backend"))
        return _poetry_dependency(text)
    if name.startswith("requirements") and name.endswith((".txt", ".in")):
        return not text.lstrip().startswith("-")
    if name in {"package.json", "Pipfile"}:
        return _poetry_dependency(text)
    if name == "go.mod":
        return _GO_REQUIRE.match(text) is not None
    return False


def _poetry_dependency(text: str) -> bool:
    match = _KEY_VERSION.match(text)
    if not match or text.lstrip().startswith("["):
        return False
    return (
        match.group("name").lower() not in _NOT_A_DEPENDENCY
        and _VERSION_VALUE.match(match.group("value")) is not None
    )


def _new_dependencies(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    for file in diff:
        path = _normalized(file.path)
        name = PurePosixPath(path).name
        for line in file.added:
            if _dependency_line(name, line):
                yield RiskSignal(
                    "newDependency", f"dependency added: {_quote(line.text)}", path, line.number
                )


# ----- authentication ------------------------------------------------------------------------
_AUTH = re.compile(
    r"auth|login|password|passwd|session|token|permission|role|jwt|oauth|credential",
    re.IGNORECASE,
)


def _authentication(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    for file in diff:
        path = _normalized(file.path)
        if is_test_path(path) or path.lower().endswith(_DOC_SUFFIXES):
            continue
        match = _AUTH.search(path)
        if match:
            yield RiskSignal(
                "authentication", f"path mentions {match.group(0).lower()!r}", path, None
            )
            continue
        for line in file.added:
            match = _AUTH.search(line.text)
            if match:
                yield RiskSignal(
                    "authentication",
                    f"added code mentions {match.group(0).lower()!r}: {_quote(line.text)}",
                    path,
                    line.number,
                )
                break


# ----- destructiveMigration ------------------------------------------------------------------
_DESTRUCTIVE = re.compile(
    r"\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bALTER\s+TABLE\b.*\bDROP\b|\bTRUNCATE\b"
    r"|\bop\.drop_table\b|\bop\.drop_column\b",
    re.IGNORECASE,
)
_DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def _destructive_migrations(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    for file in diff:
        path = _normalized(file.path)
        if not (path.lower().endswith(".sql") or "migration" in path.lower()):
            continue
        added = file.added
        for index, line in enumerate(added):
            following = added[index + 1] if index + 1 < len(added) else None
            unbounded_delete = (
                _DELETE_FROM.search(line.text) is not None
                and not _WHERE.search(line.text)
                and not (
                    following is not None
                    and not line.text.rstrip().endswith(";")
                    and following.number == line.number + 1
                    and _WHERE.search(following.text)
                )
            )
            if _DESTRUCTIVE.search(line.text) or unbounded_delete:
                yield RiskSignal(
                    "destructiveMigration",
                    f"destructive statement: {_quote(line.text)}",
                    path,
                    line.number,
                )


# ----- publicContract ------------------------------------------------------------------------
_DEFINITION = re.compile(
    r"^\s*(?:async\s+)?def\s+(?P<function>[A-Za-z]\w*)\s*\(|^\s*(?:export\s+)?class\s+(?P<cls>[A-Za-z]\w*)\b"
)


def _definitions(lines: Iterable[DiffLine]) -> list[tuple[str, DiffLine]]:
    found: list[tuple[str, DiffLine]] = []
    for line in lines:
        match = _DEFINITION.match(line.text)
        if match:
            found.append((match.group("function") or match.group("cls"), line))
    return found


def _public_contract(
    diff: Sequence[DiffFile], interface_paths: Collection[str]
) -> Iterable[RiskSignal]:
    interfaces = {_normalized(path) for path in interface_paths}
    for file in diff:
        paths = {_normalized(path) for path in (file.old_path, file.new_path) if path}
        path = _normalized(file.path)
        if paths & interfaces:
            yield RiskSignal("publicContract", "declared interface file changed", path, None)
        exported = next(
            (line for line in [*file.removed, *file.added] if "__all__" in line.text), None
        )
        if exported is not None:
            yield RiskSignal(
                "publicContract",
                f"__all__ changed: {_quote(exported.text)}",
                path,
                exported.number,
            )
        if is_test_path(path):
            continue
        readded = {name for name, _ in _definitions(file.added)}
        for name, line in _definitions(file.removed):
            if name not in readded:
                yield RiskSignal(
                    "publicContract",
                    f"public definition {name!r} removed: {_quote(line.text)}",
                    path,
                    line.number,
                )


# ----- network -------------------------------------------------------------------------------
_NETWORK = re.compile(
    r"^\s*(?:import|from)\s+(?:requests|httpx|aiohttp|urllib\.request|http\.client|socket"
    r"|websockets|grpc)\b"
    r"|^\s*from\s+(?:urllib\s+import\s+request|http\s+import\s+client)\b"
    r"|(?<![\w.])fetch\(|\baxios\b"
)


def _network(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    for file in diff:
        path = _normalized(file.path)
        if is_test_path(path):
            continue
        for line in file.added:
            if _code(line.text) and _NETWORK.search(line.text):
                yield RiskSignal(
                    "network", f"network access added: {_quote(line.text)}", path, line.number
                )


# ----- floatMoney ----------------------------------------------------------------------------
_MONEY = re.compile(
    r"price|amount|total|fare|cost|balance|fee|subtotal|money|charge|payment", re.IGNORECASE
)
_FLOAT = re.compile(r"\bfloat\(|:\s*float\b|->\s*float\b")


def _float_money(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    for file in diff:
        path = _normalized(file.path)
        if is_test_path(path):
            continue
        for line in file.added:
            if _code(line.text) and _MONEY.search(line.text) and _FLOAT.search(line.text):
                yield RiskSignal(
                    "floatMoney",
                    f"money handled as float: {_quote(line.text)}",
                    path,
                    line.number,
                )


# ----- sensitiveLogging ----------------------------------------------------------------------
_LOG_CALL = re.compile(
    r"\b(?:log|logger|logging|_log|_logger|LOG|LOGGER)\.\w+\(|(?<![\w.])print\("
    r"|\bconsole\.(?:log|info|warn|error|debug)\("
)
_SENSITIVE = re.compile(r"password|passwd|secret|token|card|cvc|cvv|ssn|api_key", re.IGNORECASE)


def _sensitive_logging(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    for file in diff:
        path = _normalized(file.path)
        if is_test_path(path):
            continue
        for line in file.added:
            call = _LOG_CALL.search(line.text)
            if call and _code(line.text) and _SENSITIVE.search(line.text, call.end()):
                yield RiskSignal(
                    "sensitiveLogging",
                    f"sensitive data may be logged: {_quote(line.text)}",
                    path,
                    line.number,
                )


# ----- deletedWithoutTests -------------------------------------------------------------------
def _deleted_without_tests(diff: Sequence[DiffFile]) -> Iterable[RiskSignal]:
    if any(is_test_path(_normalized(path)) for file in diff for path in _paths(file)):
        return
    removed = sum(
        1
        for file in diff
        if file.path.lower().endswith(_SOURCE_SUFFIXES) and not is_test_path(file.path)
        for line in file.removed
        if line.text.strip()
    )
    if removed >= _DELETED_LINES_THRESHOLD:
        yield RiskSignal(
            "deletedWithoutTests",
            f"{removed} non-test source lines removed and no test file changed",
        )


def _paths(file: DiffFile) -> list[str]:
    return [path for path in (file.old_path, file.new_path) if path]


def detect_risk_factors(
    diff: Sequence[DiffFile], *, interface_paths: Collection[str] = ()
) -> list[RiskSignal]:
    """Every risk signal of ``diff``, ordered by factor (the order of ``FACTORS``), then path,
    then line."""
    signals = [
        *_new_dependencies(diff),
        *_authentication(diff),
        *_destructive_migrations(diff),
        *_public_contract(diff, interface_paths),
        *_network(diff),
        *_float_money(diff),
        *_sensitive_logging(diff),
        *_deleted_without_tests(diff),
    ]
    return sorted(
        signals,
        key=lambda signal: (
            FACTORS.index(signal.factor),
            signal.path or "",
            signal.line or 0,
        ),
    )
