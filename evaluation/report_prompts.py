#!/usr/bin/env python3
"""Compare prompt levels (casual, minimal, full) with and without the harness, and summarize Codex.

Usage: python report_prompts.py <results-dir> <out.json> [<figures-dir>]      (0.9.0 records)
       python report_prompts.py --v2 <results-dir> <out.json>                  (2.0.0 block B)
Descriptive counts only; a delivered run is any run without the harness (baseline, direct) or an
approved governed run. The 2.0.0 flows add the casual prompt under the harness (2.0.0 asks for its
criteria instead of refusing it) and the conditions harness-core and harness-anchored; a flow with no
run is left out.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

FLOWS = [
    ("casual", "baseline"),
    ("poor", "baseline"),
    ("poor", "harness"),
    ("full", "baseline"),
    ("full", "harness"),
]
GOVERNED = ("harness", "harness-core", "harness-anchored", "harness-tiered", "clarify")
FLOWS_V2 = [
    (prompt, condition)
    for prompt in ("casual", "poor", "full")
    for condition in ("direct", "harness-core", "harness", "harness-anchored", "harness-tiered")
]
LABELS = {
    ("casual", "baseline"): "Casual\nsin harness",
    ("poor", "baseline"): "Mínimo\nsin harness",
    ("poor", "harness"): "Mínimo\ncon harness",
    ("full", "baseline"): "Completo\nsin harness",
    ("full", "harness"): "Completo\ncon harness",
}


def load(results: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(results.glob("*.jsonl")):
        if path.name.startswith(("claude-", "codex-")):
            records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return records


def delivered(r: dict[str, Any]) -> bool:
    return bool(r["harness"]["delivered"]) if r["condition"] in GOVERNED else True


def correct(r: dict[str, Any]) -> bool:
    h = r["measures"]["hidden"]  # in 2.0.0 a stopped run is measured on its quarantined change
    return h["tests"] > 1 and h["passed"] == h["tests"]


def has_tests(r: dict[str, Any]) -> bool:
    """A changed file named like a pytest module, wherever it is.

    `measures.change.testFiles` only counts paths under tests/, so it misses a test module written
    at the repository root and counts an empty tests/__init__.py.
    """
    names = (PurePosixPath(p).name for p in r["measures"]["change"]["paths"])
    return any(
        n.endswith(".py") and (n.startswith("test_") or n.endswith("_test.py")) for n in names
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    d = [r for r in rows if delivered(r)]
    nd = [r for r in rows if not delivered(r)]
    return {
        "runs": len(rows),
        "delivered": len(d),
        "deliveredDefective": sum(not correct(r) for r in d),
        "deliveredWithoutTests": sum(not has_tests(r) for r in d),
        "deliveredWithoutTestsGreenfieldOrSecurity": sum(
            not has_tests(r) for r in d if r["scenario"] != "brownfield"
        ),
        "runsGreenfieldOrSecurity": sum(r["scenario"] != "brownfield" for r in rows),
        "blockedCorrect": sum(correct(r) for r in nd),
        "blockedDefective": sum(not correct(r) for r in nd),
        "defectiveByScenario": {
            s: sum(1 for r in d if r["scenario"] == s and not correct(r))
            for s in ("greenfield", "brownfield", "security")
        },
        "costMedianUsd": statistics.median(
            [r["agent"]["costUsd"] for r in rows if r["agent"]["costUsd"] is not None]
        )
        if any(r["agent"]["costUsd"] is not None for r in rows)
        else None,
        "totalSecondsMedian": statistics.median([r["wallSeconds"] for r in rows]),
    }


def main_v2(results: Path, out: Path) -> int:
    from report import load_v2

    records = [r for r in load_v2(results) if not r["model"].startswith("gpt-")]
    flows = defaultdict(list)
    for r in records:
        flows[(r.get("prompt", "full"), r["condition"])].append(r)
    data = {
        "records": len(records),
        "promptLevels": {
            f"{p}|{c}": summarize(flows[(p, c)]) for p, c in FLOWS_V2 if flows[(p, c)]
        },
        "byModel": {
            f"{p}|{c}|{m}": summarize(rows)
            for (p, c), runs in sorted(flows.items())
            for m in sorted({r["model"] for r in runs})
            if (rows := [r for r in runs if r["model"] == m])
        },
        "outcomes": {
            f"{p}|{c}": dict(
                Counter((r.get("harness") or {}).get("outcome", "direct") for r in runs)
            )
            for (p, c), runs in sorted(flows.items())
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(data["promptLevels"], indent=1))
    return 0


def main() -> int:
    if sys.argv[1] == "--v2":
        return main_v2(Path(sys.argv[2]), Path(sys.argv[3]))
    results, out = Path(sys.argv[1]), Path(sys.argv[2])
    records = load(results)
    claude = [r for r in records if not r["model"].startswith("gpt-")]
    flows = defaultdict(list)
    for r in claude:
        flows[(r.get("prompt", "full"), r["condition"])].append(r)
    codex = defaultdict(list)
    for r in records:
        if r["model"].startswith("gpt-"):
            codex[(r["model"], r["condition"])].append(r)
    data = {
        "promptLevels": {f"{p}|{c}": summarize(flows[(p, c)]) for p, c in FLOWS},
        "codex": {
            f"{m}|{c}": {
                **summarize(rows),
                "approvedFirstGate": sum(
                    1
                    for r in rows
                    if r["condition"] == "harness" and r["harness"]["gateHistory"][:1] == ["PASSED"]
                ),
                "traceComplete": sum(
                    1
                    for r in rows
                    if r["condition"] == "harness"
                    and r["harness"]["trace"]["present"] == r["harness"]["trace"]["requiredCount"]
                ),
                "inputTokensMedian": statistics.median([r["agent"]["inputTokens"] for r in rows]),
                "outputTokensMedian": statistics.median([r["agent"]["outputTokens"] for r in rows]),
                "overheadSecondsMedian": statistics.median(
                    [r["wallSeconds"] - r["agent"]["wallSeconds"] for r in rows]
                )
                if c == "harness"
                else None,
            }
            for (m, c), rows in sorted(codex.items())
        },
    }
    out.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    if len(sys.argv) > 3:
        figure(data["promptLevels"], Path(sys.argv[3]))
    print(json.dumps(data["promptLevels"], indent=1))
    return 0


def figure(levels: dict[str, Any], target: Path) -> None:
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
    keys = [f"{p}|{c}" for p, c in FLOWS]
    x = range(len(keys))
    width = 0.38
    fig, ax = plt.subplots(figsize=(6.3, 2.9))
    series = [
        ("deliveredDefective", "Entregadas con defecto", "#2f4f6f"),
        ("deliveredWithoutTests", "Entregadas sin pruebas nuevas", "#9a9a9a"),
    ]
    for offset, (key, label, color) in zip((-width / 2, width / 2), series, strict=True):
        values = [100 * levels[k][key] / levels[k]["runs"] for k in keys]
        bars = ax.bar([i + offset for i in x], values, width, label=label, color=color)
        for bar, k in zip(bars, keys, strict=True):
            ax.annotate(
                f"{levels[k][key]}/{levels[k]['runs']}",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                ha="center",
                va="bottom",
                fontsize=7,
                xytext=(0, 2),
                textcoords="offset points",
            )
    ax.set_xticks(list(x), [LABELS[(k.split("|")[0], k.split("|")[1])] for k in keys])
    ax.set_ylabel("Ejecuciones (%)")
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))
    )
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    target.mkdir(parents=True, exist_ok=True)
    fig.savefig(target / "cap7-intencion.pdf")
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
