"""Reviewer definitions (#57): Markdown files with a YAML frontmatter.

The frontmatter says who reviews and how: the domain, the model per provider family or provider
id, the effort, the timeout, the token budget, the modes it runs in (``run``, ``hook``,
``manual``, ``staged``), its diff slice, what activates it, the tools it may use and the MCP
servers of the project's allowlist it may reach. The body is the reviewer's prompt; between the
``BEGIN``/``END`` markers it holds the rules of its domain, a block written only by
``harness review rules sync`` (``--check`` reports drift). A fixed output contract is added by
the harness to every request; it is not part of the file.

Six reviewers ship with the harness (``resources/review/agents``): quality, architecture,
resilience, tests, concurrency and pipeline-security. A project adds or replaces reviewers in
``.harness/review/agents/ID.md``. A built-in reviewer that the project did not copy renders its
block from the catalog when it is loaded, so it cannot drift."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from governed_harness.domain.errors import ConfigurationError
from governed_harness.evidence.hashing import sha256_bytes, sha256_json
from governed_harness.review.rules import Catalog, Rule, _frontmatter

AGENTS_DIRECTORY = ".harness/review/agents"
BUILTIN_REVIEWERS: tuple[str, ...] = (
    "quality",
    "architecture",
    "resilience",
    "tests",
    "concurrency",
    "pipeline-security",
)
BEGIN_MARKER = "<!-- BEGIN HARNESS REVIEW RULES (harness review rules sync; do not edit) -->"
END_MARKER = "<!-- END HARNESS REVIEW RULES -->"
Mode = Literal["run", "hook", "manual", "staged"]
MODES: tuple[Mode, ...] = ("run", "hook", "manual", "staged")
SLICES: tuple[str, ...] = ("all", "sources", "tests", "pipeline")
DEFAULT_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")
_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class ReviewerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    reviewer_id: str = Field(alias="id")
    domain: str
    title: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    """Model per provider family (``claude-code``, ``codex``) or per provider id."""
    effort: str | None = None
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=10, le=7200)
    max_budget: int | None = Field(default=None, alias="maxBudget", ge=1)
    modes: tuple[Mode, ...] = MODES
    diff_slice: str | tuple[str, ...] = Field(default="sources", alias="diffSlice")
    """``all``, ``sources``, ``tests``, ``pipeline`` or a list of path globs."""
    activation: str = "changed"
    """``changed`` (the slice is not empty), ``always``, or ``signal:NAME`` (a pack signal such as
    ``concurrency`` matches a changed line of the slice)."""
    patterns: tuple[str, ...] = ()
    """Regular expressions that also activate the reviewer when a changed line matches one."""
    tools: tuple[str, ...] = DEFAULT_TOOLS
    mcp_servers: tuple[str, ...] = Field(default=(), alias="mcpServers")
    provider: str | None = None
    max_findings: int | None = Field(default=None, alias="maxFindings", ge=1, le=200)

    @field_validator("reviewer_id", "domain")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError(f"must be lower case with dashes: {value!r}")
        return value

    @field_validator("diff_slice", mode="before")
    @classmethod
    def _slice(cls, value: Any) -> Any:
        if isinstance(value, str):
            if value not in SLICES:
                raise ValueError(f"diffSlice is one of {', '.join(SLICES)} or a list of globs")
            return value
        return tuple(str(item) for item in value or ())

    @field_validator("activation")
    @classmethod
    def _activation(cls, value: str) -> str:
        if value not in {"changed", "always"} and not value.startswith("signal:"):
            raise ValueError("activation is changed, always or signal:NAME")
        return value

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            try:
                re.compile(item)
            except re.error as error:
                raise ValueError(f"invalid pattern {item!r}: {error}") from error
        return value


@dataclass(frozen=True)
class Reviewer:
    spec: ReviewerSpec
    body: str
    """The prompt with its rules block."""
    source: str
    """``builtin`` or the project file it came from."""
    raw: str
    """The file as written (its digest is the definition hash)."""

    @property
    def reviewer_id(self) -> str:
        return self.spec.reviewer_id

    @property
    def domain(self) -> str:
        return self.spec.domain

    @property
    def definition_hash(self) -> str:
        return sha256_bytes(self.raw.encode("utf-8"))

    @property
    def builtin(self) -> bool:
        return self.source == "builtin"

    def block(self) -> str | None:
        return extract_block(self.body)

    def model_for(self, provider_id: str, family: str) -> str | None:
        return self.spec.models.get(provider_id) or self.spec.models.get(family)


# ----- the generated rules block ----------------------------------------------------------------
def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def render_rule(rule: Rule) -> str:
    parts = [
        f"- `{rule.rule_id}` ({rule.severity}, priority {rule.priority}): {_sentence(rule.rule)}"
    ]
    if rule.when:
        parts.append(f"When: {_sentence(rule.when)}")
    if rule.applies_to:
        parts.append(f"Files: {', '.join(rule.applies_to)}.")
    if rule.exceptions:
        parts.append("Exceptions: " + " ".join(_sentence(item) for item in rule.exceptions))
    if rule.bad:
        parts.append("Bad: " + "; ".join(f"`{' '.join(item.split())}`" for item in rule.bad) + ".")
    if rule.good:
        parts.append(
            "Good: " + "; ".join(f"`{' '.join(item.split())}`" for item in rule.good) + "."
        )
    budget = rule.budget
    parts.append(
        f"Confirm with at most {budget.max_reads} {budget.tool}(s) of the code under review; "
        "if you cannot confirm it, report nothing."
    )
    return " ".join(parts)


def render_block(rules: tuple[Rule, ...]) -> str:
    """The rules a reviewer receives: the model-verified rules of its domain, blocking first,
    then by priority and id. Rules verified by a tool are never listed."""
    ordered = sorted(
        (rule for rule in rules if not rule.by_tool),
        key=lambda rule: (not rule.blocking, rule.priority, rule.rule_id),
    )
    lines = [BEGIN_MARKER, "## Rules", ""]
    if ordered:
        lines.extend(render_rule(rule) for rule in ordered)
    else:
        lines.append("No rule of this domain is active; report only defects you can show.")
    lines.extend(("", END_MARKER))
    return "\n".join(lines)


def extract_block(body: str) -> str | None:
    start = body.find(BEGIN_MARKER)
    end = body.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    return body[start : end + len(END_MARKER)]


def with_block(body: str, block: str) -> str:
    current = extract_block(body)
    if current is None:
        return body.rstrip("\n") + "\n\n" + block + "\n"
    return body.replace(current, block, 1)


# ----- loading ----------------------------------------------------------------------------------
def parse_reviewer(text: str, *, source: str, fallback_id: str | None = None) -> Reviewer:
    try:
        head, body = _frontmatter(text)
    except (ValueError, yaml.YAMLError) as error:
        raise ConfigurationError(f"invalid reviewer {source}: {error}") from error
    if fallback_id and "id" not in head:
        head = {**head, "id": fallback_id}
    try:
        spec = ReviewerSpec.model_validate(head)
    except ValidationError as error:
        raise ConfigurationError(f"invalid reviewer {source}: {error}") from error
    return Reviewer(spec, body.strip("\n") + "\n", source, text)


def builtin_reviewer(reviewer_id: str) -> Reviewer:
    target = resources.files("governed_harness.resources").joinpath(
        "review", "agents", f"{reviewer_id}.md"
    )
    return parse_reviewer(target.read_text(encoding="utf-8"), source="builtin")


def load_reviewers(
    workspace: Path,
    catalog: Catalog,
    *,
    enabled: tuple[str, ...] | None = None,
    directory: str = AGENTS_DIRECTORY,
) -> list[Reviewer]:
    """The built-in reviewers and the project's (a project file replaces the built-in reviewer
    with its id), filtered by ``review.panel.reviewers``, in id order. A built-in reviewer gets
    its rules block from the catalog; a project reviewer keeps the block in its file."""
    found: dict[str, Reviewer] = {}
    for reviewer_id in BUILTIN_REVIEWERS:
        reviewer = builtin_reviewer(reviewer_id)
        block = render_block(catalog.for_domain(reviewer.domain))
        found[reviewer_id] = Reviewer(
            reviewer.spec, with_block(reviewer.body, block), "builtin", reviewer.raw
        )
    root = workspace / directory
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            source = path.relative_to(workspace).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as error:
                raise ConfigurationError(f"cannot read reviewer {source}: {error}") from error
            reviewer = parse_reviewer(text, source=source, fallback_id=path.stem)
            found[reviewer.reviewer_id] = reviewer
    selected = [found[key] for key in sorted(found) if enabled is None or key in set(enabled)]
    return selected


def reviewer_domains(workspace: Path, enabled: tuple[str, ...] | None = None) -> set[str]:
    """The domains the reviewers cover, read without the catalog (to decide which rules are
    inactive because no reviewer covers them)."""
    domains: set[str] = set()
    for reviewer_id in BUILTIN_REVIEWERS:
        if enabled is None or reviewer_id in enabled:
            domains.add(builtin_reviewer(reviewer_id).domain)
    root = workspace / AGENTS_DIRECTORY
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            try:
                reviewer = parse_reviewer(
                    path.read_text(encoding="utf-8"), source=path.name, fallback_id=path.stem
                )
            except OSError:
                continue
            if enabled is None or reviewer.reviewer_id in enabled:
                domains.add(reviewer.domain)
    return domains


# ----- sync -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SyncResult:
    reviewer_id: str
    path: str
    status: Literal["unchanged", "updated", "drift"]

    def as_dict(self) -> dict[str, str]:
        return {"reviewer": self.reviewer_id, "path": self.path, "status": self.status}


def sync_blocks(
    workspace: Path, catalog: Catalog, *, check: bool, directory: str = AGENTS_DIRECTORY
) -> list[SyncResult]:
    """Write the rules block of every project reviewer (``check``: only report the ones whose
    block differs from the catalog). The only writer of the block."""
    root = workspace / directory
    results: list[SyncResult] = []
    if not root.is_dir():
        return results
    for path in sorted(root.glob("*.md")):
        source = path.relative_to(workspace).as_posix()
        text = path.read_text(encoding="utf-8")
        reviewer = parse_reviewer(text, source=source, fallback_id=path.stem)
        block = render_block(catalog.for_domain(reviewer.domain))
        if reviewer.block() == block:
            results.append(SyncResult(reviewer.reviewer_id, source, "unchanged"))
            continue
        if check:
            results.append(SyncResult(reviewer.reviewer_id, source, "drift"))
            continue
        path.write_text(_raw_head(text) + with_block(reviewer.body, block), encoding="utf-8")
        results.append(SyncResult(reviewer.reviewer_id, source, "updated"))
    return results


def _raw_head(text: str) -> str:
    """The frontmatter of a reviewer file as written, with its closing ``---`` line."""
    if not text.startswith("---"):
        return ""
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[: index + 1]) + "\n"
    return ""


def drift(reviewers: list[Reviewer], catalog: Catalog) -> list[str]:
    """Project reviewers whose rules block is not the catalog's."""
    return [
        reviewer.reviewer_id
        for reviewer in reviewers
        if not reviewer.builtin
        and reviewer.block() != render_block(catalog.for_domain(reviewer.domain))
    ]


def definitions_hash(reviewers: list[Reviewer]) -> str:
    return sha256_json(
        [
            {
                "id": item.reviewer_id,
                "hash": item.definition_hash,
                "body": sha256_bytes(item.body.encode()),
            }
            for item in reviewers
        ]
    )


__all__ = [
    "AGENTS_DIRECTORY",
    "BEGIN_MARKER",
    "BUILTIN_REVIEWERS",
    "END_MARKER",
    "MODES",
    "SLICES",
    "Reviewer",
    "ReviewerSpec",
    "SyncResult",
    "builtin_reviewer",
    "definitions_hash",
    "drift",
    "extract_block",
    "load_reviewers",
    "parse_reviewer",
    "render_block",
    "render_rule",
    "reviewer_domains",
    "sync_blocks",
    "with_block",
]
