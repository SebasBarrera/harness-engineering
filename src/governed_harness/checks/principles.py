"""Engineering principles as deterministic proxies (#56).

A principle such as DRY or YAGNI is a judgement, but some of its violations leave a trace a tool
can measure without a model. This module measures those traces on the ChangeSet; the rest of
each principle is a checklist item of the existing review call (no extra agent call).

=====================================  ============================================  ==========
Proxy (rule id)                        Measured                                       Principle
=====================================  ============================================  ==========
``principles.dry.duplication``         a window of N normalized lines the change      DRY
                                       adds that already exists elsewhere
``principles.kiss.nesting``            an added line nested deeper than 5 levels      KISS
                                       (indentation, any language)
``principles.kiss.module-size``        a non-Python source file the change grows      KISS, Clean
                                       past the module limit                          Code
``principles.composition.inheritance`` a class the change adds or edits whose         composition
                                       inheritance chain in the workspace is deeper   over
                                       than the limit                                 inheritance
``principles.yagni.unused-public``     a public function or class the change adds     YAGNI
                                       that nothing else in the workspace names
``principles.boy-scout.reformat-only`` a file whose every change is whitespace or     Boy Scout
                                       layout (an unrelated cleanup)                  (bounded)
=====================================  ============================================  ==========

Size and complexity of Python functions, and dependency direction between layers, are the #40
checks (``architecture.*``); naming and signatures are verified by the linters of the standards
packs. Everything here is a pure function of text: nothing runs, nothing is written."""

from __future__ import annotations

import ast
import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from governed_harness.checks.model import DiffFile, Issue, is_test_path
from governed_harness.domain.enums import FindingSeverity
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".swift",
    ".cs",
    ".php",
    ".rb",
)
_FAMILY = {
    ".mjs": ".js",
    ".cjs": ".js",
    ".jsx": ".js",
    ".ts": ".js",
    ".tsx": ".js",
    ".vue": ".js",
    ".kts": ".kt",
}
_CATEGORY = "principles"
MAX_NESTING = 5
_MAX_CORPUS_FILES = 3000
_MAX_CORPUS_BYTES = 8_000_000
_TRIVIAL = re.compile(r"^[\s{}()\[\];,]*$|^(?:import|from|using|package|#include|require)\b")
_COMMENT = re.compile(r"^\s*(?:#|//|/\*|\*|--)")

PRINCIPLES_CHECKLIST: tuple[tuple[str, str], ...] = (
    ("principles.srp", "One reason to change per class or module."),
    ("principles.ocp", "Extend with new types, not by growing switches over types."),
    ("principles.lsp", "Subtypes keep their base's contract."),
    ("principles.isp", "Small, client-specific interfaces."),
    ("principles.dip", "Policy depends on abstractions; infrastructure is injected."),
    ("principles.dry", "Each rule, constant or format lives in one place."),
    ("principles.kiss", "Simplest design that meets the criteria."),
    ("principles.yagni", "Nothing the task does not ask for."),
    ("principles.least-astonishment", "Names, signatures and side effects as a reader expects."),
    ("principles.composition", "Composition over deep inheritance."),
    ("principles.boy-scout", "Cleanup only in touched code; no unrelated refactors."),
)
"""The principles checklist appended to the review call, with rule ids for its findings."""


@dataclass(frozen=True)
class PrincipleLimits:
    window: int = 6
    inheritance_depth: int = 3
    max_module_lines: int | None = 800
    unused_public: bool = True
    boy_scout: bool = True


def is_source(path: str) -> bool:
    return path.endswith(SOURCE_SUFFIXES) and not path.endswith(".d.ts")


def _family(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _FAMILY.get(suffix, suffix)


def _normalized(line: str) -> str | None:
    text = " ".join(line.split())
    if not text or _TRIVIAL.match(text) or _COMMENT.match(text) or len(text) < 4:
        return None
    return text


# ----- corpus ---------------------------------------------------------------------------------
def source_corpus(workspace: Path, families: set[str]) -> dict[str, str]:
    """The source files of the workspace in the given language families (bounded)."""
    found: dict[str, str] = {}
    total = 0
    root = workspace.resolve()
    stack = [root]
    while stack and len(found) < _MAX_CORPUS_FILES and total < _MAX_CORPUS_BYTES:
        for entry in _entries(stack.pop()):
            if entry.is_dir():
                stack.append(entry)
                continue
            relative = entry.relative_to(root).as_posix()
            data = _source_bytes(entry, relative, families)
            if data is not None:
                total += len(data)
                found[relative] = data.decode("utf-8", "replace")
    return found


def _entries(directory: Path) -> list[Path]:
    """The entries of a directory by name, without the default excludes and symbolic links
    (none when the directory cannot be read)."""
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    return [
        entry for entry in entries if entry.name not in DEFAULT_EXCLUDES and not entry.is_symlink()
    ]


def _source_bytes(entry: Path, relative: str, families: set[str]) -> bytes | None:
    """The first 400 kB of a source file of one of ``families``, else ``None``."""
    if not is_source(relative) or _family(relative) not in families:
        return None
    try:
        return entry.read_bytes()[:400_000]
    except OSError:
        return None


# ----- DRY ------------------------------------------------------------------------------------
def _windows(lines: Sequence[str], window: int) -> Iterable[tuple[int, str]]:
    """``(first line number, digest)`` of every window of ``window`` normalized lines."""
    kept = [
        (number, text) for number, raw in enumerate(lines, start=1) if (text := _normalized(raw))
    ]
    for index in range(len(kept) - window + 1):
        chunk = kept[index : index + window]
        digest = hashlib.sha256("\n".join(text for _, text in chunk).encode()).hexdigest()
        yield chunk[0][0], digest


def duplication(
    diff: Sequence[DiffFile],
    files: Mapping[str, str],
    corpus: Mapping[str, str],
    *,
    window: int,
    severity: FindingSeverity,
) -> list[Issue]:
    """Windows of added code that already exist elsewhere (another file, or earlier in the
    same file). Test files are ignored: repeated arrangement in tests is expected."""
    index: dict[str, list[tuple[str, int]]] = {}
    for path, text in corpus.items():
        if is_test_path(path):
            continue
        for line, digest in _windows(text.splitlines(), window):
            index.setdefault(digest, []).append((path, line))
    issues: list[Issue] = []
    for item in diff:
        path = item.path
        if item.is_deleted or not is_source(path) or is_test_path(path) or path not in files:
            continue
        added = {line.number for line in item.added}
        issues.extend(
            _duplicates(
                path, files[path].splitlines(), added, index, window=window, severity=severity
            )
        )
    return issues


def _duplicates(
    path: str,
    lines: Sequence[str],
    added: set[int],
    index: Mapping[str, list[tuple[str, int]]],
    *,
    window: int,
    severity: FindingSeverity,
) -> list[Issue]:
    """The windows of one file with an added line that exist elsewhere in ``index``."""
    issues: list[Issue] = []
    reported_until = 0
    for first, digest in _windows(lines, window):
        if first <= reported_until:
            continue
        if not any(number in added for number in range(first, first + window)):
            continue
        others = [
            (other, line)
            for other, line in index.get(digest, [])
            if other != path or abs(line - first) >= window
        ]
        if others:
            other, line = others[0]
            issues.append(
                Issue(
                    rule_id="principles.dry.duplication",
                    severity=severity,
                    message=(
                        f"{path}:{first} adds {window} or more lines that already exist in "
                        f"{other}:{line}"
                    ),
                    path=path,
                    line=first,
                    category=_CATEGORY,
                    recommendation="Extract the shared code into one function or module and "
                    "call it from both places.",
                )
            )
            reported_until = first + window * 2
    return issues


# ----- KISS -----------------------------------------------------------------------------------
def _indent_unit(lines: Sequence[str]) -> int:
    widths = sorted(
        {len(line) - len(line.lstrip(" ")) for line in lines if line.strip() and line[0] == " "}
    )
    steps = [b - a for a, b in zip([0, *widths], widths, strict=False) if b - a > 0]
    return min(steps) if steps else 4


def nesting(
    diff: Sequence[DiffFile], files: Mapping[str, str], *, severity: FindingSeverity
) -> list[Issue]:
    issues: list[Issue] = []
    for item in diff:
        path = item.path
        if item.is_deleted or not is_source(path) or is_test_path(path) or path not in files:
            continue
        lines = files[path].splitlines()
        unit = _indent_unit(lines)
        for line in item.added:
            text = line.text.replace("\t", " " * unit)
            if not text.strip() or _COMMENT.match(text):
                continue
            depth = (len(text) - len(text.lstrip(" "))) // max(unit, 1)
            # One level is the class or module body in most languages.
            if depth > MAX_NESTING + 1:
                issues.append(
                    Issue(
                        rule_id="principles.kiss.nesting",
                        severity=severity,
                        message=f"{path}:{line.number} is nested {depth} levels deep",
                        path=path,
                        line=line.number,
                        category=_CATEGORY,
                        recommendation="Use early returns or extract the inner block into a "
                        "named function.",
                    )
                )
                break
    return issues


def module_size(
    diff: Sequence[DiffFile],
    files: Mapping[str, str],
    limit: int | None,
    baseline: Mapping[str, int],
    *,
    severity: FindingSeverity,
) -> list[Issue]:
    """Non-Python source files grown past the limit (Python has ``architecture.module-lines``).
    A file already past it on the baseline is reported as LOW."""
    if limit is None:
        return []
    issues: list[Issue] = []
    for item in diff:
        path = item.path
        if (
            item.is_deleted
            or path.endswith(".py")
            or not is_source(path)
            or is_test_path(path)
            or path not in files
        ):
            continue
        count = len(files[path].splitlines())
        if count <= limit:
            continue
        before = baseline.get(path, 0)
        issues.append(
            Issue(
                rule_id="principles.kiss.module-size",
                severity=FindingSeverity.LOW if before > limit else severity,
                message=f"{'Pre-existing: ' if before > limit else ''}{path} has {count} lines; "
                f"the limit is {limit}.",
                path=path,
                line=1,
                category=_CATEGORY,
                recommendation="Split the file along its responsibilities.",
            )
        )
    return issues


# ----- composition over inheritance -------------------------------------------------------------
_EXTENDS = re.compile(
    r"\b(?:class|interface)\s+(?P<name>[A-Z]\w*)(?:<[^>{]*>)?\s*"
    r"(?:extends\s+(?P<base>[A-Z]\w*)|:\s*(?P<colon>[A-Z]\w*))"
)
_RUBY_CLASS = re.compile(r"^\s*class\s+(?P<name>[A-Z]\w*)\s*<\s*(?P<base>[A-Z][\w:]*)")


_NOT_A_BASE = frozenset({"object", "Protocol", "Generic", "ABC", "Enum"})


def _base_name(base: ast.expr) -> str | None:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _python_bases(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.bases:
            name = _base_name(node.bases[0])
            if name and name not in _NOT_A_BASE:
                found[node.name] = name
    return found


def _bases(path: str, text: str) -> dict[str, str]:
    """``class -> first base`` declared in one file."""
    if path.endswith(".py"):
        return _python_bases(text)
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _RUBY_CLASS.match(line) if path.endswith(".rb") else _EXTENDS.search(line)
        if match:
            parent = match.groupdict().get("base") or match.groupdict().get("colon")
            if parent:
                found[match["name"]] = str(parent).split("::")[-1]
    return found


def _chain(name: str, hierarchy: Mapping[str, str], limit: int) -> list[str]:
    """``name`` and its ancestors, stopping at a cycle or a few levels past ``limit``."""
    chain = [name]
    while chain[-1] in hierarchy and len(chain) <= limit + 5:
        parent = hierarchy[chain[-1]]
        if parent in chain:
            break
        chain.append(parent)
    return chain


def _class_line(text: str, name: str) -> int | None:
    declaration = re.compile(rf"\bclass\s+{re.escape(name)}\b")
    return next(
        (
            number
            for number, line in enumerate(text.splitlines(), start=1)
            if declaration.search(line)
        ),
        None,
    )


def inheritance_depth(
    diff: Sequence[DiffFile],
    files: Mapping[str, str],
    corpus: Mapping[str, str],
    *,
    limit: int,
    severity: FindingSeverity,
) -> list[Issue]:
    hierarchy: dict[str, str] = {}
    for path, text in corpus.items():
        hierarchy.update(_bases(path, text))
    for path, text in files.items():
        if is_source(path):
            hierarchy.update(_bases(path, text))
    issues: list[Issue] = []
    for item in diff:
        path = item.path
        if item.is_deleted or not is_source(path) or is_test_path(path) or path not in files:
            continue
        added_text = "\n".join(line.text for line in item.added)
        issues.extend(
            _inheritance_issue(path, files[path], chain, limit, severity)
            for chain in _deep_chains(path, files[path], added_text, hierarchy, limit)
        )
    return issues


def _deep_chains(
    path: str, text: str, added_text: str, hierarchy: Mapping[str, str], limit: int
) -> Iterator[list[str]]:
    """The inheritance chains past ``limit`` of the classes of a file the added lines name."""
    for name in _bases(path, text):
        if re.search(rf"\b{re.escape(name)}\b", added_text):
            chain = _chain(name, hierarchy, limit)
            if len(chain) - 1 > limit:
                yield chain


def _inheritance_issue(
    path: str, text: str, chain: list[str], limit: int, severity: FindingSeverity
) -> Issue:
    name = chain[0]
    return Issue(
        rule_id="principles.composition.inheritance",
        severity=severity,
        message=(
            f"{name} inherits through {len(chain) - 1} levels ({' -> '.join(chain)}); "
            f"the limit is {limit}"
        ),
        path=path,
        line=_class_line(text, name),
        category=_CATEGORY,
        recommendation="Prefer composition: hold the behaviour as a collaborator "
        "instead of inheriting it.",
    )


# ----- YAGNI ----------------------------------------------------------------------------------
_PUBLIC_DEFINITION = {
    ".py": re.compile(r"^(?:def|class|async\s+def)\s+(?P<name>[A-Za-z]\w*)"),
    ".js": re.compile(
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|interface|type)"
        r"\s+(?P<name>[A-Za-z_$][\w$]*)"
    ),
}


def unused_public(
    diff: Sequence[DiffFile],
    files: Mapping[str, str],
    corpus: Mapping[str, str],
    *,
    severity: FindingSeverity,
) -> list[Issue]:
    """Public top-level functions and classes (Python) and exports (JavaScript, TypeScript)
    the change adds and that no other file, test included, names."""
    issues: list[Issue] = []
    for item in diff:
        path = item.path
        family = _family(path)
        pattern = _PUBLIC_DEFINITION.get(family)
        if pattern is None or item.is_deleted or is_test_path(path) or path not in files:
            continue
        for line in item.added:
            match = pattern.match(line.text)
            if not match or match["name"].startswith("_") or match["name"] in {"main"}:
                continue
            name = match["name"]
            word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
            used_elsewhere = any(
                word.search(text) for other, text in corpus.items() if other != path
            ) or any(word.search(text) for other, text in files.items() if other != path)
            used_here = len(word.findall(files[path])) > 1
            if used_elsewhere or used_here:
                continue
            issues.append(
                Issue(
                    rule_id="principles.yagni.unused-public",
                    severity=severity,
                    message=f"{name} ({path}:{line.number}) is added as public API but nothing "
                    "in the workspace uses it, not even a test",
                    path=path,
                    line=line.number,
                    category=_CATEGORY,
                    recommendation="Remove it until the task needs it, or test the behaviour "
                    "it provides.",
                )
            )
    return issues


# ----- Boy Scout ------------------------------------------------------------------------------
def reformat_only(diff: Sequence[DiffFile], *, severity: FindingSeverity) -> list[Issue]:
    """Files whose every change is whitespace or line layout: the change touched them only to
    clean them up. Cleanup inside a file the change edits for the task is allowed."""
    issues: list[Issue] = []
    for item in diff:
        if item.is_added or item.is_deleted or not (item.added or item.removed):
            continue
        added = Counter("".join(line.text.split()) for line in item.added)
        removed = Counter("".join(line.text.split()) for line in item.removed)
        if "".join(sorted(added.elements())) == "".join(sorted(removed.elements())) or (
            "".join(line.text for line in item.added).replace(" ", "").replace("\t", "")
            == "".join(line.text for line in item.removed).replace(" ", "").replace("\t", "")
        ):
            issues.append(
                Issue(
                    rule_id="principles.boy-scout.reformat-only",
                    severity=severity,
                    message=f"{item.path} changes only whitespace or layout; the task does not "
                    "need it",
                    path=item.path,
                    line=item.added[0].number if item.added else None,
                    category=_CATEGORY,
                    recommendation="Leave unrelated files as they are; clean up only the code "
                    "the change touches.",
                )
            )
    return issues


def check_principles(
    diff: Sequence[DiffFile],
    files: Mapping[str, str],
    corpus: Mapping[str, str],
    limits: PrincipleLimits,
    *,
    severity: FindingSeverity,
    baseline_lines: Mapping[str, int] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    issues.extend(duplication(diff, files, corpus, window=limits.window, severity=severity))
    issues.extend(nesting(diff, files, severity=severity))
    issues.extend(
        module_size(diff, files, limits.max_module_lines, baseline_lines or {}, severity=severity)
    )
    issues.extend(
        inheritance_depth(diff, files, corpus, limit=limits.inheritance_depth, severity=severity)
    )
    if limits.unused_public:
        low = FindingSeverity.LOW if severity is not FindingSeverity.INFO else severity
        issues.extend(unused_public(diff, files, corpus, severity=low))
    if limits.boy_scout:
        issues.extend(reformat_only(diff, severity=severity))
    return issues


__all__ = [
    "PRINCIPLES_CHECKLIST",
    "SOURCE_SUFFIXES",
    "PrincipleLimits",
    "check_principles",
    "duplication",
    "inheritance_depth",
    "is_source",
    "module_size",
    "nesting",
    "reformat_only",
    "source_corpus",
    "unused_public",
]
