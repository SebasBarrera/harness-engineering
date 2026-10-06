"""A small, deterministic subset of JSONPath for probe assertions.

Supported: ``$`` (the document), ``.name`` and ``['name']`` (a member), ``[N]`` (an index,
negative from the end), ``[*]`` and ``.*`` (every element or member value, in order). Nothing
else: no filters, no recursive descent, no scripts. ``select`` returns the matches in document
order; a path that matches nothing returns an empty list."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_INDEX = re.compile(r"-?\d+")


@dataclass(frozen=True)
class Step:
    kind: str  # "member", "index" or "wildcard"
    name: str = ""
    index: int = 0


def parse_path(path: str) -> tuple[Step, ...]:
    """The steps of ``path``; a path outside the subset raises ``ValueError``."""
    text = path.strip()
    if not text.startswith("$"):
        raise ValueError(f"a JSON path starts with $: {path!r}")
    steps: list[Step] = []
    position = 1
    while position < len(text):
        char = text[position]
        if char == ".":
            step, position = _dot_step(text, position + 1, path)
        elif char == "[":
            step, position = _bracket_step(text, position, path)
        else:
            raise ValueError(f"unexpected {char!r} at {position} in {path!r}")
        steps.append(step)
    return tuple(steps)


def _dot_step(text: str, position: int, path: str) -> tuple[Step, int]:
    """``.*`` or ``.name`` after the dot at ``position - 1``: the step and where it ends."""
    if position < len(text) and text[position] == "*":
        return Step("wildcard"), position + 1
    match = _NAME.match(text, position)
    if match is None:
        raise ValueError(f"expected a member name at {position} in {path!r}")
    return Step("member", name=match.group(0)), match.end()


def _bracket_step(text: str, position: int, path: str) -> tuple[Step, int]:
    """``[*]``, ``['name']`` or ``[N]`` from the bracket at ``position``."""
    end = text.find("]", position)
    if end < 0:
        raise ValueError(f"unclosed [ in {path!r}")
    inner = text[position + 1 : end].strip()
    if inner == "*":
        return Step("wildcard"), end + 1
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in {"'", '"'}:
        return Step("member", name=inner[1:-1]), end + 1
    if _INDEX.fullmatch(inner):
        return Step("index", index=int(inner)), end + 1
    raise ValueError(f"unsupported selector [{inner}] in {path!r}")


def _children(value: Any, step: Step) -> list[Any]:
    """What one step selects in one value."""
    if step.kind == "member":
        return [value[step.name]] if isinstance(value, dict) and step.name in value else []
    if step.kind == "index":
        if isinstance(value, list) and -len(value) <= step.index < len(value):
            return [value[step.index]]
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return [value[key] for key in value]
    return []


def select(document: Any, path: str) -> list[Any]:
    """Every value ``path`` selects in ``document``, in document order."""
    current = [document]
    for step in parse_path(path):
        current = [child for value in current for child in _children(value, step)]
    return current


def has_wildcard(path: str) -> bool:
    return any(step.kind == "wildcard" for step in parse_path(path))


__all__ = ["Step", "has_wildcard", "parse_path", "select"]
