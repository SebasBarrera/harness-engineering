"""One self-contained HTML file of the metrics (#58, item 8): inline CSS and inline SVG charts,
no script, no font, no image and nothing fetched from the network, so it opens offline from
the file system. Every chart has a text title, a description and a table with the same numbers;
identity is never carried by color alone (legends and labels), light and dark color schemes
are both defined. Links to issues and pull requests are plain anchors: following one is the
reader's choice, the page itself loads nothing."""

from __future__ import annotations

import html
from typing import Any

_CSS = """
:root { color-scheme: light; --surface: #fcfcfb; --panel: #ffffff; --ink: #0b0b0b;
  --ink-2: #52514e; --grid: #d9d8d3; --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a;
  --over: #b42318; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --surface: #1a1a19;
  --panel: #232322; --ink: #ffffff; --ink-2: #c3c2b7; --grid: #3d3c39; --s1: #3987e5;
  --s2: #d95926; --s3: #199e70; --over: #ff8a80; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main { max-width: 1120px; margin: 0 auto; padding: 16px; }
h1 { font-size: 26px; margin: 8px 0 4px; } h2 { font-size: 19px; margin: 28px 0 8px; }
p.lead, .muted { color: var(--ink-2); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.card { background: var(--panel); border: 1px solid var(--grid); border-radius: 10px; padding: 12px; }
.card .value { font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; }
.card .label { color: var(--ink-2); font-size: 13px; }
section { background: var(--panel); border: 1px solid var(--grid); border-radius: 10px;
  padding: 12px 16px; margin-top: 16px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; font-variant-numeric: tabular-nums; }
caption { text-align: left; color: var(--ink-2); padding: 4px 0; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
td.num, th.num { text-align: right; }
.over { color: var(--over); font-weight: 650; }
svg { display: block; max-width: 100%; height: auto; }
svg text { fill: var(--ink-2); font: 12px system-ui, sans-serif; }
svg .value { fill: var(--ink); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--ink-2); margin: 4px 0; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px;
  vertical-align: -1px; }
details { margin-top: 8px; } summary { cursor: pointer; color: var(--ink-2); }
a { color: var(--s1); }
@media (forced-colors: active) { .swatch { forced-color-adjust: none; border: 1px solid CanvasText; } }
"""


def _e(value: Any) -> str:
    return html.escape("-" if value is None else str(value), quote=True)


def _num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") if value else "0"
    if isinstance(value, int):
        return f"{value:,}"
    return _e(value)


def _table(caption: str, headers: list[str], rows: list[list[Any]], numeric: set[int]) -> str:
    head = "".join(
        f'<th scope="col"{' class="num"' if index in numeric else ""}>{_e(item)}</th>'
        for index, item in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{_num(cell)}</td>' if index in numeric else f"<td>{cell}</td>"
            for index, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    if not rows:
        body = f'<tr><td colspan="{len(headers)}">No data in the period.</td></tr>'
    return f"<table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _bars(
    chart_id: str,
    title: str,
    description: str,
    rows: list[tuple[str, list[float]]],
    series: list[tuple[str, str]],
    unit: str,
) -> str:
    """Horizontal stacked bars (one row per key), labelled with the total; a native hover
    title on every segment."""
    if not rows:
        return f'<p class="muted">{_e(title)}: no data in the period.</p>'
    rows = rows[:12]
    width, label_w, bar_h, gap = 760, 190, 22, 10
    plot_w = width - label_w - 90
    peak = max(sum(values) for _, values in rows) or 1
    height = len(rows) * (bar_h + gap) + 8
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="{chart_id}-t '
        f'{chart_id}-d" width="{width}" height="{height}">',
        f'<title id="{chart_id}-t">{_e(title)}</title><desc id="{chart_id}-d">{_e(description)}</desc>',
        f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{height}" stroke="var(--grid)"/>',
    ]
    for index, (key, values) in enumerate(rows):
        y = 4 + index * (bar_h + gap)
        label = key if len(key) <= 28 else key[:27] + "…"
        parts.append(
            f'<text x="{label_w - 8}" y="{y + bar_h * 0.7:.1f}" text-anchor="end">{_e(label)}</text>'
        )
        x = float(label_w)
        for (name, color), value in zip(series, values, strict=True):
            if value <= 0:
                continue
            w = max(1.0, plot_w * value / peak)
            parts.append(
                f'<rect x="{x:.1f}" y="{y}" width="{max(1.0, w - 2):.1f}" height="{bar_h}" '
                f'rx="4" fill="var({color})"><title>{_e(key)}: {_e(name)} {_num(value)} '
                f"{_e(unit)}</title></rect>"
            )
            x += w
        parts.append(
            f'<text class="value" x="{x + 6:.1f}" y="{y + bar_h * 0.7:.1f}">'
            f"{_num(sum(values))}</text>"
        )
    parts.append("</svg>")
    legend = ""
    if len(series) > 1:
        legend = (
            '<div class="legend">'
            + "".join(
                f'<span><span class="swatch" style="background:var({color})"></span>{_e(name)}</span>'
                for name, color in series
            )
            + "</div>"
        )
    return legend + "".join(parts)


def _columns(chart_id: str, title: str, description: str, points: list[tuple[str, float]]) -> str:
    """Vertical columns over time (one series)."""
    if not points:
        return f'<p class="muted">{_e(title)}: no data in the period.</p>'
    points = points[-60:]
    width, height, left, bottom = 760, 200, 40, 36
    plot_w, plot_h = width - left - 10, height - bottom - 10
    peak = max(value for _, value in points) or 1
    step = plot_w / len(points)
    bar = max(2.0, min(28.0, step - 4))
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="{chart_id}-t '
        f'{chart_id}-d" width="{width}" height="{height}">',
        f'<title id="{chart_id}-t">{_e(title)}</title><desc id="{chart_id}-d">{_e(description)}</desc>',
        f'<line x1="{left}" y1="{10 + plot_h}" x2="{width - 10}" y2="{10 + plot_h}" stroke="var(--grid)"/>',
        f'<text x="{left - 6}" y="16" text-anchor="end">{_num(peak)}</text>',
        f'<text x="{left - 6}" y="{10 + plot_h}" text-anchor="end">0</text>',
    ]
    every = max(1, len(points) // 8)
    for index, (label, value) in enumerate(points):
        h = plot_h * value / peak
        x = left + index * step + (step - bar) / 2
        parts.append(
            f'<rect x="{x:.1f}" y="{10 + plot_h - h:.1f}" width="{bar:.1f}" height="{max(h, 0):.1f}" '
            f'rx="3" fill="var(--s1)"><title>{_e(label)}: {_num(value)}</title></rect>'
        )
        if index % every == 0:
            parts.append(
                f'<text x="{x + bar / 2:.1f}" y="{height - 14}" text-anchor="middle">'
                f"{_e(label[5:] if len(label) == 10 else label)}</text>"
            )
    parts.append("</svg>")
    return "".join(parts)


def _link(text: str, url: str | None) -> str:
    if not url:
        return _e(text)
    return f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(text)}</a>'


def render_html(report: dict[str, Any]) -> str:
    totals = report["totals"]
    tokens = report["tokens"]
    cards = [
        ("Runs", totals["runs"]),
        ("Tasks", totals["tasks"]),
        ("Agent calls", totals["agentCalls"]),
        ("Tokens (input + output)", totals["tokens"]["total"]),
        ("Reported cost (USD)", totals["cost"]["reportedUsd"]),
        ("Estimated cost (USD, estimated)", totals["cost"]["estimatedUsd"]),
        ("Human interactions", totals["humanInteractions"]),
        ("Human wait (minutes)", round(totals["humanWaitMs"] / 60000, 2)),
        ("Lines added", totals["linesAdded"]),
        ("Features delivered", totals["featuresDelivered"]),
    ]
    sections: list[str] = []
    token_series = [("input", "--s1"), ("output", "--s2")]
    for chart_id, title, key in (
        ("model", "Tokens by model", "byModel"),
        ("agent", "Tokens by agent", "byAgent"),
        ("phase", "Tokens by phase", "byPhase"),
        ("task", "Tokens by task", "byTask"),
    ):
        rows = tokens[key]
        chart = _bars(
            f"chart-{chart_id}",
            title,
            f"Input and output tokens of the agent calls, by {chart_id}; the table below has "
            "the same numbers with cache tokens and costs.",
            [(row["key"], [row["input"], row["output"]]) for row in rows],
            token_series,
            "tokens",
        )
        table = _table(
            title,
            [
                chart_id.capitalize(),
                "Calls",
                "Input",
                "Output",
                "Cache",
                "Reported USD",
                "Estimated USD",
                "Unpriced calls",
            ],
            [
                [
                    _e(row["key"]),
                    row["calls"],
                    row["input"],
                    row["output"],
                    row["cache"],
                    row["reportedCostUsd"],
                    row["estimatedCostUsd"],
                    row["unpricedCalls"],
                ]
                for row in rows
            ],
            {1, 2, 3, 4, 5, 6, 7},
        )
        sections.append(
            f'<section aria-labelledby="h-{chart_id}"><h2 id="h-{chart_id}">{_e(title)}</h2>'
            f"{chart}<details><summary>Table</summary>{table}</details></section>"
        )
    cost_rows = [
        (row["key"], [row["reportedCostUsd"], row["estimatedCostUsd"]])
        for row in tokens["byAgent"]
        if row["reportedCostUsd"] or row["estimatedCostUsd"]
    ]
    sections.append(
        '<section aria-labelledby="h-cost"><h2 id="h-cost">Cost by agent</h2>'
        '<p class="muted">Estimated costs come from the price table for calls that reported '
        "tokens without a cost; they are estimates, not charges.</p>"
        + _bars(
            "chart-cost",
            "Cost by agent in US dollars",
            "Reported and estimated cost per agent provider.",
            cost_rows,
            [("reported", "--s1"), ("estimated", "--s2")],
            "USD",
        )
        + "</section>"
    )
    models = report["models"]
    sections.append(
        '<section aria-labelledby="h-models"><h2 id="h-models">Models used</h2>'
        + _table(
            "Models used",
            ["Provider", "Model", "Calls", "Cost source"],
            [[_e(m["provider"]), _e(m["model"]), m["calls"], _e(m["cost"])] for m in models],
            {2},
        )
        + "</section>"
    )
    lines = report["lines"]
    sections.append(
        '<section aria-labelledby="h-lines"><h2 id="h-lines">Lines changed</h2>'
        + _bars(
            "chart-lines",
            "Lines added and removed by author kind",
            "Lines of the latest ChangeSet of each run, attributed by provenance to an agent "
            "invocation or to a person (out of band).",
            [
                ("Agent invocation", [lines["agent"]["added"], lines["agent"]["removed"]]),
                ("Person (out of band)", [lines["person"]["added"], lines["person"]["removed"]]),
                (
                    "Not attributed",
                    [lines["unattributed"]["added"], lines["unattributed"]["removed"]],
                ),
            ],
            [("added", "--s3"), ("removed", "--s2")],
            "lines",
        )
        + "</section>"
    )
    daily = report["trends"]["daily"]
    weekly = report["trends"]["weekly"]
    trend_table = _table(
        "Daily trend",
        ["Day", "Runs", "Tokens", "Cost USD", "Interactions", "Approved", "Closed"],
        [
            [
                _e(row["period"]),
                row["runs"],
                row["tokens"],
                row["costUsd"],
                row["interactions"],
                row["approved"],
                row["closed"],
            ]
            for row in daily
        ],
        {1, 2, 3, 4, 5, 6},
    )
    weekly_table = _table(
        "Weekly trend",
        ["Week", "Runs", "Tokens", "Cost USD", "Interactions", "Approved", "Closed"],
        [
            [
                _e(row["period"]),
                row["runs"],
                row["tokens"],
                row["costUsd"],
                row["interactions"],
                row["approved"],
                row["closed"],
            ]
            for row in weekly
        ],
        {1, 2, 3, 4, 5, 6},
    )
    sections.append(
        '<section aria-labelledby="h-trend"><h2 id="h-trend">Trends</h2>'
        + _columns(
            "chart-runs",
            "Runs per day",
            "Number of runs created each day.",
            [(row["period"], float(row["runs"])) for row in daily],
        )
        + _columns(
            "chart-tokens",
            "Tokens per day",
            "Input and output tokens of the agent calls of the runs created each day.",
            [(row["period"], float(row["tokens"])) for row in daily],
        )
        + f"<details><summary>Daily table</summary>{trend_table}</details>"
        + f"<details><summary>Weekly table</summary>{weekly_table}</details></section>"
    )
    friction = report["friction"]
    friction_rows = []
    for row in friction["perTask"]:
        over = set(row["overTarget"])
        target = row["target"] or {}
        friction_rows.append(
            [
                f"<code>{_e(row['taskId'])}</code> {_e(row['title'])}",
                _e(row["size"]),
                _e(", ".join(row["lanes"]) or "-"),
                row["runs"],
                f'<span class="over">{row["interactions"]} (over {target.get("interactions")})</span>'
                if "interactions" in over
                else row["interactions"],
                row["approvals"],
                f'<span class="over">{row["wallMinutes"]} (over {target.get("minutes")})</span>'
                if "minutes" in over
                else row["wallMinutes"],
                row["humanWaitMinutes"],
            ]
        )
    sections.append(
        '<section aria-labelledby="h-friction"><h2 id="h-friction">Friction per task</h2>'
        f'<p class="muted">Targets ({_e(friction["targetsSource"])}): '
        + "; ".join(
            f"{_e(size)}: {_e(values.get('interactions'))} interaction(s), "
            f"{_e(values.get('minutes'))} minutes"
            for size, values in friction["targets"].items()
        )
        + ". A value over its target is marked “over”.</p>"
        + _table(
            "Friction per task",
            [
                "Task",
                "Size",
                "Lane",
                "Runs",
                "Interactions",
                "Approvals",
                "Minutes",
                "Wait minutes",
            ],
            friction_rows,
            {3, 5, 7},
        )
        + "</section>"
    )
    time = report["time"]
    calls = time["agentCalls"]
    sections.append(
        '<section aria-labelledby="h-time"><h2 id="h-time">Time</h2>'
        + _table(
            "Time per phase",
            ["Phase", "Attempts", "Total ms", "Mean ms"],
            [
                [_e(row["phase"]), row["attempts"], row["totalMs"], row["meanMs"]]
                for row in time["perPhase"]
            ],
            {1, 2, 3},
        )
        + _table(
            "Agent calls",
            ["Calls", "Total ms", "Mean ms", "Median ms", "90th percentile ms"],
            [[calls["count"], calls["totalMs"], calls["meanMs"], calls["p50Ms"], calls["p90Ms"]]],
            {0, 1, 2, 3, 4},
        )
        + "</section>"
    )
    quality = report["quality"]
    sections.append(
        '<section aria-labelledby="h-quality"><h2 id="h-quality">Quality</h2>'
        + _table(
            "Quality of the runs",
            ["Measure", "Value"],
            [
                ["Runs decided", quality["decided"]],
                ["Approved the first time", quality["approvedFirstTime"]],
                ["Approved the first time (rate)", quality["approvedFirstTimeRate"]],
                ["Corrections", quality["corrections"]],
                ["Requested changes", quality["requestedChanges"]],
                ["Blocked runs", quality["blockedRuns"]],
                ["Waiting for a decision", quality["waitingDecision"]],
                ["Rejected runs", quality["rejectedRuns"]],
                ["Closed runs", quality["closedRuns"]],
                ["Pre-authorised approvals applied", quality["preAuthorizedApprovals"]],
            ],
            {1},
        )
        + _table(
            "Findings by rule",
            ["Rule", "Count", "By severity"],
            [
                [
                    f"<code>{_e(row['ruleId'])}</code>",
                    row["count"],
                    _e(", ".join(f"{k} {v}" for k, v in sorted(row["bySeverity"].items()))),
                ]
                for row in quality["findingsByRule"][:40]
            ],
            {1},
        )
        + "</section>"
    )
    delivery = report["delivery"]
    features = [
        [
            f"<code>{_e(item['taskId'])}</code> {_e(item['title'])}",
            _e(item["closedAt"][:10]),
            ", ".join(_link(issue["issue"], issue.get("url")) for issue in item["issues"]) or "-",
            _link(f"#{item['pullRequest']['number']}", item["pullRequest"].get("url"))
            if item.get("pullRequest") and item["pullRequest"].get("number") is not None
            else "-",
        ]
        for item in delivery["featuresDelivered"]
    ]
    sections.append(
        '<section aria-labelledby="h-delivery"><h2 id="h-delivery">Delivered</h2>'
        + _table(
            f"{len(delivery['featuresDelivered'])} feature(s) delivered, "
            f"{len(delivery['issuesResolved'])} issue(s) resolved",
            ["Task", "Closed", "Issues", "Pull request"],
            features,
            set(),
        )
        + "</section>"
    )
    narrative = report.get("narrative")
    if narrative:
        sections.append(
            '<section aria-labelledby="h-narrative"><h2 id="h-narrative">Narrative summary</h2>'
            f'<p class="muted">Generated on demand by <code>{_e(narrative["command"])}</code>; not '
            f"verified by the harness.</p><p>{_e(narrative['text']).replace(chr(10), '<br>')}</p>"
            "</section>"
        )
    notes = "".join(f"<li>{_e(note)}</li>" for note in report["notes"])
    projects = ", ".join(_e(item["projectId"]) for item in report["projects"]) or "none"
    card_html = "".join(
        f'<div class="card"><div class="value">{_num(value)}</div>'
        f'<div class="label">{_e(label)}</div></div>'
        for label, value in cards
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>Harness metrics</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<header>\n<h1>Harness metrics</h1>\n"
        f'<p class="lead">{totals["runs"]} run(s) of {projects}. Generated '
        f"{_e(report['generatedAt'])} by harness {_e(report['harnessVersion'])}; computed from "
        "the records only, no model was called. This file loads nothing from the network.</p>\n"
        f'</header>\n<main>\n<div class="cards">{card_html}</div>\n'
        + "\n".join(sections)
        + f'\n<section aria-labelledby="h-notes"><h2 id="h-notes">Notes</h2><ul>{notes}</ul></section>'
        "\n</main>\n</body>\n</html>\n"
    )


__all__ = ["render_html"]
