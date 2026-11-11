"""Language standards packs (#56): rule cards for the agent and the tools that verify them.

A pack is two files shipped as package resources, ``standards/<pack>/cards.yaml`` and
``standards/<pack>/tools.yaml``:

* a **card** is one short rule with an id, the rule, its rationale, explicit exceptions, the
  files it applies to (globs) and what verifies it: tool rule ids (``ruff:B006``,
  ``eslint:eqeqeq``) or ``review`` when only a reviewer can judge it;
* a **tool** is a linter or analyzer with the configuration files that show the repository uses
  it, the command that runs it with machine-readable output, the parser of that output and a
  recommended configuration (documentation; the harness never writes or installs it);
* the pack also declares how to detect its language (marker files, extensions, dependencies),
  its test and BDD runners and the BDD frameworks it recognizes.

The repository's own standards take precedence: ``<standards.path>/<pack>/cards.yaml`` replaces
cards with the same id, adds new ones and may list ``disabled`` ids; ``tools.yaml`` there
replaces tools by id. A directory there with an id no pack has is a pack of the repository."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from governed_harness.domain.errors import ConfigurationError
from governed_harness.evidence.hashing import sha256_json
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

BUILTIN_PACKS: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "node",
    "react",
    "angular",
    "vue",
    "java",
    "kotlin",
    "go",
    "rust",
    "swift",
    "csharp",
    "php",
    "ruby",
)
"""The packs shipped with the harness, in the order their cards are listed."""

_CARD_ID = re.compile(r"^[a-z][a-z0-9-]*\.[a-z0-9][a-z0-9.-]*$")
_MAX_WALK_FILES = 20_000
_MAX_MANIFEST_BYTES = 400_000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class Card(_Model):
    card_id: str = Field(alias="id")
    title: str
    rule: str
    rationale: str = ""
    exceptions: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = Field(alias="appliesTo", min_length=1)
    verified_by: tuple[str, ...] = Field(alias="verifiedBy", min_length=1)
    principle: str | None = None

    @field_validator("card_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _CARD_ID.match(value):
            raise ValueError(f"card id must be <pack>.<name> in lower case: {value!r}")
        return value

    @property
    def needs_review(self) -> bool:
        """A card no tool verifies: the reviewer checks it."""
        return "review" in self.verified_by

    def applies(self, path: str) -> bool:
        return any(_glob_match(path, pattern) for pattern in self.applies_to)

    def compact(self) -> dict[str, Any]:
        """What an agent receives: id, rule and exceptions (no rationale, to save tokens)."""
        value: dict[str, Any] = {"id": self.card_id, "rule": self.rule}
        if self.exceptions:
            value["exceptions"] = list(self.exceptions)
        return value


class PackTool(_Model):
    tool_id: str = Field(alias="id")
    title: str = ""
    config_files: tuple[str, ...] = Field(default=(), alias="configFiles")
    """Files that show the repository uses the tool; ``file#section`` also requires the
    section text in the file (``pyproject.toml#[tool.ruff``)."""
    command: tuple[str, ...] | None = None
    parser: str | None = None
    covered_by: tuple[str, ...] = Field(default=(), alias="coveredBy")
    """Profile validators that already run the tool: it is not added twice."""
    principles: tuple[str, ...] = ()
    """Engineering principles the tool's rules give evidence of (``yagni``, ``kiss``...)."""
    recommended: str | None = None
    """Recommended configuration, for the documentation and ``harness standards show``."""


class Detection(_Model):
    markers: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    """Names that must appear in a dependency manifest (package.json, pyproject.toml...)."""
    requires: tuple[str, ...] = ()
    """Packs that must also be detected (a framework pack needs its language)."""


class Runner(_Model):
    test: tuple[str, ...] | None = None
    test_files: bool = Field(default=False, alias="testFiles")
    """Whether the test runner accepts test file paths as arguments."""
    bdd: tuple[str, ...] | None = None


class BddFramework(_Model):
    name: str
    markers: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()


class Pack(_Model):
    pack_id: str = Field(alias="pack")
    title: str
    extends: tuple[str, ...] = ()
    detect: Detection = Field(default_factory=Detection)
    runner: Runner = Field(default_factory=Runner)
    bdd_frameworks: tuple[BddFramework, ...] = Field(default=(), alias="bddFrameworks")
    tools: tuple[PackTool, ...] = ()
    cards: tuple[Card, ...] = ()
    source: str = "builtin"

    @property
    def digest(self) -> str:
        return sha256_json(self.model_dump(mode="json", by_alias=True))


def _glob_match(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    if fnmatch.fnmatchcase(path, pattern):
        return True
    # "**/*.py" also matches a file at the root.
    return pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])


# ----- loading --------------------------------------------------------------------------------
_CARDS_FILE = "cards.yaml"
_TOOLS_FILE = "tools.yaml"


def _resource_yaml(pack: str, name: str) -> dict[str, Any]:
    target = resources.files("governed_harness.resources").joinpath("standards", pack, name)
    if not target.is_file():
        return {}
    value = yaml.safe_load(target.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=64)
def builtin_pack(pack: str) -> Pack:
    if pack not in BUILTIN_PACKS:
        raise ConfigurationError(
            f"unknown standards pack {pack!r}; known: {', '.join(BUILTIN_PACKS)}"
        )
    cards = _resource_yaml(pack, _CARDS_FILE)
    tools = _resource_yaml(pack, _TOOLS_FILE)
    return Pack.model_validate({**tools, **cards, "pack": pack})


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"cannot read standards file {path}: {error}") from error
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"standards file {path} must be a mapping")
    return value


def load_pack(
    pack: str,
    workspace: Path | None = None,
    overrides: str | None = None,
    disabled: tuple[str, ...] = (),
) -> Pack:
    """A pack with the repository's overrides applied (repository > pack)."""
    directory = (workspace / overrides / pack) if workspace is not None and overrides else None
    local_cards = _local_yaml(directory, _CARDS_FILE)
    local_tools = _local_yaml(directory, _TOOLS_FILE)
    if pack in BUILTIN_PACKS:
        base = builtin_pack(pack)
    elif local_cards or local_tools:
        base = _repository_pack(pack, local_cards, local_tools)
        local_cards, local_tools = {}, {}
    else:
        raise ConfigurationError(
            f"unknown standards pack {pack!r}; known: {', '.join(BUILTIN_PACKS)}"
        )
    local_card_items, local_tool_items = _local_items(pack, local_cards, local_tools)
    off = set(disabled) | set(local_cards.get("disabled") or [])
    cards: dict[str, Card] = {item.card_id: item for item in base.cards}
    cards.update({item.card_id: item for item in local_card_items})
    tools: dict[str, PackTool] = {item.tool_id: item for item in base.tools}
    tools.update({item.tool_id: item for item in local_tool_items})
    changed = bool(local_card_items or local_tool_items or off & set(cards))
    return base.model_copy(
        update={
            "cards": tuple(item for item in cards.values() if item.card_id not in off),
            "tools": tuple(tools.values()),
            "source": "builtin+repository" if changed and base.source == "builtin" else base.source,
        }
    )


def _local_yaml(directory: Path | None, name: str) -> dict[str, Any]:
    """A file of the repository's overrides of a pack, empty when there is none."""
    if directory is None or not (directory / name).is_file():
        return {}
    return _read_yaml(directory / name)


def _repository_pack(pack: str, local_cards: dict[str, Any], local_tools: dict[str, Any]) -> Pack:
    """A pack the repository defines on its own (no built-in pack of that name)."""
    try:
        return Pack.model_validate(
            {
                "title": pack,
                **{k: v for k, v in local_tools.items() if k != "disabled"},
                **{k: v for k, v in local_cards.items() if k != "disabled"},
                "pack": pack,
                "source": "repository",
            }
        )
    except ValueError as error:
        raise ConfigurationError(f"invalid repository pack {pack}: {error}") from error


def _local_items(
    pack: str, local_cards: dict[str, Any], local_tools: dict[str, Any]
) -> tuple[list[Card], list[PackTool]]:
    try:
        cards = [Card.model_validate(item) for item in local_cards.get("cards") or []]
        tools = [PackTool.model_validate(item) for item in local_tools.get("tools") or []]
    except ValueError as error:
        raise ConfigurationError(f"invalid repository standards for {pack}: {error}") from error
    return cards, tools


# ----- detection ------------------------------------------------------------------------------
@dataclass
class WorkspaceFacts:
    """What detection reads from a workspace, once: file names, extensions, manifests."""

    files: list[str] = field(default_factory=list)
    extensions: dict[str, int] = field(default_factory=dict)
    manifests: str = ""

    @classmethod
    def read(cls, workspace: Path) -> WorkspaceFacts:
        facts = cls()
        root = workspace.resolve()
        manifests: list[str] = []
        for path in _walk(root):
            relative = path.relative_to(root).as_posix()
            facts.files.append(relative)
            suffix = path.suffix.lower()
            if suffix:
                facts.extensions[suffix] = facts.extensions.get(suffix, 0) + 1
            if _is_manifest(path.name):
                try:
                    manifests.append(
                        path.read_bytes()[:_MAX_MANIFEST_BYTES].decode("utf-8", "replace")
                    )
                except OSError:
                    continue
        facts.manifests = "\n".join(manifests)
        return facts

    def has(self, marker: str) -> bool:
        if any(char in marker for char in "*?["):
            return any(_glob_match(item, marker) for item in self.files)
        return marker in self.files or any(
            item.startswith(marker.rstrip("/") + "/") for item in self.files
        )

    def depends_on(self, name: str) -> bool:
        return re.search(rf"(?<![\w@/.-]){re.escape(name)}(?![\w-])", self.manifests) is not None


_MANIFESTS = (
    "package.json",
    "pyproject.toml",
    "setup.cfg",
    "Pipfile",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Cargo.toml",
    "go.mod",
    "Package.swift",
)


def _is_manifest(name: str) -> bool:
    return (
        name in _MANIFESTS
        or name.endswith(".csproj")
        or (name.startswith("requirements") and name.endswith(".txt"))
    )


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    stack = [root]
    while stack and len(found) < _MAX_WALK_FILES:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name in DEFAULT_EXCLUDES or entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                found.append(entry)
    return found


def detect_packs(workspace: Path, technologies: tuple[str, ...] = ()) -> list[str]:
    """The packs whose language the workspace uses, in ``BUILTIN_PACKS`` order. A profile
    technology (``python``, ``node``) selects its pack directly."""
    facts = WorkspaceFacts.read(workspace)
    return detect_from_facts(facts, technologies)


def detect_from_facts(facts: WorkspaceFacts, technologies: tuple[str, ...] = ()) -> list[str]:
    found: list[str] = []
    for pack_id in BUILTIN_PACKS:
        detection = builtin_pack(pack_id).detect
        hit = pack_id in technologies
        if not hit and detection.markers:
            hit = any(facts.has(marker) for marker in detection.markers)
        if not hit and detection.extensions:
            hit = any(facts.extensions.get(ext, 0) for ext in detection.extensions)
        if hit and detection.dependencies:
            hit = any(facts.depends_on(name) for name in detection.dependencies)
        if hit and detection.requires:
            hit = all(item in found for item in detection.requires)
        if hit:
            found.append(pack_id)
    return found


def bdd_frameworks(packs: list[Pack], facts: WorkspaceFacts) -> list[str]:
    """The BDD frameworks the repository shows (feature files alone count as ``gherkin``)."""
    names: list[str] = []
    for pack in packs:
        for framework in pack.bdd_frameworks:
            marker_hit = any(facts.has(item) for item in framework.markers)
            dependency_hit = any(facts.depends_on(item) for item in framework.dependencies)
            if marker_hit or dependency_hit:
                names.append(framework.name)
    if not names and any(item.endswith(".feature") for item in facts.files):
        names.append("gherkin")
    return list(dict.fromkeys(names))


# ----- the packs of a project ------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectStandards:
    packs: tuple[Pack, ...]
    detected: tuple[str, ...]
    configured: tuple[str, ...] | None

    @property
    def digest(self) -> str:
        return sha256_json([item.digest for item in self.packs])

    def pack(self, pack_id: str) -> Pack | None:
        return next((item for item in self.packs if item.pack_id == pack_id), None)


def _with_extends(ids: list[str]) -> list[str]:
    ordered: list[str] = []
    for pack_id in ids:
        if pack_id in BUILTIN_PACKS:
            for parent in builtin_pack(pack_id).extends:
                if parent not in ordered:
                    ordered.append(parent)
        if pack_id not in ordered:
            ordered.append(pack_id)
    return ordered


def _repository_pack_ids(local: Path, packs: tuple[str, ...] | None) -> list[str]:
    """The repository-only packs (directories of the overrides that name no built-in pack)
    the configuration selects: all of them under ``auto`` or no list."""
    if not local.is_dir():
        return []
    return [
        directory.name
        for directory in sorted(local.iterdir())
        if directory.is_dir()
        and directory.name not in BUILTIN_PACKS
        and (packs is None or "auto" in packs or directory.name in packs)
    ]


def project_standards(
    workspace: Path,
    *,
    packs: tuple[str, ...] | None,
    overrides: str | None,
    disabled: tuple[str, ...] = (),
    technologies: tuple[str, ...] = (),
) -> ProjectStandards:
    """The effective packs: configured ids (``auto`` expands to the detected ones) plus the
    packs they extend, with the repository's overrides and repository-only packs."""
    detected = detect_packs(workspace, technologies)
    wanted: list[str] = []
    for item in packs or ("auto",):
        if item == "auto":
            wanted.extend(detected)
        else:
            wanted.append(item)
    if overrides:
        wanted.extend(_repository_pack_ids(workspace / overrides, packs))
    ids = _with_extends(list(dict.fromkeys(wanted)))
    return ProjectStandards(
        packs=tuple(load_pack(item, workspace, overrides, disabled) for item in ids),
        detected=tuple(detected),
        configured=packs,
    )


def describe_pack(pack: Pack, *, include_cards: bool = True) -> dict[str, Any]:
    value: dict[str, Any] = {
        "pack": pack.pack_id,
        "title": pack.title,
        "source": pack.source,
        "digest": pack.digest,
        "extends": list(pack.extends),
        "runner": pack.runner.model_dump(mode="json", by_alias=True, exclude_none=True),
        "bddFrameworks": [item.name for item in pack.bdd_frameworks],
        "tools": [
            {
                "id": item.tool_id,
                "configFiles": list(item.config_files),
                "command": list(item.command or ()),
                "parser": item.parser,
                "coveredBy": list(item.covered_by),
                "recommended": item.recommended,
            }
            for item in pack.tools
        ],
        "cards": len(pack.cards),
    }
    if include_cards:
        value["cards"] = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in pack.cards
        ]
    return value


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


__all__ = [
    "BUILTIN_PACKS",
    "BddFramework",
    "Card",
    "Detection",
    "Pack",
    "PackTool",
    "ProjectStandards",
    "Runner",
    "WorkspaceFacts",
    "bdd_frameworks",
    "builtin_pack",
    "describe_pack",
    "detect_from_facts",
    "detect_packs",
    "load_pack",
    "project_standards",
]
