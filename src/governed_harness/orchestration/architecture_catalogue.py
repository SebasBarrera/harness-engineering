"""The catalogue of architecture styles and their experts (wave 11, #97).

Under ``architecture.catalogue: experts`` a new project is offered ten styles (layered, hexagonal,
clean, ddd, modular-monolith, microservices, mvvm, mvi, event-driven, cqrs). Each one ships as a
Markdown file in ``resources/architecture``: a front matter with the purpose, when it fits, its
benefits and costs and the layer rules where they apply; sections with the structure, the best
practices and the dependency rules; and a last section, ``Expert prompt``, which is what the
expert for that style is told. The documentation is read by people and agents; only the expert
prompt travels in the requests (the documentation is copied into the project when a style is
chosen, and requests point to it)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml

from governed_harness.configuration.engineering import CATALOGUE_STYLES

EXPERT_HEADING = "## Expert prompt"
STYLE_DOC_FILE = "architecture-style.md"
RECOMMENDATION_FILE = "architecture-options.md"
ORGANIZATION_FILE = "architecture-organization.md"
_MAX_DOC = 20_000


@dataclass(frozen=True)
class StyleEntry:
    """One style of the catalogue."""

    style: str
    title: str
    purpose: str
    fits: tuple[str, ...]
    benefits: tuple[str, ...]
    costs: tuple[str, ...]
    layers: tuple[dict[str, Any], ...]
    allow: dict[str, list[str]]
    document: str
    expert_prompt: str

    def summary(self) -> dict[str, Any]:
        """What the advise request carries about the style: enough to choose, not the document."""
        return {
            "id": self.style,
            "title": self.title,
            "purpose": self.purpose,
            "fits": list(self.fits),
        }

    def as_option(self) -> dict[str, Any]:
        """The style as an architecture option, with the layer rules of its template."""
        return {
            "id": f"catalogue-{self.style}",
            "style": self.style,
            "title": self.title,
            "benefits": list(self.benefits),
            "costs": list(self.costs),
            "fit": "; ".join(self.fits),
            "supports": [],
            "recommended": False,
            "layers": [
                {"name": item["name"], "paths": list(item["paths"]), "modules": []}
                for item in self.layers
            ],
            "allow": {key: list(value) for key, value in self.allow.items()},
        }


def _split(text: str) -> tuple[dict[str, Any], str]:
    """The front matter and the body of a catalogue file."""
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front)
    if not isinstance(meta, dict):
        raise ValueError("a catalogue file needs a front matter")
    return meta, body


def _entry(text: str) -> StyleEntry:
    meta, body = _split(text)
    document, _, prompt = body.partition(EXPERT_HEADING)
    if not prompt.strip():
        raise ValueError(f"catalogue style {meta.get('id')!r} has no expert prompt")
    return StyleEntry(
        style=str(meta["id"]),
        title=str(meta["title"]),
        purpose=str(meta["purpose"]),
        fits=tuple(str(item) for item in meta["fits"]),
        benefits=tuple(str(item) for item in meta["benefits"]),
        costs=tuple(str(item) for item in meta["costs"]),
        layers=tuple(meta.get("layers") or ()),
        allow={str(key): list(value) for key, value in (meta.get("allow") or {}).items()},
        document=document.strip()[:_MAX_DOC] + "\n",
        expert_prompt=prompt.strip(),
    )


@cache
def style_entry(style: str) -> StyleEntry:
    """The catalogue entry of ``style``; a style outside the catalogue is an error."""
    if style not in CATALOGUE_STYLES:
        raise ValueError(
            f"{style!r} is not a style of the catalogue ({', '.join(CATALOGUE_STYLES)})"
        )
    target = resources.files("governed_harness.resources").joinpath("architecture", f"{style}.md")
    return _entry(target.read_text(encoding="utf-8"))


def catalogue() -> tuple[StyleEntry, ...]:
    return tuple(style_entry(style) for style in CATALOGUE_STYLES)


def in_catalogue(style: Any) -> bool:
    return isinstance(style, str) and style in CATALOGUE_STYLES


def render_recommendation(options: list[dict[str, Any]], project_kind: dict[str, Any]) -> str:
    """The report a person reads before choosing (Markdown): the style the agent recommends, why,
    the characteristics of the project that support it, the other options, and how to decide."""
    recommended = next((item for item in options if item.get("recommended")), None)
    lines = ["# Architecture recommendation", ""]
    if recommended is not None:
        lines += [
            f"Recommended: **{recommended['title']}** (`{recommended['style']}`, option "
            f"`{recommended['id']}`).",
            "",
            "## Why",
            "",
            recommended["fit"] or "The agent gave no further reason.",
            "",
            "## Characteristics of the project that support it",
            "",
            *[f"- {item}" for item in recommended.get("supports") or []],
            "",
        ]
    lines += ["## Options", ""]
    for item in options:
        mark = " (recommended)" if item.get("recommended") else ""
        lines.append(f"- `{item['id']}`: {item['title']} ({item['style']}){mark}")
        lines += [f"  - benefit: {text}" for text in item["benefits"]]
        lines += [f"  - cost: {text}" for text in item["costs"]]
    styles = ", ".join(CATALOGUE_STYLES)
    lines += [
        "",
        "## How to decide",
        "",
        "- Accept the recommendation: `harness architecture decide --option <recommended id> "
        "--digest <digest> --rationale <why>`.",
        "- Choose another option of the list with its id.",
        f"- Choose any style of the catalogue with `--style <style>` ({styles}).",
        "",
        f"Project kind: {project_kind.get('kind', 'unknown')}.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def expert_block(style: str) -> dict[str, Any]:
    """What the requests carry about the chosen style: the expert prompt and where the
    documentation of the style lives in the project."""
    entry = style_entry(style)
    return {"prompt": entry.expert_prompt, "document": f".harness/{STYLE_DOC_FILE}"}


__all__ = [
    "ORGANIZATION_FILE",
    "RECOMMENDATION_FILE",
    "STYLE_DOC_FILE",
    "StyleEntry",
    "catalogue",
    "expert_block",
    "in_catalogue",
    "render_recommendation",
    "style_entry",
]
