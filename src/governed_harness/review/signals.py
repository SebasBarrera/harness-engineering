"""Diff slices and the signals that activate a reviewer (#57).

A reviewer reads only its slice of the diff (``sources``, ``tests``, ``pipeline``, ``all`` or
path globs) and runs only when the slice has its signal: no changed test, no tests reviewer; no
concurrency primitive on a changed line, no concurrency reviewer; no script, hook or CI file, no
pipeline reviewer. The signals of a language (its concurrency primitives, its calls that leave
the process) come from the standards packs (``review.yaml``); the machinery here knows no
language."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from governed_harness.checks.model import is_test_path
from governed_harness.review.diff import FileChange, glob_match
from governed_harness.review.reviewers import Reviewer
from governed_harness.review.rules import pack_review
from governed_harness.standards.packs import BUILTIN_PACKS, builtin_pack

PIPELINE_GLOBS: tuple[str, ...] = (
    ".github/workflows/*",
    ".github/actions/**",
    ".gitlab-ci.yml",
    ".gitlab/**",
    "bitbucket-pipelines.yml",
    "azure-pipelines.yml",
    ".azure-pipelines/**",
    ".circleci/**",
    "Jenkinsfile",
    ".gitea/workflows/*",
    ".forgejo/workflows/*",
    ".buildkite/**",
    ".travis.yml",
    "Makefile",
    "*.mk",
    "Dockerfile",
    "*.Dockerfile",
    "docker-compose*.yml",
    "*.sh",
    "*.bash",
    "scripts/**",
    "hooks/**",
    ".githooks/**",
    ".husky/**",
    "lefthook.yml",
)
"""Language-neutral files of the pipeline slice; packs add their own (``review.yaml``)."""

_DOC_SUFFIXES = (".md", ".rst", ".adoc", ".txt")


@lru_cache(maxsize=1)
def _pack_tables() -> tuple[
    tuple[tuple[str, tuple[str, ...]], ...], tuple[str, ...], dict[str, dict[str, tuple[str, ...]]]
]:
    extensions: list[tuple[str, tuple[str, ...]]] = []
    pipeline: list[str] = []
    signals: dict[str, dict[str, tuple[str, ...]]] = {}
    for pack in BUILTIN_PACKS:
        meta = pack_review(pack)
        extensions.append((pack, tuple(builtin_pack(pack).detect.extensions)))
        pipeline.extend(str(item) for item in meta.get("pipeline") or [])
        signals[pack] = {
            str(name): tuple(str(item) for item in patterns or [])
            for name, patterns in (meta.get("signals") or {}).items()
        }
    return tuple(extensions), tuple(dict.fromkeys(pipeline)), signals


def pipeline_globs() -> tuple[str, ...]:
    return (*PIPELINE_GLOBS, *_pack_tables()[1])


def is_pipeline_path(path: str) -> bool:
    return any(glob_match(path, pattern) for pattern in pipeline_globs())


def is_doc_path(path: str) -> bool:
    return path.lower().endswith(_DOC_SUFFIXES)


def packs_of(path: str) -> list[str]:
    suffix = PurePosixPath(path).suffix.lower()
    return [pack for pack, extensions in _pack_tables()[0] if suffix and suffix in extensions]


def signal_patterns(name: str, path: str, packs: Sequence[str] | None = None) -> tuple[str, ...]:
    """The patterns of signal ``name`` for a file, from the packs of its extension (limited to
    the project's packs when given)."""
    table = _pack_tables()[2]
    found: list[str] = []
    for pack in packs_of(path):
        if packs is not None and pack not in packs:
            continue
        found.extend(table.get(pack, {}).get(name, ()))
    return tuple(dict.fromkeys(found))


def in_slice(path: str, diff_slice: str | tuple[str, ...]) -> bool:
    if isinstance(diff_slice, tuple):
        return any(glob_match(path, pattern) for pattern in diff_slice)
    if diff_slice == "all":
        return True
    if diff_slice == "tests":
        return is_test_path(path)
    if diff_slice == "pipeline":
        return is_pipeline_path(path)
    return not is_test_path(path) and not is_pipeline_path(path) and not is_doc_path(path)


def slice_files(files: Iterable[FileChange], diff_slice: str | tuple[str, ...]) -> list[FileChange]:
    return [item for item in files if not item.binary and in_slice(item.path, diff_slice)]


@dataclass(frozen=True)
class Activation:
    active: bool
    reason: str
    signals: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"active": self.active, "reason": self.reason}
        if self.signals:
            value["signals"] = list(self.signals)
        return value


def _changed_text(item: FileChange) -> Iterable[str]:
    for line in item.added:
        yield line.text
    for line in item.removed:
        yield line.text


def activation(
    reviewer: Reviewer, files: list[FileChange], packs: Sequence[str] | None = None
) -> Activation:
    """Whether a reviewer runs on its slice, and why (recorded in the report)."""
    spec = reviewer.spec
    if not files:
        return Activation(False, "no file of its slice changed")
    hits: list[str] = []
    for pattern in spec.patterns:
        compiled = re.compile(pattern)
        for item in files:
            if any(compiled.search(text) for text in _changed_text(item)):
                hits.append(f"pattern:{pattern}@{item.path}")
                break
    if spec.activation == "always":
        return Activation(True, "always", tuple(hits))
    if spec.activation == "changed":
        return Activation(True, f"{len(files)} file(s) of its slice changed", tuple(hits))
    name = spec.activation.split(":", 1)[1]
    for item in files:
        for pattern in signal_patterns(name, item.path, packs):
            compiled = re.compile(pattern)
            if any(compiled.search(text) for text in _changed_text(item)):
                hits.append(f"{name}@{item.path}")
                break
    if hits:
        return Activation(True, f"signal {name} on a changed line", tuple(hits))
    return Activation(False, f"no {name} signal on a changed line of its slice")


__all__ = [
    "PIPELINE_GLOBS",
    "Activation",
    "activation",
    "in_slice",
    "is_doc_path",
    "is_pipeline_path",
    "packs_of",
    "pipeline_globs",
    "signal_patterns",
    "slice_files",
]
