"""Configuration of the verification ladder, certification and delivery hygiene (since 1.1,
issue #55).

Every section and key here is optional. A key that is absent keeps the 1.0.0 behaviour and is
left out of the serialized configuration, so the configuration snapshot (and its digest) of a
project written before these settings existed does not change. ``harness init`` writes them.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from governed_harness.configuration.agent_results import off_from_yaml
from governed_harness.domain.enums import VerificationLevel

_ENV_NAME = re.compile(r"^[A-Za-z_]\w{0,127}$", re.ASCII)
_BRANCH_FORBIDDEN = re.compile(r"(\.\.|[\s~^:?*\[\\]|@\{|//|^/|/$|\.lock$|^-)")


class _Section(BaseModel):
    """A section whose unset (``None``) keys are left out of the serialized configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


LadderMode = Literal["enforce", "warn", "off"]
DEFAULT_LADDER_LEVEL = VerificationLevel.L1
DEFAULT_DEFERRED_EXPIRY_DAYS = 14
DEFAULT_DETECTION_TIMEOUT_SECONDS = 10


class LadderConfig(_Section):
    """The verification ladder and certification (#55, items 1, 2, 4 and 6).

    * ``mode``: ``enforce`` makes a criterion whose declared rung is not reached a ``HIGH``
      finding (the gate fails until a person decides); ``warn`` makes it ``LOW``; ``off``
      computes nothing.
    * ``defaultLevel``: the rung a criterion without ``verification.level`` requires for the
      certification status. Its gap is reported and never blocks: only a declared rung does.
    * ``deferredExpiryDays``: how long a deferred verification may stay pending.
    * ``preflight``: run the probes and the frozen acceptance tests on the baseline in PLANNING
      and classify the run READY, PARTIAL or UNAVAILABLE before anything changes.
    * ``capabilityDetection``: run the read-only detection commands of the profiles'
      verification capabilities (a simulator, an emulator, a container engine), never an
      installation."""

    mode: LadderMode | None = None
    default_level: VerificationLevel | None = Field(default=None, alias="defaultLevel")
    deferred_expiry_days: int | None = Field(default=None, alias="deferredExpiryDays", ge=1, le=365)
    preflight: bool | None = None
    capability_detection: bool | None = Field(default=None, alias="capabilityDetection")
    detection_timeout_seconds: int | None = Field(
        default=None, alias="detectionTimeoutSeconds", ge=1, le=120
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.mode in {"enforce", "warn"}

    @property
    def required_default(self) -> VerificationLevel:
        return self.default_level or DEFAULT_LADDER_LEVEL

    @property
    def expiry_days(self) -> int:
        return self.deferred_expiry_days or DEFAULT_DEFERRED_EXPIRY_DAYS

    @property
    def detection_timeout(self) -> int:
        return self.detection_timeout_seconds or DEFAULT_DETECTION_TIMEOUT_SECONDS


DEFAULT_MUTATION_HUNKS = 10
DEFAULT_MUTATION_SECONDS = 300


class MutationConfig(_Section):
    """Discriminating evidence and light mutation (#55, item 5): new tests are classified by
    running them on the baseline (fail before and pass after: discriminating; pass before and
    after: weak; fail after: broken), and each changed hunk of the code is reverted in a scratch
    copy, one at a time, to see that some test fails. ``enforce`` makes a change no test
    exercises ``HIGH``, ``warn`` ``LOW``."""

    mode: LadderMode | None = None
    max_hunks: int | None = Field(default=None, alias="maxHunks", ge=1, le=100)
    max_seconds: int | None = Field(default=None, alias="maxSeconds", ge=10, le=7200)

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def enabled(self) -> bool:
        return self.mode in {"enforce", "warn"}

    @property
    def hunk_limit(self) -> int:
        return self.max_hunks or DEFAULT_MUTATION_HUNKS

    @property
    def time_limit(self) -> int:
        return self.max_seconds or DEFAULT_MUTATION_SECONDS


ContractMode = Literal["enforce", "batch", "off"]
StopCondition = Literal["unresolvable-ambiguity", "scope-contradiction", "destructive-collision"]
STOP_CONDITIONS: tuple[StopCondition, ...] = (
    "unresolvable-ambiguity",
    "scope-contradiction",
    "destructive-collision",
)
DEFAULT_INTERRUPTION_TARGET = 2


class InterruptionConfig(_Section):
    """The interruption budget of a run (#55, item 8): the number of human interactions the
    run should need and the conditions under which the harness stops and asks a person.
    Interactions are measured and reported; going over the target is reported, never hidden."""

    target: int | None = Field(default=None, ge=0, le=100)
    stop_conditions: tuple[StopCondition, ...] | None = Field(default=None, alias="stopConditions")

    @property
    def effective_target(self) -> int:
        return DEFAULT_INTERRUPTION_TARGET if self.target is None else self.target

    @property
    def conditions(self) -> tuple[StopCondition, ...]:
        return STOP_CONDITIONS if self.stop_conditions is None else self.stop_conditions


IsolationMode = Literal["none", "worktree"]
DEFAULT_ISOLATION_BRANCH = "harness/{taskId}-{runId}"
DEFAULT_ISOLATION_DIRECTORY = ".harness/worktrees"


class IsolationConfig(_Section):
    """Worktree isolation per run (#55, item 9). ``mode: worktree`` (or ``run start --isolate
    worktree``) creates a Git worktree on a new branch from the updated base branch and runs
    the phases there; a branch or directory that already exists stops the run (BLOCKED), and
    the harness never resets or deletes anything it did not create."""

    mode: IsolationMode | None = None
    branch: str | None = None
    base: str | None = None
    fetch: bool | None = None
    remote: str | None = None
    directory: str | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_none(cls, value: Any) -> Any:
        return "none" if value is False or value == "off" else value

    @field_validator("branch")
    @classmethod
    def _branch_template(cls, value: str | None) -> str | None:
        if value is None:
            return value
        sample = value.replace("{runId}", "run_x").replace("{taskId}", "task_x")
        if not sample.strip() or _BRANCH_FORBIDDEN.search(sample) or "{" in sample:
            raise ValueError(
                f"branch must be a valid branch name; only {{runId}} and {{taskId}} are "
                f"replaced: {value!r}"
            )
        return value

    @field_validator("directory")
    @classmethod
    def _relative_directory(cls, value: str | None) -> str | None:
        if value is not None:
            path = PurePosixPath(value.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not value.strip():
                raise ValueError(f"the worktree directory must be inside the workspace: {value!r}")
        return value

    @property
    def effective_mode(self) -> IsolationMode:
        return self.mode or "none"

    @property
    def branch_template(self) -> str:
        return self.branch or DEFAULT_ISOLATION_BRANCH

    @property
    def worktree_directory(self) -> str:
        return (self.directory or DEFAULT_ISOLATION_DIRECTORY).rstrip("/")


class ToolRequirement(_Section):
    """A tool the environment must provide: ``command`` prints its version and ``pattern`` (a
    regular expression) must match the output, for example ``3\\.12``."""

    name: str = Field(min_length=1, max_length=100)
    command: tuple[str, ...] = Field(min_length=1)
    pattern: str | None = None

    @field_validator("pattern")
    @classmethod
    def _regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as error:
                raise ValueError(f"invalid pattern {value!r}: {error}") from error
        return value


class GitHooksConfig(_Section):
    """Git hooks the repository relies on: each must be present and executable (under
    ``core.hooksPath`` when set). ``install`` is the command the project declares to install
    them; the harness names it, and runs it only when a person asks (``harness doctor
    --install-hooks``). Hooks are never bypassed."""

    required: tuple[str, ...] | None = None
    install: tuple[str, ...] | None = None

    @field_validator("required")
    @classmethod
    def _hook_names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for name in value or ():
            if not re.fullmatch(r"[a-z][a-z-]{1,40}", name):
                raise ValueError(f"not a Git hook name: {name!r}")
        return value


DirtyTreePolicy = Literal["allow", "warn", "block"]
BaselinePolicy = Literal["require", "report", "off"]


class EnvironmentConfig(_Section):
    """Generic environment preflight in DISCOVERY and ``harness doctor`` (#55, item 10).

    * ``tools``: tool versions the project requires;
    * ``variables``: environment variables that must be set (names only, never values);
    * ``gitHooks``: hooks that must be present and executable;
    * ``dirtyTree``: what DISCOVERY does with uncommitted changes (``block``, ``warn``,
      ``allow``);
    * ``baseline``: run the mandatory validators on the baseline before the change: ``require``
      blocks DISCOVERY when they do not pass, ``report`` records it."""

    tools: tuple[ToolRequirement, ...] | None = None
    variables: tuple[str, ...] | None = None
    git_hooks: GitHooksConfig | None = Field(default=None, alias="gitHooks")
    dirty_tree: DirtyTreePolicy | None = Field(default=None, alias="dirtyTree")
    baseline: BaselinePolicy | None = None

    @field_validator("variables")
    @classmethod
    def _names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for name in value or ():
            if not _ENV_NAME.match(name):
                raise ValueError(f"not an environment variable name: {name!r}")
        return value

    @field_validator("baseline", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)


class PullRequestConfig(_Section):
    """``delivery.pullRequest``: create the pull or merge request of an approved run's branch
    at CLOSURE (#55, item 13), when the operational contract authorises it. Where and how is
    the forge's (``delivery.forge``, #56): the forge, the repository, ``baseBranch``, ``labels``
    and ``template``; ``draft`` here takes precedence over ``delivery.forge.draft``."""

    create: bool | None = None
    draft: bool | None = None


CommentPolicy = Literal["notClean", "always", "never"]


class InstructionsConfig(_Section):
    """``harness config lint`` (#55, item 14): the agent instruction files to read and their
    precedence (``harness`` is the project configuration). The first entry wins a conflict."""

    files: tuple[str, ...] | None = None
    precedence: tuple[str, ...] | None = None

    @field_validator("files")
    @classmethod
    def _relative(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for item in value or ():
            path = PurePosixPath(item.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not item.strip():
                raise ValueError(f"an instruction file must be relative: {item!r}")
        return value


DEFAULT_INSTRUCTION_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules",
    ".github/copilot-instructions.md",
)
"""Agent instruction files ``harness config lint`` reads when ``instructions.files`` is not set
(a directory such as ``.cursor/rules`` is read file by file)."""


class CapabilityDetection(_Section):
    """A read-only check that a capability is available: a command that must exit 0 (and,
    with ``expect``, print a line matching it), or a path that must exist in the workspace."""

    command: tuple[str, ...] | None = None
    expect: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _one(self) -> CapabilityDetection:
        if (self.command is None) == (self.path is None):
            raise ValueError("a detection needs exactly one of command or path")
        if self.expect is not None:
            re.compile(self.expect)
        return self


class VerificationCapability(_Section):
    """What a profile offers at one rung of the ladder: the validators whose passing result
    establishes it, the read-only detections that tell whether it is available here, and the
    rung to fall back to when it is not."""

    level: VerificationLevel
    provides: str = Field(min_length=1, max_length=500)
    validators: tuple[str, ...] | None = None
    detect: tuple[CapabilityDetection, ...] | None = None
    probes: bool | None = None
    """The rung is reached through declared probes (L3, L4)."""
    fallback: str | None = Field(default=None, max_length=500)


class ProfileVerification(_Section):
    """``verification`` of a technology profile: its capabilities per rung (#55, item 6)."""

    capabilities: tuple[VerificationCapability, ...] | None = None
    mutation_command: tuple[str, ...] | None = Field(default=None, alias="mutationCommand")
    """The command that runs a list of test files (``{paths}`` is replaced by them): light
    mutation and the classification of new tests use it."""


__all__ = [
    "DEFAULT_INSTRUCTION_FILES",
    "STOP_CONDITIONS",
    "BaselinePolicy",
    "CapabilityDetection",
    "CommentPolicy",
    "ContractMode",
    "DirtyTreePolicy",
    "EnvironmentConfig",
    "GitHooksConfig",
    "InstructionsConfig",
    "InterruptionConfig",
    "IsolationConfig",
    "LadderConfig",
    "MutationConfig",
    "ProfileVerification",
    "PullRequestConfig",
    "ToolRequirement",
    "VerificationCapability",
]
