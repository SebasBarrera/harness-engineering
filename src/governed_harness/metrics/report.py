"""``harness metrics``: the runs of one repository, or of every repository of the run registry,
aggregated into one report (#58, items 8 to 10). Deterministic: the report is computed from the
records and the event chains only, with zero model calls.

What it never contains: a person's name or any per-person indicator. Interactions, approvals
and human wait are counted per run, task and project (the thesis requirement RD-16 the
earlier ``scripts/metrics_report.py`` already kept); lines are attributed to an agent invocation
or to a person (out of band), never to whom."""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from governed_harness import __version__
from governed_harness.agents.routing import TaskSignals, classify_size
from governed_harness.configuration.friction import (
    DEFAULT_FRICTION_TARGETS,
    FrictionTarget,
    ModelPrice,
)
from governed_harness.domain.enums import DecisionKind, ResultStatus
from governed_harness.domain.models import (
    AgentInvocation,
    ChangeSet,
    ComponentProvenance,
    Execution,
    Finding,
    HumanDecision,
    PhaseExecution,
    ResourceUsage,
    Task,
)
from governed_harness.events.sqlite_store import SQLiteEventStore, StoredEvent
from governed_harness.metrics.prices import call_cost
from governed_harness.storage.sqlite import SQLiteStateStore
from governed_harness.telemetry.metrics import HUMAN_INTERACTION_EVENTS, human_interactions

SCHEMA_VERSION = "1.0"
_ISSUE_TEXT = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE)
_APPROVALS = frozenset({DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION})
_RELATIVE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}
_PHASES = (
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


@dataclass
class ProjectSource:
    """The records of one project: its state and event stores and its settings."""

    project_id: str
    workspace: str
    state: SQLiteStateStore
    events: SQLiteEventStore
    targets: Mapping[Any, FrictionTarget] | None = None
    forge: dict[str, str] | None = None
    """``kind``, ``host`` and ``repository`` of the origin remote, for issue links."""


@dataclass(frozen=True)
class Filters:
    since: datetime | None = None
    task: str | None = None
    model: str | None = None
    agent: str | None = None
    all_repos: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since.isoformat() if self.since else None,
            "task": self.task,
            "model": self.model,
            "agent": self.agent,
            "allRepos": self.all_repos,
        }


def parse_since(value: str | None, now: datetime | None = None) -> datetime | None:
    """``YYYY-MM-DD``, an ISO 8601 instant, or a relative ``7d``/``2w``/``12h``."""
    if not value:
        return None
    text = value.strip()
    # A plain parse instead of a regular expression: linear on any input (``--since`` comes
    # from the command line). ``isdecimal`` accepts exactly what ``\d`` accepted.
    unit = _RELATIVE_UNITS.get(text[-1:])
    if unit is not None and text[:-1].isdecimal():
        return (now or datetime.now(UTC)) - timedelta(**{unit: int(text[:-1])})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError as error:
            raise ValueError(
                f"--since takes YYYY-MM-DD, an ISO 8601 instant or 12h, 7d, 2w: {value!r}"
            ) from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class _Call:
    invocation: AgentInvocation
    usage: dict[str, Any] | None
    reported: float | None
    estimated: float | None
    cost_source: str

    @property
    def tokens(self) -> dict[str, int]:
        usage = self.usage or {}
        return {
            name: int(usage.get(field) or 0)
            for name, field in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"),
                ("cache", "cache_tokens"),
            )
        }

    @property
    def duration_ms(self) -> int:
        delta = self.invocation.finished_at - self.invocation.started_at
        return max(0, int(delta.total_seconds() * 1000))


@dataclass
class _Run:
    project_id: str
    execution: Execution
    task: Task | None
    events: list[StoredEvent]
    calls: list[_Call]
    phases: list[PhaseExecution]
    decisions: list[HumanDecision]
    change_set: ChangeSet | None
    provenance: ComponentProvenance | None
    findings: list[Finding]
    lane: dict[str, Any] | None
    size: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    pull_request: dict[str, Any] | None = None

    @property
    def run_id(self) -> str:
        return self.execution.execution_id

    def times(self) -> tuple[datetime, datetime]:
        stamps = [datetime.fromisoformat(event.occurred_at) for event in self.events]
        if not stamps:
            return self.execution.created_at, self.execution.updated_at
        return min(stamps), max(stamps)


def _flag_json(state: SQLiteStateStore, key: str) -> Any:
    raw = state.get_flag(key)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _size(state: SQLiteStateStore, execution: Execution, task: Task | None) -> str:
    """The lane's size when the run recorded one, else the router's classification of the
    task's requirements and owned paths (lines are not read: the workspace may be elsewhere)."""
    lane = _flag_json(state, f"lane:{execution.execution_id}")
    if isinstance(lane, dict) and lane.get("size") in {"S", "M", "L"}:
        return str(lane["size"])
    if task is None:
        return "S"
    owned = task.metadata.get("ownedPaths") or [patch.path for patch in task.implementation.patches]
    signals = TaskSignals(
        requirements=len(task.requirements) or len(task.acceptance_criteria),
        files=len(list(owned)),
        loc=0,
    )
    return classify_size(signals, None)[0]


def _issue_link(forge: Mapping[str, str] | None, number: str) -> str | None:
    if not forge or not forge.get("host") or not forge.get("repository"):
        return None
    base = f"https://{forge['host']}/{forge['repository']}"
    kind = forge.get("kind")
    if kind == "gitlab":
        return f"{base}/-/issues/{number}"
    if kind == "azure-devops":
        parts = forge["repository"].split("/")
        return (
            f"https://{forge['host']}/{parts[0]}/{parts[1]}/_workitems/edit/{number}"
            if len(parts) >= 2
            else None
        )
    return f"{base}/issues/{number}"


def _pull_link(forge: Mapping[str, str] | None, number: Any) -> str | None:
    if not forge or not isinstance(number, int):
        return None
    base = f"https://{forge.get('host')}/{forge.get('repository')}"
    kind = forge.get("kind")
    if kind == "gitlab":
        return f"{base}/-/merge_requests/{number}"
    if kind == "bitbucket":
        return f"{base}/pull-requests/{number}"
    if kind == "azure-devops":
        return None
    return f"{base}/pull/{number}"


def task_issues(task: Task) -> list[str]:
    """Issue numbers a task names: ``metadata.issues`` / ``metadata.issue`` and closing
    keywords (``Closes #12``) in its title and intent."""
    found: list[str] = []
    for key in ("issues", "issue"):
        value = task.metadata.get(key)
        values = value if isinstance(value, list) else [value] if value is not None else []
        for item in values:
            text = str(item).strip().lstrip("#")
            if text.isdigit():
                found.append(text)
    for text in (task.title, task.intent):
        found.extend(_ISSUE_TEXT.findall(text))
    return list(dict.fromkeys(found))


def collect(sources: Iterable[ProjectSource], filters: Filters) -> list[_Run]:
    runs: list[_Run] = []
    for source in sources:
        state = source.state
        executions = state.list("execution", Execution, project_id=source.project_id)
        for execution in executions:
            if filters.task and execution.task_id != filters.task:
                continue
            if filters.since and execution.created_at < filters.since:
                continue
            try:
                task: Task | None = state.get("task", execution.task_id, Task)
            except Exception:  # noqa: BLE001 - a missing task still counts the run
                task = None
            run_id = execution.execution_id
            invocations = state.list("agent_invocation", AgentInvocation, execution_id=run_id)
            usages = {
                item.invocation_id: item.model_dump(mode="json")
                for item in state.list("resource_usage", ResourceUsage, execution_id=run_id)
                if item.invocation_id
            }
            calls: list[_Call] = []
            for invocation in invocations:
                if filters.model and invocation.model != filters.model:
                    continue
                if filters.agent and invocation.provider != filters.agent:
                    continue
                usage = usages.get(invocation.invocation_id)
                calls.append(_Call(invocation, usage, None, None, "none"))
            if (filters.model or filters.agent) and not calls:
                continue
            changes = state.list("change_set", ChangeSet, execution_id=run_id)
            current = [item for item in changes if item.digest == execution.change_set_digest]
            ordered = current or sorted(changes, key=lambda item: item.created_at)
            change_set: ChangeSet | None = ordered[-1] if ordered else None
            provenances = [
                item
                for item in state.list(
                    "component_provenance", ComponentProvenance, execution_id=run_id
                )
                if change_set is not None and item.change_set_digest == change_set.digest
            ]
            events = source.events.list(run_id)
            pull: dict[str, Any] | None = None
            for event in events:
                if event.event_type == "delivery.pull-request.created":
                    number = event.payload.get("number")
                    pull = {
                        "number": number,
                        "forge": event.payload.get("forge"),
                        "repository": event.payload.get("repository"),
                        "url": event.payload.get("url")
                        or _pull_link(
                            {
                                "kind": str(event.payload.get("forge") or ""),
                                "host": str(event.payload.get("host") or ""),
                                "repository": str(event.payload.get("repository") or ""),
                            }
                            if event.payload.get("host")
                            else source.forge,
                            number,
                        ),
                    }
            lane = _flag_json(state, f"lane:{run_id}")
            run = _Run(
                project_id=source.project_id,
                execution=execution,
                task=task,
                events=events,
                calls=calls,
                phases=state.list("phase", PhaseExecution, execution_id=run_id),
                decisions=sorted(
                    state.list("decision", HumanDecision, execution_id=run_id),
                    key=lambda item: item.decided_at,
                ),
                change_set=change_set,
                provenance=provenances[-1] if provenances else None,
                findings=state.list("finding", Finding, execution_id=run_id),
                lane=lane if isinstance(lane, dict) else None,
                size=_size(state, execution, task),
                pull_request=pull,
            )
            if task is not None:
                run.issues = [
                    {"issue": f"#{number}", "url": _issue_link(source.forge, number)}
                    for number in task_issues(task)
                ]
            runs.append(run)
    return sorted(runs, key=lambda item: item.execution.created_at)


# ----- aggregation ------------------------------------------------------------------------------
def _token_row(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "calls": 0,
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": 0,
        "reportedCostUsd": 0.0,
        "estimatedCostUsd": 0.0,
        "unpricedCalls": 0,
        "durationMs": 0,
    }


def _add_call(row: dict[str, Any], call: _Call) -> None:
    row["calls"] += 1
    for name, value in call.tokens.items():
        row[name] += value
    row["reportedCostUsd"] += call.reported or 0.0
    row["estimatedCostUsd"] += call.estimated or 0.0
    row["unpricedCalls"] += 1 if call.cost_source == "unpriced" else 0
    row["durationMs"] += call.duration_ms


def _finish_rows(rows: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    finished = []
    for row in rows.values():
        row["reportedCostUsd"] = round(row["reportedCostUsd"], 6)
        row["estimatedCostUsd"] = round(row["estimatedCostUsd"], 6)
        row["costUsd"] = round(row["reportedCostUsd"] + row["estimatedCostUsd"], 6)
        row["tokens"] = row["input"] + row["output"]
        finished.append(row)
    return sorted(finished, key=lambda item: (-item["tokens"], item["key"]))


def _human_wait_ms(events: list[StoredEvent]) -> int:
    """Time the run waited for a person: from each phase that ended BLOCKED to the next event
    a person caused or the next phase that started, whichever came first."""
    total = 0
    waiting_since: datetime | None = None
    for event in events:
        moment = datetime.fromisoformat(event.occurred_at)
        if (
            event.event_type == "phase.completed"
            and event.payload.get("status") == ResultStatus.BLOCKED.value
        ):
            waiting_since = waiting_since or moment
            continue
        if waiting_since is None:
            continue
        human = (
            event.event_type in HUMAN_INTERACTION_EVENTS and event.actor.get("actorType") == "HUMAN"
        )
        if human or event.event_type == "phase.started":
            total += max(0, int((moment - waiting_since).total_seconds() * 1000))
            waiting_since = None
    return total


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def build_report(
    runs: list[_Run],
    *,
    filters: Filters,
    prices: Mapping[str, ModelPrice],
    targets: Mapping[Any, FrictionTarget] | None,
    projects: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """The report of ``runs``: deterministic for the same records, filters, prices and
    ``now``."""
    generated = now or datetime.now(UTC)
    effective_targets = {
        size: FrictionTarget.model_validate(values)
        for size, values in DEFAULT_FRICTION_TARGETS.items()
    }
    targets_source = "default"
    if targets:
        effective_targets.update(targets)
        targets_source = "configured"
    for run in runs:
        for call in run.calls:
            priced = call_cost(call.usage, prices, call.invocation.provider, call.invocation.model)
            call.reported, call.estimated, call.cost_source = (
                priced.reported,
                priced.estimated,
                priced.source,
            )
    by: dict[str, dict[str, dict[str, Any]]] = {
        name: {} for name in ("agent", "model", "task", "phase", "callKind", "project")
    }
    totals_row = _token_row("total")
    models: dict[tuple[str, str], dict[str, Any]] = {}
    durations: list[int] = []
    for run in runs:
        for call in run.calls:
            invocation = call.invocation
            keys = {
                "agent": invocation.provider,
                "model": invocation.model or "unknown",
                "task": run.execution.task_id,
                "phase": invocation.phase_id.value,
                "callKind": invocation.call_kind or "implement",
                "project": run.project_id,
            }
            for name, value in keys.items():
                _add_call(by[name].setdefault(value, _token_row(value)), call)
            _add_call(totals_row, call)
            durations.append(call.duration_ms)
            model_entry = models.setdefault(
                (invocation.provider, invocation.model or "unknown"),
                {
                    "provider": invocation.provider,
                    "model": invocation.model or "unknown",
                    "calls": 0,
                    "costSources": set(),
                },
            )
            model_entry["calls"] += 1
            model_entry["costSources"].add(call.cost_source)
    lines: dict[str, Any] = {
        "added": 0,
        "removed": 0,
        "agent": {"added": 0, "removed": 0, "files": 0},
        "person": {"added": 0, "removed": 0, "files": 0},
        "unattributed": {"added": 0, "removed": 0, "files": 0},
    }
    issues: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    tasks: dict[str, dict[str, Any]] = {}
    phase_times: dict[str, list[int]] = defaultdict(list)
    findings_by_rule: dict[str, dict[str, Any]] = {}
    quality = {
        "runs": len(runs),
        "decided": 0,
        "approvedFirstTime": 0,
        "corrections": 0,
        "requestedChanges": 0,
        "blockedRuns": 0,
        "waitingDecision": 0,
        "rejectedRuns": 0,
        "closedRuns": 0,
        "preAuthorizedApprovals": 0,
    }
    run_rows: list[dict[str, Any]] = []
    daily: dict[str, dict[str, Any]] = {}
    weekly: dict[str, dict[str, Any]] = {}
    for run in runs:
        execution = run.execution
        started, finished = run.times()
        wall_ms = int((finished - started).total_seconds() * 1000)
        interactions = sum(human_interactions(run.events).values())
        approvals = sum(1 for item in run.decisions if item.decision in _APPROVALS)
        wait_ms = _human_wait_ms(run.events)
        corrections = sum(1 for event in run.events if event.event_type == "correction.authorized")
        requested = sum(
            1 for item in run.decisions if item.decision is DecisionKind.REQUEST_CHANGES
        )
        quality["corrections"] += corrections
        quality["requestedChanges"] += requested
        if run.decisions:
            quality["decided"] += 1
            first = run.decisions[0]
            before_first = [
                event
                for event in run.events
                if event.event_type == "correction.authorized"
                and datetime.fromisoformat(event.occurred_at) <= first.decided_at
            ]
            if first.decision in _APPROVALS and not before_first:
                quality["approvedFirstTime"] += 1
        quality["preAuthorizedApprovals"] += sum(
            1 for item in run.decisions if item.pre_authorization_id
        )
        if execution.status is ResultStatus.BLOCKED:
            if execution.current_phase.value == "DECISION":
                quality["waitingDecision"] += 1
            else:
                quality["blockedRuns"] += 1
        if any(item.decision is DecisionKind.REJECT for item in run.decisions):
            quality["rejectedRuns"] += 1
        closed = execution.status is ResultStatus.PASSED
        if closed:
            quality["closedRuns"] += 1
        for finding in run.findings:
            row = findings_by_rule.setdefault(
                finding.rule_id, {"ruleId": finding.rule_id, "count": 0, "bySeverity": {}}
            )
            row["count"] += 1
            severity = finding.severity.value
            row["bySeverity"][severity] = row["bySeverity"].get(severity, 0) + 1
        for phase in run.phases:
            if phase.started_at and phase.finished_at:
                phase_times[phase.phase_id.value].append(
                    int((phase.finished_at - phase.started_at).total_seconds() * 1000)
                )
        added = removed = 0
        if run.change_set is not None:
            sources = (
                {item.path: item.source for item in run.provenance.files} if run.provenance else {}
            )
            for changed in run.change_set.files:
                added += changed.additions
                removed += changed.deletions
                source = sources.get(changed.path)
                author = (
                    "agent"
                    if source == "AGENT"
                    else "person"
                    if source == "OUT_OF_BAND"
                    else "unattributed"
                )
                lines[author]["added"] += changed.additions
                lines[author]["removed"] += changed.deletions
                lines[author]["files"] += 1
        lines["added"] += added
        lines["removed"] += removed
        title = run.task.title if run.task else execution.task_id
        if closed:
            closed_at = finished.isoformat()
            features.append(
                {
                    "projectId": run.project_id,
                    "taskId": execution.task_id,
                    "title": title,
                    "runId": run.run_id,
                    "closedAt": closed_at,
                    "issues": run.issues,
                    "pullRequest": run.pull_request,
                }
            )
            for issue in run.issues:
                issues.append(
                    {
                        **issue,
                        "projectId": run.project_id,
                        "taskId": execution.task_id,
                        "runId": run.run_id,
                        "closedAt": closed_at,
                    }
                )
        tokens = sum(call.tokens["input"] + call.tokens["output"] for call in run.calls)
        cost = sum((call.reported or 0.0) + (call.estimated or 0.0) for call in run.calls)
        task_key = f"{run.project_id}:{execution.task_id}"
        entry = tasks.setdefault(
            task_key,
            {
                "projectId": run.project_id,
                "taskId": execution.task_id,
                "title": title,
                "size": run.size,
                "lanes": [],
                "runs": 0,
                "interactions": 0,
                "approvals": 0,
                "humanWaitMs": 0,
                "agentMs": 0,
                "tokens": 0,
                "costUsd": 0.0,
                "start": started,
                "end": finished,
                "phasesMs": {},
            },
        )
        entry["runs"] += 1
        entry["interactions"] += interactions
        entry["approvals"] += approvals
        entry["humanWaitMs"] += wait_ms
        entry["agentMs"] += sum(call.duration_ms for call in run.calls)
        entry["tokens"] += tokens
        entry["costUsd"] += cost
        entry["start"] = min(entry["start"], started)
        entry["end"] = max(entry["end"], finished)
        if run.lane and run.lane.get("lane"):
            entry["lanes"].append(
                "fast→full" if run.lane.get("escalated") else str(run.lane["lane"])
            )
        for phase in run.phases:
            if phase.started_at and phase.finished_at:
                phase_key = phase.phase_id.value
                entry["phasesMs"][phase_key] = entry["phasesMs"].get(phase_key, 0) + int(
                    (phase.finished_at - phase.started_at).total_seconds() * 1000
                )
        run_rows.append(
            {
                "projectId": run.project_id,
                "runId": run.run_id,
                "taskId": execution.task_id,
                "status": execution.status.value,
                "phase": execution.current_phase.value,
                "size": run.size,
                "lane": (
                    ("fast→full" if run.lane.get("escalated") else run.lane.get("lane"))
                    if run.lane
                    else None
                ),
                "createdAt": execution.created_at.isoformat(),
                "wallMs": wall_ms,
                "humanWaitMs": wait_ms,
                "agentCalls": len(run.calls),
                "inputTokens": sum(call.tokens["input"] for call in run.calls),
                "outputTokens": sum(call.tokens["output"] for call in run.calls),
                "cacheTokens": sum(call.tokens["cache"] for call in run.calls),
                "reportedCostUsd": round(sum(call.reported or 0.0 for call in run.calls), 6),
                "estimatedCostUsd": round(sum(call.estimated or 0.0 for call in run.calls), 6),
                "interactions": interactions,
                "approvals": approvals,
                "corrections": corrections,
                "linesAdded": added,
                "linesRemoved": removed,
            }
        )
        day = execution.created_at.astimezone(UTC).date()
        iso = day.isocalendar()
        for bucket_map, period in (
            (daily, day.isoformat()),
            (weekly, f"{iso.year}-W{iso.week:02d}"),
        ):
            bucket = bucket_map.setdefault(
                period,
                {
                    "period": period,
                    "runs": 0,
                    "tokens": 0,
                    "costUsd": 0.0,
                    "interactions": 0,
                    "approved": 0,
                    "closed": 0,
                },
            )
            bucket["runs"] += 1
            bucket["tokens"] += tokens
            bucket["costUsd"] = round(bucket["costUsd"] + cost, 6)
            bucket["interactions"] += interactions
            bucket["approved"] += 1 if approvals else 0
            bucket["closed"] += 1 if closed else 0
    friction_rows = []
    by_size: dict[str, dict[str, Any]] = {}
    for entry in tasks.values():  # noqa: B007 - one row per task
        wall_minutes = round((entry["end"] - entry["start"]).total_seconds() / 60, 2)
        target = effective_targets.get(entry["size"])
        over: list[str] = []
        if target is not None:
            if target.interactions is not None and entry["interactions"] > target.interactions:
                over.append("interactions")
            if target.minutes is not None and wall_minutes > target.minutes:
                over.append("minutes")
        row = {
            "projectId": entry["projectId"],
            "taskId": entry["taskId"],
            "title": entry["title"],
            "size": entry["size"],
            "lanes": entry["lanes"],
            "runs": entry["runs"],
            "interactions": entry["interactions"],
            "approvals": entry["approvals"],
            "wallMinutes": wall_minutes,
            "humanWaitMinutes": round(entry["humanWaitMs"] / 60000, 2),
            "agentMinutes": round(entry["agentMs"] / 60000, 2),
            "tokens": entry["tokens"],
            "costUsd": round(entry["costUsd"], 6),
            "phasesMs": entry["phasesMs"],
            "target": target.model_dump(mode="json", by_alias=True) if target else None,
            "overTarget": over,
        }
        friction_rows.append(row)
        size_row = by_size.setdefault(
            entry["size"], {"size": entry["size"], "tasks": 0, "overTarget": 0, "_i": [], "_m": []}
        )
        size_row["tasks"] += 1
        size_row["overTarget"] += 1 if over else 0
        size_row["_i"].append(entry["interactions"])
        size_row["_m"].append(wall_minutes)
    for size_row in by_size.values():
        size_row["meanInteractions"] = round(statistics.fmean(size_row.pop("_i")), 2)
        size_row["meanWallMinutes"] = round(statistics.fmean(size_row.pop("_m")), 2)
    model_rows = [
        {
            "provider": entry["provider"],
            "model": entry["model"],
            "calls": entry["calls"],
            "cost": "reported"
            if entry["costSources"] == {"reported"}
            else "estimated"
            if "estimated" in entry["costSources"]
            else "unpriced"
            if "unpriced" in entry["costSources"]
            else "none",
        }
        for entry in sorted(models.values(), key=lambda item: (-item["calls"], item["model"]))
    ]
    totals = _finish_rows({"total": totals_row})[0]
    decided = quality["decided"]
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated.isoformat(),
        "harnessVersion": __version__,
        "filters": filters.as_dict(),
        "projects": projects,
        "notes": [
            "Computed from the run records and event chains only; no model was called.",
            (
                "Costs: reportedCostUsd is what providers reported; estimatedCostUsd is computed "
                "from the price table for calls that reported tokens without a cost (estimated)."
            ),
            (
                "Tokens: input includes cache tokens when the provider reports both; reasoning "
                "tokens are shown apart and not priced separately."
            ),
            (
                "No per-person indicator: interactions, approvals and waits are counted per run, "
                "task and project."
            ),
        ],
        "totals": {
            "runs": len(runs),
            "tasks": len(tasks),
            "agentCalls": totals["calls"],
            "tokens": {
                "input": totals["input"],
                "output": totals["output"],
                "reasoning": totals["reasoning"],
                "cache": totals["cache"],
                "total": totals["tokens"],
            },
            "cost": {
                "reportedUsd": totals["reportedCostUsd"],
                "estimatedUsd": totals["estimatedCostUsd"],
                "totalUsd": totals["costUsd"],
                "unpricedCalls": totals["unpricedCalls"],
            },
            "linesAdded": lines["added"],
            "linesRemoved": lines["removed"],
            "humanInteractions": sum(row["interactions"] for row in run_rows),
            "approvals": sum(row["approvals"] for row in run_rows),
            "humanWaitMs": sum(row["humanWaitMs"] for row in run_rows),
            "issuesResolved": len(issues),
            "featuresDelivered": len(features),
        },
        "tokens": {
            "byAgent": _finish_rows(by["agent"]),
            "byModel": _finish_rows(by["model"]),
            "byTask": _finish_rows(by["task"]),
            "byPhase": _finish_rows(by["phase"]),
            "byCallKind": _finish_rows(by["callKind"]),
            "byProject": _finish_rows(by["project"]),
        },
        "models": model_rows,
        "lines": lines,
        "delivery": {"issuesResolved": issues, "featuresDelivered": features},
        "time": {
            "perPhase": [
                {
                    "phase": phase,
                    "attempts": len(phase_times[phase]),
                    "totalMs": sum(phase_times[phase]),
                    "meanMs": int(statistics.fmean(phase_times[phase])),
                }
                for phase in _PHASES
                if phase_times.get(phase)
            ],
            "agentCalls": {
                "count": len(durations),
                "totalMs": sum(durations),
                "meanMs": int(statistics.fmean(durations)) if durations else None,
                "p50Ms": _percentile(durations, 0.5),
                "p90Ms": _percentile(durations, 0.9),
            },
            "humanWaitMs": sum(row["humanWaitMs"] for row in run_rows),
        },
        "quality": {
            **quality,
            "approvedFirstTimeRate": round(quality["approvedFirstTime"] / decided, 4)
            if decided
            else None,
            "findingsByRule": sorted(
                findings_by_rule.values(), key=lambda item: (-item["count"], item["ruleId"])
            ),
        },
        "friction": {
            "targets": {
                size: target.model_dump(mode="json", by_alias=True)
                for size, target in sorted(effective_targets.items())
            },
            "targetsSource": targets_source,
            "perTask": sorted(friction_rows, key=lambda item: (item["projectId"], item["taskId"])),
            "bySize": [by_size[size] for size in ("S", "M", "L") if size in by_size],
        },
        "trends": {
            "daily": [daily[key] for key in sorted(daily)],
            "weekly": [weekly[key] for key in sorted(weekly)],
        },
        "runs": run_rows,
    }
    return report


__all__ = [
    "SCHEMA_VERSION",
    "Filters",
    "ProjectSource",
    "build_report",
    "collect",
    "parse_since",
    "task_issues",
]
