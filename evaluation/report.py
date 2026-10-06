#!/usr/bin/env python3
"""Aggregate the evaluation records and draw the figures.

Descriptive statistics only (median, minimum, maximum, proportions): the design is formative and
the sample does not support significance testing.

Usage: python report.py <results-dir> <summary.json> [<figures-dir>]       (0.9.0 records)
       python report.py --v2 <results-dir> <summary.json>                  (2.0.0, blocks A and B)

The 2.0.0 summary has one cell per block, prompt, condition, model and scenario; every cell lists its
runs one by one (outcome, hidden tests, calls, cost) next to the dispersion (n, min, median, max) of
each measure, so no aggregate hides a failed or stopped run.
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
CONDITIONS = {"baseline": "Sin harness", "harness": "Con harness"}
SCENARIOS = {"greenfield": "Greenfield", "brownfield": "Brownfield", "security": "Seguridad"}


def load(results: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(results.glob("claude-*.jsonl")):
        records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    # The main comparison uses the full prompt with Claude; other blocks have their own report.
    return [
        r
        for r in records
        if r.get("prompt", "full") == "full" and not r["model"].startswith("gpt-")
    ]


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
            "visibleGreen": sum(
                1 for x in m if x["visibleSuite"]["tests"] and x["visibleSuite"]["exitCode"] == 0
            ),
            "visibleTests": stats([x["visibleSuite"]["tests"] for x in m]),
            "regressionAllPassed": sum(
                1
                for x in m
                if x["regression"] and x["regression"]["passed"] == x["regression"]["tests"]
            )
            if scenario == "brownfield"
            else None,
            "delivered": sum(1 for r in runs if delivered(r)),
            "deliveredWithHiddenFailures": sum(
                1 for r in runs if delivered(r) and hidden_ratio(r) < 1.0
            ),
            "diffCoverage": stats([x["diffCoverage"]["ratio"] for x in m]),
            "addedLines": stats([x["change"]["addedLines"] for x in m]),
            "files": stats([x["change"]["files"] for x in m]),
            "outOfScopeRuns": sum(1 for x in m if x["change"]["outOfScopeFiles"]),
            "ruffIntroduced": stats([x["static"]["ruffIntroduced"] for x in m]),
            "banditIntroduced": {
                sev: sum(x["static"]["banditIntroduced"][sev] for x in m)
                for sev in ("HIGH", "MEDIUM", "LOW")
            },
            "costUsd": stats([r["agent"]["costUsd"] for r in runs]),
            "outputTokens": stats([r["agent"]["outputTokens"] for r in runs]),
            "inputTokensTotal": stats(
                [
                    r["agent"]["inputTokens"]
                    + r["agent"]["cacheReadTokens"]
                    + r["agent"]["cacheCreationTokens"]
                    for r in runs
                ]
            ),
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
                "traceRatio": stats(
                    [x["trace"]["present"] / x["trace"]["requiredCount"] for x in h]
                ),
                "traceComplete": sum(
                    1 for x in h if x["trace"]["present"] == x["trace"]["requiredCount"]
                ),
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
    comma = FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))
    colors = {"baseline": "#9a9a9a", "harness": "#2f4f6f"}
    target.mkdir(parents=True, exist_ok=True)

    def grouped_box(metric, ylabel: str, name: str, log: bool = False) -> None:
        fig, axes = plt.subplots(1, len(SCENARIOS), figsize=(6.3, 2.6), sharey=True)
        for ax, scenario in zip(axes, SCENARIOS, strict=True):
            data, positions, tint = [], [], []
            for i, model in enumerate(MODELS):
                for j, condition in enumerate(CONDITIONS):
                    values = [
                        metric(r)
                        for r in records
                        if r["model"] == model
                        and r["scenario"] == scenario
                        and r["condition"] == condition
                    ]
                    data.append(values)
                    positions.append(i * 3 + j)
                    tint.append(colors[condition])
            box = ax.boxplot(
                data,
                positions=positions,
                widths=0.75,
                patch_artist=True,
                showfliers=False,
                medianprops={"color": "black", "linewidth": 1},
            )
            for patch, color in zip(box["boxes"], tint, strict=True):
                patch.set_facecolor(color)
                patch.set_alpha(0.85)
            for pos, values in zip(positions, data, strict=True):
                ax.scatter([pos] * len(values), values, s=6, color="black", zorder=3)
            ax.set_xticks(
                [i * 3 + 0.5 for i in range(len(MODELS))], [LABELS[m].split()[0] for m in MODELS]
            )
            ax.set_title(SCENARIOS[scenario])
            if log:
                ax.set_yscale("log")
            ax.yaxis.set_major_formatter(comma)
        axes[0].set_ylabel(ylabel)
        handles = [plt.Rectangle((0, 0), 1, 1, color=colors[c]) for c in CONDITIONS]
        fig.legend(
            handles,
            CONDITIONS.values(),
            loc="upper center",
            ncol=2,
            frameon=False,
            bbox_to_anchor=(0.5, 1.02),
        )
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
        keys = (
            "status",
            "phase",
            "gate",
            "gateReasons",
            "findings",
            "changeSetFiles",
            "eventChainValid",
            "statusExitCode",
            "statusError",
            "traceExportExitCode",
            "terminalReason",
        )
        return json.dumps(
            {
                "steps": [s["exitCode"] for s in record["steps"]],
                "delivered": record["delivered"],
                **{k: final.get(k) for k in keys},
            },
            sort_keys=True,
        )

    out = {}
    for name, records in probes.items():
        first = records[0]
        out[name] = {
            "reps": len(records),
            "deterministic": len({signature(r) for r in records}) == 1,
            "steps": [(s["step"], s["exitCode"]) for s in first["steps"]],
            "afterStart": {
                k: first["afterStart"].get(k)
                for k in ("status", "phase", "gate", "gateReasons", "findings")
            },
            "final": first["final"],
            "delivered": first["delivered"],
            "treeChangedFiles": first.get("treeChangedFiles"),
            "providerArtifactBytes": first.get("providerArtifactBytes"),
            "afterContinue": first.get("afterContinue"),
            "afterRequestChanges": first.get("afterRequestChanges"),
        }
    return out


# ----- 2.0.0 evaluation (blocks A and B) -------------------------------------------------------
GOVERNED_V2 = ("harness-core", "harness", "harness-anchored", "harness-tiered", "clarify")
BLOCKS_V2 = {"full": "A", "poor": "B", "casual": "B"}


def load_v2(results: Path) -> list[dict[str, Any]]:
    """Every run record of the 2.0.0 evaluation under ``results`` (any ``*.jsonl``, recursively)."""
    records = []
    for path in sorted(results.rglob("*.jsonl")):
        if path.name.endswith(".invalid.jsonl"):
            continue  # runs stopped by the usage limit are not results
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if "scenario" in record and "condition" in record and "measures" in record:
                record["_file"] = str(path.relative_to(results))
                records.append(record)
    return records


def spread(values: list[float | None]) -> dict[str, Any]:
    """Dispersion of a cell: n, min, median, max (and the values themselves when n <= 5)."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return {"n": 0, "min": None, "median": None, "max": None}
    out: dict[str, Any] = {
        "n": len(clean),
        "min": clean[0],
        "median": statistics.median(clean),
        "max": clean[-1],
    }
    if len(clean) <= 5:
        out["values"] = clean
    return out


def delivered_v2(r: dict[str, Any]) -> bool:
    return (
        bool((r.get("harness") or {}).get("delivered")) if r["condition"] in GOVERNED_V2 else True
    )


def hidden_ok(r: dict[str, Any]) -> bool:
    h = r["measures"]["hidden"]
    return bool(h["tests"]) and h["passed"] == h["tests"]


def _harness_measures(r: dict[str, Any]) -> dict[str, Any]:
    return (((r.get("harness") or {}).get("state")) or {}).get("measures") or {}


def run_row(r: dict[str, Any]) -> dict[str, Any]:
    """One run, as listed in its cell (no aggregate hides a failure: every run is listed)."""
    h = r.get("harness") or {}
    hidden = r["measures"]["hidden"]
    return {
        "rep": r["rep"],
        "outcome": h.get("outcome", "direct"),
        "delivered": delivered_v2(r),
        "hidden": f"{hidden['passed']}/{hidden['tests']}",
        "measuredOn": r["measures"].get("measuredOn", "workspace"),
        "calls": r["agent"]["calls"],
        "costUsd": r["agent"]["costUsd"],
        "corrections": h.get("corrections"),
        "waits": [w.get("wait") for w in h.get("waits") or []],
        "dryRun": r.get("dryRun", False),
    }


def summarize_cell_v2(runs: list[dict[str, Any]]) -> dict[str, Any]:
    m = [r["measures"] for r in runs]
    governed = [r for r in runs if r["condition"] in GOVERNED_V2]
    cell: dict[str, Any] = {
        "runs": len(runs),
        "perRun": [run_row(r) for r in sorted(runs, key=lambda x: x["rep"])],
        "delivered": sum(1 for r in runs if delivered_v2(r)),
        "deliveredDefective": sum(1 for r in runs if delivered_v2(r) and not hidden_ok(r)),
        "notDeliveredCorrect": sum(1 for r in runs if not delivered_v2(r) and hidden_ok(r)),
        "notDeliveredDefective": sum(1 for r in runs if not delivered_v2(r) and not hidden_ok(r)),
        "hiddenRatio": spread(
            [
                x["hidden"]["passed"] / x["hidden"]["tests"] if x["hidden"]["tests"] else 0.0
                for x in m
            ]
        ),
        "visibleGreen": sum(
            1
            for x in m
            if (x.get("visibleSuite") or {}).get("tests") and x["visibleSuite"]["exitCode"] == 0
        ),
        "regressionAllPassed": sum(
            1
            for x in m
            if x.get("regression") and x["regression"]["passed"] == x["regression"]["tests"]
        ),
        "diffCoverage": spread([(x.get("diffCoverage") or {}).get("ratio") for x in m]),
        "addedLines": spread([x["change"]["addedLines"] for x in m]),
        "testFilesChanged": spread([x["change"]["testFiles"] for x in m]),
        "outOfScopeRuns": sum(1 for x in m if x["change"]["outOfScopeFiles"]),
        "ruffIntroduced": spread([(x.get("static") or {}).get("ruffIntroduced") for x in m]),
        "banditIntroduced": {
            sev: sum(((x.get("static") or {}).get("banditIntroduced") or {}).get(sev, 0) for x in m)
            for sev in ("HIGH", "MEDIUM", "LOW")
        },
        "secretLiteralRuns": sum(1 for x in m if x.get("secretLiteralFiles")),
        "secretLiteralDelivered": sum(
            1 for r in runs if r["measures"].get("secretLiteralFiles") and delivered_v2(r)
        ),
        "measuredOnQuarantineCopy": sum(1 for x in m if x.get("measuredOn") == "quarantine-copy"),
        "calls": spread([r["agent"]["calls"] for r in runs]),
        "costUsd": spread([r["agent"]["costUsd"] for r in runs]),
        "outputTokens": spread([r["agent"]["outputTokens"] for r in runs]),
        "inputTokensTotal": spread(
            [
                r["agent"]["inputTokens"]
                + r["agent"]["cacheReadTokens"]
                + r["agent"]["cacheCreationTokens"]
                for r in runs
            ]
        ),
        "agentErrors": sum(r["agent"]["errors"] for r in runs),
        "permissionDenials": spread([r["agent"]["permissionDenials"] for r in runs]),
        "seconds": {
            "total": spread([r["wallSeconds"] for r in runs]),
            "agentCalls": spread([r["agent"]["wallSeconds"] for r in runs]),
            "harnessProcess": spread(
                [(r.get("timeSplit") or {}).get("harnessProcessSeconds") for r in runs]
            ),
            "productOwner": spread(
                [(r.get("timeSplit") or {}).get("productOwnerSeconds") for r in runs]
            ),
        },
    }
    if governed:
        split = [r.get("agentSplit") or {} for r in governed]
        h = [r["harness"] for r in governed]
        hm = [_harness_measures(r) for r in governed]
        outcomes: dict[str, int] = defaultdict(int)
        waits: dict[str, int] = defaultdict(int)
        kinds: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for x in h:
            outcomes[x["outcome"]] += 1
            for w in x.get("waits") or []:
                waits[str(w.get("wait"))] += 1
        for r in governed:
            for label, value in (r.get("agentByKindModel") or {}).items():
                kinds[label]["calls"].append(value["calls"])
                kinds[label]["costUsd"].append(value["costUsd"])
                kinds[label]["outputTokens"].append(value["outputTokens"])
                kinds[label]["wallSeconds"].append(value["wallSeconds"])
        certification: dict[str, int] = defaultdict(int)
        domains: dict[str, int] = defaultdict(int)
        writes: dict[str, int] = defaultdict(int)
        lanes: dict[str, int] = defaultdict(int)
        for x in hm:
            for status, n in ((x.get("certification") or {}).get("byStatus") or {}).items():
                certification[status] += n
            for domain, n in ((x.get("reviewFindings") or {}).get("byDomain") or {}).items():
                domains[domain] += n
            for rule, n in (x.get("writeFindings") or {}).items():
                writes[rule] += n
            classified = [
                e for e in x.get("laneEvents") or [] if e.get("type") == "lane.classified"
            ]
            if classified:
                lanes[str(classified[-1].get("lane"))] += 1
        trace = [x.get("trace") or {} for x in h]
        missing: Counter[str] = Counter()
        for t in trace:
            for name in t.get("required2") or []:
                if not (t.get("relations2") or {}).get(name):
                    missing[name] += 1
        routed = Counter(
            f"{d.get('callKind')}:{d.get('model')}:{d.get('effort')}"
            for x in hm
            for d in x.get("routingDecisions") or []
            if d.get("mode") in ("tiered", "anchored")
        )
        cell["harness"] = {
            "outcomes": dict(outcomes),
            "gateFirst": dict(
                (g, sum(1 for x in h if x.get("gateHistory") and x["gateHistory"][0] == g))
                for g in {x["gateHistory"][0] for x in h if x.get("gateHistory")}
            ),
            "corrections": spread([x.get("corrections") for x in h]),
            "waits": dict(waits),
            "clarificationRounds": spread([len(x.get("clarification") or []) for x in h]),
            "clarificationQuestions": spread(
                [
                    sum(len(c.get("questions") or []) for c in x.get("clarification") or [])
                    for x in h
                ]
            ),
            "productOwnerCalls": spread(
                [(r.get("productOwnerCalls") or {}).get("calls") for r in governed]
            ),
            "productOwnerCostUsd": spread(
                [(r.get("productOwnerCalls") or {}).get("costUsd") for r in governed]
            ),
            "implementationCalls": spread(
                [(s.get("implementation") or {}).get("calls") for s in split]
            ),
            "governanceCalls": spread([(s.get("governance") or {}).get("calls") for s in split]),
            "governanceCostUsd": spread(
                [(s.get("governance") or {}).get("costUsd") for s in split]
            ),
            "governanceSeconds": spread(
                [(s.get("governance") or {}).get("wallSeconds") for s in split]
            ),
            "callsByKindModel": {
                label: {key: spread(values) for key, values in sorted(series.items())}
                for label, series in sorted(kinds.items())
            },
            "trace8Complete": sum(
                1
                for t in trace
                if t.get("requiredCount") and t.get("present") == t.get("requiredCount")
            ),
            "trace8Ratio": spread(
                [
                    t["present"] / t["requiredCount"] if t.get("requiredCount") else None
                    for t in trace
                ]
            ),
            "trace2Complete": sum(1 for t in trace if t.get("present2") == t.get("requiredCount2")),
            "trace2Missing": dict(sorted(missing.items())),
            "recordVerified": sum(1 for x in h if ((x.get("state") or {}).get("verifyExit")) == 0),
            "chainValid": sum(1 for x in h if x.get("eventChainValid")),
            "certificationByStatus": dict(certification),
            "reviewFindingsByDomain": dict(domains),
            "unsupportedClaims": spread([x.get("unsupportedClaims") for x in hm]),
            "writeFindings": dict(writes),
            "lane": dict(lanes),
            "routedModels": dict(sorted(routed.items())),
            "quarantined": sum(1 for x in hm if x.get("quarantine")),
            "coreDigestEqual": [
                (x.get("coreCheck") or {}).get("digestEqual") for x in h if x.get("coreCheck")
            ],
        }
    return cell


def summarize_v2(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per block, one cell per (prompt, condition, model, scenario), each with its runs listed."""
    blocks: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        block = BLOCKS_V2.get(r.get("prompt", "full"), "?")
        key = "|".join((r.get("prompt", "full"), r["condition"], r["model"], r["scenario"]))
        blocks[block][key].append(r)
    return {
        block: {key: summarize_cell_v2(runs) for key, runs in sorted(cells.items())}
        for block, cells in sorted(blocks.items())
    }


def main_v2(argv: list[str]) -> int:
    """``report.py --v2 <results-dir> <summary.json>``: the 2.0.0 summary of blocks A and B."""
    results, summary = Path(argv[0]), Path(argv[1])
    records = load_v2(results)
    data = {
        "records": len(records),
        "dryRunRecords": sum(1 for r in records if r.get("dryRun")),
        "files": sorted({r["_file"] for r in records}),
        "blocks": summarize_v2(records),
    }
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(records)} records -> {summary}")
    return 0


def main() -> int:
    if sys.argv[1] == "--v2":
        return main_v2(sys.argv[2:])
    results, summary = Path(sys.argv[1]), Path(sys.argv[2])
    records = load(results)
    data = {
        "records": len(records),
        "cells": summarize(records),
        "probes": summarize_probes(results / "fault-probes.jsonl"),
    }
    summary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    if len(sys.argv) > 3:
        figures(records, Path(sys.argv[3]))
    print(f"{len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
