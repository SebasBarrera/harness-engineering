"""Configuration of the review panel (``review.panel``, since #57).

Every key is optional and left out of the serialized configuration when unset, so a
``project.yaml`` without ``review.panel`` keeps the earlier behaviour and its configuration
digest: the single second reviewer of #38 under ``review.agentReview``. ``harness init`` writes
the section."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator

from governed_harness.configuration.agent_results import _Section, off_from_yaml

PanelPolicy = Literal["enforce", "warn", "off"]
AutoFixMode = Literal["scoped", "off"]
SecondOpinionMode = Literal["blocking", "off"]

DEFAULT_MAX_FINDINGS = 20
DEFAULT_PARALLEL = 4
DEFAULT_BASE_TOKENS = 6_000
DEFAULT_TOKENS_PER_LINE = 30
DEFAULT_MAX_TOKENS = 60_000
DEFAULT_CACHE_TTL_DAYS = 14
DEFAULT_CACHE_ENTRIES = 500
DEFAULT_AUTOFIX_ATTEMPTS = 2

_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class PanelBudget(_Section):
    """The token budget of one reviewer call, proportional to the size of its diff slice:
    ``baseTokens + tokensPerLine * changed lines``, at most ``maxTokens`` (or the reviewer's own
    ``maxBudget``)."""

    base_tokens: int | None = Field(default=None, alias="baseTokens", ge=0)
    tokens_per_line: int | None = Field(default=None, alias="tokensPerLine", ge=0)
    max_tokens: int | None = Field(default=None, alias="maxTokens", ge=1)

    def for_lines(self, lines: int, cap: int | None = None) -> int:
        base = self.base_tokens if self.base_tokens is not None else DEFAULT_BASE_TOKENS
        per_line = (
            self.tokens_per_line if self.tokens_per_line is not None else DEFAULT_TOKENS_PER_LINE
        )
        ceiling = self.max_tokens if self.max_tokens is not None else DEFAULT_MAX_TOKENS
        if cap is not None:
            ceiling = min(ceiling, cap)
        return max(1, min(ceiling, base + per_line * max(lines, 0)))


class PanelCache(_Section):
    """The review cache under ``.harness/review/cache``: a time to live and an entry limit."""

    enabled: bool | None = None
    ttl_days: int | None = Field(default=None, alias="ttlDays", ge=1, le=365)
    max_entries: int | None = Field(default=None, alias="maxEntries", ge=1, le=100_000)

    @property
    def on(self) -> bool:
        return self.enabled is not False

    @property
    def ttl_seconds(self) -> int:
        return (self.ttl_days or DEFAULT_CACHE_TTL_DAYS) * 86_400

    @property
    def limit(self) -> int:
        return self.max_entries or DEFAULT_CACHE_ENTRIES


class ConsistencyCheck(_Section):
    """A deterministic check of the project that runs before any reviewer; when it fails no
    model is called (#57 item 12)."""

    check_id: str = Field(alias="id", min_length=1, max_length=64)
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1, le=3600)


class AutoFixConfig(_Section):
    """Scoped auto-fix: only errors on lines the agent wrote in the run (provenance) go back to
    IMPLEMENTATION, at most ``maxAttempts`` times; suggestions never do."""

    mode: AutoFixMode | None = None
    max_attempts: int | None = Field(default=None, alias="maxAttempts", ge=0, le=10)

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @property
    def attempts(self) -> int:
        return self.max_attempts if self.max_attempts is not None else DEFAULT_AUTOFIX_ATTEMPTS


class SecondOpinionConfig(_Section):
    """An optional second opinion on the blocking findings only (``mode: blocking``), from
    ``provider`` (default: the fallback provider, else the panel's provider)."""

    mode: SecondOpinionMode | None = None
    provider: str | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)


class ReviewPanelConfig(_Section):
    """The review panel (#57): reviewers by domain over diff slices, a layered rule catalog
    (built-in, language packs, project), a fixed output contract and a verdict the harness
    recomputes. Replaces the single reviewer of ``review.agentReview`` in INDEPENDENT_REVIEW and
    runs outside governed runs with ``harness review-code``."""

    mode: PanelPolicy | None = None
    reviewers: tuple[str, ...] | None = None
    """The reviewers that may run (built-in and project ids); absent: all of them."""
    max_findings: int | None = Field(default=None, alias="maxFindings", ge=1, le=200)
    parallel: int | None = Field(default=None, ge=1, le=16)
    budget: PanelBudget | None = None
    cache: PanelCache | None = None
    provider: str | None = None
    """The provider of the reviewers (an ``agentProviders`` id); absent: the review call's."""
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    """The alternate provider of the one retry after an invalid answer (UNKNOWN)."""
    consistency_checks: tuple[ConsistencyCheck, ...] | None = Field(
        default=None, alias="consistencyChecks"
    )
    run_tools: bool | None = Field(default=None, alias="runTools")
    """Outside a governed run, run the pack tools the repository configures for the rules they
    verify (``verified_by: tool:...``); inside a run the validators already ran them."""
    auto_fix: AutoFixConfig | None = Field(default=None, alias="autoFix")
    second_opinion: SecondOpinionConfig | None = Field(default=None, alias="secondOpinion")
    evidence_refs: bool | None = Field(default=None, alias="evidenceRefs")
    """Record a passing review as ``refs/harness/review/pass/SHA`` (or ``pass-warn``)."""
    comment: bool | None = None
    """Post one pull or merge request comment per passing result through the forge layer."""
    base_branches: dict[str, str] | None = Field(default=None, alias="baseBranches")
    """Branch-name globs and the base branch each one is reviewed against (``feat/*: develop``)."""
    mcp_servers: tuple[str, ...] | None = Field(default=None, alias="mcpServers")
    """The MCP servers (names of the repository's ``.mcp.json``) a reviewer may use; absent or
    empty: none."""

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @field_validator("reviewers")
    @classmethod
    def _ids(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        for item in value or ():
            if not _ID.match(item):
                raise ValueError(f"reviewer id must be lower case with dashes: {item!r}")
        return value

    @field_validator("consistency_checks")
    @classmethod
    def _unique_checks(
        cls, value: tuple[ConsistencyCheck, ...] | None
    ) -> tuple[ConsistencyCheck, ...] | None:
        ids = [item.check_id for item in value or ()]
        if len(set(ids)) != len(ids):
            raise ValueError("consistency check ids must be unique")
        return value

    @property
    def enabled(self) -> bool:
        return self.mode in {"enforce", "warn"}

    @property
    def finding_limit(self) -> int:
        return self.max_findings or DEFAULT_MAX_FINDINGS

    @property
    def workers(self) -> int:
        return self.parallel or DEFAULT_PARALLEL

    @property
    def effective_budget(self) -> PanelBudget:
        return self.budget or PanelBudget()

    @property
    def effective_cache(self) -> PanelCache:
        return self.cache or PanelCache()


__all__ = [
    "DEFAULT_AUTOFIX_ATTEMPTS",
    "DEFAULT_BASE_TOKENS",
    "DEFAULT_CACHE_ENTRIES",
    "DEFAULT_CACHE_TTL_DAYS",
    "DEFAULT_MAX_FINDINGS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_PARALLEL",
    "DEFAULT_TOKENS_PER_LINE",
    "AutoFixConfig",
    "ConsistencyCheck",
    "PanelBudget",
    "PanelCache",
    "ReviewPanelConfig",
    "SecondOpinionConfig",
]
