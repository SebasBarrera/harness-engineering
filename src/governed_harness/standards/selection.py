"""Which cards an agent call receives, and which tools verify the rest (#56).

Token cost rule: the implement request carries only the cards that apply to the files the call
works on (the task's paths, the context manifest, the files changed so far), compact (id, rule,
exceptions) and capped by ``standards.maxCards``; the review call's checklist carries only the
cards no tool verifies, for the files the ChangeSet changed. The selection is a pure function of
the packs' digests and the paths, so the same inputs give the same cards (and the same prompt
prefix), and the harness caches it by that digest."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from governed_harness.configuration.models import ValidatorDefinition
from governed_harness.evidence.hashing import sha256_json
from governed_harness.standards.packs import Card, PackTool, ProjectStandards

TOOL_VALIDATOR_PREFIX = "standards"


def selection_digest(standards: ProjectStandards, paths: Iterable[str], limit: int) -> str:
    return sha256_json({"packs": standards.digest, "paths": sorted(set(paths)), "limit": limit})


def select_cards(
    standards: ProjectStandards,
    paths: Iterable[str],
    limit: int,
    *,
    review_only: bool = False,
) -> list[Card]:
    """The cards that apply to any of ``paths``, in pack order, at most ``limit``.
    ``review_only`` keeps the cards no tool verifies (the reviewer's checklist)."""
    wanted = sorted(set(paths))
    selected: list[Card] = []
    for pack in standards.packs:
        for card in pack.cards:
            if review_only and not card.needs_review:
                continue
            if any(card.applies(path) for path in wanted):
                selected.append(card)
    return selected[:limit]


def cards_payload(cards: list[Card], digest: str, *, packs: list[str]) -> dict[str, Any]:
    """The ``standards`` block of a request."""
    return {
        "digest": digest,
        "packs": packs,
        "cards": [item.compact() for item in cards],
        "note": "Follow these cards in the files they apply to; an exception listed on a card "
        "is allowed. Tools verify the cards that name a tool rule.",
    }


def checklist_payload(cards: list[Card]) -> list[dict[str, Any]]:
    return [{"id": item.card_id, "rule": item.rule} for item in cards]


# ----- the tools of the packs as validators ------------------------------------------------------
def _config_present(workspace: Path, entry: str) -> bool:
    name, _, section = entry.partition("#")
    target = (workspace / name).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        return False
    if any(char in name for char in "*?["):
        return any(workspace.glob(name))
    if not target.is_file():
        return False
    if not section:
        return True
    try:
        return section in target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def tool_validators(
    standards: ProjectStandards,
    workspace: Path,
    existing: Iterable[str],
) -> list[tuple[str, PackTool, ValidatorDefinition]]:
    """An optional validator for each pack tool the repository configures (one of its
    ``configFiles`` exists) and no selected validator already runs (``coveredBy``). Detection
    only: a tool that is not installed is ``NOT_APPLICABLE``, and nothing is installed."""
    known = set(existing)
    found: list[tuple[str, PackTool, ValidatorDefinition]] = []
    for pack in standards.packs:
        for tool in pack.tools:
            if not tool.command or not tool.config_files:
                continue
            if any(item in known for item in tool.covered_by):
                continue
            if not any(_config_present(workspace, entry) for entry in tool.config_files):
                continue
            validator_id = f"{TOOL_VALIDATOR_PREFIX}.{pack.pack_id}.{tool.tool_id}"
            if validator_id in known:
                continue
            known.add(validator_id)
            found.append(
                (
                    pack.pack_id,
                    tool,
                    ValidatorDefinition.model_validate(
                        {
                            "id": validator_id,
                            "command": list(tool.command),
                            "mandatory": False,
                            "whenAvailable": True,
                            "timeoutSeconds": 600,
                            **({"parser": tool.parser} if tool.parser else {}),
                        }
                    ),
                )
            )
    return found


__all__ = [
    "TOOL_VALIDATOR_PREFIX",
    "cards_payload",
    "checklist_payload",
    "select_cards",
    "selection_digest",
    "tool_validators",
]
