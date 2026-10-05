"""A bounded, deterministic context manifest for the agent (issue #41).

Agents spend much of a run discovering which files matter. The manifest gives them a short,
ranked starting list chosen only from the task and the workspace (paths the task names, the
declared interfaces, files changed by earlier steps, requirement ids and key terms), so the same
task and workspace always give the same manifest and its digest can be recorded as evidence.

The manifest is guidance, never a restriction: the agent may still read any file of the
workspace, and the sandbox, not this list, decides what it can touch.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from governed_harness.domain.models import Task
from governed_harness.evidence.hashing import sha256_bytes, sha256_json
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

__all__ = [
    "GUIDANCE",
    "MAX_FILE_BYTES",
    "build_manifest",
    "key_terms",
    "referenced_paths",
]

GUIDANCE = (
    "Start from these files; they were chosen deterministically from the task. "
    "You may read any other file of the workspace."
)
MAX_FILE_BYTES = 1024 * 1024
"""Files larger than this are never candidates: they are usually generated or data files."""

_BINARY_PROBE = 8192
_WORD = re.compile(r"[A-Za-z0-9_]+")
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./\-]+")
_STOPWORDS: frozenset[str] = frozenset(
    {
        "about",
        "above",
        "after",
        "again",
        "against",
        "always",
        "among",
        "before",
        "being",
        "below",
        "between",
        "could",
        "doing",
        "during",
        "every",
        "first",
        "given",
        "having",
        "might",
        "never",
        "other",
        "should",
        "second",
        "shall",
        "since",
        "their",
        "there",
        "these",
        "third",
        "those",
        "through",
        "under",
        "until",
        "using",
        "value",
        "values",
        "where",
        "which",
        "while",
        "within",
        "without",
        "would",
        "return",
        "returns",
        "must",
    }
)


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def key_terms(task: Task, limit: int = 12) -> tuple[str, ...]:
    """The most frequent meaningful words of the task's title, requirements and criteria.

    Short words and common English words carry no signal about which files matter, so they
    are dropped; ties are broken alphabetically to keep the result deterministic.
    """
    texts = [task.title]
    texts += [requirement.text for requirement in task.requirements]
    texts += [criterion.text for criterion in task.acceptance_criteria]
    counts: Counter[str] = Counter(
        word for text in texts for word in _words(text) if len(word) >= 5 and word not in _STOPWORDS
    )
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(word for word, _count in ranked[:limit])


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _task_texts(task: Task) -> list[str]:
    texts = [task.intent, *task.constraints]
    texts += [requirement.text for requirement in task.requirements]
    for criterion in task.acceptance_criteria:
        texts.append(criterion.text)
        if criterion.verification_hint:
            texts.append(criterion.verification_hint)
    texts += list(_strings(task.metadata))
    return texts


def _normalise(path: str) -> str:
    """A workspace-relative POSIX path without a leading ``./``."""
    cleaned = path.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _inside(root: Path, relative: str) -> bool:
    if not relative or relative.startswith("/") or PurePosixPath(relative).is_absolute():
        return False
    if ".." in PurePosixPath(relative).parts:
        return False
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return resolved.is_file()


def referenced_paths(task: Task, workspace: Path) -> tuple[str, ...]:
    """Workspace files the task mentions by path.

    Only paths that stay inside the workspace count: a ``../`` or absolute path in the task is
    ignored rather than followed, so the manifest can never point outside the root.
    """
    root = workspace.resolve()
    found: set[str] = set()
    for text in _task_texts(task):
        for token in _PATH_TOKEN.findall(text):
            candidate = _normalise(token.rstrip(".-").lstrip("-"))
            if "/" not in candidate and "." not in candidate:
                continue
            if _inside(root, candidate):
                found.add(candidate)
    return tuple(sorted(found))


def _walk(root: Path, excludes: Collection[str]) -> Iterator[tuple[str, Path]]:
    """Regular files of the workspace in sorted order, without excluded dirs or symlinks."""
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in excludes and not (base / name).is_symlink()
        ]
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path.relative_to(root).as_posix(), path


def _read_text(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:_BINARY_PROBE]:
        return None
    return data


def _id_pattern(identifier: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(identifier)}(?!\w)")


def _score(
    relative: str,
    content: str,
    *,
    referenced: set[str],
    interfaces: set[str],
    changed: set[str],
    lessons: set[str],
    id_patterns: Sequence[tuple[str, re.Pattern[str]]],
    terms: Sequence[str],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for paths, points, reason in (
        (referenced, 100, "referenced by the task"),
        (interfaces, 60, "declared interface"),
        (changed, 50, "changed by an earlier step"),
        (lessons, 20, "path of an active lesson"),
    ):
        if relative in paths:
            score += points
            reasons.append(reason)

    named = [identifier for identifier, pattern in id_patterns if pattern.search(content)]
    if named:
        score += min(40 + 5 * (len(named) - 1), 60)
        reasons.append(f"names {', '.join(named[:5])}")
        if "test" in relative.lower():
            score += 10
            reasons.append("test naming a requirement")

    lowered_path = relative.lower()
    in_path = [term for term in terms if term in lowered_path]
    if in_path:
        score += min(10 * len(in_path), 30)
        reasons.extend(f"path mentions {term}" for term in in_path[:3])

    content_words = set(_words(content))
    in_content = [term for term in terms if term in content_words]
    if in_content:
        score += min(2 * len(in_content), 20)
        reasons.append(f"mentions {len(in_content)} key term(s)")
    return score, reasons


def build_manifest(
    workspace: Path,
    task: Task,
    *,
    changed_paths: Sequence[str] = (),
    interface_paths: Sequence[str] = (),
    lesson_paths: Sequence[str] = (),
    max_files: int = 40,
    max_bytes: int = 400_000,
    excludes: Collection[str] = DEFAULT_EXCLUDES,
) -> dict[str, Any]:
    """Rank the workspace files by relevance to the task and keep the best within the limits.

    A file that would exceed ``max_bytes`` is skipped and the next one is tried, so one large
    file does not crowd out several small relevant ones.
    """
    root = workspace.resolve()
    terms = key_terms(task)
    identifiers = list(
        dict.fromkeys(
            [requirement.requirement_id for requirement in task.requirements]
            + [criterion.criterion_id for criterion in task.acceptance_criteria]
        )
    )
    id_patterns = [(identifier, _id_pattern(identifier)) for identifier in identifiers]
    referenced = set(referenced_paths(task, root))
    interfaces = {_normalise(path) for path in interface_paths}
    changed = {_normalise(path) for path in changed_paths}
    lessons = {_normalise(path) for path in lesson_paths}

    candidates: list[tuple[int, str, int, str, list[str]]] = []
    for relative, path in _walk(root, excludes):
        data = _read_text(path)
        if data is None:
            continue
        score, reasons = _score(
            relative,
            data.decode("utf-8", errors="replace"),
            referenced=referenced,
            interfaces=interfaces,
            changed=changed,
            lessons=lessons,
            id_patterns=id_patterns,
            terms=terms,
        )
        if score > 0:
            candidates.append((score, relative, len(data), sha256_bytes(data), reasons))
    candidates.sort(key=lambda item: (-item[0], item[1]))

    files: list[dict[str, Any]] = []
    total = 0
    for score, relative, size, digest, reasons in candidates:
        if len(files) >= max_files:
            break
        if total + size > max_bytes:
            continue
        total += size
        files.append(
            {
                "path": relative,
                "sizeBytes": size,
                "digest": digest,
                "score": score,
                "reasons": reasons,
            }
        )
    return {
        "schemaVersion": "1.0",
        "files": files,
        "terms": list(terms),
        "limits": {"maxFiles": max_files, "maxBytes": max_bytes},
        "candidates": len(candidates),
        "omitted": len(candidates) - len(files),
        "totalBytes": total,
        "guidance": GUIDANCE,
        "digest": sha256_json({"files": files, "terms": list(terms)}),
    }
