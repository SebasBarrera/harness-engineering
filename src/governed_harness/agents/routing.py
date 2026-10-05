"""Model and effort of each agent call (issue #44).

The policy is a cascade in the sense of FrugalGPT and AutoMix: a call starts on the cheapest
rung that the task size justifies and climbs a ladder only after a quality failure. The scorer
that decides whether to climb is not a model but the harness's own deterministic verifier
(VERIFICATION and REVIEW findings), so the decision stays reproducible. On the ladder effort is
raised before the model is changed, because a higher effort on the same model is usually the
cheaper fix. Planning calls (clarify, plan, review) have their own rung so the planner can be
stronger than the executor.

Everything here is pure: the same signals, history and policy always give the same decision,
and the decision carries the digest of the policy it came from so it can be audited later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from governed_harness.configuration.agent_results import (
    DEFAULT_ROUTING_TABLES,
    DEFAULT_SIZE_THRESHOLDS,
    AgentCallConfig,
    AgentRoutingConfig,
    CallKind,
    FamilyTable,
    ProviderFamily,
    Rung,
    SizeClass,
    SizeThresholds,
)
from governed_harness.evidence.hashing import sha256_json

__all__ = [
    "DEFAULT_MAX_ESCALATIONS",
    "CalibrationRow",
    "RoutingDecision",
    "RoutingHistory",
    "TaskSignals",
    "calibrate",
    "can_escalate",
    "classify_size",
    "flags_for",
    "provider_family",
    "select",
    "suggest_table",
]

DEFAULT_MAX_ESCALATIONS = 2
"""Escalation steps allowed per run when ``agentRouting.maxEscalations`` is not set."""

_SIZE_ORDER: dict[str, int] = {"S": 0, "M": 1, "L": 2}
_SIGNALS: tuple[str, ...] = ("requirements", "files", "loc")
_ESCALATING_KINDS: frozenset[str] = frozenset({"implement", "review"})


@dataclass(frozen=True)
class TaskSignals:
    """What is known about the size of a task before an agent touches it."""

    requirements: int
    files: int
    loc: int
    risk_flags: tuple[str, ...] = ()
    planned_large: bool = False


@dataclass(frozen=True)
class RoutingHistory:
    """Quality failures of this run that already led to an escalation."""

    escalations: int = 0


@dataclass(frozen=True)
class RoutingDecision:
    """The model and effort chosen for one call and why, recorded as evidence.

    ``escalations`` is the number of ladder steps requested for this call (0 when the call
    did not escalate); ``rung`` is the ladder index used, or ``None`` when no ladder applies.
    """

    call_kind: str
    mode: str
    family: str
    size: str | None
    rule: str
    model: str | None
    effort: str | None
    rung: int | None
    escalations: int
    policy_digest: str
    flags: tuple[str, ...]
    warning: str | None = None
    """Since #59: why the decision fell back (a call kind without its own routing entry)."""

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "callKind": self.call_kind,
            "mode": self.mode,
            "family": self.family,
            "size": self.size,
            "rule": self.rule,
            "model": self.model,
            "effort": self.effort,
            "rung": self.rung,
            "escalations": self.escalations,
            "policyDigest": self.policy_digest,
            "flags": list(self.flags),
        }
        if self.warning is not None:
            value["warning"] = self.warning
        return value


def _bounds(thresholds: SizeThresholds | None, name: str) -> tuple[int, int]:
    configured: tuple[int, int] | None = getattr(thresholds, name, None) if thresholds else None
    return configured if configured is not None else DEFAULT_SIZE_THRESHOLDS[name]


def classify_size(signals: TaskSignals, thresholds: SizeThresholds | None) -> tuple[SizeClass, str]:
    """Classify a task as S, M or L and say which signal decided.

    The largest class among the signals wins, so a task with few requirements but many files
    is still treated as large. A risk flag lifts S to M because a risky small change deserves
    the stronger rung; a plan that already judged the task large forces L.
    """
    size: SizeClass = "S"
    rule = "all signals within S"
    for name in _SIGNALS:
        small, medium = _bounds(thresholds, name)
        value: int = getattr(signals, name)
        if value <= small:
            continue
        current: SizeClass = "M" if value <= medium else "L"
        bound = small if current == "M" else medium
        if _SIZE_ORDER[current] > _SIZE_ORDER[size]:
            size = current
            rule = f"{name}={value}>{bound} -> {current}"
    if size == "S" and signals.risk_flags:
        size = "M"
        rule = f"risk:{','.join(signals.risk_flags)} -> M"
    if signals.planned_large and size != "L":
        size = "L"
        rule = "planned-large -> L"
    return size, rule


def _basename(element: str) -> str:
    return PureWindowsPath(PurePosixPath(element).name).name.lower()


def provider_family(
    provider_id: str,
    command: Sequence[str],
    families: Mapping[str, ProviderFamily] | None,
) -> ProviderFamily:
    """Tell which CLI family a provider belongs to, so the right flags are passed.

    An explicit ``agentRouting.families`` entry wins; otherwise the provider id and the
    executable names in its command are matched, and anything unknown is ``generic`` (which
    receives no model or effort flags at all).
    """
    if families and provider_id in families:
        return families[provider_id]
    names = [provider_id.lower(), *(_basename(element) for element in command)]
    if any("claude" in name for name in names):
        return "claude-code"
    if any("codex" in name for name in names):
        return "codex"
    return "generic"


def flags_for(family: ProviderFamily, model: str | None, effort: str | None) -> tuple[str, ...]:
    """The command-line flags that pass a model and an effort to a CLI of the family."""
    flags: list[str] = []
    if family == "claude-code":
        if model:
            flags += ["--model", model]
        if effort:
            flags += ["--effort", effort]
    elif family == "codex":
        if model:
            flags += ["-m", model]
        if effort:
            flags += ["-c", f'model_reasoning_effort="{effort}"']
    return tuple(flags)


def _max_escalations(policy: AgentRoutingConfig | None) -> int:
    if policy is None or policy.max_escalations is None:
        return DEFAULT_MAX_ESCALATIONS
    return policy.max_escalations


def _tiered(policy: AgentRoutingConfig | None) -> bool:
    return policy is not None and policy.mode == "tiered"


def can_escalate(history: RoutingHistory, policy: AgentRoutingConfig | None) -> bool:
    """Whether a further quality failure may still move the next call up the ladder."""
    return _tiered(policy) and history.escalations < _max_escalations(policy)


def _policy_digest(policy: AgentRoutingConfig | None, family: ProviderFamily) -> str:
    dumped = policy.model_dump(mode="json", by_alias=True) if policy is not None else {}
    return sha256_json({"policy": dumped, "family": family})


def _table(policy: AgentRoutingConfig, family: ProviderFamily) -> FamilyTable | None:
    if policy.tables and family in policy.tables:
        return policy.tables[family]
    default = DEFAULT_ROUTING_TABLES.get(family)
    return FamilyTable.model_validate(default) if default is not None else None


def _kind_rung(
    table: FamilyTable, call_kind: CallKind, size: SizeClass
) -> tuple[Rung | None, str | None]:
    """The rung of a call kind and, when the kind has no entry of its own and the implement
    rung of the size stands in for it (#59), the warning that says so."""
    implement = table.implement.get(size) if table.implement else None
    if call_kind == "implement":
        return implement, None
    rung: Rung | None
    if call_kind == "locate":
        # The cheapest adequate rung (#55): the family's own locate rung, else the bottom of
        # its escalation ladder.
        rung = table.locate or (table.ladder[0] if table.ladder else None)
    elif call_kind == "architecture":
        # One survey or advice per project (#56): its own rung, else the planning rung.
        rung = table.architecture or table.plan
    else:
        rung = getattr(table, call_kind, None)
    if rung is not None or implement is None:
        return rung, None
    return implement, (
        f"no routing entry for the {call_kind} call: the implement rung of size {size} is used"
    )


def _ladder_index(ladder: tuple[Rung, ...], rung: Rung, *, by_model: bool) -> int | None:
    for index, candidate in enumerate(ladder):
        if candidate.model == rung.model and candidate.effort == rung.effort:
            return index
    if by_model:
        for index, candidate in enumerate(ladder):
            if candidate.model == rung.model:
                return index
    return None


def select(
    call_kind: CallKind,
    signals: TaskSignals,
    history: RoutingHistory,
    policy: AgentRoutingConfig | None,
    *,
    family: ProviderFamily,
    override: AgentCallConfig | None = None,
) -> RoutingDecision:
    """Choose the model and effort of one agent call.

    An explicit per-call configuration wins over the tables. Without a ``tiered`` policy, or
    without a table for the family, the provider keeps its own model (``fixed``). Only
    ``implement`` and ``review`` escalate, because those are the calls whose output the
    deterministic verifier scores; the size is always classified so the record shows it.
    """
    size, _size_rule = classify_size(signals, policy.thresholds if policy else None)
    mode = policy.mode if policy is not None and policy.mode is not None else "fixed"
    digest = _policy_digest(policy, family)

    warning: str | None = None

    def decision(
        rule: str,
        model: str | None,
        effort: str | None,
        rung: int | None = None,
        escalations: int = 0,
    ) -> RoutingDecision:
        return RoutingDecision(
            call_kind=call_kind,
            mode=mode,
            family=family,
            size=size,
            rule=rule,
            model=model,
            effort=effort,
            rung=rung,
            escalations=escalations,
            policy_digest=digest,
            flags=flags_for(family, model, effort),
            warning=warning,
        )

    if override is not None and (override.model or override.effort):
        return decision("override", override.model, override.effort)
    if policy is None or not _tiered(policy):
        return decision("fixed", None, None)
    table = _table(policy, family)
    if table is None:
        return decision("fixed", None, None)
    base, warning = _kind_rung(table, call_kind, size)
    if base is None:
        return decision("fixed", None, None)
    if warning is not None:
        return decision(
            f"fallback:implement:{call_kind}:{size}",
            base.model,
            base.effort,
            _ladder_index(table.ladder, base, by_model=False) if table.ladder else None,
        )

    ladder = table.ladder or ()
    steps = min(history.escalations, _max_escalations(policy))
    if call_kind in _ESCALATING_KINDS and steps > 0 and ladder:
        start = _ladder_index(ladder, base, by_model=True) or 0
        index = min(start + steps, len(ladder) - 1)
        chosen = ladder[index]
        return decision(
            f"escalation:{steps}:{call_kind}:{size}", chosen.model, chosen.effort, index, steps
        )
    index_used = _ladder_index(ladder, base, by_model=False) if ladder else None
    return decision(f"tier:{call_kind}:{size}", base.model, base.effort, index_used)


# ----- Calibration (N14: cost per approved task) ------------------------------------------
@dataclass(frozen=True)
class CalibrationRow:
    """One agent call of a finished run, with its cost and whether the run was approved."""

    family: str
    call_kind: str
    size: str | None
    model: str | None
    effort: str | None
    cost_usd: float | None
    approved: bool
    run_id: str


_GroupKey = tuple[str, str, str | None, str | None, str | None]


def calibrate(rows: Sequence[CalibrationRow]) -> list[dict[str, Any]]:
    """Summarise cost per approved task for each family, call kind, size, model and effort.

    Cost per approved task (not cost per call) is the measure because a cheap rung that needs
    many retries or never gets approved is not cheap.
    """
    groups: dict[_GroupKey, list[CalibrationRow]] = {}
    for row in rows:
        key: _GroupKey = (row.family, row.call_kind, row.size, row.model, row.effort)
        groups.setdefault(key, []).append(row)

    summary: list[dict[str, Any]] = []
    for (family, call_kind, size, model, effort), members in groups.items():
        runs = {row.run_id for row in members}
        approved = {row.run_id for row in members if row.approved}
        known = [row.cost_usd for row in members if row.cost_usd is not None]
        cost = round(sum(known), 6) if known else None
        per_task = round(cost / len(approved), 6) if cost is not None and approved else None
        summary.append(
            {
                "family": family,
                "callKind": call_kind,
                "size": size,
                "model": model,
                "effort": effort,
                "runs": len(runs),
                "approvedRuns": len(approved),
                "costUsd": cost,
                "costPerApprovedTask": per_task,
                "approvalRate": round(len(approved) / len(runs), 6),
            }
        )
    summary.sort(
        key=lambda item: (
            item["family"],
            item["callKind"],
            item["size"] or "",
            item["costPerApprovedTask"] is None,
            item["costPerApprovedTask"] or 0.0,
            item["model"] or "",
            item["effort"] or "",
        )
    )
    return summary


def suggest_table(summary: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, str | None]]]:
    """Suggest the cheapest approved (model, effort) per family and implement size.

    Groups with fewer than two approved runs are ignored so a single lucky run does not set
    the table. The result is only a suggestion for the maintainer; nothing is applied.
    """
    best: dict[str, dict[str, dict[str, Any]]] = {}
    for item in summary:
        if item["callKind"] != "implement" or item["size"] not in _SIZE_ORDER:
            continue
        per_task = item["costPerApprovedTask"]
        if per_task is None or item["approvedRuns"] < 2:
            continue
        sizes = best.setdefault(item["family"], {})
        current = sizes.get(item["size"])
        candidate_key = (per_task, item["model"] or "", item["effort"] or "")
        if current is None or candidate_key < (
            current["costPerApprovedTask"],
            current["model"] or "",
            current["effort"] or "",
        ):
            sizes[item["size"]] = item
    return {
        family: {
            size: {"model": sizes[size]["model"], "effort": sizes[size]["effort"]}
            for size in sorted(sizes, key=lambda name: _SIZE_ORDER[name])
        }
        for family, sizes in sorted(best.items())
    }
