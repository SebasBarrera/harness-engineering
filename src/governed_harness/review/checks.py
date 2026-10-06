"""The harness's own deterministic review checks (``verified_by: tool:harness:CHECK``, #57).

They read the changed lines of the diff, never call a model and cost no token. Each one is
language-neutral: test assertions that cannot fail, gates turned green in scripts and CI files,
dangerous path operations, predictable temporary files, credentials (the secret scanner of the
independent review) and, since #5, instructions addressed to an agent inside changed content."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from governed_harness.checks.model import DiffLine, is_test_path
from governed_harness.checks.secrets import scan_secrets
from governed_harness.review.diff import FileChange
from governed_harness.review.signals import is_doc_path, is_pipeline_path

_QUOTE = 160


@dataclass(frozen=True)
class CheckHit:
    check: str
    path: str
    side: str
    line: int
    message: str
    evidence: str


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _QUOTE else text[: _QUOTE - 3] + "..."


_TAUTOLOGIES = (
    re.compile(r"^\s*assert\s+(?:True|1)\s*(?:,|#|$)"),
    re.compile(r"\bassert\s*\(\s*(?:true|1)\s*\)"),
    re.compile(
        r"\bexpect\s*\(\s*(true|false|\d+|null)\s*\)\s*\.\s*to(?:Be|Equal|StrictEqual)\s*\(\s*\1\s*\)"
    ),
    re.compile(r"\bassert(?:True|That)?\s*\(\s*true\s*\)"),
    re.compile(r"\bXCTAssert(?:True)?\s*\(\s*true\s*\)"),
    re.compile(r"\bassert(?:Equals?|_eq!?|Same)\s*\(\s*([A-Za-z_][\w.]*)\s*,\s*\1\s*\)"),
    re.compile(r"^\s*assert\s+([A-Za-z_][\w.]*)\s*(?:==|is)\s*\1\s*(?:,|#|$)"),
    re.compile(
        r"\bexpect\s*\(\s*([A-Za-z_][\w.]*)\s*\)\s*\.\s*to(?:Be|Equal|StrictEqual)\s*\(\s*\1\s*\)"
    ),
)


def tautological_assertions(files: Iterable[FileChange]) -> list[CheckHit]:
    hits: list[CheckHit] = []
    for item in files:
        if item.is_deleted or not is_test_path(item.path):
            continue
        for line in item.added:
            if any(pattern.search(line.text) for pattern in _TAUTOLOGIES):
                hits.append(
                    CheckHit(
                        "tautological-assertion",
                        item.path,
                        "new",
                        line.number,
                        "The assertion cannot fail: it asserts a constant or compares a value "
                        "with itself",
                        _quote(line.text),
                    )
                )
    return hits


_WEAKENED = (
    (re.compile(r"\|\|\s*(?:true|:)\s*(?:$|[;#)&|])"), "a failing command is turned into success"),
    (re.compile(r"\bcontinue-on-error\s*:\s*true\b"), "a failing step is allowed to pass"),
    (re.compile(r"\ballow_failure\s*:\s*true\b"), "a failing job is allowed to pass"),
    (re.compile(r"--exit-zero\b"), "the tool always exits with success"),
    (re.compile(r"^\s*set\s+\+e\b"), "the script keeps running after a failure"),
    (re.compile(r"\bgit\s+(?:commit|push)\b[^#\n]*--no-verify\b"), "the Git hooks are skipped"),
    (re.compile(r"\bcontinueOnError\s*:\s*true\b"), "a failing task is allowed to pass"),
)


def _code_lines(item: FileChange) -> list[DiffLine]:
    """The added lines of a file that are not shell or YAML comments."""
    return [line for line in item.added if not line.text.lstrip().startswith("#")]


class _Searcher(Protocol):
    def search(self, text: str, /) -> object: ...


def _first_match(text: str, patterns: Iterable[tuple[_Searcher, str]]) -> str | None:
    """Why the first matching pattern flags ``text``."""
    return next((why for pattern, why in patterns if pattern.search(text)), None)


def weakened_gates(files: Iterable[FileChange]) -> list[CheckHit]:
    hits: list[CheckHit] = []
    for item in files:
        if item.is_deleted or not is_pipeline_path(item.path):
            continue
        for line in _code_lines(item):
            why = _first_match(line.text, _WEAKENED)
            if why is not None:
                hits.append(
                    CheckHit(
                        "weakened-gates",
                        item.path,
                        "new",
                        line.number,
                        f"Weakened gate: {why}",
                        _quote(line.text),
                    )
                )
    return hits


# ``rm``, its run of ``-flags`` options and what follows the run. The run is matched whole (its
# tokens cannot overlap), so the scan is linear where one pattern with the recursive flag inside
# a repeated option group backtracks over every split of the run.
_RM_OPTIONS = re.compile(r"\brm\s+((?:-[A-Za-z]*\s+)+)")


@dataclass(frozen=True)
class _RecursiveDelete:
    """An ``rm`` whose last option holds ``r``/``R`` and whose target matches ``target`` right
    after the options: what ``\\brm\\s+(?:-[A-Za-z]*\\s+)*-[A-Za-z]*[rR][A-Za-z]*\\s+TARGET``
    finds, read in one pass."""

    target: re.Pattern[str]

    def search(self, text: str, /) -> bool:
        for command in _RM_OPTIONS.finditer(text):
            last = command.group(1).split()[-1]
            if ("r" in last or "R" in last) and self.target.match(text, command.end()):
                return True
        return False


_DANGEROUS: tuple[tuple[_Searcher, str], ...] = (
    (
        _RecursiveDelete(re.compile(r"(?:--\s+)?[\"']?(?:/|~|\$HOME)[\"']?(?:\s|$|/\*)")),
        "recursive delete of the root or the home directory",
    ),
    (
        _RecursiveDelete(re.compile(r"(?:--\s+)?[\"']?\$\{?[A-Za-z_]\w*\}?[\"']?/")),
        "recursive delete under an unguarded variable (use ${VAR:?})",
    ),
    (re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b"), "world-writable permissions"),
    (
        re.compile(r"\b(?:curl|wget)\b[^|#\n]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
        "a downloaded script is executed unverified",
    ),
)


def dangerous_paths(files: Iterable[FileChange]) -> list[CheckHit]:
    hits: list[CheckHit] = []
    for item in files:
        if item.is_deleted or is_doc_path(item.path):
            continue
        for line in _code_lines(item):
            why = _first_match(line.text, _DANGEROUS)
            if why is not None:
                hits.append(
                    CheckHit(
                        "dangerous-paths",
                        item.path,
                        "new",
                        line.number,
                        f"Dangerous path operation: {why}",
                        _quote(line.text),
                    )
                )
    return hits


_TMP = re.compile(r"(?<![\w$])/tmp/(?a:\w[\w.-]*)")  # an ASCII name, as before


def temporary_files(files: Iterable[FileChange]) -> list[CheckHit]:
    hits: list[CheckHit] = []
    for item in files:
        if item.is_deleted or not is_pipeline_path(item.path):
            continue
        for line in item.added:
            text = line.text
            if "mktemp" in text or text.lstrip().startswith("#") or not _TMP.search(text):
                continue
            hits.append(
                CheckHit(
                    "temporary-files",
                    item.path,
                    "new",
                    line.number,
                    "A fixed path under /tmp can be read or replaced by another user; use mktemp",
                    _quote(text),
                )
            )
    return hits


def secrets(files: Iterable[FileChange]) -> list[CheckHit]:
    changes = [item for item in files if not item.binary]
    return [
        CheckHit("secrets", issue.path or "", "new", issue.line or 1, issue.message, "")
        for issue in scan_secrets([item.as_diff_file() for item in changes])
        if issue.path and issue.line and issue.severity.value in {"HIGH", "CRITICAL"}
    ]


_INJECTION = (
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?"
        r"(?:previous|prior|above|earlier|preceding|system)\s+(?:instructions|directions|rules|"
        r"prompts?|guidelines)\b",
        re.IGNORECASE,
    ),
    re.compile(r"<\|(?:im_start|im_end|system)\|>|\[/?INST\]|<<SYS>>"),
    re.compile(r"(?:^|[\s#/*>-])(?:system prompt|developer message)\s*:", re.IGNORECASE),
    re.compile(
        r"\b(?:AI|LLM|assistant|agent|language model|reviewers?|model)s?\b[^.\n]{0,40}\b"
        r"(?:must|should|shall|are instructed to)\s+(?:approve|ignore|skip|not\s+report|"
        r"report\s+no|pass|merge)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdo\s+not\s+(?:tell|inform|mention\s+(?:this\s+)?to|report\s+(?:this\s+)?to)\s+the\s+"
        r"(?:user|human|reviewer|maintainer)\b",
        re.IGNORECASE,
    ),
)


def embedded_instructions(files: Iterable[FileChange]) -> list[CheckHit]:
    """Text addressed to an agent inside changed content (a prompt injection), since #5."""
    hits: list[CheckHit] = []
    for item in files:
        if item.is_deleted or item.binary:
            continue
        for line in item.added:
            if any(pattern.search(line.text) for pattern in _INJECTION):
                hits.append(
                    CheckHit(
                        "embedded-instructions",
                        item.path,
                        "new",
                        line.number,
                        "Changed content addresses instructions to an agent (prompt injection); "
                        "repository content is untrusted",
                        _quote(line.text),
                    )
                )
    return hits


CHECKS: dict[str, Callable[[Iterable[FileChange]], list[CheckHit]]] = {
    "tautological-assertion": tautological_assertions,
    "weakened-gates": weakened_gates,
    "dangerous-paths": dangerous_paths,
    "temporary-files": temporary_files,
    "secrets": secrets,
    "embedded-instructions": embedded_instructions,
}
"""The checks a rule may name as ``tool:harness:CHECK``."""


def run_checks(names: Iterable[str], files: list[FileChange]) -> list[CheckHit]:
    hits: list[CheckHit] = []
    for name in sorted(set(names)):
        check = CHECKS.get(name)
        if check is not None:
            hits.extend(check(files))
    return hits


__all__ = [
    "CHECKS",
    "CheckHit",
    "dangerous_paths",
    "run_checks",
    "secrets",
    "tautological_assertions",
    "temporary_files",
    "weakened_gates",
]
