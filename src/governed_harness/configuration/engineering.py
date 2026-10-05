"""Configuration of the engineering settings (since 1.1, issue #56).

Forges, language standards packs, engineering principles, the testing strategy, the
architecture and the project setup questions of a new project. Every section and key is
optional: a key that is absent keeps the 1.0.0 behaviour and is left out of the serialized
configuration, so the configuration snapshot (and its digest) of a project written before these
settings existed does not change. ``harness init`` writes them.

Token cost rule of the whole section: tools verify, models only judge what tools cannot. The
standards cards sent to an agent are the ones that apply to the files it works on, the
principles checklist rides on the existing review call, and the architecture survey of an
existing project is one call per project, cached."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from governed_harness.configuration.agent_results import (
    AgentCallConfig,
    Policy,
    _Section,
    off_from_yaml,
)
from governed_harness.domain.enums import FindingSeverity

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _relative(value: str, what: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value.strip() or value.startswith("/") or ".." in path.parts:
        raise ValueError(f"{what} must be relative to the workspace: {value!r}")
    return value


# ----- forges (#56, item 1) ------------------------------------------------------------------
ForgeKind = Literal["auto", "github", "gitlab", "bitbucket", "azure-devops", "gitea"]
ForgeTransport = Literal["cli", "api"]


class ForgeConfig(_Section):
    """Where pull or merge requests, comments, statuses and quality reports go.

    ``kind: auto`` (or absent) detects the forge from the ``origin`` remote: github.com,
    gitlab.com or a host with ``gitlab`` in its name, bitbucket.org, dev.azure.com or
    ``*.visualstudio.com``, and a host with ``gitea`` or ``forgejo`` in its name. Tokens are
    read only from the environment variable ``tokenEnv`` (default per forge) and never logged.
    """

    kind: ForgeKind | None = None
    transport: ForgeTransport | None = None
    repository: str | None = None
    """``owner/name`` (GitHub, Gitea, Bitbucket ``workspace/repo``), a GitLab project path
    (``group/subgroup/project``) or ``organization/project/repository`` (Azure DevOps).
    Default: derived from the ``origin`` remote."""
    api_url: str | None = Field(default=None, alias="apiUrl")
    token_env: str | None = Field(default=None, alias="tokenEnv")
    base_branch: str | None = Field(default=None, alias="baseBranch")
    labels: tuple[str, ...] | None = None
    template: str | None = None
    """A pull request template in the repository whose text heads the description."""
    draft: bool | None = None
    code_quality: bool | None = Field(default=None, alias="codeQuality")
    """Also attach the findings as a GitLab Code Quality report (written next to SARIF)."""

    @field_validator("token_env")
    @classmethod
    def _env(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.match(value):
            raise ValueError(f"tokenEnv is not an environment variable name: {value!r}")
        return value

    @field_validator("api_url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("apiUrl must use https (or point to localhost)")
        return value.rstrip("/")

    @field_validator("template")
    @classmethod
    def _template(cls, value: str | None) -> str | None:
        return _relative(value, "template") if value is not None else None

    @field_validator("repository")
    @classmethod
    def _repository(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_.~-]+(/[A-Za-z0-9_. ~-]+)+", value):
            raise ValueError(f"repository must be a path such as owner/name: {value!r}")
        return value


# ----- standards packs (#56, item 2) --------------------------------------------------------
CardsMode = Literal["auto", "off"]
ToolsMode = Literal["detect", "off"]
DEFAULT_STANDARDS_PATH = ".harness/standards"
DEFAULT_MAX_CARDS = 12


class StandardsConfig(_Section):
    """Language standards packs shipped with the harness.

    * ``packs``: ``[auto]`` (or absent) selects the packs of the detected languages; otherwise
      pack ids (``python``, ``typescript``, ``java``, ...).
    * ``cards: auto``: the cards that apply to the files a call works on are added, compact, to
      the implement request; the cards a tool cannot verify become the checklist of the
      existing review call. Selected deterministically and cached by digest.
    * ``tools: detect``: each pack tool whose configuration file is in the repository becomes
      an optional validator (parsed output, never installed by the harness).
    * ``path``: repository overrides (``<path>/<pack>/cards.yaml``) take precedence over the
      pack: a card with the same id replaces the pack's card, ``disabled`` drops cards.
    """

    packs: tuple[str, ...] | None = None
    cards: CardsMode | None = None
    max_cards: int | None = Field(default=None, alias="maxCards", ge=1, le=100)
    tools: ToolsMode | None = None
    path: str | None = None
    disabled: tuple[str, ...] | None = None

    @field_validator("cards", "tools", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        return _relative(value, "standards.path") if value is not None else None

    @property
    def cards_enabled(self) -> bool:
        return self.cards == "auto"

    @property
    def tools_enabled(self) -> bool:
        return self.tools == "detect"

    @property
    def overrides_path(self) -> str:
        return self.path or DEFAULT_STANDARDS_PATH

    @property
    def card_limit(self) -> int:
        return self.max_cards or DEFAULT_MAX_CARDS


# ----- engineering principles (#56, item 3) -------------------------------------------------
DEFAULT_DUPLICATION_WINDOW = 6
DEFAULT_INHERITANCE_DEPTH = 3


class PrinciplesConfig(_Section):
    """Engineering principles checked by deterministic proxies in VERIFICATION, with a
    checklist for the rest inside the existing review call (no extra agent call).

    ``mode: enforce`` makes the proxies a mandatory check whose HIGH findings block; ``warn``
    records them as LOW. ``duplicationWindow`` (lines), ``maxInheritanceDepth``,
    ``unusedPublic`` (YAGNI), ``boyScout`` (changes in files outside the task scope) and
    ``checklist`` (the review call) may be set one by one."""

    mode: Policy | None = None
    duplication_window: int | None = Field(default=None, alias="duplicationWindow", ge=3, le=200)
    max_inheritance_depth: int | None = Field(
        default=None, alias="maxInheritanceDepth", ge=1, le=20
    )
    unused_public: bool | None = Field(default=None, alias="unusedPublic")
    boy_scout: bool | None = Field(default=None, alias="boyScout")
    checklist: bool | None = None
    severity: FindingSeverity | None = None
    """Severity of a principle finding under ``enforce`` (default ``MEDIUM``: shown, not
    blocking under the default ``findingBlockSeverities``; ``HIGH`` makes them block)."""

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.mode in {"enforce", "warn"}

    @property
    def window(self) -> int:
        return self.duplication_window or DEFAULT_DUPLICATION_WINDOW

    @property
    def inheritance_depth(self) -> int:
        return self.max_inheritance_depth or DEFAULT_INHERITANCE_DEPTH


# ----- testing strategy (#56, item 4) -------------------------------------------------------
TestingStrategy = Literal["auto", "tdd", "bdd", "conventional"]
DEFAULT_FEATURES_DIRECTORY = "features"


class TestingConfig(_Section):
    """How the change is tested.

    * ``strategy: auto`` follows what the repository does (feature files or a BDD framework:
      ``bdd``; tests: ``conventional``) and, when nothing is known, asks in INTENT;
    * ``tdd``: the tests the change adds must fail on the code before it (red), pass after it
      (green) and stay green with the principles checks clean (refactor); recorded as evidence;
    * ``bdd``: the acceptance call writes the criteria as Gherkin scenarios, a person approves
      them, the agent writes the step definitions; the scenarios are frozen.

    ``featuresDirectory``, ``bddCommand`` and ``testCommand`` override the pack defaults."""

    __test__ = False  # not a pytest test class

    strategy: TestingStrategy | None = None
    features_directory: str | None = Field(default=None, alias="featuresDirectory")
    bdd_command: tuple[str, ...] | None = Field(default=None, alias="bddCommand")
    test_command: tuple[str, ...] | None = Field(default=None, alias="testCommand")

    @field_validator("features_directory")
    @classmethod
    def _directory(cls, value: str | None) -> str | None:
        return _relative(value, "featuresDirectory") if value is not None else None

    @field_validator("bdd_command", "test_command")
    @classmethod
    def _command(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and not value:
            raise ValueError("a command must not be empty")
        return value

    @property
    def features_path(self) -> str:
        return (self.features_directory or DEFAULT_FEATURES_DIRECTORY).rstrip("/")


# ----- architecture (#56, item 5) -----------------------------------------------------------
ArchitectureStyle = Literal[
    "ddd",
    "hexagonal",
    "clean",
    "layered",
    "modular-monolith",
    "microservices",
    "mvvm",
    "mvi",
    "custom",
]
ARCHITECTURE_STYLES: tuple[str, ...] = (
    "ddd",
    "hexagonal",
    "clean",
    "layered",
    "modular-monolith",
    "microservices",
    "mvvm",
    "mvi",
    "custom",
)
ArchitectureMode = Literal["agent", "off"]
RefreshMode = Literal["auto", "manual"]


class LayerConfig(_Section):
    """A layer: the files that belong to it (``paths``, globs relative to the workspace) and/or
    the module prefixes (``modules``, dotted: ``shop.domain``) imports name it by."""

    name: str
    paths: tuple[str, ...] | None = None
    modules: tuple[str, ...] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(f"layer name must be lower case letters, digits, - or _: {value!r}")
        return value

    @model_validator(mode="after")
    def _has_members(self) -> LayerConfig:
        if not self.paths and not self.modules:
            raise ValueError(f"layer {self.name!r} needs paths or modules")
        return self


class ArchitectureSettings(_Section):
    """The architecture of the project and how it is enforced.

    ``style`` and ``layers`` with ``allow`` (the layers each layer may depend on; a layer may
    always use itself) are the rules: every import across layers that ``allow`` does not list is
    a forbidden dependency (the #40 check), ``enforce`` makes it HIGH, ``warn`` LOW. With
    ``mode: agent`` a new project gets one advise call (options with trade-offs, a person
    chooses, an ADR is recorded) and an existing project one survey call whose inferred rules a
    person approves; the survey is cached in ``.harness/architecture.md`` and refreshed only on
    demand (``harness architecture refresh``) or, under ``refresh: auto``, when the layout of
    the source directories changed materially."""

    mode: ArchitectureMode | None = None
    style: ArchitectureStyle | None = None
    layers: tuple[LayerConfig, ...] | None = None
    allow: dict[str, tuple[str, ...]] | None = None
    enforce: Policy | None = None
    refresh: RefreshMode | None = None
    agent: AgentCallConfig | None = None

    @field_validator("mode", "enforce", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @model_validator(mode="after")
    def _known_layers(self) -> ArchitectureSettings:
        names = [item.name for item in self.layers or ()]
        if len(names) != len(set(names)):
            raise ValueError("layer names must be unique")
        for source, targets in (self.allow or {}).items():
            unknown = [item for item in (source, *targets) if item not in names]
            if unknown:
                raise ValueError(f"allow names unknown layer(s): {', '.join(sorted(set(unknown)))}")
        return self

    @property
    def agent_enabled(self) -> bool:
        return self.mode == "agent"

    @property
    def policy(self) -> str:
        return self.enforce or "enforce"


# ----- project setup questions (#56, item 6) ------------------------------------------------
ProjectSetupMode = Literal["ask", "off"]
