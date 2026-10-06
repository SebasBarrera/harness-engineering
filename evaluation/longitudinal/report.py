#!/usr/bin/env python3
"""Summarize the longitudinal sessions per model and condition, and draw the chapter figure.

Usage: python report.py <results-dir> <out.json> [<figures-dir>]
Reads <results-dir>/longitudinal-*.jsonl. Descriptive values only: medians, ranges and counts.
A session is valid when its final oracle ran the 25 hidden checks.

2.0.0 sessions (block D) add the conditions ``structured`` and the governed ``harness``,
``harness-core`` and ``harness-anchored``; each cell reports the dispersion (min, median, max) of its
sessions, the governance calls apart from the implementation calls, the increments the harness did not
deliver and every session's outcome per increment, so no aggregate hides a stopped increment.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"]
LABELS = {
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-opus-5-5": "Opus 5.5",
}
CONDITIONS = [
    ("baseline", "Prompts casuales, sin harness", "#9a9a9a"),
    ("harness", "Tareas estructuradas, con harness", "#2f4f6f"),
]
GOVERNED = ("harness", "harness-core", "harness-anchored", "harness-tiered", "harness-legacy")
HIDDEN_TOTAL = 25


def load(results: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(results.glob("longitudinal-*.jsonl")):
        records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return records


def valid(r: dict[str, Any]) -> bool:
    return r["final"]["hiddenTotal"] == HIDDEN_TOTAL


def spread(values: list[float | None]) -> dict[str, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"median": None, "min": None, "max": None}
    return {"median": statistics.median(clean), "min": min(clean), "max": max(clean)}


def first_pass(r: dict[str, Any]) -> int:
    """Increments whose main step left no failing hidden check."""
    return sum(1 for s in r["steps"] if s["kind"] == "main" and not s["failingChecks"])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mains = [s for r in rows for s in r["steps"] if s["kind"] == "main"]
    harness = [s["harness"] for s in mains if "harness" in s]
    return {
        "sessions": len(rows),
        "complete": sum(r["final"]["hiddenPassed"] == HIDDEN_TOTAL for r in rows),
        "sessionsWithoutFix": sum(r["fixRequests"] == 0 for r in rows),
        "fixRequestsTotal": sum(r["fixRequests"] for r in rows),
        "incrementsFirstPass": sum(first_pass(r) for r in rows),
        "increments": len(mains),
        "hiddenFailedAfterMain": sum(len(s["failingChecks"]) for s in mains),
        "prompts": spread([r["prompts"] for r in rows]),
        "costUsd": spread([r["usage"]["costUsd"] for r in rows]),
        "wallSeconds": spread([r["wallSeconds"] for r in rows]),
        "turns": spread([r["usage"]["turns"] for r in rows]),
        "visibleTests": spread([r["final"]["visibleTests"] for r in rows]),
        "sourceLines": spread([r["final"]["sourceLines"] for r in rows]),
        "lineCoverage": spread([r["final"]["lineCoverage"] for r in rows]),
        "ruffFindings": spread([r["final"]["ruffFindings"] for r in rows]),
        "banditFindings": sum(sum(r["final"]["bandit"].values()) for r in rows),
        "harnessIncrementsApprovedFirstGate": sum(
            h["gateHistory"][:1] == ["PASSED"] for h in harness
        ),
        "harnessIncrementsTraceComplete": sum(
            h["trace"]["present"] == h["trace"]["requiredCount"] for h in harness
        ),
        "harnessIncrementsNotDelivered": sum(not h["delivered"] for h in harness),
        "governanceCalls": spread([r["usage"].get("governanceCalls", 0) for r in rows]),
        "governanceCostUsd": spread([r["usage"].get("governanceCostUsd", 0) for r in rows]),
        "calls": spread([r["usage"]["calls"] for r in rows]),
        "harnessOutcomes": dict(Counter(h.get("outcome") for h in harness)),
        "sessionsDetail": [
            {
                "rep": r["rep"],
                "hidden": f"{r['final']['hiddenPassed']}/{r['final']['hiddenTotal']}",
                "fixRequests": r["fixRequests"],
                "increments": [
                    f"{s['increment']}:{(s.get('harness') or {}).get('outcome', 'direct')}"
                    for s in r["steps"]
                    if s["kind"] == "main"
                ],
            }
            for r in sorted(rows, key=lambda x: x["rep"])
        ],
    }


def main() -> int:
    results, out = Path(sys.argv[1]), Path(sys.argv[2])
    records = load(results)
    good = [r for r in records if valid(r)]
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in good:
        cells[(r["model"], r["condition"])].append(r)
        by_condition[r["condition"]].append(r)
    data = {
        "sessionsRecorded": len(records),
        "sessionsValid": len(good),
        "invalid": [
            {"model": r["model"], "condition": r["condition"], "rep": r["rep"]}
            for r in records
            if not valid(r)
        ],
        "conditions": {c: summarize(rows) for c, rows in sorted(by_condition.items())},
        "cells": {f"{m}|{c}": summarize(rows) for (m, c), rows in sorted(cells.items())},
    }
    out.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    if len(sys.argv) > 3:
        figure(good, Path(sys.argv[3]))
    print(json.dumps(data["conditions"], indent=1, sort_keys=True))
    return 0


def figure(rows: list[dict[str, Any]], target: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
        }
    )
    panels = [
        ("Pedidos al agente por sesión", lambda r: r["prompts"]),
        ("Costo por sesión (USD)", lambda r: r["usage"]["costUsd"]),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(6.3, 2.7))
    for ax, (title, value) in zip(axes, panels, strict=True):
        for offset, (condition, label, color) in zip((-0.17, 0.17), CONDITIONS, strict=True):
            labelled = False
            for i, model in enumerate(MODELS):
                values = sorted(
                    value(r) for r in rows if r["model"] == model and r["condition"] == condition
                )
                if not values:
                    continue
                spreadx = [
                    i + offset + 0.07 * (k - (len(values) - 1) / 2) for k in range(len(values))
                ]
                ax.scatter(
                    spreadx, values, s=22, color=color, label=None if labelled else label, zorder=3
                )
                labelled = True
                ax.hlines(
                    statistics.median(values),
                    i + offset - 0.13,
                    i + offset + 0.13,
                    color=color,
                    linewidth=1.2,
                )
        ax.set_xticks(range(len(MODELS)), [LABELS[m] for m in MODELS])
        ax.set_ylabel(title)
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))
        )
    axes[0].legend(frameon=False, loc="lower left", fontsize=7)
    fig.tight_layout()
    target.mkdir(parents=True, exist_ok=True)
    fig.savefig(target / "cap7-iterativo.pdf")
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
