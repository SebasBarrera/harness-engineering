"""The overall status of the period shown at the top of the HTML report.

The rule is explicit and deterministic: it reads only numbers the report already holds and
compares each with zero. It is an aid to read the report, never a quality certification: it
says nothing about the code, only that no run of the period shows one of the four signals
below (see ``docs/es/reporte-de-metricas.md``)."""

from __future__ import annotations

from typing import Any

STATUS_CLEAR = "CLEAR"
STATUS_ATTENTION = "ATTENTION"
STATUS_NO_DATA = "NO_DATA"

# (condition id, label, where the count comes from)
CONDITIONS: tuple[tuple[str, str], ...] = (
    ("blockedRuns", "Runs stopped (blocked outside a decision)"),
    ("rejectedRuns", "Runs rejected by a person"),
    ("openBlockingFindings", "HIGH or CRITICAL findings in runs that did not close"),
    ("openFailedGates", "Runs that did not close whose latest gate failed"),
)


def period_status(counts: dict[str, int], runs: int) -> dict[str, Any]:
    """``NO_DATA`` without runs; ``ATTENTION`` when any condition count is above zero;
    ``CLEAR`` otherwise. Each condition is reported with its count so the reader sees why."""
    conditions = [
        {"id": key, "label": label, "count": counts[key], "met": counts[key] == 0}
        for key, label in CONDITIONS
    ]
    if runs == 0:
        status = STATUS_NO_DATA
    elif all(item["met"] for item in conditions):
        status = STATUS_CLEAR
    else:
        status = STATUS_ATTENTION
    return {
        "status": status,
        "rule": (
            "ATTENTION when any condition count is above zero, CLEAR when all are zero, "
            "NO_DATA without runs. Derived from the records of the period only; not a "
            "quality certification."
        ),
        "conditions": conditions,
        "waitingDecision": counts.get("waitingDecision", 0),
    }


__all__ = ["CONDITIONS", "STATUS_ATTENTION", "STATUS_CLEAR", "STATUS_NO_DATA", "period_status"]
