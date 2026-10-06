"""The review panel (#57): reviewers by domain over diff slices, with a deterministic verdict.

``run_panel`` is the whole review, independent of where it runs (INDEPENDENT_REVIEW of a
governed run, or ``harness review-code`` outside one). The caller gives it the diff, the catalog,
the reviewers and an ``Invoker`` that calls the providers; everything else is decided here, in
this order, so that no token is spent that a cheaper step could save:

1. the global cache: the same review of the same diff in the same mode is not repeated;
2. the project's consistency checks: when one fails, no model is called;
3. the deterministic rules (``verified_by: tool:...``): the harness's own checks and, outside a
   run, the linters the repository configures; they never reach a model;
4. the reviewers whose mode matches, whose slice changed and whose signal is present;
5. per reviewer: the per-reviewer cache, else one call with a budget proportional to the slice;
   an invalid answer is ``UNKNOWN`` and gets one retry with the alternate provider; a persistent
   ``UNKNOWN`` blocks;
6. the findings: outside the reportable locations dropped, outside the catalog downgraded
   without concrete evidence, capped, ordered; an optional second opinion on the blocking ones;
7. the verdict, recomputed: ``FAIL`` with any error or failed consistency check, ``UNKNOWN``
   with a reviewer that never answered, ``PASS_WARN`` with suggestions only, else ``PASS``.

The report is deterministic: the same inputs and answers give the same report and digest."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from governed_harness.agents.requests import REQUEST_SCHEMA_VERSION
from governed_harness.configuration.review import ReviewPanelConfig
from governed_harness.evidence.hashing import sha256_bytes, sha256_json
from governed_harness.review.cache import ReviewCache, global_key, reviewer_key
from governed_harness.review.checks import CHECKS, run_checks
from governed_harness.review.contract import (
    ContractError,
    ReviewFinding,
    Verdict,
    contract_text,
    normalize,
    verdict_of,
)
from governed_harness.review.diff import (
    FileChange,
    Locations,
    diff_hash,
    parse_diff,
    render,
    reportable_locations,
)
from governed_harness.review.reviewers import Reviewer, definitions_hash, drift
from governed_harness.review.rules import HARNESS_TOOL, Catalog, Rule
from governed_harness.review.signals import activation, slice_files

REPORT_SCHEMA_VERSION = "1.0"
MAX_SLICE_CHARS = 200_000
SECOND_OPINION_ID = "second-opinion"


# ----- what the caller provides -----------------------------------------------------------------
@dataclass(frozen=True)
class ReviewerCall:
    reviewer: str
    provider: str
    request: dict[str, Any]
    model: str | None
    effort: str | None
    timeout_seconds: int | None
    max_tokens: int
    attempt: int = 1


@dataclass(frozen=True)
class ReviewerAnswer:
    status: Literal["ANSWERED", "ERROR"]
    result: dict[str, Any] | None
    summary: str
    provider: str
    model: str | None = None
    """The model the call was configured with (``None``: the provider's default)."""
    tokens: int | None = None
    invocation_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class Invoker(Protocol):
    def invoke(self, calls: Sequence[ReviewerCall], workers: int) -> list[ReviewerAnswer]:
        """Answer every call (in parallel up to ``workers``), in the order of ``calls``."""


Route = Callable[[Reviewer, str], tuple[str | None, str | None]]
"""The model and effort of a reviewer on a provider."""


@dataclass
class PanelInputs:
    workspace: Path
    diff_text: str
    mode: str
    catalog: Catalog
    reviewers: list[Reviewer]
    settings: ReviewPanelConfig
    provider: str
    invoker: Invoker
    route: Route
    runner_version: str
    base: str | None = None
    head: str | None = None
    fallback: str | None = None
    forced_model: str | None = None
    skip: tuple[str, ...] = ()
    packs: tuple[str, ...] | None = None
    cache: ReviewCache | None = None
    consistency: Callable[[], list[dict[str, Any]]] | None = None
    linters: Callable[[list[Rule], Locations], list[ReviewFinding]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    mcp: dict[str, Any] = field(default_factory=dict)
    """The MCP configuration reviewers may use (servers of the allowlist only)."""
    untrusted_notice: str | None = None
    """Since #5: the notice that repository content is untrusted context, not instructions."""


# ----- the report -------------------------------------------------------------------------------
@dataclass
class ReviewerOutcome:
    reviewer: str
    domain: str
    status: str
    reason: str
    activation: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    changed_lines: int = 0
    slice_hash: str | None = None
    budget: int | None = None
    provider: str | None = None
    configured_model: str | None = None
    executed_model: str | None = None
    attempts: int = 0
    cache: str = "off"
    tokens: int = 0
    over_budget: bool = False
    dropped_outside: int = 0
    downgraded: int = 0
    truncated: int = 0
    summary: str = ""
    findings: list[ReviewFinding] = field(default_factory=list)
    invocation_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.reviewer,
            "domain": self.domain,
            "status": self.status,
            "reason": self.reason,
            "activation": self.activation,
            "files": self.files,
            "changedLines": self.changed_lines,
            "findings": len(self.findings),
        }
        if self.status not in {"SKIPPED"}:
            value.update(
                {
                    "sliceHash": self.slice_hash,
                    "budget": self.budget,
                    "provider": self.provider,
                    "model": {
                        "configured": self.configured_model,
                        "executed": self.executed_model,
                    },
                    "attempts": self.attempts,
                    "cache": self.cache,
                    "tokens": self.tokens,
                    "overBudget": self.over_budget,
                    "droppedOutside": self.dropped_outside,
                    "downgraded": self.downgraded,
                    "truncated": self.truncated,
                    "summary": self.summary,
                }
            )
        return value


@dataclass
class PanelReport:
    mode: str
    base: str | None
    head: str | None
    diff_hash: str
    provider: str
    fallback: str | None
    verdict: Verdict
    blocking: bool
    findings: list[ReviewFinding]
    reviewers: list[ReviewerOutcome]
    catalog: dict[str, Any]
    consistency: list[dict[str, Any]]
    deterministic: dict[str, Any]
    drift: list[str]
    locations: int
    second_opinion: dict[str, Any] | None = None
    cache: str = "off"
    reviewer_cache_hits: int = 0

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.reviewers) + int(
            (self.second_opinion or {}).get("tokens") or 0
        )

    @property
    def model_calls(self) -> int:
        calls = sum(item.attempts for item in self.reviewers if item.cache != "hit")
        return calls + (1 if self.second_opinion and self.second_opinion.get("status") else 0)

    def body(self) -> dict[str, Any]:
        """The deterministic part of the report (what its digest covers)."""
        return {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "mode": self.mode,
            "base": self.base,
            "head": self.head,
            "diffHash": self.diff_hash,
            "verdict": self.verdict,
            "findings": [item.as_dict() for item in self.findings],
            "reviewers": [{"id": item.reviewer, "status": item.status} for item in self.reviewers],
            "consistency": [
                {"id": item["id"], "status": item["status"]} for item in self.consistency
            ],
            "catalog": self.catalog.get("digest"),
        }

    @property
    def digest(self) -> str:
        return sha256_json(self.body())

    def as_dict(self) -> dict[str, Any]:
        errors = sum(1 for item in self.findings if item.blocking)
        return {
            **self.body(),
            "digest": self.digest,
            "provider": self.provider,
            "fallbackProvider": self.fallback,
            "blocking": self.blocking,
            "counts": {
                "findings": len(self.findings),
                "errors": errors,
                "suggestions": len(self.findings) - errors,
                "reportableLines": self.locations,
                "droppedOutside": sum(item.dropped_outside for item in self.reviewers),
                "downgraded": sum(item.downgraded for item in self.reviewers),
                "truncated": sum(item.truncated for item in self.reviewers),
            },
            "reviewers": [item.as_dict() for item in self.reviewers],
            "consistency": self.consistency,
            "deterministic": self.deterministic,
            "catalog": self.catalog,
            "drift": self.drift,
            "secondOpinion": self.second_opinion,
            "cache": {"global": self.cache, "reviewerHits": self.reviewer_cache_hits},
            "tokens": {
                "total": self.tokens,
                "byReviewer": {item.reviewer: item.tokens for item in self.reviewers},
                "modelCalls": self.model_calls,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PanelReport:
        reviewers = [_outcome_from_dict(item) for item in value.get("reviewers") or []]
        return cls(
            mode=str(value["mode"]),
            base=value.get("base"),
            head=value.get("head"),
            diff_hash=str(value["diffHash"]),
            provider=str(value.get("provider") or ""),
            fallback=value.get("fallbackProvider"),
            verdict=value["verdict"],
            blocking=bool(value.get("blocking")),
            findings=[ReviewFinding.from_dict(item) for item in value.get("findings") or []],
            reviewers=reviewers,
            catalog=dict(value.get("catalog") or {}),
            consistency=list(value.get("consistency") or []),
            deterministic=dict(value.get("deterministic") or {}),
            drift=list(value.get("drift") or []),
            locations=int((value.get("counts") or {}).get("reportableLines") or 0),
            second_opinion=value.get("secondOpinion"),
        )


def _outcome_from_dict(item: Mapping[str, Any]) -> ReviewerOutcome:
    """A reviewer outcome of a cached report (its findings are in the report's list)."""
    model = item.get("model") or {}
    return ReviewerOutcome(
        reviewer=str(item["id"]),
        domain=str(item.get("domain") or ""),
        status=str(item["status"]),
        reason=str(item.get("reason") or ""),
        activation=dict(item.get("activation") or {}),
        files=list(item.get("files") or []),
        changed_lines=int(item.get("changedLines") or 0),
        slice_hash=item.get("sliceHash"),
        budget=item.get("budget"),
        provider=item.get("provider"),
        configured_model=model.get("configured"),
        executed_model=model.get("executed"),
        attempts=int(item.get("attempts") or 0),
        cache=str(item.get("cache") or "off"),
        tokens=0,
        summary=str(item.get("summary") or ""),
    )


# ----- helpers ----------------------------------------------------------------------------------
def options_of(settings: ReviewPanelConfig) -> dict[str, Any]:
    """The settings that change a review's result (part of both cache keys)."""
    second = settings.second_opinion
    return {
        "maxFindings": settings.finding_limit,
        "budget": settings.effective_budget.model_dump(mode="json", by_alias=True),
        "runTools": bool(settings.run_tools),
        "secondOpinion": second.model_dump(mode="json", by_alias=True) if second else None,
        "consistency": [
            item.model_dump(mode="json", by_alias=True)
            for item in settings.consistency_checks or ()
        ],
        "policy": settings.mode or "enforce",
    }


def _rules_by_id(rules: Sequence[Rule]) -> dict[str, Rule]:
    return {rule.rule_id: rule for rule in rules}


def deterministic_findings(
    catalog: Catalog, files: list[FileChange], locations: Locations
) -> tuple[list[ReviewFinding], dict[str, Any]]:
    """The findings of the rules verified by the harness's own checks."""
    by_check: dict[str, list[Rule]] = {}
    unknown: list[str] = []
    for rule in catalog.tool_rules():
        for name in (name for tool, name in rule.tools if tool == HARNESS_TOOL):
            if name in CHECKS:
                by_check.setdefault(name, []).append(rule)
            else:
                unknown.append(f"{rule.rule_id}: tool:{HARNESS_TOOL}:{name}")
    findings = [
        ReviewFinding(
            reviewer=HARNESS_TOOL,
            file=hit.path,
            side=hit.side,
            line=hit.line,
            rule=rule.rule_id,
            severity="error" if rule.blocking else "suggestion",
            issue=hit.message,
            evidence=hit.evidence,
            priority=rule.priority,
            source=f"tool:{HARNESS_TOOL}:{hit.check}",
        )
        for hit in run_checks(by_check, files)
        for rule in by_check.get(hit.check, [])
        if rule.applies(hit.path) and locations.allows(hit.path, hit.side, hit.line)
    ]
    return findings, {
        "checks": sorted(by_check),
        "unknownChecks": sorted(unknown),
        "rules": sorted(rule.rule_id for rule in catalog.tool_rules()),
    }


def _dedupe(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    seen: set[tuple[str, str, int, str]] = set()
    kept: list[ReviewFinding] = []
    for item in sorted(findings, key=ReviewFinding.sort_key):
        if item.key in seen:
            continue
        seen.add(item.key)
        kept.append(item)
    return kept


@dataclass
class _Planned:
    reviewer: Reviewer
    outcome: ReviewerOutcome
    files: list[FileChange]
    locations: Locations
    request: dict[str, Any]
    key: str | None
    provider: str
    model: str | None
    effort: str | None
    rules: dict[str, Rule]


def _request(
    inputs: PanelInputs,
    reviewer: Reviewer,
    files: list[FileChange],
    locations: Locations,
    budget: int,
    model: str | None,
    effort: str | None,
    limit: int,
) -> dict[str, Any]:
    text = render(files)
    truncated = len(text) > MAX_SLICE_CHARS
    notice = f"\n\n{inputs.untrusted_notice}" if inputs.untrusted_notice else ""
    request: dict[str, Any] = {
        "schemaVersion": REQUEST_SCHEMA_VERSION,
        "kind": "review",
        "readOnly": True,
        "instructions": reviewer.body.rstrip("\n") + notice + "\n\n" + contract_text(limit),
        "workspace": str(inputs.workspace),
        "reviewer": {
            "id": reviewer.reviewer_id,
            "domain": reviewer.domain,
        },
        "outputContract": {"maxFindings": limit},
        "slice": {
            "files": [item.path for item in files],
            "changedLines": sum(item.changed_lines for item in files),
            "diff": text[:MAX_SLICE_CHARS],
            "truncated": truncated,
        },
        "reportableLocations": locations.as_ranges(),
        "budget": {"maxTokens": budget},
        "isolation": {
            "readOnly": True,
            "tools": list(reviewer.spec.tools),
            "mcpServers": sorted(
                set(reviewer.spec.mcp_servers) & set(inputs.mcp.get("mcpServers") or {})
            ),
        },
    }
    if model or effort:
        request["routing"] = {"model": model, "effort": effort}
    request.update(inputs.extra)
    return request


def _message_hash(request: Mapping[str, Any]) -> str:
    return sha256_json(
        {key: value for key, value in request.items() if key not in {"instructions", "workspace"}}
    )


def _plan(inputs: PanelInputs, files: list[FileChange], limit: int) -> list[_Planned]:
    settings = inputs.settings
    planned: list[_Planned] = []
    for reviewer in sorted(inputs.reviewers, key=lambda item: item.reviewer_id):
        outcome = ReviewerOutcome(reviewer.reviewer_id, reviewer.domain, "SKIPPED", "")
        if reviewer.reviewer_id in inputs.skip:
            outcome.reason = "skipped by --skip"
            planned.append(
                _Planned(reviewer, outcome, [], Locations({}), {}, None, "", None, None, {})
            )
            continue
        if inputs.mode not in reviewer.spec.modes:
            outcome.reason = f"does not run in {inputs.mode} mode"
            planned.append(
                _Planned(reviewer, outcome, [], Locations({}), {}, None, "", None, None, {})
            )
            continue
        selected = slice_files(files, reviewer.spec.diff_slice)
        active = activation(reviewer, selected, inputs.packs)
        outcome.activation = active.as_dict()
        outcome.files = [item.path for item in selected]
        outcome.changed_lines = sum(item.changed_lines for item in selected)
        rules = inputs.catalog.for_domain(reviewer.domain, ai_only=True)
        if not active.active:
            outcome.reason = active.reason
            planned.append(
                _Planned(reviewer, outcome, [], Locations({}), {}, None, "", None, None, {})
            )
            continue
        provider = reviewer.spec.provider or inputs.provider
        model, effort = inputs.route(reviewer, provider)
        if inputs.forced_model:
            model = inputs.forced_model
        budget = settings.effective_budget.for_lines(
            outcome.changed_lines, reviewer.spec.max_budget
        )
        locations = reportable_locations(selected)
        own_limit = min(limit, reviewer.spec.max_findings or limit)
        request = _request(inputs, reviewer, selected, locations, budget, model, effort, own_limit)
        outcome.status = "PENDING"
        outcome.reason = active.reason
        outcome.slice_hash = diff_hash(request["slice"]["diff"])
        outcome.budget = budget
        outcome.provider = provider
        outcome.configured_model = model
        key = None
        if inputs.cache is not None:
            key = reviewer_key(
                base=inputs.base,
                provider=provider,
                fallback=inputs.fallback,
                forced_model=inputs.forced_model,
                reviewer=reviewer.reviewer_id,
                model=model,
                slice_hash=outcome.slice_hash,
                prompt_hash=sha256_bytes(request["instructions"].encode("utf-8")),
                message_hash=_message_hash(request),
                definition_hash=reviewer.definition_hash,
                runner_version=inputs.runner_version,
                mcp_hash=sha256_json(inputs.mcp),
                options={**options_of(settings), "maxFindings": own_limit},
            )
        planned.append(
            _Planned(
                reviewer,
                outcome,
                selected,
                locations,
                request,
                key,
                provider,
                model,
                effort,
                _rules_by_id(rules),
            )
        )
    return planned


def _apply_answer(item: _Planned, answer: ReviewerAnswer, limit: int) -> bool:
    """Record an answer on the outcome; ``True`` when it followed the contract."""
    outcome = item.outcome
    outcome.attempts += 1
    outcome.tokens += answer.tokens or 0
    outcome.provider = answer.provider
    if answer.invocation_id:
        outcome.invocation_ids.append(answer.invocation_id)
    outcome.evidence_refs.extend(answer.evidence_refs)
    if answer.status != "ANSWERED" or answer.result is None:
        outcome.reason = f"no valid answer from {answer.provider}: {answer.summary}"
        return False
    changes = {change.path: change for change in item.files}
    try:
        normalized = normalize(
            item.reviewer.reviewer_id,
            answer.result,
            rules=item.rules,
            locations=item.locations,
            changes=changes,
            limit=min(limit, item.reviewer.spec.max_findings or limit),
        )
    except ContractError as error:
        outcome.reason = f"the answer of {answer.provider} does not follow the contract: {error}"
        return False
    outcome.findings = normalized.findings
    outcome.dropped_outside = normalized.dropped_outside
    outcome.downgraded = normalized.downgraded
    outcome.truncated = normalized.truncated
    outcome.summary = normalized.summary
    outcome.configured_model = answer.model or outcome.configured_model
    outcome.executed_model = normalized.model or outcome.configured_model
    outcome.status = verdict_of(normalized.findings)
    outcome.over_budget = outcome.budget is not None and outcome.tokens > outcome.budget
    return True


def _cached_value(outcome: ReviewerOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "summary": outcome.summary,
        "provider": outcome.provider,
        "configuredModel": outcome.configured_model,
        "executedModel": outcome.executed_model,
        "droppedOutside": outcome.dropped_outside,
        "downgraded": outcome.downgraded,
        "truncated": outcome.truncated,
        "attempts": outcome.attempts,
        "findings": [item.as_dict() for item in outcome.findings],
    }


def _from_cache(outcome: ReviewerOutcome, value: Mapping[str, Any]) -> None:
    outcome.status = str(value["status"])
    outcome.summary = str(value.get("summary") or "")
    outcome.provider = value.get("provider") or outcome.provider
    outcome.configured_model = value.get("configuredModel")
    outcome.executed_model = value.get("executedModel")
    outcome.dropped_outside = int(value.get("droppedOutside") or 0)
    outcome.downgraded = int(value.get("downgraded") or 0)
    outcome.truncated = int(value.get("truncated") or 0)
    outcome.attempts = int(value.get("attempts") or 1)
    outcome.findings = [ReviewFinding.from_dict(item) for item in value.get("findings") or []]
    outcome.cache = "hit"
    outcome.tokens = 0


def _call_of(inputs: PanelInputs, item: _Planned, provider: str, attempt: int) -> ReviewerCall:
    """The call of a planned reviewer on ``provider``; on another provider than planned (the
    fallback) the model and effort are routed again."""
    request = item.request
    model, effort = item.model, item.effort
    if provider != item.provider:
        model, effort = inputs.route(item.reviewer, provider)
        model = inputs.forced_model or model
        request = {key: value for key, value in request.items() if key != "routing"}
        if model or effort:
            request["routing"] = {"model": model, "effort": effort}
    return ReviewerCall(
        reviewer=item.reviewer.reviewer_id,
        provider=provider,
        request=request,
        model=model,
        effort=effort,
        timeout_seconds=item.reviewer.spec.timeout_seconds,
        max_tokens=item.outcome.budget or 1,
        attempt=attempt,
    )


def _uncached(cache: ReviewCache | None, pending: list[_Planned]) -> list[_Planned]:
    """The planned reviewers to call: a cached answer fills the others."""
    calls: list[_Planned] = []
    for item in pending:
        cached = cache.get(item.key) if cache is not None and item.key else None
        if cached is not None:
            _from_cache(item.outcome, cached)
            continue
        item.outcome.cache = "miss" if cache is not None else "off"
        calls.append(item)
    return calls


def _call_reviewers(inputs: PanelInputs, planned: list[_Planned], limit: int) -> None:
    pending = [item for item in planned if item.outcome.status == "PENDING"]
    if not pending:
        return
    cache = inputs.cache
    calls = _uncached(cache, pending)
    workers = inputs.settings.workers
    answers = inputs.invoker.invoke(
        [_call_of(inputs, item, item.provider, 1) for item in calls], workers
    )
    retry = [
        item
        for item, answer in zip(calls, answers, strict=True)
        if not _apply_answer(item, answer, limit)
    ]
    if retry:
        second = inputs.invoker.invoke(
            [_call_of(inputs, item, inputs.fallback or item.provider, 2) for item in retry],
            workers,
        )
        for item, answer in zip(retry, second, strict=True):
            if not _apply_answer(item, answer, limit):
                item.outcome.status = "UNKNOWN"
                item.outcome.findings = []
    if cache is None:
        return
    for item in calls:
        if item.key and item.outcome.status != "UNKNOWN":
            cache.put(item.key, _cached_value(item.outcome))


def _second_opinion(
    inputs: PanelInputs, findings: list[ReviewFinding], files: list[FileChange], limit: int
) -> tuple[list[ReviewFinding], dict[str, Any] | None]:
    config = inputs.settings.second_opinion
    blocking = [item for item in findings if item.blocking and item.source == "ai"]
    if config is None or config.mode != "blocking" or not blocking:
        return findings, None
    provider = config.provider or inputs.fallback or inputs.provider
    touched = sorted({item.file for item in blocking})
    selected = [item for item in files if item.path in touched]
    locations = reportable_locations(selected)
    request: dict[str, Any] = {
        "schemaVersion": REQUEST_SCHEMA_VERSION,
        "kind": "review",
        "readOnly": True,
        "instructions": (
            "You give a second opinion on blocking review findings. For each finding below, "
            "read the code it points to and keep it only if you confirm the defect; return the "
            "findings you confirm, unchanged, and drop the others.\n\n" + contract_text(limit)
        ),
        "workspace": str(inputs.workspace),
        "reviewer": {"id": SECOND_OPINION_ID, "domain": "review", "title": "Second opinion"},
        "outputContract": {"maxFindings": limit},
        "slice": {"files": touched, "diff": render(selected)[:MAX_SLICE_CHARS]},
        "reportableLocations": locations.as_ranges(),
        "findings": [item.as_dict() for item in blocking],
        "isolation": {"readOnly": True, "tools": ["Read", "Grep", "Glob"], "mcpServers": []},
        **inputs.extra,
    }
    answer = inputs.invoker.invoke(
        [
            ReviewerCall(
                reviewer=SECOND_OPINION_ID,
                provider=provider,
                request=request,
                model=inputs.forced_model,
                effort=None,
                timeout_seconds=None,
                max_tokens=inputs.settings.effective_budget.for_lines(
                    sum(item.changed_lines for item in selected)
                ),
            )
        ],
        1,
    )[0]
    record: dict[str, Any] = {"provider": provider, "tokens": answer.tokens or 0}
    confirmed: set[tuple[str, str, int, str]] = set()
    try:
        if answer.status != "ANSWERED" or answer.result is None:
            raise ContractError(answer.summary)
        normalized = normalize(
            SECOND_OPINION_ID,
            answer.result,
            rules={},
            locations=locations,
            changes={item.path: item for item in selected},
            limit=len(blocking) + limit,
        )
        confirmed = {(item.file, item.side, item.line, item.rule) for item in normalized.findings}
    except ContractError as error:
        record.update({"status": "UNKNOWN", "reason": str(error), "kept": len(blocking)})
        return findings, record
    kept: list[ReviewFinding] = []
    refuted = 0
    for item in findings:
        if item.blocking and item.source == "ai" and item.key not in confirmed:
            refuted += 1
            kept.append(
                replace(item, severity="suggestion", note="not confirmed by the second opinion")
            )
        else:
            kept.append(item)
    record.update({"status": "ANSWERED", "confirmed": len(blocking) - refuted, "refuted": refuted})
    return sorted(kept, key=ReviewFinding.sort_key), record


# ----- the panel --------------------------------------------------------------------------------
def _global_key(inputs: PanelInputs, hashed: str) -> str:
    """The cache key of a whole review."""
    return global_key(
        mode=inputs.mode,
        diff_hash=hashed,
        definitions_hash=sha256_json([definitions_hash(inputs.reviewers), inputs.catalog.digest]),
        runner_version=inputs.runner_version,
        skip=inputs.skip,
        forced_model=inputs.forced_model,
        provider=inputs.provider,
        fallback=inputs.fallback,
        options={**options_of(inputs.settings), "base": inputs.base, "head": inputs.head},
    )


def _deterministic(
    inputs: PanelInputs, files: list[FileChange], locations: Locations, report: PanelReport
) -> list[ReviewFinding]:
    """The findings of the harness's checks and of the repository's linters, counted in the
    report."""
    findings, report.deterministic = deterministic_findings(inputs.catalog, files, locations)
    if inputs.linters is not None:
        linted = inputs.linters(
            [
                rule
                for rule in inputs.catalog.tool_rules()
                if any(t != HARNESS_TOOL for t, _ in rule.tools)
            ],
            locations,
        )
        report.deterministic["linterFindings"] = len(linted)
        findings.extend(linted)
    report.deterministic["findings"] = len(findings)
    return findings


def _verdict(
    failed: bool, reviewers: list[ReviewerOutcome], findings: list[ReviewFinding]
) -> Verdict:
    """FAIL when a consistency check failed, UNKNOWN when a reviewer never answered, else the
    verdict of the findings."""
    if failed:
        return "FAIL"
    if any(item.status == "UNKNOWN" for item in reviewers):
        return "UNKNOWN"
    return verdict_of(findings)


def run_panel(inputs: PanelInputs) -> PanelReport:
    settings = inputs.settings
    limit = settings.finding_limit
    files = parse_diff(inputs.diff_text)
    locations = reportable_locations(files)
    hashed = diff_hash(inputs.diff_text)
    drifted = drift(inputs.reviewers, inputs.catalog)
    key = _global_key(inputs, hashed) if inputs.cache is not None else None
    cached = inputs.cache.get(key) if inputs.cache is not None and key is not None else None
    if cached is not None:
        report = PanelReport.from_dict(cached)
        report.cache = "hit"
        return report
    report = PanelReport(
        mode=inputs.mode,
        base=inputs.base,
        head=inputs.head,
        diff_hash=hashed,
        provider=inputs.provider,
        fallback=inputs.fallback,
        verdict="PASS",
        blocking=False,
        findings=[],
        reviewers=[],
        catalog=inputs.catalog.summary(),
        consistency=[],
        deterministic={},
        drift=drifted,
        locations=locations.count(),
        cache="miss" if inputs.cache is not None else "off",
    )
    # 2. consistency checks: when one fails no model is called.
    if inputs.consistency is not None:
        report.consistency = inputs.consistency()
    failed = [item for item in report.consistency if item.get("status") != "PASSED"]
    # 3. deterministic rules.
    findings = _deterministic(inputs, files, locations, report)
    if failed:
        report.reviewers = [
            ReviewerOutcome(
                reviewer.reviewer_id,
                reviewer.domain,
                "SKIPPED",
                "a consistency check failed: no model was called",
            )
            for reviewer in sorted(inputs.reviewers, key=lambda item: item.reviewer_id)
        ]
    else:
        # 4-5. reviewers.
        planned = _plan(inputs, files, limit)
        _call_reviewers(inputs, planned, limit)
        report.reviewers = [item.outcome for item in planned]
        report.reviewer_cache_hits = sum(1 for item in report.reviewers if item.cache == "hit")
        findings.extend(finding for outcome in report.reviewers for finding in outcome.findings)
    # 6. findings.
    merged = _dedupe(findings)
    merged, report.second_opinion = _second_opinion(inputs, merged, files, limit)
    if len(merged) > limit:
        report.deterministic["truncated"] = len(merged) - limit
        merged = merged[:limit]
    report.findings = merged
    # 7. verdict.
    report.verdict = _verdict(bool(failed), report.reviewers, merged)
    report.blocking = report.verdict in {"FAIL", "UNKNOWN"} and settings.mode != "warn"
    if inputs.cache is not None and key is not None and report.verdict != "UNKNOWN":
        inputs.cache.put(key, report.as_dict())
    return report


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "Invoker",
    "PanelInputs",
    "PanelReport",
    "ReviewerAnswer",
    "ReviewerCall",
    "ReviewerOutcome",
    "Route",
    "deterministic_findings",
    "options_of",
    "run_panel",
]
