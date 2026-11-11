#!/usr/bin/env python3
"""H41: anchored vs fixed model routing, router invariants and cost projection (``CATALOGO.md``
§2.7, ``PREREGISTRO.md``). No model is called and no run is started.

The router of the harness under test (``governed_harness.agents.routing``: ``select``,
``select_reviewer``, ``can_escalate``) is imported by the Python of the harness's environment and
evaluated over a grid: the 7 call kinds x the sizes S, M, L (signals within S, 6 requirements,
16 requirements), S with a risk factor (-> M) and ``planned_large`` (-> L) x 0 to
``maxEscalations + 2`` earlier escalations, plus ``select_reviewer`` for the 6 reviewers of the
panel. Family ``claude-code``, default tables and thresholds. When the imported package's version
is not the one ``harness --version`` reports, the record is an apparatus error.

Conditions: ``fixed``, ``tiered`` (``maxEscalations`` 2), ``anchored`` (2, the value of
``harness init``), ``anchored-max0``, ``anchored-max1``; cases: the anchors
``claude-haiku-5-5``, ``claude-sonnet-5-5`` and ``claude-opus-5-5``.

*Above the anchor*: the tier of the chosen model by name (haiku < sonnet < opus), computed here,
independent of ``routing.model_rank``, is above the anchor's; a decision without a model (the
provider keeps its own, the anchor) is not above. The cost projection prices a run (clarify,
plan, acceptance, architecture, locate, review and the 6 reviewers once, implement 1 + f times,
f = 0..3 quality failures, each escalating implement and review) with a declared price table and
a declared token profile (input 40 % and output 10 % of the size's token budget); under
``fixed`` every call is priced at the anchor.

Usage:
  python evaluation/deterministic/h41_routing_invariants.py --out DIR [--harness PATH]
      [--wheel WHEEL] [--only claude-haiku-5-5,...] [--conditions anchored,...]
      [--prices FILE] [--summarize-only]

Writes ``DIR/h41-routing-invariants.jsonl``, ``DIR/h41-routing-invariants-summary.json`` and
``DIR/environment.json``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import harness_version  # noqa: E402
from resource_support import main, proportion  # noqa: E402

FAMILY = "claude-code"
ANCHORS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
CALL_KINDS = ("implement", "clarify", "review", "plan", "acceptance", "architecture", "locate")
ESCALATING = ("implement", "review")
CONDITIONS: dict[str, tuple[str, int]] = {
    "fixed": ("fixed", 2),
    "tiered": ("tiered", 2),
    "anchored": ("anchored", 2),
    "anchored-max0": ("anchored", 0),
    "anchored-max1": ("anchored", 1),
}
ANCHORED = ("anchored", "anchored-max0", "anchored-max1")
# USD per million tokens: input, output, cache read (Claude API reference cached 2026-11-03).
# A declared assumption, replaceable with --prices FILE; the harness ships no prices.
PRICES: dict[str, dict[str, float | None]] = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache": None},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache": 0.10},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache": 0.20},
}
BUDGETS = {"S": 50_000, "M": 200_000, "L": 800_000}
INPUT_SHARE, OUTPUT_SHARE = 0.4, 0.1
FAILURES = (0, 1, 2, 3)
ONCE = ("clarify", "plan", "acceptance", "architecture", "locate")
TIERS = ("haiku", "sonnet", "opus")


def take_prices() -> str | None:
    """``--prices FILE`` is read here (the shared command line does not know it)."""
    if "--prices" not in sys.argv:
        return None
    index = sys.argv.index("--prices")
    value = sys.argv[index + 1] if index + 1 < len(sys.argv) else None
    del sys.argv[index : index + 2]
    return value


PRICES_FILE = take_prices() if __name__ == "__main__" else None
if PRICES_FILE:
    loaded = yaml.safe_load(Path(PRICES_FILE).read_text(encoding="utf-8"))
    PRICES = {
        str(k): {"input": v["input"], "output": v["output"], "cache": v.get("cache")}
        for k, v in (loaded.get("prices", loaded)).items()
    }


def tier(model: str | None) -> int | None:
    if model is None:
        return None
    lowered = model.lower()
    return next((index for index, name in enumerate(TIERS) if name in lowered), None)


def signals(routing: Any) -> dict[str, Any]:
    make = routing.TaskSignals
    return {
        "S": make(requirements=1, files=1, loc=10),
        "M": make(requirements=6, files=1, loc=10),
        "L": make(requirements=16, files=1, loc=10),
        "S+risk": make(requirements=1, files=1, loc=10, risk_flags=("authentication",)),
        "planned-large": make(requirements=1, files=1, loc=10, planned_large=True),
    }


def import_router(args: argparse.Namespace) -> tuple[Any, Any, Any]:
    import governed_harness
    from governed_harness.agents import routing
    from governed_harness.configuration import agent_results

    reported = harness_version(args.harness)
    if not re.search(rf"\b{re.escape(governed_harness.__version__)}\b", reported):
        raise RuntimeError(
            f"imported governed_harness {governed_harness.__version__} is not the harness under "
            f"test ({reported!r}): run the suite with the Python of the harness's environment"
        )
    return routing, agent_results, governed_harness.__version__


def runner(
    item: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    routing, agent_results, version = import_router(args)
    anchor = item["id"]
    mode, maximum = CONDITIONS[condition]
    policy = agent_results.AgentRoutingConfig.model_validate(
        {"mode": mode, "maxEscalations": maximum}
    )
    anchor_tier = tier(anchor)
    decisions: list[dict[str, Any]] = []

    def record(kind: str, label: str, history: int, decision: Any) -> dict[str, Any]:
        chosen = decision.model or anchor
        chosen_tier = tier(chosen)
        entry = {
            "callKind": kind,
            "signals": label,
            "earlierEscalations": history,
            "size": decision.size,
            "rule": decision.rule,
            "model": decision.model,
            "effort": decision.effort,
            "rung": decision.rung,
            "escalations": decision.escalations,
            "anchorRecorded": decision.anchor,
            "aboveAnchor": chosen_tier is not None
            and anchor_tier is not None
            and chosen_tier > anchor_tier,
            "unknownTier": chosen_tier is None,
            "canEscalate": routing.can_escalate(
                routing.RoutingHistory(escalations=history), policy
            ),
        }
        decisions.append(entry)
        return entry

    for label, signal in signals(routing).items():
        for kind in CALL_KINDS:
            for history in range(maximum + 3):
                decision = routing.select(
                    kind,
                    signal,
                    routing.RoutingHistory(escalations=history),
                    policy,
                    family=FAMILY,
                    anchor=anchor,
                )
                record(kind, label, history, decision)
    reviewers = sorted(agent_results.DEFAULT_ROUTING_TABLES[FAMILY]["reviewers"])
    for reviewer in reviewers:
        decision = routing.select_reviewer(reviewer, policy, family=FAMILY, anchor=anchor)
        record(f"reviewer:{reviewer}", "panel", 0, decision)
    tiered_mode = mode in {"tiered", "anchored"}
    violations = [
        d
        for d in decisions
        if d["escalations"] > maximum
        or (d["callKind"] not in ESCALATING and d["escalations"] != 0)
        or (tiered_mode and d["earlierEscalations"] >= maximum and d["canEscalate"])
        or (not tiered_mode and d["canEscalate"])
    ]
    return {
        "anchor": anchor,
        "mode": mode,
        "maxEscalations": maximum,
        "harnessPackageVersion": version,
        "decisions": decisions,
        "decisionCount": len(decisions),
        "aboveAnchor": sum(d["aboveAnchor"] for d in decisions),
        "aboveAnchorCalls": sorted(
            {
                f"{d['callKind']}/{d['signals']}/{d['earlierEscalations']}"
                for d in decisions
                if d["aboveAnchor"]
            }
        ),
        "unknownTier": sum(d["unknownTier"] for d in decisions),
        "escalationViolations": [
            f"{d['callKind']}/{d['signals']}/{d['earlierEscalations']}" for d in violations
        ],
        "maxEscalationsObserved": max(d["escalations"] for d in decisions),
        "projection": projection(routing, policy, anchor, mode, reviewers),
        "brief": f"above={sum(d['aboveAnchor'] for d in decisions)} violations={len(violations)}",
    }


def price(model: str, tokens_in: float, tokens_out: float) -> float | None:
    table = PRICES.get(model)
    if table is None:
        return None
    return (tokens_in * float(table["input"]) + tokens_out * float(table["output"])) / 1_000_000


def projection(
    routing: Any, policy: Any, anchor: str, mode: str, reviewers: list[str]
) -> dict[str, Any]:
    """Projected USD of one run per size and number of quality failures (declared profile)."""
    cells: dict[str, Any] = {}
    sizes = {k: v for k, v in signals(routing).items() if k in {"S", "M", "L"}}
    for label, signal in sizes.items():
        for failures in FAILURES:
            calls: list[tuple[str, int]] = [(k, 0) for k in ONCE]
            calls += [("implement", h) for h in range(failures + 1)]
            calls += [("review", failures)]
            priced: list[tuple[str, str]] = []
            for kind, history in calls:
                decision = routing.select(
                    kind,
                    signal,
                    routing.RoutingHistory(escalations=history),
                    policy,
                    family=FAMILY,
                    anchor=anchor,
                )
                model = anchor if mode == "fixed" else decision.model or anchor
                priced.append((model, decision.size or label))
            for reviewer in reviewers:
                decision = routing.select_reviewer(reviewer, policy, family=FAMILY, anchor=anchor)
                # select_reviewer classifies empty signals; the tokens follow the task's size.
                priced.append((anchor if mode == "fixed" else decision.model or anchor, label))
            total = 0.0
            unpriced: list[str] = []
            for model, size in priced:
                budget = BUDGETS.get(size, BUDGETS["S"])
                cost = price(model, budget * INPUT_SHARE, budget * OUTPUT_SHARE)
                if cost is None:
                    unpriced.append(model)
                else:
                    total += cost
            models = [model for model, _ in priced]
            cells[f"{label}/f{failures}"] = {
                "usd": round(total, 6),
                "calls": len(models),
                "unpriced": sorted(set(unpriced)),
                "models": {m: models.count(m) for m in sorted(set(models))},
            }
    return cells


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in ok if r["condition"] == condition]
        if not rows:
            continue
        decisions = sum(r["decisionCount"] for r in rows)
        by_condition[condition] = {
            "anchors": sorted(r["anchor"] for r in rows),
            "decisions": decisions,
            "aboveAnchor": proportion(sum(r["aboveAnchor"] for r in rows), decisions),
            "aboveAnchorByAnchor": {r["anchor"]: r["aboveAnchor"] for r in rows},
            "aboveAnchorCalls": {r["anchor"]: r["aboveAnchorCalls"] for r in rows},
            "unknownTier": sum(r["unknownTier"] for r in rows),
            "escalationViolations": {r["anchor"]: r["escalationViolations"] for r in rows},
            "maxEscalationsObserved": {r["anchor"]: r["maxEscalationsObserved"] for r in rows},
            "projectedUsd": {
                r["anchor"]: {cell: v["usd"] for cell, v in r["projection"].items()} for r in rows
            },
        }
    fixed = {r["anchor"]: r for r in ok if r["condition"] == "fixed"}
    versus: dict[str, Any] = {}
    exceed: list[str] = []
    for condition in ANCHORED:
        for r in (x for x in ok if x["condition"] == condition):
            base = fixed.get(r["anchor"])
            if base is None:
                continue
            ratios = {}
            for cell, value in r["projection"].items():
                reference = base["projection"][cell]["usd"]
                ratios[cell] = round(value["usd"] / reference, 4) if reference else None
                if value["usd"] > reference:
                    exceed.append(f"{condition}/{r['anchor']}/{cell}")
            versus[f"{condition}/{r['anchor']}"] = ratios
    anchored_rows = [r for r in ok if r["condition"] in ANCHORED]
    above = sum(r["aboveAnchor"] for r in anchored_rows)
    expected = len(ANCHORED) * len(ANCHORS)
    summary: dict[str, Any] = {
        "suite": "H41",
        "title": "anchored vs fixed routing: router invariants and cost projection",
        "family": FAMILY,
        "prices": {
            "table": PRICES,
            "source": PRICES_FILE or "declared assumption (Claude API reference cached 2026-11-03)",
        },
        "tokenProfile": {
            "inputShareOfBudget": INPUT_SHARE,
            "outputShareOfBudget": OUTPUT_SHARE,
            "budgets": BUDGETS,
            "note": "the same tokens per call in every mode: a projection, not a measurement",
        },
        "byCondition": by_condition,
        "anchoredOverFixedRatio": versus,
        "anchoredCellsAboveFixed": exceed,
    }
    summary["criteria"] = {
        "statement": "0 decisions above the anchor in the three anchored conditions",
        "aboveAnchorAnchored": above,
        "met": len(anchored_rows) == expected and above == 0,
        "evaluable": len(anchored_rows) == expected,
        "reportedApart": {
            "anchoredCostAtMostFixedEveryCell": not exceed if len(versus) == expected else None,
            "escalationViolations": sum(len(r["escalationViolations"]) for r in ok),
        },
    }
    return summary


CASES = [{"id": anchor} for anchor in ANCHORS]

if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H41",
            title="anchored vs fixed routing: router invariants and cost projection",
            stem="h41-routing-invariants",
            cases=CASES,
            conditions_of=lambda item: list(CONDITIONS),
            all_conditions=list(CONDITIONS),
            runner=runner,
            summarize=summarize,
            extra_environment=lambda args: {
                "pythonExecutable": Path(sys.executable).name,
                "pricesSource": PRICES_FILE or "declared assumption",
            },
        )
    )
