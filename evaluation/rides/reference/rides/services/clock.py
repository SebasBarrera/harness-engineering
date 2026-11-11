"""The virtual clock (X4): ``advance`` runs every due timer in time order."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ..domain.records import Timer, TimerKind
from .context import Context


class ClockService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.handlers: dict[str, Callable[[Timer], None]] = {}
        self.after_advance: list[Callable[[], None]] = []

    def on(self, kind: TimerKind, handler: Callable[[Timer], None]) -> None:
        self.handlers[kind] = handler

    def advance(self, minutes: Any = 0, seconds: Any = 0) -> None:
        for value, name in ((minutes, "minutes"), (seconds, "seconds")):
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        target = self.state.now + timedelta(minutes=minutes, seconds=seconds)
        timers = self.state.timers
        while timers and timers[0].due <= target:
            timer = heapq.heappop(timers)
            # A timer is processed at its own due time, so everything it causes is stamped
            # (and schedules further timers) as if the clock had been running.
            self.state.now = max(self.state.now, timer.due)
            self.handlers[timer.kind](timer)
        self.state.now = target
        for hook in self.after_advance:
            hook()
