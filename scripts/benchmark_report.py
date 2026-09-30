#!/usr/bin/env python3
"""Render benchmark results of the Governed Agent Harness as a self-contained HTML report.

Inputs are the JSON files written by ``harness benchmark run`` (one per repetition, at least
one) and, optionally, ``harness benchmark scenarios`` and the thesis reference values. Charts
are inline SVG generated here (no external scripts); every chart has an equivalent data table.

It can also emit the ``customSmallerIsBetter`` format of
benchmark-action/github-action-benchmark (``[{"name", "unit", "value"}]``) for trend history.

Usage::

    python scripts/benchmark_report.py --micro run-*.json [--scenarios scenarios.json]
        [--reference docs/benchmarks/thesis-reference.json]
        --output report.html [--trend trend.json]
"""

from __future__ import annotations

import argparse
import html
import json
import math
import statistics
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

OPERATIONS = (
    "stateTransition",
    "gateEvaluation",
    "canonicalHash",
    "artifactPutDeduplicated",
    "eventAppend",
    "processDirect",
    "processGoverned",
)
LABELS = {
    "stateTransition": "State transition",
    "gateEvaluation": "Gate evaluation",
    "canonicalHash": "Canonical hash (digest)",
    "artifactPutDeduplicated": "Artifact put (deduplicated)",
    "eventAppend": "Event append (hash chain)",
    "processDirect": "Process launch, direct",
    "processGoverned": "Process launch, governed",
}
WIDTH = 760
BAR_H = 22
GAP = 10
LEFT = 210


def load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def fmt(value: float) -> str:
    if value == 0:
        return "0"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    if abs(value) >= 1:
        return f"{value:.3f}"
    return f"{value:.3g}"


def esc(value: object) -> str:
    return html.escape(str(value))


def log_axis(values: Iterable[float]) -> tuple[float, float]:
    positive = [v for v in values if v > 0]
    lo = math.floor(math.log10(min(positive)))
    hi = math.ceil(math.log10(max(positive)))
    return float(lo), float(hi if hi > lo else lo + 1)


def x_log(value: float, lo: float, hi: float, span: float) -> float:
    return LEFT + (math.log10(max(value, 10**lo)) - lo) / (hi - lo) * span


def axis_ticks(lo: float, hi: float, span: float, height: float, unit: str) -> str:
    parts = []
    for exponent in range(int(lo), int(hi) + 1):
        x = LEFT + (exponent - lo) / (hi - lo) * span
        label = f"{10**exponent:,} {unit}" if exponent >= 0 else f"{10.0**exponent:g} {unit}"
        parts.append(
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height:.1f}" class="grid"/>'
            f'<text x="{x:.1f}" y="{height + 16:.1f}" class="tick" text-anchor="middle">{esc(label)}</text>'
        )
    return "".join(parts)


def bar_chart_log(
    title: str, rows: Sequence[tuple[str, list[tuple[str, float]]]], unit: str, desc: str
) -> str:
    """Grouped horizontal bars on a log10 axis. rows = [(label, [(series, value), ...])]."""
    series_count = max(len(values) for _, values in rows)
    lo, hi = log_axis(v for _, values in rows for _, v in values)
    span = WIDTH - LEFT - 70
    group_h = series_count * BAR_H + GAP
    height = len(rows) * group_h
    body = [axis_ticks(lo, hi, span, height, unit)]
    for index, (label, values) in enumerate(rows):
        y0 = index * group_h
        body.append(
            f'<text x="{LEFT - 8}" y="{y0 + series_count * BAR_H / 2 + 4:.1f}" class="label" '
            f'text-anchor="end">{esc(label)}</text>'
        )
        for s_index, (series, value) in enumerate(values):
            y = y0 + s_index * BAR_H
            x_end = x_log(value, lo, hi, span)
            body.append(
                f'<rect x="{LEFT}" y="{y + 3}" width="{max(x_end - LEFT, 1):.1f}" height="{BAR_H - 6}" '
                f'class="s{s_index}"><title>{esc(label)} — {esc(series)}: {fmt(value)} {esc(unit)}</title></rect>'
                f'<text x="{x_end + 4:.1f}" y="{y + BAR_H / 2 + 4:.1f}" class="value">{fmt(value)}</text>'
            )
    legend = "".join(
        f'<span class="key"><span class="swatch s{i}"></span>{esc(name)}</span>'
        for i, (name, _) in enumerate(rows[0][1])
    )
    return (
        f'<figure><figcaption>{esc(title)}</figcaption><div class="legend">{legend}</div>'
        f'<svg viewBox="0 0 {WIDTH} {height + 28}" role="img" aria-label="{esc(desc)}">{"".join(body)}</svg>'
        f"</figure>"
    )


def dispersion_chart(title: str, samples: dict[str, list[float]], desc: str) -> str:
    lo, hi = log_axis(v for values in samples.values() for v in values)
    span = WIDTH - LEFT - 40
    row_h = 26
    height = len(samples) * row_h
    body = [axis_ticks(lo, hi, span, height, "ms")]
    for index, (op, values) in enumerate(samples.items()):
        y = index * row_h + row_h / 2
        xs = [x_log(v, lo, hi, span) for v in values]
        body.append(
            f'<text x="{LEFT - 8}" y="{y + 4:.1f}" class="label" text-anchor="end">{esc(LABELS[op])}</text>'
            f'<line x1="{min(xs):.1f}" y1="{y:.1f}" x2="{max(xs):.1f}" y2="{y:.1f}" class="range"/>'
        )
        for value, x in zip(values, xs, strict=True):
            body.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="dot">'
                f"<title>{esc(LABELS[op])}: {fmt(value)} ms</title></circle>"
            )
    return (
        f"<figure><figcaption>{esc(title)}</figcaption>"
        f'<svg viewBox="0 0 {WIDTH} {height + 28}" role="img" aria-label="{esc(desc)}">{"".join(body)}</svg>'
        f"</figure>"
    )


def table(headers: Sequence[str], rows: Iterable[Sequence[object]], caption: str) -> str:
    head = "".join(f"<th scope='col'>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows)
    return (
        f"<div class='table'><table><caption>{esc(caption)}</caption>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def aggregate(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for op in OPERATIONS:
        entries = [run["benchmarks"][op] for run in runs if op in run["benchmarks"]]
        if not entries:
            continue
        medians = [float(e["medianMs"]) for e in entries]
        result[op] = {
            "medians": medians,
            "median": statistics.median(medians),
            "p95": statistics.median(float(e["p95Ms"]) for e in entries),
            "ops": statistics.median(float(e["operationsPerSecond"]) for e in entries),
            "iterations": int(float(entries[0]["iterations"])),
            "min": min(medians),
            "max": max(medians),
            "stdev": statistics.stdev(medians) if len(medians) > 1 else 0.0,
        }
    overheads = [run["benchmarks"].get("processGovernanceOverhead", {}) for run in runs]
    result["processGovernanceOverhead"] = {
        "absolute": [float(o["absoluteMedianMs"]) for o in overheads if o],
        "relative": [float(o["relativeMedianPercent"]) for o in overheads if o],
        "note": next((o.get("note", "") for o in overheads if o), ""),
    }
    return result


def trend(agg: dict[str, dict[str, Any]]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = [
        {"name": f"{LABELS[op]} (median)", "unit": "ms", "value": round(agg[op]["median"], 6)}
        for op in OPERATIONS
        if op in agg
    ]
    overhead = agg["processGovernanceOverhead"]
    if overhead["relative"]:
        points.append(
            {
                "name": "Governed process overhead (relative median)",
                "unit": "%",
                "value": round(statistics.median(overhead["relative"]), 3),
            }
        )
    return points


STYLE = """
:root{--bg:#fbfbfa;--fg:#1d2330;--muted:#5b6475;--card:#fff;--line:#d9dde5;--grid:#e7eaf0;
--s0:#2563eb;--s1:#d97706;--s2:#059669;--warn-bg:#fff7e6;--warn-fg:#7a4b00;--accent:#2563eb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#10141b;--fg:#e6e9ef;
--muted:#9aa4b5;--card:#171c25;--line:#2a3140;--grid:#222937;--s0:#6ea0ff;--s1:#f0a94b;--s2:#3ccf9a;
--warn-bg:#2b2210;--warn-fg:#f3cf85;--accent:#6ea0ff}}
:root[data-theme="dark"]{--bg:#10141b;--fg:#e6e9ef;--muted:#9aa4b5;--card:#171c25;--line:#2a3140;
--grid:#222937;--s0:#6ea0ff;--s1:#f0a94b;--s2:#3ccf9a;--warn-bg:#2b2210;--warn-fg:#f3cf85;--accent:#6ea0ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.6rem;margin:.2em 0}
h2{font-size:1.15rem;margin:2em 0 .6em;border-bottom:1px solid var(--line);padding-bottom:.3em}
.muted{color:var(--muted)}.warn{background:var(--warn-bg);color:var(--warn-fg);border-radius:8px;
padding:12px 14px;margin:16px 0}figure{margin:12px 0;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:12px}figcaption{font-weight:600;margin-bottom:6px}svg{width:100%;height:auto}
.grid{stroke:var(--grid)}.tick,.value{fill:var(--muted);font-size:11px}.label{fill:var(--fg);font-size:12px}
.s0{fill:var(--s0);background:var(--s0)}.s1{fill:var(--s1);background:var(--s1)}.s2{fill:var(--s2);background:var(--s2)}
.dot{fill:var(--s0);fill-opacity:.8}.range{stroke:var(--s0);stroke-opacity:.4;stroke-width:6;stroke-linecap:round}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-bottom:6px}
.key{display:inline-flex;align-items:center;gap:6px}.swatch{width:12px;height:12px;border-radius:3px;display:inline-block}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
caption{text-align:left;color:var(--muted);padding:4px 0}th,td{border-bottom:1px solid var(--line);
padding:6px 8px;text-align:left;vertical-align:top}th{font-weight:600}
details{margin:6px 0 12px}summary{cursor:pointer;color:var(--accent)}code{font-size:.92em}
"""


def render(
    runs: list[dict[str, Any]],
    scenarios: dict[str, Any] | None,
    reference: dict[str, Any] | None,
    title: str,
) -> str:
    agg = aggregate(runs)
    ops = [op for op in OPERATIONS if op in agg]
    env = runs[0].get("environment", {})
    method = runs[0].get("methodology", {})
    sections: list[str] = []

    sections.append(
        '<p class="warn"><strong>Read this first.</strong> These are synthetic microbenchmarks of '
        "local harness operations, run on shared CI runners with no warm-up. They quantify "
        "runtime overhead in the recorded environment only. They do not measure developer "
        "productivity, code quality or statistical significance.</p>"
    )
    sections.append(
        f"<p class='muted'>{len(runs)} repetition(s) of <code>harness benchmark run</code>; "
        "values are the median across repetitions of each run's median (p95 likewise).</p>"
    )

    rows = [(LABELS[op], [("median", agg[op]["median"]), ("p95", agg[op]["p95"])]) for op in ops]
    sections.append("<h2>Latency per operation</h2>")
    sections.append(
        bar_chart_log(
            "Median and p95 latency per operation (log scale, milliseconds)",
            rows,
            "ms",
            "Horizontal bar chart of median and p95 latency per operation; data in the table below.",
        )
    )
    sections.append(
        table(
            [
                "Operation",
                "Iterations/run",
                "Median ms",
                "p95 ms",
                "Ops/s",
                "Min–max of medians",
                "Std. dev.",
            ],
            [
                (
                    LABELS[op],
                    agg[op]["iterations"],
                    fmt(agg[op]["median"]),
                    fmt(agg[op]["p95"]),
                    fmt(agg[op]["ops"]),
                    f"{fmt(agg[op]['min'])} – {fmt(agg[op]['max'])}",
                    fmt(agg[op]["stdev"]),
                )
                for op in ops
            ],
            "Latency per operation across repetitions",
        )
    )

    overhead = agg["processGovernanceOverhead"]
    if "processDirect" in agg and "processGoverned" in agg:
        sections.append("<h2>Direct versus governed process launch</h2>")
        rel = statistics.median(overhead["relative"]) if overhead["relative"] else float("nan")
        absolute = statistics.median(overhead["absolute"]) if overhead["absolute"] else float("nan")
        sections.append(
            bar_chart_log(
                f"Process launch median: governed overhead {fmt(absolute)} ms ({rel:.2f} %)",
                [
                    (LABELS["processDirect"], [("median", agg["processDirect"]["median"])]),
                    (LABELS["processGoverned"], [("median", agg["processGoverned"]["median"])]),
                ],
                "ms",
                "Bar chart comparing direct and governed process launch medians.",
            )
        )
        sections.append(
            table(
                ["Repetition", "Absolute median overhead (ms)", "Relative median overhead (%)"],
                [
                    (i + 1, fmt(a), f"{r:.2f}")
                    for i, (a, r) in enumerate(
                        zip(overhead["absolute"], overhead["relative"], strict=True)
                    )
                ],
                f"Governance overhead per repetition. {overhead['note']}",
            )
        )

    sections.append("<h2>Throughput</h2>")
    sections.append(
        bar_chart_log(
            "Operations per second (log scale; higher is faster)",
            [(LABELS[op], [("ops/s", agg[op]["ops"])]) for op in ops],
            "ops/s",
            "Horizontal bar chart of operations per second; data in the latency table.",
        )
    )

    if len(runs) > 1:
        sections.append("<h2>Dispersion across repetitions</h2>")
        sections.append(
            dispersion_chart(
                "Median of each repetition (one dot per repetition, log scale)",
                {op: agg[op]["medians"] for op in ops},
                "Dot plot of per-repetition medians for each operation.",
            )
        )
        sections.append(
            table(
                ["Operation", *[f"Run {i + 1} median ms" for i in range(len(runs))]],
                [(LABELS[op], *[fmt(v) for v in agg[op]["medians"]]) for op in ops],
                "Per-repetition medians",
            )
        )

    if reference:
        sections.append("<h2>Thesis reference point</h2>")
        sections.append(f"<p class='muted'>{esc(reference.get('source', ''))}</p>")
        ref_ops = reference.get("medianMs", {})
        sections.append(
            table(
                ["Operation", "Thesis median ms (v0.8.0 cut)", "This report median ms"],
                [
                    (
                        LABELS.get(op, op),
                        fmt(float(value)),
                        fmt(agg[op]["median"]) if op in agg else "—",
                    )
                    for op, value in ref_ops.items()
                ],
                "Reference values recorded in the thesis; environments differ, compare orders of magnitude only.",
            )
        )
        over = reference.get("processGovernanceOverhead", {})
        if over:
            sections.append(
                "<p>Thesis governed-process overhead: "
                f"{esc(over.get('absoluteMedianMs'))} ms, "
                f"{esc(', '.join(f'{v} %' for v in over.get('relativeMedianPercent', [])))}.</p>"
            )

    if scenarios:
        sections.append("<h2>End-to-end scenarios</h2>")
        rows_s = []
        for name, data in sorted(scenarios.get("scenarios", {}).items()):
            direct = data.get("directImplementationAndTests", {})
            governed = data.get("governedEndToEnd", {})
            over = data.get("governanceOverhead", {})
            last = data.get("lastRun", {})
            rows_s.append(
                (
                    name,
                    data.get("status", ""),
                    int(float(direct.get("iterations", 0))),
                    fmt(float(direct.get("medianMs", 0))),
                    fmt(float(governed.get("medianMs", 0))),
                    f"{float(over.get('relativeMedianPercent', 0)):.1f}",
                    last.get("events", ""),
                    last.get("eventChainValid", ""),
                )
            )
        sections.append(
            table(
                [
                    "Profile",
                    "Status",
                    "Iterations",
                    "Direct median ms",
                    "Governed median ms",
                    "Overhead %",
                    "Events (last run)",
                    "Chain valid",
                ],
                rows_s,
                "Direct patch-and-test path versus the complete governed path (programmatic approval, no human wait)",
            )
        )
        sections.append(
            table(
                ["Key", "Value"],
                sorted(scenarios.get("methodology", {}).items()),
                "Scenario methodology",
            )
        )

    sections.append("<h2>Environment and methodology</h2>")
    sections.append(
        table(["Key", "Value"], sorted(env.items()), "Environment reported by the harness")
    )
    sections.append(
        table(["Key", "Value"], sorted(method.items()), "Methodology reported by the harness")
    )
    generated = sorted(run.get("generatedAt", "") for run in runs)
    sections.append(
        f"<p class='muted'>Generated from runs between {esc(generated[0])} and {esc(generated[-1])} "
        f"(schemaVersion {esc(runs[0].get('schemaVersion', '?'))}).</p>"
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{STYLE}</style></head><body><main>"
        f"<h1>{esc(title)}</h1>{''.join(sections)}</main></body></html>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--micro", nargs="+", type=Path, required=True, help="harness benchmark run outputs"
    )
    parser.add_argument("--scenarios", type=Path, help="harness benchmark scenarios output")
    parser.add_argument("--reference", type=Path, help="thesis reference values JSON")
    parser.add_argument("--output", type=Path, required=True, help="HTML report path")
    parser.add_argument("--trend", type=Path, help="write customSmallerIsBetter JSON here")
    parser.add_argument("--title", default="Governed Agent Harness — benchmark report")
    args = parser.parse_args(argv)

    runs = [load(path) for path in args.micro]
    for path, run in zip(args.micro, runs, strict=True):
        missing = [key for key in ("benchmarks", "environment", "methodology") if key not in run]
        if missing:
            raise SystemExit(f"{path}: missing keys {missing}")
    scenarios = load(args.scenarios) if args.scenarios else None
    reference = load(args.reference) if args.reference else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(runs, scenarios, reference, args.title), encoding="utf-8")
    if args.trend:
        args.trend.write_text(json.dumps(trend(aggregate(runs)), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} from {len(runs)} repetition(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
