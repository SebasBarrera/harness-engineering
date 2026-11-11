#!/usr/bin/env python3
"""Summarize the large-project runs per model and cell, and draw the figure.

Usage: python report.py <results-dir> <out.json> [<figures-dir>]
Descriptive only: medians and ranges over the repetitions of each cell.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

CELLS = [
    ("one_line", "direct"), ("paragraph", "direct"), ("requirements", "direct"), ("super", "direct"),
    ("super", "stepwise"), ("one_line", "harness"), ("paragraph", "harness"), ("requirements", "harness"),
    ("super", "harness"),
]
LABELS = {
    ("one_line", "direct"): "Frase\nsin harness", ("paragraph", "direct"): "Párrafo\nsin harness",
    ("requirements", "direct"): "Requisitos\nsin harness", ("super", "direct"): "Súper prompt\nsin harness",
    ("super", "stepwise"): "Súper prompt\npor pasos,\nsin harness", ("one_line", "harness"): "Frase\ncon harness",
    ("paragraph", "harness"): "Párrafo\ncon harness", ("requirements", "harness"): "Requisitos\ncon harness",
    ("super", "harness"): "Súper prompt\ncon harness",
}
MODELS = ["claude-sonnet-5-5", "claude-opus-5-5"]


def load(results: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(results.glob("large-*.jsonl")):
        records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return records


def spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hidden = [r["final"]["hidden"]["passed"] for r in rows]
    parts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        for p, (ok, total) in r["final"]["hidden"]["byPart"].items():
            parts[p][0] += ok
            parts[p][1] += total
    steps = [s for r in rows for s in r["steps"] if "harness" in s]
    governed = [s["harness"] for s in steps]
    return {
        "runs": len(rows),
        "hiddenPassed": spread(hidden),
        "hiddenTotal": 68,
        "complete": sum(h == 68 for h in hidden),
        "byPart": {p: parts[p] for p in sorted(parts)},
        "costUsd": spread([r["usage"]["costUsd"] for r in rows]),
        "wallSeconds": spread([r["wallSeconds"] for r in rows]),
        "agentCalls": spread([r["usage"]["calls"] for r in rows]),
        "outputTokens": spread([r["usage"]["outputTokens"] for r in rows]),
        "agentErrors": sum(r["usage"]["errors"] for r in rows),
        "visibleTests": spread([r["final"]["visibleTests"] for r in rows]),
        "visibleSuitePasses": sum(r["final"]["visibleSuiteExit"] == 0 for r in rows),
        "sourceLines": spread([r["final"]["sourceLines"] for r in rows]),
        "lineCoverage": spread([r["final"]["lineCoverage"] for r in rows if r["final"]["lineCoverage"] is not None]),
        "ruffFindings": spread([r["final"]["ruffFindings"] for r in rows]),
        "banditFindings": sum(sum(r["final"]["bandit"].values()) for r in rows),
        "governedSteps": len(governed),
        "stepsApprovedFirstGate": sum(g.get("gateHistory", [])[:1] == ["PASSED"] for g in governed),
        "stepsDelivered": sum(bool(g.get("delivered")) for g in governed),
        "stepsRefused": sum(str(g.get("outcome", "")).startswith("task-refused") for g in governed),
        "corrections": sum(g.get("corrections", 0) for g in governed),
        "stepsTraceComplete": sum(
            1 for g in governed if g.get("trace") and g["trace"]["present"] == g["trace"]["requiredCount"]),
        "stepOutcomes": dict(sorted(
            ((o, sum(1 for g in governed if g.get("outcome") == o)) for o in {g.get("outcome") for g in governed}),
            key=lambda kv: str(kv[0]))),
    }


def main() -> int:
    results, out = Path(sys.argv[1]), Path(sys.argv[2])
    records = load(results)
    cells: dict[str, Any] = {}
    for model in MODELS:
        for level, condition in CELLS:
            rows = [r for r in records if r["model"] == model and r["level"] == level and r["condition"] == condition]
            if rows:
                cells[f"{model}|{level}|{condition}"] = summarize(rows)
    data = {"runs": len(records), "cells": cells}
    out.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    for key, cell in cells.items():
        h = cell["hiddenPassed"]
        print(f"{key:45s} n={cell['runs']} oculto {h['median']:>4} ({h['min']}-{h['max']})  "
              f"USD {cell['costUsd']['median']:.2f}  s {cell['wallSeconds']['median']:.0f}  "
              f"suite verde {cell['visibleSuitePasses']}/{cell['runs']}")
    if len(sys.argv) > 3:
        figure(cells, Path(sys.argv[3]))
    return 0


def figure(cells: dict[str, Any], target: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
                         "font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    fig, ax = plt.subplots(figsize=(6.3, 3.1))
    width = 0.38
    colors = {"claude-sonnet-5-5": "#9a9a9a", "claude-opus-5-5": "#2f4f6f"}
    names = {"claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5"}
    for offset, model in zip((-width / 2, width / 2), MODELS, strict=True):
        for i, (level, condition) in enumerate(CELLS):
            cell = cells.get(f"{model}|{level}|{condition}")
            if not cell:
                continue
            h = cell["hiddenPassed"]
            ax.bar(i + offset, h["median"], width, color=colors[model], label=names[model] if i == 0 else None)
            ax.plot([i + offset, i + offset], [h["min"], h["max"]], color="black", linewidth=0.8)
    ax.axvline(4.5, color="#cccccc", linewidth=0.8)
    ax.set_xticks(range(len(CELLS)), [LABELS[c] for c in CELLS], fontsize=7)
    ax.set_ylim(0, 70)
    ax.set_ylabel("Comprobaciones ocultas superadas (de 68)")
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    target.mkdir(parents=True, exist_ok=True)
    fig.savefig(target / "cap7-proyecto-grande.pdf")
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
