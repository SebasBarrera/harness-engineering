"""Governed budget of agent calls (#42): accounting from the provider's records and limits.

Usage is what the providers reported (``ResourceUsage``) and the wall time the harness measured
for each agent invocation; nothing is estimated. Limits apply per agent call, per task (every
run of the task id) and per run, for cost (USD, only when a provider reports it), tokens
(input, output and reasoning) and wall seconds. Crossing ``warnAt`` of a limit records a
warning once; crossing a limit fails closed: no further agent call is made until a person
raises the limit (``harness budget raise``), which is recorded on the run's event chain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from governed_harness.configuration.agent_results import BudgetConfig, BudgetLimits
from governed_harness.domain.models import AgentInvocation, ResourceUsage

Scope = Literal["call", "task", "run"]
Metric = Literal["costUsd", "tokens", "wallSeconds"]
METRICS: tuple[Metric, ...] = ("costUsd", "tokens", "wallSeconds")
SCOPES: tuple[Scope, ...] = ("call", "task", "run")


@dataclass(frozen=True)
class Usage:
    cost_usd: float | None = None
    tokens: int = 0
    wall_seconds: float = 0.0
    calls: int = 0

    def value(self, metric: Metric) -> float | None:
        if metric == "costUsd":
            return self.cost_usd
        if metric == "tokens":
            return float(self.tokens)
        return self.wall_seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "costUsd": None if self.cost_usd is None else round(self.cost_usd, 6),
            "tokens": self.tokens,
            "wallSeconds": round(self.wall_seconds, 3),
            "calls": self.calls,
        }


def usage_of(invocations: Iterable[AgentInvocation], usages: Iterable[ResourceUsage]) -> Usage:
    """The usage of a set of agent invocations: reported tokens and cost, measured wall time."""
    by_invocation = {item.invocation_id: item for item in usages if item.invocation_id}
    cost: float | None = None
    tokens = 0
    wall = 0.0
    calls = 0
    for invocation in invocations:
        calls += 1
        wall += max(0.0, (invocation.finished_at - invocation.started_at).total_seconds())
        reported = by_invocation.get(invocation.invocation_id)
        if reported is None:
            continue
        tokens += (
            (reported.input_tokens or 0)
            + (reported.output_tokens or 0)
            + (reported.reasoning_tokens or 0)
        )
        if reported.cost_usd is not None:
            cost = (cost or 0.0) + reported.cost_usd
    return Usage(cost_usd=cost, tokens=tokens, wall_seconds=wall, calls=calls)


@dataclass(frozen=True)
class Crossing:
    scope: Scope
    metric: Metric
    used: float
    limit: float

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.metric}:{self.limit:g}"

    def as_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "metric": self.metric, "used": self.used, "limit": self.limit}

    def describe(self) -> str:
        unit = {"costUsd": "USD", "tokens": "tokens", "wallSeconds": "s"}[self.metric]
        return f"{self.scope} {self.metric} {self.used:g} of {self.limit:g} {unit}"


@dataclass(frozen=True)
class BudgetCheck:
    exceeded: tuple[Crossing, ...] = ()
    warnings: tuple[Crossing, ...] = ()
    remaining: dict[str, dict[str, float]] = field(default_factory=dict)


def _limits(config: BudgetConfig, scope: Scope) -> BudgetLimits | None:
    return {"call": config.per_call, "task": config.per_task, "run": config.per_run}[scope]


def effective_limit(
    config: BudgetConfig, scope: Scope, metric: Metric, raised: dict[str, dict[str, float]]
) -> float | None:
    """The configured limit, or the one a person raised it to (never lower)."""
    limits = _limits(config, scope)
    configured: float | None = None
    if limits is not None:
        value = {
            "costUsd": limits.cost_usd,
            "tokens": limits.tokens,
            "wallSeconds": limits.wall_seconds,
        }[metric]
        configured = float(value) if value is not None else None
    override = raised.get(scope, {}).get(metric)
    if override is None:
        return configured
    return override if configured is None else max(configured, override)


def evaluate(
    config: BudgetConfig,
    usage: dict[Scope, Usage],
    raised: dict[str, dict[str, float]] | None = None,
) -> BudgetCheck:
    """Compare the usage of each scope with its limits. A scope without a usage entry is not
    checked (the call scope is checked only after a call)."""
    raised = raised or {}
    exceeded: list[Crossing] = []
    warnings: list[Crossing] = []
    remaining: dict[str, dict[str, float]] = {}
    for scope in SCOPES:
        current = usage.get(scope)
        for metric in METRICS:
            limit = effective_limit(config, scope, metric, raised)
            if limit is None:
                continue
            used = current.value(metric) if current is not None else None
            if scope != "call":
                remaining.setdefault(scope, {})[metric] = max(0.0, limit - (used or 0.0))
            else:
                remaining.setdefault(scope, {})[metric] = limit
            if used is None:
                continue
            crossing = Crossing(scope, metric, round(used, 6), limit)
            if used >= limit:
                exceeded.append(crossing)
            elif used >= limit * config.warning_ratio:
                warnings.append(crossing)
    return BudgetCheck(tuple(exceeded), tuple(warnings), remaining)
