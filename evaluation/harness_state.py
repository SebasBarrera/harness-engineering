"""Per-run measures read from the harness's own record (governed conditions of 2.0.0).

Everything here reads what the harness recorded; nothing is recomputed from the workspace (that is
``measure.py``, applied identically to every condition). The harness's numbers are a second source
(N04): the evaluation's own measure of the agent calls is the adapter's ``agent-calls/`` records.

* the state database: ``.harness/state.db`` of the workspace (1.0.0 layout, harness-core) or the
  run registry under ``HARNESS_STATE_DIR`` (``runtime.stateDir: auto``, what ``harness init``
  writes), which the evaluation points inside the run directory;
* ``trace_completeness``: the eight relations of the 0.9.0 evaluation, unchanged, and the
  relations 2.0.0 adds (kept apart, so the original count stays comparable);
* ``run_measures``: agent invocations by call kind and model, routing decisions, the ladder's
  certification per criterion, review findings by domain and reviewer, clarification questions,
  unsupported claims, denied or out-of-ChangeSet writes, fast-lane decisions, phase seconds and
  ``harness verify`` of the record chain.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

PHASES = [
    "INTENT",
    "DISCOVERY",
    "SPECIFICATION",
    "PLANNING",
    "IMPLEMENTATION",
    "VERIFICATION",
    "INDEPENDENT_REVIEW",
    "DECISION",
    "CLOSURE",
]
WRITE_RULES = (
    "sandbox.",
    "workspace.",
    "harness.owned-paths",
    "owned-paths",
    "excluded",
    "protected",
)
CLAIM_RULES = ("agent.unsupported-claim",)


def review_domain(rule_id: str) -> str:
    """The domain of a review finding: ``review.panel.DOMAIN.rule`` for the panel's reviewers,
    ``review-rule`` for the deterministic review rules (``review.todo``, ...)."""
    parts = rule_id.split(".")
    if rule_id.startswith("review.panel.") and len(parts) > 2:
        return parts[2]
    if rule_id.startswith("review.agent") or rule_id.startswith("review.single"):
        return "single-reviewer"
    return "review-rule" if rule_id.startswith("review.") else parts[0]


def state_db(workspace: Path, run_dir: Path) -> Path | None:
    """The state database of the run: the workspace's (1.0.0 layout) or the external registry."""
    local = workspace / ".harness" / "state.db"
    if local.exists():
        return local
    found = sorted((run_dir / "state").glob("*/state.db")) if (run_dir / "state").is_dir() else []
    return found[0] if found else None


class State:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def close(self) -> None:
        self.db.close()

    def records(self, kind: str, execution_id: str | None = None) -> list[dict[str, Any]]:
        if execution_id is None:
            rows = self.db.execute(
                "select payload_json from records where record_type = ? order by rowid", (kind,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "select payload_json from records where record_type = ? and (execution_id = ? or execution_id is null)"
                " order by rowid",
                (kind, execution_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def record_types(self) -> dict[str, int]:
        return dict(
            self.db.execute(
                "select record_type, count(*) from records group by record_type"
            ).fetchall()
        )

    def events(self, execution_id: str | None = None) -> list[dict[str, Any]]:
        query = "select event_type, occurred_at, payload_json, execution_id from events"
        args: tuple[Any, ...] = ()
        if execution_id is not None:
            query += " where execution_id = ?"
            args = (execution_id,)
        rows = self.db.execute(query + " order by global_sequence", args).fetchall()
        return [
            {"type": r[0], "at": r[1], "payload": json.loads(r[2]), "executionId": r[3]}
            for r in rows
        ]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds(item: dict[str, Any], start: str = "startedAt", end: str = "finishedAt") -> float:
    if item.get(start) and item.get(end):
        return (_instant(item[end]) - _instant(item[start])).total_seconds()
    return 0.0


def trace_completeness(
    state: State,
    status: dict[str, Any],
    verify_exit: int | None = None,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Required causal relations present in the record, by run progress.

    ``relations``/``required``/``present``/``requiredCount``: the eight relations of the 0.9.0
    evaluation (``run_eval.py`` of that version), computed the same way. ``relations2``: the relations
    2.0.0 adds, each required only when the run used the setting that creates it."""
    execution = status["execution"]
    run_id = execution["executionId"]

    def records(kind: str) -> list[dict[str, Any]]:
        return state.records(kind, run_id)

    digest = execution.get("changeSetDigest")
    phases = {phase["phaseId"] for phase in status["phases"]}
    gates = [g for g in records("gate") if g.get("changeSetDigest") == digest]
    decisions = [d for d in records("decision") if d.get("changeSetDigest") == digest]
    relations = {
        "task": any(t.get("taskId") == execution["taskId"] for t in records("task")),
        "plan": any(p.get("taskId") == execution["taskId"] for p in records("plan")),
        "agentInvocation": any(
            a.get("model") and a.get("promptDigest") for a in records("agent_invocation")
        ),
        "changeSet": any(c.get("digest") == digest for c in records("change_set")),
        "validationsBound": any(
            v.get("changeSetDigest") == digest and v.get("mandatory") for v in records("validation")
        ),
        "gateBound": any(g.get("inputRefs") for g in gates),
        "decisionBound": any(
            d.get("gateEvaluationId") in {g["gateEvaluationId"] for g in gates} for d in decisions
        ),
        "eventChain": bool(status["eventChainValid"]),
    }
    required = ["task", "plan", "eventChain"]
    if "IMPLEMENTATION" in phases:
        required += ["agentInvocation", "changeSet"]
    if "VERIFICATION" in phases:
        required += ["validationsBound"]
    if "DECISION" in phases:
        required += ["gateBound", "decisionBound"]
    present = [name for name in required if relations[name]]

    # ----- the relations of 2.0.0 ------------------------------------------------------------
    events = state.events(run_id)
    types = Counter(e["type"] for e in events)
    clar_requests = records("clarification_request")
    clar_answers = records("clarification")
    invocations = records("agent_invocation")
    validations = [v for v in records("validation") if v.get("changeSetDigest") == digest]
    rel2: dict[str, bool] = {}
    req2: list[str] = []
    if clar_requests:
        req2.append("clarificationAnswered")
        rel2["clarificationAnswered"] = bool(clar_answers) and all(
            str((a.get("actor") or {}).get("actorType")) == "HUMAN" for a in clar_answers
        )
    if types.get("acceptance.tests.proposed"):
        req2.append("acceptanceDecided")
        rel2["acceptanceDecided"] = any(
            t.startswith("acceptance.tests.") and t != "acceptance.tests.proposed" for t in types
        )
    trace_validations = [v for v in validations if "traceab" in str(v.get("validatorId", ""))]
    if trace_validations:
        req2.append("requirementTraced")
        rel2["requirementTraced"] = all(v.get("status") == "PASSED" for v in trace_validations)
    certification = (brief or {}).get("certification")
    if certification:
        req2.append("certificationBound")
        rel2["certificationBound"] = certification.get("changeSetDigest") == digest
    review_calls = [i for i in invocations if i.get("callKind") == "review"]
    if review_calls and "INDEPENDENT_REVIEW" in phases:
        req2.append("reviewBound")
        rel2["reviewBound"] = any(
            "review" in str(v.get("validatorId", "")) and v.get("changeSetDigest") == digest
            for v in validations
        )
    tiered = [
        e
        for e in events
        if e["type"] == "agent.routing.decided" and e["payload"].get("mode") == "tiered"
    ]
    if tiered:
        # Every call the router decides is recorded; the review panel's reviewers take their model
        # from the reviewer table and are counted apart (reviewerCalls in run_measures).
        req2.append("routingRecorded")
        routed_kinds = Counter(str(e["payload"].get("callKind")) for e in tiered)
        called = Counter(
            str(i.get("callKind") or "implement")
            for i in invocations
            if i.get("callKind") != "review"
        )
        rel2["routingRecorded"] = all(routed_kinds[k] >= n for k, n in called.items())
    if verify_exit is not None:
        req2.append("recordVerified")
        rel2["recordVerified"] = verify_exit == 0
    return {
        "relations": relations,
        "required": required,
        "present": len(present),
        "requiredCount": len(required),
        "relations2": rel2,
        "required2": req2,
        "present2": sum(1 for name in req2 if rel2.get(name)),
        "requiredCount2": len(req2),
    }


def run_measures(
    state: State,
    status: dict[str, Any],
    brief: dict[str, Any] | None,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """The 2.0.0 measures of one run from its record (see the module docstring)."""
    run_id = status["execution"]["executionId"]
    invocations = state.records("agent_invocation", run_id)
    events = state.events(run_id)
    by_kind: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "seconds": 0.0, "models": Counter()}
    )
    for item in invocations:
        kind = item.get("callKind") or "implement"
        cell = by_kind[kind]
        cell["calls"] += 1
        cell["seconds"] += _seconds(item)
        cell["models"][str(item.get("model"))] += 1
        cell.setdefault("statuses", Counter())[str(item.get("status"))] += 1
    usages = state.records("resource_usage", run_id)
    usage_by_invocation = {u.get("invocationId"): u for u in usages}
    for item in invocations:
        usage = usage_by_invocation.get(item.get("invocationId")) or {}
        cell = by_kind[item.get("callKind") or "implement"]
        for key in ("inputTokens", "outputTokens", "cacheTokens", "costUsd"):
            if usage.get(key) is not None:
                cell[key] = round(cell.get(key, 0) + usage[key], 6)
    routing = [e["payload"] for e in events if e["type"] == "agent.routing.decided"]
    phases: dict[str, float] = defaultdict(float)
    attempts: Counter[str] = Counter()
    for phase in state.records("phase", run_id):
        phases[phase["phaseId"]] += _seconds(phase)
        attempts[phase["phaseId"]] += 1
    model_seconds_by_phase: dict[str, float] = defaultdict(float)
    for item in invocations:
        model_seconds_by_phase[str(item.get("phaseId"))] += _seconds(item)
    certification = (brief or {}).get("certification") or {}
    criteria = certification.get("criteria") or []
    rule_counts = Counter(f["ruleId"] for f in findings)
    review = [
        f
        for f in findings
        if str(f.get("validatorId", "")).startswith(("review", "harness.review"))
        or str(f.get("ruleId", "")).startswith("review.")
    ]
    panel_events = [e["payload"] for e in events if e["type"].startswith("review.panel")]
    lane = [
        {
            "type": e["type"],
            **{
                k: e["payload"].get(k)
                for k in ("lane", "size", "reasons", "reason")
                if k in e["payload"]
            },
        }
        for e in events
        if e["type"].startswith(("lane.", "friction."))
    ]
    return {
        "invocationsByKind": {
            kind: {
                **{k: v for k, v in cell.items() if k not in ("models", "statuses")},
                "seconds": round(cell["seconds"], 3),
                "models": dict(cell["models"]),
                "statuses": dict(cell.get("statuses", {})),
            }
            for kind, cell in sorted(by_kind.items())
        },
        "routingDecisions": [
            {
                k: r.get(k)
                for k in (
                    "callKind",
                    "mode",
                    "family",
                    "size",
                    "rule",
                    "model",
                    "effort",
                    "escalations",
                    "warning",
                )
            }
            for r in routing
        ],
        "phaseSeconds": {p: round(phases[p], 3) for p in PHASES if p in phases},
        "phaseAttempts": {p: attempts[p] for p in PHASES if p in attempts},
        "modelSecondsByPhase": {p: round(s, 3) for p, s in sorted(model_seconds_by_phase.items())},
        "certification": {
            "status": certification.get("status"),
            "criteria": [
                {k: c.get(k) for k in ("criterionId", "required", "achieved", "status")}
                for c in criteria
            ],
            "byStatus": dict(Counter(str(c.get("status")) for c in criteria)),
        }
        if certification
        else None,
        "preflight": (brief or {}).get("preflight"),
        "deferred": len((brief or {}).get("deferred") or []),
        "checklistItems": len((brief or {}).get("checklist") or []),
        "riskFactors": (brief or {}).get("riskFactors"),
        "reviewFindings": {
            "total": len(review),
            "byDomain": dict(Counter(review_domain(str(f["ruleId"])) for f in review)),
            "bySeverity": dict(Counter(f["severity"] for f in review)),
            "byValidator": dict(Counter(str(f.get("validatorId")) for f in review)),
        },
        "panel": panel_events,
        "unsupportedClaims": sum(rule_counts[r] for r in CLAIM_RULES),
        # Who decided and who answered: a human actor id only (agent.* is refused by the harness).
        "decisionActors": dict(
            Counter(
                str((d.get("actor") or {}).get("actorId"))
                for d in state.records("decision", run_id)
            )
        ),
        "clarificationActors": dict(
            Counter(
                str((c.get("actor") or {}).get("actorId"))
                for c in state.records("clarification", run_id)
            )
        ),
        "writeFindings": {
            r: n for r, n in sorted(rule_counts.items()) if any(k in r for k in WRITE_RULES)
        },
        "findingRules": dict(sorted(rule_counts.items())),
        "laneEvents": lane,
        "eventTypes": dict(sorted(Counter(e["type"] for e in events).items())),
        "quarantine": next(
            (
                {
                    k: e["payload"].get(k)
                    for k in ("reason", "paths", "patchRef", "unrestorablePaths")
                }
                for e in events
                if e["type"] == "workspace.quarantined"
            ),
            None,
        ),
    }


def collect(
    harness: Callable[..., Any],
    workspace: Path,
    run_dir: Path,
    run_id: str,
    status: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Everything the harness recorded about the run that the evaluation reports, plus the raw
    outputs kept in the run directory (``harness-metrics.json``, ``brief.json``)."""

    def as_json(proc: Any) -> Any:
        try:
            return json.loads(proc.stdout)
        except (ValueError, TypeError):
            return None

    brief = as_json(harness(workspace, "review", "--path", ".", "--run", run_id, "--json")) or {}
    (run_dir / "brief.json").write_text(json.dumps(brief, indent=1), encoding="utf-8")
    verify = harness(workspace, "verify", "--path", ".", "--run", run_id)
    (run_dir / "verify.txt").write_text(
        verify.stdout[-20000:] + verify.stderr[-4000:], encoding="utf-8"
    )
    metrics_file = run_dir / "harness-metrics.json"
    metrics = harness(
        workspace, "metrics", "--path", ".", "--format", "json", "--output", str(metrics_file)
    )
    path = state_db(workspace, run_dir)
    out: dict[str, Any] = {
        "verifyExit": verify.returncode,
        "metricsExit": metrics.returncode,
        "stateDb": "workspace" if path and path.parent.parent == workspace else "registry",
    }
    if path is None:
        out["error"] = "no state database"
        return out
    state = State(path)
    try:
        out["trace"] = trace_completeness(state, status, verify.returncode, brief)
        out["measures"] = run_measures(state, status, brief, findings)
        out["recordTypes"] = state.record_types()
        out["clarificationRequests"] = [
            {
                "questions": [
                    {k: q.get(k) for k in ("ruleId", "category", "target")}
                    for q in r.get("questions") or []
                ]
            }
            for r in state.records("clarification_request", run_id)
        ]
    finally:
        state.close()
    return out
