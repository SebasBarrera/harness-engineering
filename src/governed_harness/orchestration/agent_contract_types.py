"""Value types the agent contract shares with the agent results (wave 11, #97), apart from both
so that neither imports the other."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallLimits:
    """The token budget and the time limit of one call, from the size of its task."""

    size: str
    tokens: int
    seconds: int


@dataclass(frozen=True)
class SentInfo:
    """What the harness did to the request of a call: the task fields it kept and left out."""

    kept: tuple[str, ...]
    omitted: tuple[str, ...]
    iteration: int
