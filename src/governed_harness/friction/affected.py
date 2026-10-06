"""The Python tests a ChangeSet affects (#58, item 5), found from the paths and the imports.

A test file is affected when the ChangeSet changes it, when its name names a changed module
(``test_pricing.py`` for ``pricing.py``) or when it imports a changed module (``import pricing``,
``from sample.pricing import ...``, ``from sample import pricing``). Nothing is executed; the
selection only orders the work: the affected tests run first so a failing attempt stops early,
and the full suite still runs before the gate. Other technologies have no selection (their
full suite runs as before)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from governed_harness.checks.model import is_test_path
from governed_harness.validators.traceability import discover_test_files

MAX_TEST_FILE_BYTES = 1_000_000
MAX_AFFECTED_TESTS = 200
_SOURCE_ROOTS = ("src/", "lib/")


def _module_names(path: str) -> set[str]:
    """Dotted names a changed Python file can be imported as (with and without the source
    root), and its last component."""
    pure = PurePosixPath(path)
    parts = list(pure.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return set()
    names = {".".join(parts), parts[-1]}
    for root in _SOURCE_ROOTS:
        prefix = PurePosixPath(root).parts
        if tuple(parts[: len(prefix)]) == prefix and len(parts) > len(prefix):
            names.add(".".join(parts[len(prefix) :]))
    return {name for name in names if name}


def _imports(text: str, names: set[str]) -> bool:
    for name in names:
        dotted = re.escape(name)
        head, _, last = name.rpartition(".")
        patterns = [
            rf"^\s*import\s+{dotted}\b",
            rf"^\s*from\s+{dotted}(\.\w+)*\s+import\b",
        ]
        if head:
            patterns.append(rf"^\s*from\s+{re.escape(head)}\s+import\s+[^#\n]*\b{last}\b")
        if any(re.search(pattern, text, re.MULTILINE) for pattern in patterns):
            return True
    return False


def affected_python_tests(workspace: Path, changed: Iterable[str]) -> list[str]:
    """Workspace-relative test files affected by the ``changed`` paths, sorted (at most
    ``MAX_AFFECTED_TESTS``). Empty when no Python file changed."""
    changed_paths = [item.replace("\\", "/") for item in changed]
    python = [item for item in changed_paths if item.endswith(".py")]
    if not python:
        return []
    selected: set[str] = {
        item
        for item in python
        if is_test_path(item)
        and PurePosixPath(item).name.startswith("test")
        and (workspace / item).is_file()
    }
    sources = [item for item in python if not is_test_path(item)]
    names: set[str] = set()
    stems: set[str] = set()
    for item in sources:
        names |= _module_names(item)
        stems.add(PurePosixPath(item).stem)
    if names:
        for relative in discover_test_files(workspace, ("python",)):
            if relative in selected or not relative.endswith(".py"):
                continue
            stem = PurePosixPath(relative).stem
            if stem.removeprefix("test_") in stems or stem.removesuffix("_test") in stems:
                selected.add(relative)
                continue
            path = workspace / relative
            try:
                if path.stat().st_size > MAX_TEST_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _imports(text, names):
                selected.add(relative)
    return sorted(selected)[:MAX_AFFECTED_TESTS]


__all__ = ["affected_python_tests"]
