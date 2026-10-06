"""The review rule catalog in three layers (#57).

* **A, built-in**: language-neutral rules shipped with the harness
  (``resources/review/rules.yaml``): tests, resilience, concurrency, pipeline security, quality
  and architecture.
* **B, language packs**: the cards of the Wave 6 standards packs of the project, read as rules;
  the pack's ``review.yaml`` gives a card its domain and says which cards block.
* **C, project**: rules the team writes for its framework and its business in
  ``.harness/review/rules/DOMAIN.md`` (Markdown, one ``## rule-id`` section per rule).

Rules merge by id with precedence C > B > A; a rule that ``supersedes`` others removes them. A
rule is verified either by a model (``verified_by: ai``) or by a tool (``tool:harness:CHECK`` for
the harness's own deterministic checks, ``tool:TOOL:RULE`` for a linter rule): a tool rule is
never sent to a model. A rule whose input is missing (its tool is not configured, a file it
``requires`` is absent, no reviewer covers its domain) is reported inactive with the reason."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from governed_harness.domain.errors import ConfigurationError
from governed_harness.evidence.hashing import sha256_json
from governed_harness.review.diff import glob_match

Severity = Literal["blocking", "warning"]
Layer = Literal["A", "B", "C"]

RULES_DIRECTORY = ".harness/review/rules"
HARNESS_TOOL = "harness"
DEFAULT_PRIORITY = 50
_RULE_ID = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$")
_DOMAIN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_LAYER_RANK: dict[str, int] = {"A": 0, "B": 1, "C": 2}


class ConfirmationBudget(BaseModel):
    """How much a reviewer may read to confirm a finding of the rule. A reviewer that cannot
    confirm within it reports nothing: the catalog prefers false negatives."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    tool: str = "read"
    max_reads: int = Field(default=2, alias="maxReads", ge=0, le=20)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    rule_id: str = Field(alias="id")
    domain: str
    title: str = ""
    rule: str = Field(min_length=1)
    severity: Severity = "warning"
    when: str = ""
    applies_to: tuple[str, ...] = Field(default=(), alias="appliesTo")
    exceptions: tuple[str, ...] = ()
    good: tuple[str, ...] = ()
    bad: tuple[str, ...] = ()
    budget: ConfirmationBudget = Field(default_factory=ConfirmationBudget)
    supersedes: tuple[str, ...] = ()
    priority: int = Field(default=DEFAULT_PRIORITY, ge=1, le=100)
    """Lower is reported first among findings of the same severity."""
    verified_by: tuple[str, ...] = Field(default=("ai",), alias="verifiedBy")
    requires: tuple[str, ...] = ()
    """Inputs the rule needs: ``tool:ID`` or ``file:GLOB``; without one it is inactive."""
    layer: Layer = "A"
    source: str = "builtin"

    @field_validator("rule_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _RULE_ID.match(value):
            raise ValueError(f"rule id must be dotted lower case (domain.name): {value!r}")
        return value

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        if not _DOMAIN.match(value):
            raise ValueError(f"domain must be lower case with dashes: {value!r}")
        return value

    @field_validator("verified_by", mode="before")
    @classmethod
    def _verified_by(cls, value: Any) -> Any:
        items = [value] if isinstance(value, str) else list(value or ["ai"])
        for item in items:
            if item != "ai" and not (
                isinstance(item, str) and item.startswith("tool:") and len(item.split(":")) >= 2
            ):
                raise ValueError(f"verified_by is ai or tool:TOOL[:RULE], got {item!r}")
        if "ai" in items and len(items) > 1:
            raise ValueError("verified_by is either ai or a list of tool ids")
        return tuple(items)

    @property
    def by_tool(self) -> bool:
        return self.verified_by != ("ai",)

    @property
    def tools(self) -> tuple[tuple[str, str], ...]:
        """``(tool, rule)`` of every ``tool:TOOL:RULE`` entry (rule empty for a whole tool)."""
        found: list[tuple[str, str]] = []
        for item in self.verified_by:
            if item.startswith("tool:"):
                _, tool, *rest = item.split(":", 2)
                found.append((tool, rest[0] if rest else ""))
        return tuple(found)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"

    def applies(self, path: str) -> bool:
        return not self.applies_to or any(glob_match(path, item) for item in self.applies_to)


@dataclass(frozen=True)
class InactiveRule:
    rule: Rule
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.rule.rule_id,
            "domain": self.rule.domain,
            "layer": self.rule.layer,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Catalog:
    rules: tuple[Rule, ...]
    inactive: tuple[InactiveRule, ...] = ()

    @property
    def digest(self) -> str:
        return sha256_json([rule.model_dump(mode="json", by_alias=True) for rule in self.rules])

    def get(self, rule_id: str) -> Rule | None:
        return next((rule for rule in self.rules if rule.rule_id == rule_id), None)

    def for_domain(self, domain: str, *, ai_only: bool = False) -> tuple[Rule, ...]:
        return tuple(
            rule
            for rule in self.rules
            if rule.domain == domain and (not ai_only or not rule.by_tool)
        )

    def tool_rules(self) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.by_tool)

    def summary(self) -> dict[str, Any]:
        layers: dict[str, int] = {"A": 0, "B": 0, "C": 0}
        for rule in self.rules:
            layers[rule.layer] += 1
        return {
            "digest": self.digest,
            "active": len(self.rules),
            "byLayer": layers,
            "byTool": len(self.tool_rules()),
            "inactive": [item.as_dict() for item in self.inactive],
        }


# ----- layer A ----------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def builtin_rules() -> tuple[Rule, ...]:
    target = resources.files("governed_harness.resources").joinpath("review", "rules.yaml")
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return tuple(
        Rule.model_validate({**item, "layer": "A", "source": "builtin"})
        for item in data.get("rules") or []
    )


# ----- layer B ----------------------------------------------------------------------------------
@lru_cache(maxsize=64)
def pack_review(pack: str) -> dict[str, Any]:
    """The review metadata of a built-in pack (``standards/PACK/review.yaml``): card domains,
    blocking cards, review signals and pipeline files."""
    target = resources.files("governed_harness.resources").joinpath(
        "standards", pack, "review.yaml"
    )
    if not target.is_file():
        return {}
    value = yaml.safe_load(target.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def pack_rules(packs: Iterable[Any]) -> list[Rule]:
    """The cards of the project's standards packs as rules (layer B)."""
    rules: list[Rule] = []
    for pack in packs:
        meta = pack_review(pack.pack_id)
        domains: Mapping[str, str] = meta.get("domains") or {}
        blocking = set(meta.get("blocking") or [])
        for card in pack.cards:
            verified = [
                f"tool:{item}" for item in card.verified_by if item != "review" and ":" in item
            ]
            rules.append(
                Rule.model_validate(
                    {
                        "id": card.card_id,
                        "domain": domains.get(card.card_id, "quality"),
                        "title": card.title,
                        "rule": card.rule,
                        "severity": "blocking" if card.card_id in blocking else "warning",
                        "appliesTo": list(card.applies_to),
                        "exceptions": list(card.exceptions),
                        "verifiedBy": verified or ["ai"],
                        "layer": "B",
                        "source": f"pack:{pack.pack_id}",
                    }
                )
            )
        for item in meta.get("rules") or []:
            rules.append(
                Rule.model_validate({**item, "layer": "B", "source": f"pack:{pack.pack_id}"})
            )
    return rules


# ----- layer C ----------------------------------------------------------------------------------
_HEADING = re.compile(r"^##\s+([^:]+?)\s*(?::\s*(.*))?$")
_FIELD = re.compile(r"^[-*]\s+([A-Za-z_]+)\s*:\s*(.*)$")
_LIST_FIELDS = {"exceptions", "appliesTo", "applies_to", "supersedes", "requires", "verifiedBy"}
_ALIASES = {
    "verified_by": "verifiedBy",
    "applies_to": "appliesTo",
    "max_reads": "maxReads",
}


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n")
    for index in range(1, len(parts)):
        if parts[index].strip() == "---":
            head = yaml.safe_load("\n".join(parts[1:index])) or {}
            if not isinstance(head, dict):
                raise ValueError("the frontmatter must be a mapping")
            return head, "\n".join(parts[index + 1 :])
    raise ValueError("the frontmatter is not closed with ---")


def _split_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("["):
        parsed = yaml.safe_load(value)
        return [str(item) for item in parsed or []]
    return [item.strip() for item in value.split(";") if item.strip()]


def _budget(value: str) -> dict[str, Any]:
    match = re.search(r"(\d+)", value)
    tool = re.search(r"\b(read|grep|search|open)\w*", value)
    return {
        "maxReads": int(match.group(1)) if match else 2,
        "tool": tool.group(1) if tool else "read",
    }


class _Section:
    """One ``## rule-id: Title`` section read line by line: the leading ``- key: value``
    fields, the rule's paragraph and the code blocks under ``Good:`` and ``Bad:``."""

    def __init__(self, rule_id: str, title: str, domain: str) -> None:
        self.data: dict[str, Any] = {"id": rule_id, "domain": domain, "title": title}
        self.paragraph: list[str] = []
        self.examples: dict[str, list[str]] = {"good": [], "bad": []}
        self.target: str | None = None
        self.block: list[str] | None = None

    def feed(self, line: str) -> None:
        stripped = line.strip()
        if self.block is not None:
            self._block_line(self.block, line, stripped)
            return
        if stripped.startswith("```"):
            self.block = []
            return
        lowered = stripped.lower().rstrip(":")
        if lowered in {"good", "bad"} and stripped.endswith(":"):
            self.target = lowered
            return
        field = _FIELD.match(stripped)
        if field and not self.paragraph:
            self._field(field.group(1), field.group(2).strip())
            return
        if stripped and self.target is None:
            self.paragraph.append(stripped)

    def _block_line(self, block: list[str], line: str, stripped: str) -> None:
        if not stripped.startswith("```"):
            block.append(line)
            return
        if self.target is not None:
            self.examples[self.target].append("\n".join(block))
        self.block = None

    def _field(self, name: str, value: str) -> None:
        key = _ALIASES.get(name, name)
        if key in _LIST_FIELDS:
            self.data[key] = _split_list(value)
        elif key == "budget":
            self.data["budget"] = _budget(value)
        elif key == "priority":
            self.data["priority"] = int(value)
        else:
            self.data[key] = value

    def as_data(self) -> dict[str, Any]:
        return {
            **self.data,
            "rule": " ".join(self.paragraph),
            "good": self.examples["good"],
            "bad": self.examples["bad"],
        }


def parse_rules_markdown(text: str, *, domain: str, source: str) -> list[Rule]:
    """The rules of one project file: a ``## rule-id: Title`` section per rule, ``- key: value``
    fields (severity, priority, when, exceptions, appliesTo, verifiedBy, supersedes, requires,
    budget), the rule as the first paragraph, and ``Good:``/``Bad:`` code blocks."""
    head, body = _frontmatter(text)
    domain = str(head.get("domain") or domain)
    sections: list[tuple[str, str, list[str]]] = []
    for line in body.split("\n"):
        heading = _HEADING.match(line)
        if heading:
            sections.append((heading.group(1), (heading.group(2) or "").strip(), []))
        elif sections:
            sections[-1][2].append(line)
    rules: list[Rule] = []
    for rule_id, title, lines in sections:
        section = _Section(rule_id, title, domain)
        for line in lines:
            section.feed(line)
        try:
            rules.append(Rule.model_validate({**section.as_data(), "layer": "C", "source": source}))
        except ValidationError as error:
            raise ConfigurationError(
                f"invalid review rule {rule_id} in {source}: {error}"
            ) from error
    return rules


def project_rules(workspace: Path, directory: str = RULES_DIRECTORY) -> list[Rule]:
    root = workspace / directory
    if not root.is_dir():
        return []
    rules: list[Rule] = []
    for path in sorted(root.glob("*.md")):
        source = path.relative_to(workspace).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            rules.extend(parse_rules_markdown(text, domain=path.stem, source=source))
        except (OSError, ValueError, yaml.YAMLError) as error:
            if isinstance(error, ConfigurationError):
                raise
            raise ConfigurationError(f"cannot read review rules {source}: {error}") from error
    return rules


# ----- merge ------------------------------------------------------------------------------------
def merge(
    layers: Iterable[Rule],
    *,
    available_tools: set[str],
    reviewer_domains: set[str],
    workspace: Path | None = None,
) -> Catalog:
    """Merge the layers by id (C > B > A), apply ``supersedes`` and set aside the rules whose
    input is missing."""
    by_id: dict[str, Rule] = {}
    for rule in layers:
        current = by_id.get(rule.rule_id)
        if current is None or _LAYER_RANK[rule.layer] >= _LAYER_RANK[current.layer]:
            by_id[rule.rule_id] = rule
    superseded = _superseded(by_id)
    inactive: list[InactiveRule] = []
    active: list[Rule] = []
    for rule_id in sorted(by_id):
        rule = by_id[rule_id]
        reason = _missing_input(rule, available_tools, reviewer_domains, workspace)
        if rule_id in superseded:
            reason = f"superseded by {superseded[rule_id]}"
        if reason is not None:
            inactive.append(InactiveRule(rule, reason))
        else:
            active.append(rule)
    return Catalog(tuple(active), tuple(inactive))


def _superseded(by_id: dict[str, Rule]) -> dict[str, str]:
    """``rule id -> the rule that supersedes it`` (the first one, in merge order)."""
    superseded: dict[str, str] = {}
    for rule in by_id.values():
        for target in rule.supersedes:
            if target in by_id and target != rule.rule_id:
                superseded.setdefault(target, rule.rule_id)
    return superseded


def _missing_input(
    rule: Rule, tools: set[str], domains: set[str], workspace: Path | None
) -> str | None:
    for tool, _name in rule.tools:
        if tool != HARNESS_TOOL and tool not in tools:
            return f"input missing: the tool {tool} is not configured in the repository"
    for item in rule.requires:
        kind, _, value = item.partition(":")
        if kind == "tool" and value != HARNESS_TOOL and value not in tools:
            return f"input missing: the tool {value} is not configured in the repository"
        if kind == "file" and workspace is not None and not _file_exists(workspace, value):
            return f"input missing: no file matches {value}"
    if not rule.by_tool and rule.domain not in domains:
        return f"input missing: no reviewer covers the domain {rule.domain}"
    return None


def _file_exists(workspace: Path, pattern: str) -> bool:
    if not any(char in pattern for char in "*?["):
        return (workspace / pattern).exists()
    return next(iter(workspace.glob(pattern)), None) is not None


def configured_tools(standards: Any, workspace: Path, validator_ids: Iterable[str]) -> set[str]:
    """The pack tools the repository runs: a profile validator covers it, or one of its
    configuration files is present."""
    from governed_harness.standards.selection import tool_validators

    if standards is None:
        return set()
    known = set(validator_ids)
    tools = {
        tool.tool_id
        for pack in standards.packs
        for tool in pack.tools
        if any(item in known for item in tool.covered_by)
    }
    tools.update(
        tool.tool_id for _pack, tool, _definition in tool_validators(standards, workspace, known)
    )
    return tools


__all__ = [
    "HARNESS_TOOL",
    "RULES_DIRECTORY",
    "Catalog",
    "ConfirmationBudget",
    "InactiveRule",
    "Rule",
    "builtin_rules",
    "configured_tools",
    "merge",
    "pack_review",
    "pack_rules",
    "parse_rules_markdown",
    "project_rules",
]
