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
            position += 1
            if position < len(text) and text[position] == "*":
                steps.append(Step("wildcard"))
                position += 1
                continue
            match = _NAME.match(text, position)
            if match is None:
                raise ValueError(f"expected a member name at {position} in {path!r}")
            steps.append(Step("member", name=match.group(0)))
            position = match.end()
        elif char == "[":
            end = text.find("]", position)
            if end < 0:
                raise ValueError(f"unclosed [ in {path!r}")
            inner = text[position + 1 : end].strip()
            if inner == "*":
                steps.append(Step("wildcard"))
            elif len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in {"'", '"'}:
                steps.append(Step("member", name=inner[1:-1]))
            elif _INDEX.fullmatch(inner):
                steps.append(Step("index", index=int(inner)))
            else:
                raise ValueError(f"unsupported selector [{inner}] in {path!r}")
            position = end + 1
        else:
            raise ValueError(f"unexpected {char!r} at {position} in {path!r}")
    return tuple(steps)


def select(document: Any, path: str) -> list[Any]:
    """Every value ``path`` selects in ``document``, in document order."""
    current = [document]
    for step in parse_path(path):
        following: list[Any] = []
        for value in current:
            if step.kind == "member":
                if isinstance(value, dict) and step.name in value:
                    following.append(value[step.name])
            elif step.kind == "index":
                if isinstance(value, list) and -len(value) <= step.index < len(value):
                    following.append(value[step.index])
            elif isinstance(value, list):
                following.extend(value)
            elif isinstance(value, dict):
                following.extend(value[key] for key in value)
        current = following
    return current


def has_wildcard(path: str) -> bool:
    return any(step.kind == "wildcard" for step in parse_path(path))


__all__ = ["Step", "has_wildcard", "parse_path", "select"]
