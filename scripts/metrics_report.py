#!/usr/bin/env python3
"""Render the process telemetry of governed runs as a self-contained HTML report.

The report reads projects through the package's application layer
(``HarnessApplication.list_runs`` and ``status``), never through SQL, so it sees exactly what the
CLI and the API see. It aggregates **per run and per project, never per actor**: the harness
deliberately produces no per-person indicators (thesis requirement RD-16), and this report keeps
that property.

Usage::

    python scripts/metrics_report.py PROJECT_DIR [PROJECT_DIR ...] --output metrics.html
        [--json metrics.json]
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from governed_harness.application import HarnessApplication

PHASES = (
    "INTENT",
    "DISCOVERY",
    "SPECIFICATION",
    "PLANNING",
    "IMPLEMENTATION",
    "VERIFICATION",
    "INDEPENDENT_REVIEW",
    "DECISION",
    "CLOSURE",
)


def esc(value: object) -> str:
    return html.escape(str(value))


def fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def table(headers: Sequence[str], rows: Iterable[Sequence[object]], caption: str) -> str:
    head = "".join(f"<th scope='col'>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(fmt(c))}</td>" for c in row) + "</tr>" for row in rows
    )
    return (
        f"<div class='table'><table><caption>{esc(caption)}</caption>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def bars(title: str, items: Sequence[tuple[str, float]], unit: str, desc: str) -> str:
    """Horizontal bars on a linear axis with the value printed next to each bar."""
    width, left, bar_h = 760, 200, 24
    peak = max((value for _, value in items), default=0) or 1
    span = width - left - 90
    parts = []
    for index, (label, value) in enumerate(items):
        y = index * bar_h
        length = max(value / peak * span, 1 if value else 0)
        parts.append(
            f'<text x="{left - 8}" y="{y + 16}" class="label" text-anchor="end">{esc(label)}</text>'
            f'<rect x="{left}" y="{y + 4}" width="{length:.1f}" height="{bar_h - 8}" class="bar">'
            f"<title>{esc(label)}: {esc(fmt(value))} {esc(unit)}</title></rect>"
            f'<text x="{left + length + 6:.1f}" y="{y + 16}" class="value">{esc(fmt(value))} {esc(unit)}</text>'
        )
    height = len(items) * bar_h + 4
    return (
        f"<figure><figcaption>{esc(title)}</figcaption>"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(desc)}">{"".join(parts)}</svg></figure>'
    )


def phase_duration_ms(phase: dict[str, Any]) -> int | None:
    start, end = phase.get("startedAt"), phase.get("finishedAt")
    if not start or not end:
        return None
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return int(delta.total_seconds() * 1000)


def collect(projects: Sequence[Path]) -> list[dict[str, Any]]:
    app = HarnessApplication()
    runs: list[dict[str, Any]] = []
    for project in projects:
        for execution in app.list_runs(project):
            status = app.status(project, execution.execution_id)
            runs.append({"project": project.name, "status": status})
    return runs


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    gate_states = Counter((r["status"]["gate"] or {}).get("status", "NO_GATE") for r in runs)
    run_states = Counter(r["status"]["execution"]["status"] for r in runs)
    decisions = Counter((r["status"]["humanDecision"] or {}).get("decision", "NONE") for r in runs)
    phase_ms: dict[str, list[int]] = defaultdict(list)
    for run in runs:
        for phase in run["status"]["phases"]:
            duration = phase_duration_ms(phase)
            if duration is not None:
                phase_ms[phase["phaseId"]].append(duration)
    quality: dict[str, Counter[str]] = defaultdict(Counter)
    definitions: dict[str, dict[str, Any]] = {}
    for run in runs:
        for key, metric in run["status"]["metrics"].items():
            quality[key][metric["quality"]] += 1
            definitions.setdefault(key, metric)
    return {
        "runs": len(runs),
        "projects": sorted({r["project"] for r in runs}),
        "gateStates": dict(gate_states),
        "runStates": dict(run_states),
        "finalDecisions": dict(decisions),
        "phaseMedianMs": {p: statistics.median(v) for p, v in phase_ms.items()},
        "metricQuality": {k: dict(v) for k, v in quality.items()},
        "definitions": definitions,
    }


STYLE = """
:root{--bg:#fbfbfa;--fg:#1d2330;--muted:#5b6475;--card:#fff;--line:#d9dde5;--bar:#2563eb;--note-bg:#eef4ff}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#10141b;--fg:#e6e9ef;--muted:#9aa4b5;
--card:#171c25;--line:#2a3140;--bar:#6ea0ff;--note-bg:#18233a}}
:root[data-theme="dark"]{--bg:#10141b;--fg:#e6e9ef;--muted:#9aa4b5;--card:#171c25;--line:#2a3140;--bar:#6ea0ff;--note-bg:#18233a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.15rem;margin:2em 0 .6em;border-bottom:1px solid var(--line);padding-bottom:.3em}
.muted{color:var(--muted)}.note{background:var(--note-bg);border-radius:8px;padding:12px 14px;margin:16px 0}
figure{margin:12px 0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
figcaption{font-weight:600;margin-bottom:6px}svg{width:100%;height:auto}.bar{fill:var(--bar)}
.label{fill:var(--fg);font-size:12px}.value{fill:var(--muted);font-size:11px}.table{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}caption{text-align:left;color:var(--muted);padding:4px 0}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{font-weight:600}
"""


def render(runs: list[dict[str, Any]], summary: dict[str, Any], title: str) -> str:
    s: list[str] = [
        "<p class='note'>Process telemetry of governed runs, read through the harness application "
        "layer. Values are aggregated per run and per project only; the harness produces "
        "<strong>no per-person indicators</strong>. Every metric carries its data quality "
        "(OBSERVED, REPORTED, DERIVED, ESTIMATED, NOT_AVAILABLE); token and cost values stay "
        "NOT_AVAILABLE unless the provider reports them.</p>",
        f"<p class='muted'>{summary['runs']} run(s) across {len(summary['projects'])} project(s): "
        f"{esc(', '.join(summary['projects']))}.</p>",
    ]
    s.append("<h2>Gate states</h2>")
    gate_items = sorted(summary["gateStates"].items())
    s.append(
        bars(
            "Latest gate evaluation per run",
            [(k, float(v)) for k, v in gate_items],
            "runs",
            "Bar chart of runs per gate state; data in the table below.",
        )
    )
    s.append(table(["Gate state", "Runs"], gate_items, "Latest gate evaluation per run"))
    s.append(
        table(
            ["Run state", "Runs"], sorted(summary["runStates"].items()), "Execution state per run"
        )
    )

    s.append("<h2>Duration per phase</h2>")
    phase_items = [
        (p, float(summary["phaseMedianMs"][p])) for p in PHASES if p in summary["phaseMedianMs"]
    ]
    s.append(
        bars(
            "Median wall-clock duration per phase attempt",
            phase_items,
            "ms",
            "Bar chart of median phase duration; data in the table below.",
        )
    )
    s.append(
        table(["Phase", "Median ms"], phase_items, "Median duration per phase attempt, all runs")
    )

    s.append("<h2>Attempts, corrections and human wait per run</h2>")
    rows = []
    for run in runs:
        m = run["status"]["metrics"]
        e = run["status"]["execution"]
        rows.append(
            (
                run["project"],
                e["executionId"][-12:],
                e["status"],
                (run["status"]["gate"] or {}).get("status", "—"),
                m["implementation.attempts"]["value"],
                m["correction.cycles"]["value"],
                m["review.cycles"]["value"],
                m["validation.non_passed"]["value"],
                m["changesets.count"]["value"],
                m["human.decisions"]["value"],
                m["duration.human_wait_ms"]["value"],
                run["status"]["eventCount"],
                run["status"]["eventChainValid"],
            )
        )
    s.append(
        table(
            [
                "Project",
                "Run",
                "State",
                "Gate",
                "Impl. attempts",
                "Correction cycles",
                "Review cycles",
                "Non-passed validations",
                "ChangeSets",
                "Human decisions",
                "Human wait ms",
                "Events",
                "Chain valid",
            ],
            rows,
            "Per-run process metrics (no actor dimension)",
        )
    )

    s.append("<h2>Authorized exceptions</h2>")
    exceptions = [
        (
            r["project"],
            r["status"]["execution"]["executionId"][-12:],
            (r["status"]["gate"] or {}).get("status", "—"),
            r["status"]["humanDecision"]["rationale"],
        )
        for r in runs
        if (r["status"]["humanDecision"] or {}).get("decision") == "APPROVE_EXCEPTION"
    ]
    s.append(
        table(
            ["Project", "Run", "Gate at decision", "Recorded rationale"],
            exceptions,
            f"{len(exceptions)} run(s) closed with APPROVE_EXCEPTION",
        )
    )
    s.append(
        table(
            ["Final decision", "Runs"],
            sorted(summary["finalDecisions"].items()),
            "Latest human decision per run",
        )
    )

    s.append("<h2>Data quality per metric</h2>")
    quality_rows = []
    for key in sorted(summary["definitions"]):
        d = summary["definitions"][key]
        q = ", ".join(f"{k}×{v}" for k, v in sorted(summary["metricQuality"][key].items()))
        quality_rows.append((key, d["unit"], q, d["definition"], d["source"]))
    s.append(
        table(
            ["Metric", "Unit", "Quality (runs)", "Definition", "Source"],
            quality_rows,
            "Quality label reported by the harness for each metric",
        )
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{STYLE}</style></head><body><main>"
        f"<h1>{esc(title)}</h1>{''.join(s)}</main></body></html>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("projects", nargs="+", type=Path, help="governed project directories")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", type=Path, help="also write the aggregated summary as JSON")
    parser.add_argument("--title", default="Governed Agent Harness — process metrics")
    args = parser.parse_args(argv)
    runs = collect([p.resolve() for p in args.projects])
    if not runs:
        raise SystemExit("no governed runs found in the given projects")
    summary = summarize(runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(runs, summary, args.title), encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {k: v for k, v in summary.items() if k != "definitions"}, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"wrote {args.output} from {summary['runs']} run(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
