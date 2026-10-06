"""The formats of ``harness metrics``: JSON (``metrics.json``), a Markdown summary, CSV (one
row per run), the Prometheus text exposition format and one self-contained HTML file."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from typing import Any

from governed_harness.metrics.html import render_html

FORMATS = ("json", "md", "html", "csv", "prometheus")


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") if value else "0"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(_fmt(cell) for cell in row) + " |" for row in rows)
    return lines


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Harness metrics",
        "",
        f"Generated {report['generatedAt']} by harness {report['harnessVersion']} from "
        f"{totals['runs']} run(s) of {len(report['projects'])} project(s). "
        "Deterministic: no model was called.",
        "",
        "## Totals",
        "",
        *_table(
            ["Measure", "Value"],
            [
                ["Runs", totals["runs"]],
                ["Tasks", totals["tasks"]],
                ["Agent calls", totals["agentCalls"]],
                ["Input tokens", totals["tokens"]["input"]],
                ["Output tokens", totals["tokens"]["output"]],
                ["Cache tokens (in input)", totals["tokens"]["cache"]],
                ["Reported cost (USD)", totals["cost"]["reportedUsd"]],
                ["Estimated cost (USD, estimated)", totals["cost"]["estimatedUsd"]],
                ["Calls without a price", totals["cost"]["unpricedCalls"]],
                ["Lines added", totals["linesAdded"]],
                ["Lines removed", totals["linesRemoved"]],
                ["Human interactions", totals["humanInteractions"]],
                ["Approvals", totals["approvals"]],
                ["Human wait (minutes)", round(totals["humanWaitMs"] / 60000, 2)],
                ["Issues resolved", totals["issuesResolved"]],
                ["Features delivered", totals["featuresDelivered"]],
            ],
        ),
        "",
    ]
    for title, key in (
        ("Tokens by agent", "byAgent"),
        ("Tokens by model", "byModel"),
        ("Tokens by phase", "byPhase"),
        ("Tokens by task", "byTask"),
    ):
        rows = report["tokens"][key]
        lines += [f"## {title}", ""]
        if rows:
            lines += _table(
                ["Key", "Calls", "Input", "Output", "Cache", "Reported USD", "Estimated USD"],
                [
                    [
                        row["key"],
                        row["calls"],
                        row["input"],
                        row["output"],
                        row["cache"],
                        row["reportedCostUsd"],
                        row["estimatedCostUsd"],
                    ]
                    for row in rows
                ],
            )
        else:
            lines.append("No agent call.")
        lines.append("")
    agent, person = report["lines"]["agent"], report["lines"]["person"]
    lines += [
        "## Lines",
        "",
        *_table(
            ["Author", "Added", "Removed", "Files"],
            [
                ["Agent invocation", agent["added"], agent["removed"], agent["files"]],
                ["Person (out of band)", person["added"], person["removed"], person["files"]],
                [
                    "Not attributed (no provenance)",
                    report["lines"]["unattributed"]["added"],
                    report["lines"]["unattributed"]["removed"],
                    report["lines"]["unattributed"]["files"],
                ],
            ],
        ),
        "",
        "## Quality",
        "",
    ]
    quality = report["quality"]
    lines += _table(
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
    )
    lines += ["", "## Friction per task", ""]
    friction = report["friction"]["perTask"]
    if friction:
        lines += _table(
            [
                "Task",
                "Size",
                "Lane",
                "Runs",
                "Interactions",
                "Approvals",
                "Minutes",
                "Wait minutes",
                "Over target",
            ],
            [
                [
                    row["taskId"],
                    row["size"],
                    ", ".join(row["lanes"]) or "-",
                    row["runs"],
                    row["interactions"],
                    row["approvals"],
                    row["wallMinutes"],
                    row["humanWaitMinutes"],
                    ", ".join(row["overTarget"]) or "no",
                ]
                for row in friction
            ],
        )
    else:
        lines.append("No task.")
    delivery = report["delivery"]
    lines += ["", "## Delivered", ""]
    if delivery["featuresDelivered"]:
        lines += [
            f"- `{item['taskId']}` {item['title']} ({item['closedAt'][:10]})"
            + (
                f", issues {', '.join(issue['issue'] for issue in item['issues'])}"
                if item["issues"]
                else ""
            )
            for item in delivery["featuresDelivered"]
        ]
    else:
        lines.append("Nothing closed in the period.")
    lines += ["", "## Notes", "", *[f"- {note}" for note in report["notes"]], ""]
    narrative = report.get("narrative")
    if narrative:
        lines += [
            "## Narrative summary",
            "",
            f"Generated on demand by `{narrative['command']}`; not verified by the harness.",
            "",
            narrative["text"].strip(),
            "",
        ]
    return "\n".join(lines)


def render_csv(report: dict[str, Any]) -> str:
    buffer = io.StringIO()
    rows = report["runs"]
    columns = [
        "projectId",
        "runId",
        "taskId",
        "status",
        "phase",
        "size",
        "lane",
        "createdAt",
        "wallMs",
        "humanWaitMs",
        "agentCalls",
        "inputTokens",
        "outputTokens",
        "cacheTokens",
        "reportedCostUsd",
        "estimatedCostUsd",
        "interactions",
        "approvals",
        "corrections",
        "linesAdded",
        "linesRemoved",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column) for column in columns})
    return buffer.getvalue()


def _label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_prometheus(report: dict[str, Any]) -> str:
    lines: list[str] = []

    def metric(
        name: str, kind: str, help_text: str, samples: list[tuple[dict[str, Any], Any]]
    ) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            rendered = ",".join(f'{key}="{_label(item)}"' for key, item in labels.items())
            lines.append(f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}")

    totals = report["totals"]
    metric("harness_runs", "gauge", "Runs in the report.", [({}, totals["runs"])])
    metric("harness_tasks", "gauge", "Tasks in the report.", [({}, totals["tasks"])])
    metric(
        "harness_runs_by_status",
        "gauge",
        "Runs by status.",
        [
            ({"status": status}, sum(1 for row in report["runs"] if row["status"] == status))
            for status in sorted({row["status"] for row in report["runs"]})
        ],
    )
    metric(
        "harness_agent_calls",
        "gauge",
        "Agent calls by model.",
        [({"model": row["key"]}, row["calls"]) for row in report["tokens"]["byModel"]],
    )
    metric(
        "harness_tokens",
        "gauge",
        "Tokens by model and kind (cache is part of input).",
        [
            ({"model": row["key"], "kind": kind}, row[kind])
            for row in report["tokens"]["byModel"]
            for kind in ("input", "output", "reasoning", "cache")
        ],
    )
    metric(
        "harness_cost_usd",
        "gauge",
        "Cost in USD by model; source=estimated comes from the price table.",
        [
            ({"model": row["key"], "source": source}, row[key])
            for row in report["tokens"]["byModel"]
            for source, key in (("reported", "reportedCostUsd"), ("estimated", "estimatedCostUsd"))
        ],
    )
    metric(
        "harness_lines",
        "gauge",
        "Lines of the latest ChangeSet of each run by author kind.",
        [
            ({"author": author, "change": change}, report["lines"][author][change])
            for author in ("agent", "person", "unattributed")
            for change in ("added", "removed")
        ],
    )
    metric(
        "harness_human_interactions",
        "gauge",
        "Human interactions over the runs (no per-person breakdown).",
        [({}, totals["humanInteractions"])],
    )
    metric(
        "harness_human_wait_seconds",
        "gauge",
        "Time the runs waited for a person.",
        [({}, round(totals["humanWaitMs"] / 1000, 3))],
    )
    metric(
        "harness_tasks_over_friction_target",
        "gauge",
        "Tasks over their friction target by size.",
        [({"size": row["size"]}, row["overTarget"]) for row in report["friction"]["bySize"]],
    )
    return "\n".join(lines) + "\n"


RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "json": render_json,
    "md": render_markdown,
    "html": render_html,
    "csv": render_csv,
    "prometheus": render_prometheus,
}


def render(report: dict[str, Any], fmt: str) -> str:
    if fmt not in RENDERERS:
        raise ValueError(f"--format is one of {', '.join(FORMATS)}, got {fmt!r}")
    return RENDERERS[fmt](report)


__all__ = [
    "FORMATS",
    "render",
    "render_csv",
    "render_json",
    "render_markdown",
    "render_prometheus",
]
