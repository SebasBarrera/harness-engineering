"""Estimated cost of the agent calls whose provider reported tokens without a cost (#58,
item 10).

The price table maps a model id (or ``provider/model``) to US dollars per million input,
output and cached tokens. It comes from ``metrics.prices`` in ``project.yaml`` and from
``harness metrics --prices FILE`` (which takes precedence); the harness ships no prices, because
prices change and a number nobody configured would be invented. An estimate is always reported
apart from the reported cost and labelled ``estimated``; it is never written to a run or
counted against the budget."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from governed_harness.configuration.friction import ModelPrice
from governed_harness.domain.errors import ConfigurationError

PER_TOKENS = 1_000_000


@dataclass(frozen=True)
class CallCost:
    reported: float | None
    estimated: float | None
    source: str
    """``reported``, ``estimated``, ``unpriced`` (tokens but no price) or ``none`` (no usage)."""


def load_prices(path: Path) -> dict[str, ModelPrice]:
    """A price table file: a mapping of model ids to ``input``, ``output`` and optional
    ``cache`` prices (YAML or JSON), optionally under a top-level ``prices`` key."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"cannot read the price table {path}: {error}") from error
    if isinstance(raw, dict) and isinstance(raw.get("prices"), dict):
        raw = raw["prices"]
    if not isinstance(raw, dict):
        raise ConfigurationError(f"the price table {path} must map model ids to prices")
    try:
        return {str(key): ModelPrice.model_validate(value) for key, value in raw.items()}
    except ValidationError as error:
        raise ConfigurationError(f"invalid price table {path}: {error}") from error


def price_for(
    prices: Mapping[str, ModelPrice], provider: str | None, model: str | None
) -> ModelPrice | None:
    """The price of a call: ``provider/model`` first, then the model id, then (for a call
    that recorded no model) the provider id."""
    if model:
        if provider and f"{provider}/{model}" in prices:
            return prices[f"{provider}/{model}"]
        if model in prices:
            return prices[model]
        return None
    return prices.get(provider) if provider else None


def call_cost(
    usage: Mapping[str, Any] | None,
    prices: Mapping[str, ModelPrice],
    provider: str | None,
    model: str | None,
) -> CallCost:
    """The reported cost of a call, or its estimate from the price table."""
    if not usage:
        return CallCost(None, None, "none")
    reported = usage.get("cost_usd")
    if isinstance(reported, int | float):
        return CallCost(float(reported), None, "reported")
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_tokens = min(int(usage.get("cache_tokens") or 0), input_tokens)
    if not (input_tokens or output_tokens):
        return CallCost(None, None, "none")
    price = price_for(prices, provider, model)
    if price is None:
        return CallCost(None, None, "unpriced")
    cache_price = price.cache if price.cache is not None else price.input
    estimate = (
        (input_tokens - cache_tokens) * price.input
        + cache_tokens * cache_price
        + output_tokens * price.output
    ) / PER_TOKENS
    return CallCost(None, round(estimate, 6), "estimated")


__all__ = ["PER_TOKENS", "CallCost", "call_cost", "load_prices", "price_for"]
