"""The change type of a ChangeSet, from its paths only (#58, item 6).

A file is *documentation* by its suffix (``.md``, ``.rst``, ``.adoc``, ``.txt``) or its
conventional name (``LICENSE``, ``CHANGELOG``...), *configuration* by its suffix (``.yaml``,
``.toml``, ``.ini``, ``.json``...) or its name (``.gitignore``, ``.editorconfig``...), and
*code* otherwise, tests included. A ChangeSet whose files are all documentation is
``documentation``, all configuration ``configuration``, a mix of the two
``documentation-configuration`` and anything with a code file ``code``. Only the first three are
exempt from requiring new tests and requirement traceability; every other check still runs (a
configuration change that weakens a control is still found by the checks that look for it)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Literal

ChangeType = Literal["documentation", "configuration", "documentation-configuration", "code"]
PathKind = Literal["documentation", "configuration", "code"]

_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt", ".asciidoc"})
_DOC_NAMES = frozenset(
    {
        "LICENSE",
        "LICENCE",
        "NOTICE",
        "AUTHORS",
        "CHANGELOG",
        "CHANGES",
        "CONTRIBUTORS",
        "COPYING",
        "README",
        "HISTORY",
        "CODEOWNERS",
    }
)
_CONFIG_SUFFIXES = frozenset(
    {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".json", ".jsonc", ".properties"}
)
_CONFIG_NAMES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".dockerignore",
        ".prettierrc",
        ".prettierignore",
        ".eslintrc",
        ".eslintignore",
        ".npmrc",
        ".nvmrc",
        ".python-version",
        ".tool-versions",
        ".markdownlint",
        ".env.example",
    }
)
_EXEMPT: frozenset[ChangeType] = frozenset(
    {"documentation", "configuration", "documentation-configuration"}
)


def path_kind(path: str) -> PathKind:
    """Whether one workspace path is documentation, configuration or code."""
    pure = PurePosixPath(path.replace("\\", "/"))
    name = pure.name
    suffix = pure.suffix.lower()
    if suffix in _DOC_SUFFIXES:
        return "documentation"
    if name in _CONFIG_NAMES or suffix in _CONFIG_SUFFIXES:
        return "configuration"
    if not suffix and name.upper() in _DOC_NAMES:
        return "documentation"
    return "code"


def change_type(paths: Iterable[str]) -> ChangeType:
    """The change type of the ChangeSet made of ``paths`` (``code`` when it is empty)."""
    kinds = {path_kind(path) for path in paths}
    if not kinds or "code" in kinds:
        return "code"
    if kinds == {"documentation"}:
        return "documentation"
    if kinds == {"configuration"}:
        return "configuration"
    return "documentation-configuration"


def exempt_from_tests(kind: ChangeType) -> bool:
    """Whether a ChangeSet of this type needs no new tests and no requirement traceability."""
    return kind in _EXEMPT


__all__ = ["ChangeType", "PathKind", "change_type", "exempt_from_tests", "path_kind"]
