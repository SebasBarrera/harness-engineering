#!/usr/bin/env python3
"""Aggregate the evaluation records and draw the figures.

Descriptive statistics only (median, minimum, maximum, proportions): the design is formative and
the sample does not support significance testing.

Usage: python report.py <results-dir> <summary.json> [<figures-dir>]
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"]
LABELS = {"claude-haiku-4-5-20251001": "Haiku 4.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5"}
CONDITIONS = {"baseline": "Sin harness", "harness": "Con harness"}
SCENARIOS = {"greenfield": "Greenfield", "brownfield": "Brownfield", "security": "Seguridad"}


def load(results: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(results.glob("claude-*.jsonl")):
        records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return records


def stats(values: list[float | None]) -> dict[str, Any]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "median": None, "min": None, "max": None, "mean": None}
    return {
        "n": len(clean),
        "median": statistics.median(clean),
        "min": min(clean),
        "max": max(clean),
        "mean": statistics.fmean(clean),
        "sum": sum(clean),
    }


def hidden_ratio(r: dict[str, Any]) -> float | None:
    h = r["measures"]["hidden"]
    return h["passed"] / h["tests"] if h["tests"] else 0.0


def delivered(r: dict[str, Any]) -> bool:
    return r["harness"]["delivered"] if r["condition"] == "harness" else True


def overhead(r: dict[str, Any]) -> float:
    return r["wallSeconds"] - r["agent"]["wallSeconds"]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        cells[(r["model"], r["scenario"], r["condition"])].append(r)
    out: dict[str, Any] = {}
    for (model, scenario, condition), runs in sorted(cells.items()):
        m = [r["measures"] for r in runs]
        cell: dict[str, Any] = {
            "runs": len(runs),
            "hiddenRatio": stats([hidden_ratio(r) for r in runs]),
            "hiddenAllPassed": sum(1 for r in runs if hidden_ratio(r) == 1.0),
            "visibleGreen": sum(1 for x in m if x["visibleSuite"]["tests"] and x["visibleSuite"]["exitCode"] == 0),
            "visibleTests": stats([x["visibleSuite"]["tests"] for x in m]),
            "regressionAllPassed": sum(
                1 for x in m if x["regression"] and x["regression"]["passed"] == x["regression"]["tests"]
            ) if scenario == "brownfield" else None,
            "delivered": sum(1 for r in runs if delivered(r)),
            "deliveredWithHiddenFailures": sum(1 for r in runs if delivered(r) and hidden_ratio(r) < 1.0),
            "diffCoverage": stats([x["diffCoverage"]["ratio"] for x in m]),
            "addedLines": stats([x["change"]["addedLines"] for x in m]),
            "files": stats([x["change"]["files"] for x in m]),
            "outOfScopeRuns": sum(1 for x in m if x["change"]["outOfScopeFiles"]),
            "ruffIntroduced": stats([x["static"]["ruffIntroduced"] for x in m]),
            "banditIntroduced": {
                sev: sum(x["static"]["banditIntroduced"][sev] for x in m) for sev in ("HIGH", "MEDIUM", "LOW")
            },
            "costUsd": stats([r["agent"]["costUsd"] for r in runs]),
            "outputTokens": stats([r["agent"]["outputTokens"] for r in runs]),
            "inputTokensTotal": stats([
                r["agent"]["inputTokens"] + r["agent"]["cacheReadTokens"] + r["agent"]["cacheCreationTokens"]
                for r in runs
            ]),
            "turns": stats([r["agent"]["turns"] for r in runs]),
            "agentCalls": stats([r["agent"]["calls"] for r in runs]),
            "agentErrors": sum(r["agent"]["errors"] for r in runs),
            "agentSeconds": stats([r["agent"]["wallSeconds"] for r in runs]),
            "totalSeconds": stats([r["wallSeconds"] for r in runs]),
            "overheadSeconds": stats([overhead(r) for r in runs]),
            "permissionDenials": stats([r["agent"]["permissionDenials"] for r in runs]),
            "secretLiteralRuns": sum(1 for x in m if x.get("secretLiteralFiles")),
            "secretLiteralDelivered": sum(
                1 for r in runs if r["measures"].get("secretLiteralFiles") and delivered(r)
            ),
            "secretLiteralPaths": sorted({p for x in m for p in x.get("secretLiteralFiles", [])}),
        }
        if condition == "harness":
            h = [r["harness"] for r in runs]
            outcomes: dict[str, int] = defaultdict(int)
            for x in h:
                outcomes[x["outcome"]] += 1
            findings: dict[str, int] = defaultdict(int)
            for x in h:
                for f in x["findings"]:
                    findings[f"{f['severity']}:{f['ruleId']}"] += 1
            cell["harness"] = {
                "outcomes": dict(outcomes),
                "gateFirst": dict(
                    (g, sum(1 for x in h if x["gateHistory"] and x["gateHistory"][0] == g))
                    for g in {x["gateHistory"][0] for x in h if x["gateHistory"]}
                ),
                "corrections": sum(x["corrections"] for x in h),
                "events": stats([x["eventCount"] for x in h]),
                "chainValid": sum(1 for x in h if x["eventChainValid"]),
                "traceRatio": stats([x["trace"]["present"] / x["trace"]["requiredCount"] for x in h]),
                "traceComplete": sum(1 for x in h if x["trace"]["present"] == x["trace"]["requiredCount"]),
                "findings": dict(findings),
                "attempts": stats([x["harnessMetrics"].get("implementation.attempts") for x in h]),
                "harnessTotalMs": stats([x["harnessMetrics"].get("duration.total_ms") for x in h]),
                "toolMs": stats([x["harnessMetrics"].get("duration.tool_ms") for x in h]),
            }
        out[f"{model}|{scenario}|{condition}"] = cell
    return out


def figures(records: list[dict[str, Any]], target: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "pdf.fonttype": 42,
    })
    comma = FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))
    colors = {"baseline": "#9a9a9a", "harness": "#2f4f6f"}
    target.mkdir(parents=True, exist_ok=True)

    def grouped_box(metric, ylabel: str, name: str, log: bool = False) -> None:
        fig, axes = plt.subplots(1, len(SCENARIOS), figsize=(6.3, 2.6), sharey=True)
        for ax, scenario in zip(axes, SCENARIOS):
            data, positions, tint = [], [], []
            for i, model in enumerate(MODELS):
                for j, condition in enumerate(CONDITIONS):
                    values = [metric(r) for r in records if r["model"] == model
                              and r["scenario"] == scenario and r["condition"] == condition]
                    data.append(values)
                    positions.append(i * 3 + j)
                    tint.append(colors[condition])
            box = ax.boxplot(data, positions=positions, widths=0.75, patch_artist=True, showfliers=False,
                             medianprops={"color": "black", "linewidth": 1})
            for patch, color in zip(box["boxes"], tint):
                patch.set_facecolor(color)
                patch.set_alpha(0.85)
            for pos, values in zip(positions, data):
                ax.scatter([pos] * len(values), values, s=6, color="black", zorder=3)
            ax.set_xticks([i * 3 + 0.5 for i in range(len(MODELS))], [LABELS[m].split()[0] for m in MODELS])
            ax.set_title(SCENARIOS[scenario])
            if log:
                ax.set_yscale("log")
            ax.yaxis.set_major_formatter(comma)
        axes[0].set_ylabel(ylabel)
        handles = [plt.Rectangle((0, 0), 1, 1, color=colors[c]) for c in CONDITIONS]
        fig.legend(handles, CONDITIONS.values(), loc="upper center", ncol=2, frameon=False,
                   bbox_to_anchor=(0.5, 1.02))
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        fig.savefig(target / name)
        plt.close(fig)

    grouped_box(lambda r: r["wallSeconds"] / 60, "Tiempo total (min)", "cap7-tiempo-total.pdf")
    grouped_box(lambda r: r["agent"]["costUsd"], "Costo del agente (USD)", "cap7-costo.pdf")


def summarize_probes(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    probes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text().splitlines():
        record = json.loads(line)
        probes[record["probe"]].append(record)

    def signature(record: dict[str, Any]) -> str:
        final = record["final"]
        keys = ("status", "phase", "gate", "gateReasons", "findings", "changeSetFiles", "eventChainValid",
                "statusExitCode", "statusError", "traceExportExitCode", "terminalReason")
        return json.dumps({"steps": [s["exitCode"] for s in record["steps"]], "delivered": record["delivered"],
                           **{k: final.get(k) for k in keys}}, sort_keys=True)

    out = {}
    for name, records in probes.items():
        first = records[0]
        out[name] = {
            "reps": len(records),
            "deterministic": len({signature(r) for r in records}) == 1,
            "steps": [(s["step"], s["exitCode"]) for s in first["steps"]],
            "afterStart": {k: first["afterStart"].get(k) for k in ("status", "phase", "gate", "gateReasons", "findings")},
            "final": first["final"],
            "delivered": first["delivered"],
            "treeChangedFiles": first.get("treeChangedFiles"),
            "providerArtifactBytes": first.get("providerArtifactBytes"),
            "afterContinue": first.get("afterContinue"),
            "afterRequestChanges": first.get("afterRequestChanges"),
        }
    return out


def main() -> int:
    results, summary = Path(sys.argv[1]), Path(sys.argv[2])
    records = load(results)
    data = {"records": len(records), "cells": summarize(records),
            "probes": summarize_probes(results / "fault-probes.jsonl")}
    summary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    if len(sys.argv) > 3:
        figures(records, Path(sys.argv[3]))
    print(f"{len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
