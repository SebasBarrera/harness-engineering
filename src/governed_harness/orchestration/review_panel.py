"""The review panel in INDEPENDENT_REVIEW (#57), replacing the single reviewer of #38.

Under ``review.panel`` the second review of a run is the panel of ``governed_harness.review``:
the same cost rule as before (no reviewer while a blocking deterministic validator fails, and
again only when the ChangeSet digest changed), then the consistency checks, the deterministic
rules and the reviewers by domain over their slices, in parallel and read-only. The rules a
linter verifies are not run again here: the validators of VERIFICATION already ran them.

Every request, answer, sandbox and the report are evidence of the run; the findings are
recorded under ``review.agent`` so the gate, the brief and the corrections treat them as the
single reviewer's. Errors are ``HIGH`` under ``enforce`` (they block the gate) and ``MEDIUM``
under ``warn``; suggestions are ``LOW``; a reviewer that never answered (``UNKNOWN``) blocks.

Scoped auto-fix (``review.panel.autoFix: scoped``): only errors on lines the agent wrote in this
run go back to IMPLEMENTATION: an added line of a file whose provenance is the agent's
(``provenance.agentSnapshots``), never a line of a file a person also edited, never a removed
line and never a suggestion; at most ``maxAttempts`` times, each one recorded. Other errors stay
for the person who decides."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from governed_harness import __version__
from governed_harness.agents import AgentCallResult
from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.review import ReviewPanelConfig
from governed_harness.domain.enums import (
    ActorType,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.models import (
    Actor,
    ChangeSet,
    ComponentProvenance,
    Execution,
    Finding,
    PhaseExecution,
)
from governed_harness.orchestration.engine_types import READ_ONLY_RULE, ReviewOutcome
from governed_harness.review.cache import ReviewCache
from governed_harness.review.contract import ReviewFinding
from governed_harness.review.invoke import BuiltProvider, ProviderInvoker
from governed_harness.review.panel import PanelInputs, PanelReport, ReviewerCall, run_panel
from governed_harness.review.project import (
    consistency_runner,
    panel_context,
    review_setup,
    reviewer_route,
)
from governed_harness.review.providers import build_reviewer_provider, mcp_digest, mcp_servers
from governed_harness.runtime.workspace import WorkspaceDiff

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

AUTOFIX_FLAG = "reviewfix"
PANEL_RULE_PREFIX = "review.panel"


class _SerializedObserver:
    """The process ledger of a run, written by one thread at a time (reviewers run in
    parallel)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.lock = threading.Lock()

    def started(self, pid: int, argv: tuple[str, ...]) -> None:
        with self.lock:
            self.inner.started(pid, argv)

    def finished(self, pid: int) -> None:
        with self.lock:
            self.inner.finished(pid)


def compact_task(task: Any) -> dict[str, Any]:
    """What a reviewer needs of the task: the intent, the requirement and criterion texts and
    the constraints (not the whole task document)."""
    return {
        "intent": task.intent,
        "requirements": [item.text for item in task.requirements],
        "acceptanceCriteria": [item.text for item in task.acceptance_criteria],
        "constraints": list(task.constraints),
    }


class PanelReview:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def settings(self) -> ReviewPanelConfig | None:
        review = self.results.project.review
        return review.panel if review is not None else None

    @property
    def configured(self) -> bool:
        settings = self.settings
        return settings is not None and settings.enabled

    # ----- the invoker -----------------------------------------------------------------------
    def _invoker(
        self, execution: Execution, phase: PhaseExecution, servers: dict[str, Any]
    ) -> ProviderInvoker:
        results = self.results
        engine = results.engine
        resolved = results.s.resolved
        workspace = results.s.paths.workspace
        runtime = resolved.project.runtime
        runner = engine._runner(execution)
        if runner.observer is not None:
            runner.observer = _SerializedObserver(runner.observer)

        def build(provider_id: str) -> BuiltProvider | str:
            built = build_reviewer_provider(
                resolved,
                provider_id,
                workspace=workspace,
                protected=engine._protected_paths(),
                host=engine.sandbox_host,
                allow_network=engine._agent_network_allowed(),
            )
            if isinstance(built, str):
                return built
            refs: tuple[str, ...] = ()
            if built.sandbox is not None:
                refs = (
                    results.record_json(
                        execution,
                        phase.phase_id,
                        {**built.sandbox.evidence(), "readOnlyWorkspace": True},
                        kind="review-sandbox",
                        summary=f"Read-only sandbox of the reviewers on {provider_id}",
                    ),
                )
            return BuiltProvider(built.built.provider, built.built.actor, refs)

        def before(calls: Sequence[ReviewerCall]) -> str | None:
            blocked = results.before_agent_call(execution, phase, "review")
            return blocked.summary if blocked is not None else None

        def record_request(call: ReviewerCall) -> str | None:
            return results.record_json(
                execution,
                phase.phase_id,
                call.request,
                kind="agent-review-request",
                summary=f"Review request of the {call.reviewer} reviewer to {call.provider}",
            )

        def after(call: ReviewerCall, result: AgentCallResult) -> None:
            engine._save_agent_result(execution, phase, result.execution)
            results.after_agent_call(execution, phase, result.execution)

        def on_violation(diff: WorkspaceDiff, restored: int, unrestorable: int) -> None:
            shown = ", ".join(item.path for item in diff.changes[:10])
            results.record_finding(
                execution,
                validator_id="harness.agent-calls",
                rule_id=READ_ONLY_RULE,
                category="agent-protocol",
                severity=FindingSeverity.HIGH,
                message=(
                    f"A reviewer of the review panel changed {len(diff.changes)} path(s): "
                    f"{shown}; restored {restored}, could not restore {unrestorable}"
                ),
                path=diff.changes[0].path,
                recommendation="Use a provider that honours readOnly requests.",
            )

        return ProviderInvoker(
            workspace=workspace,
            artifacts=results.s.artifacts,
            runner=runner,
            provenance=engine._provenance(execution),
            build=build,
            grants_for=lambda actor: grants_from_rules(
                execution.execution_id, actor, resolved.effective_capabilities
            ),
            execution_id=execution.execution_id,
            phase_id=PhaseId.INDEPENDENT_REVIEW,
            default_timeout=engine._bounded_timeout(runtime.command_timeout_seconds),
            max_output_bytes=runtime.max_output_bytes,
            cancelled=lambda: engine.is_cancelled(execution.execution_id),
            before=before,
            record_request=record_request,
            after=after,
            on_violation=on_violation,
            mcp_servers=servers,
        )

    # ----- the review --------------------------------------------------------------------------
    def run(
        self, execution: Execution, phase: PhaseExecution, change_set: ChangeSet
    ) -> ReviewOutcome:
        settings = self.settings
        assert settings is not None
        results = self.results
        engine = results.engine
        resolved = results.s.resolved
        workspace = results.s.paths.workspace
        setup = review_setup(resolved, workspace)
        servers = mcp_servers(workspace, settings.mcp_servers)
        review = resolved.project.review
        reviewer_config = review.reviewer if review is not None else None
        provider = (
            settings.provider
            or (reviewer_config.provider if reviewer_config else None)
            or results.provider_for(execution, "review")
        )
        cache_settings = settings.effective_cache
        diff = engine._compute_owned_diff(execution).unified_diff.decode("utf-8", "replace")
        untrusted, notice = panel_context(resolved, workspace)
        inputs = PanelInputs(
            workspace=workspace,
            diff_text=diff,
            mode="run",
            catalog=setup.catalog,
            reviewers=setup.reviewers,
            settings=settings,
            provider=provider,
            invoker=self._invoker(execution, phase, servers),
            route=reviewer_route(resolved),
            runner_version=__version__,
            base=results.s.state.get_flag(f"baseline:{execution.execution_id}") or None,
            head=change_set.digest,
            fallback=settings.fallback_provider,
            packs=setup.packs or None,
            cache=ReviewCache(
                results.s.paths.harness_dir / "review" / "cache",
                ttl_seconds=cache_settings.ttl_seconds,
                max_entries=cache_settings.limit,
            )
            if cache_settings.on
            else None,
            consistency=consistency_runner(
                resolved,
                workspace,
                engine._runner(execution),
                execution_id=execution.execution_id,
                cancelled=lambda: engine.is_cancelled(execution.execution_id),
            ),
            extra={"task": compact_task(engine.run_task(execution)), **untrusted},
            untrusted_notice=notice,
            mcp={"mcpServers": servers, "digest": mcp_digest(servers)},
        )
        report = run_panel(inputs)
        enforce = settings.mode != "warn"
        report_ref = results.record_json(
            execution,
            phase.phase_id,
            report.as_dict(),
            kind="review-panel-report",
            summary=(
                f"Review panel: {report.verdict}, {len(report.findings)} finding(s), "
                f"{report.tokens} reported token(s), cache {report.cache}"
            ),
        )
        results.s.events.append(
            execution.execution_id,
            "review.panel.completed",
            {
                "verdict": report.verdict,
                "digest": report.digest,
                "findings": len(report.findings),
                "reviewers": {item.reviewer: item.status for item in report.reviewers},
                "tokens": report.tokens,
                "cache": report.cache,
                "evidenceRef": report_ref,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        findings, by_key = self._record_findings(execution, report, report_ref, enforce)
        unknown = [item.reviewer for item in report.reviewers if item.status == "UNKNOWN"]
        status = ResultStatus.BLOCKED if unknown and enforce else ResultStatus.PASSED
        results.record_validation(
            execution,
            validator_id="review.agent",
            digest=change_set.digest,
            status=status,
            kind=ValidationKind.SUCCESS
            if status is ResultStatus.PASSED
            else ValidationKind.PROTOCOL_ERROR,
            mandatory=enforce,
            summary=(
                f"Review panel by {provider}: {report.verdict}, {len(findings)} finding(s)"
                + (f"; no valid answer from {', '.join(unknown)}" if unknown else "")
            ),
            findings=tuple(findings),
            evidence_refs=(report_ref,),
        )
        results.set_flag_json(
            f"agentreview:{execution.execution_id}", {"digest": change_set.digest}
        )
        if not enforce:
            return ReviewOutcome(True)
        if unknown:
            # A reviewer that never answered blocks; nothing goes back to the agent.
            return ReviewOutcome(True)
        blocking = [
            (item, by_key.get(item.finding_id))
            for item in findings
            if item.severity in results.agent_review.blocking_severities()
        ]
        if not blocking:
            return ReviewOutcome(True)
        return ReviewOutcome(True, self._auto_fix(execution, change_set, blocking))

    def _record_findings(
        self, execution: Execution, report: PanelReport, report_ref: str, enforce: bool
    ) -> tuple[list[Finding], dict[str, ReviewFinding]]:
        results = self.results
        recorded: list[Finding] = []
        by_key: dict[str, ReviewFinding] = {}
        error = FindingSeverity.HIGH if enforce else FindingSeverity.MEDIUM
        for item in report.consistency:
            if item.get("status") == "PASSED":
                continue
            recorded.append(
                results.record_finding(
                    execution,
                    validator_id="review.agent",
                    rule_id=f"{PANEL_RULE_PREFIX}.consistency.{item['id']}",
                    category="agent-review",
                    severity=error,
                    message=(
                        f"The consistency check {item['id']} failed ({item.get('summary', '')}); "
                        "no reviewer was called"
                    ),
                    evidence_refs=(report_ref,),
                )
            )
        for outcome in report.reviewers:
            if outcome.status != "UNKNOWN":
                continue
            recorded.append(
                results.record_finding(
                    execution,
                    validator_id="review.agent",
                    rule_id=f"{PANEL_RULE_PREFIX}.unknown",
                    category="agent-protocol",
                    severity=error,
                    message=f"The {outcome.reviewer} reviewer gave no valid answer: {outcome.reason}",
                    evidence_refs=(report_ref, *outcome.evidence_refs),
                )
            )
        for finding in report.findings:
            actor = (
                Actor(actor_type=ActorType.AGENT, actor_id=f"agent.{report.provider}", version="1")
                if finding.source == "ai"
                else Actor(
                    actor_type=ActorType.TOOL,
                    actor_id=f"validator.review.{finding.reviewer}",
                    version="1",
                )
            )
            side = "" if finding.side == "new" else " (a removed line)"
            entry = results.record_finding(
                execution,
                validator_id="review.agent",
                rule_id=f"{PANEL_RULE_PREFIX}.{finding.rule}",
                category="agent-review",
                severity=error if finding.blocking else FindingSeverity.LOW,
                message=f"[{finding.reviewer}] {finding.issue}{side}",
                path=finding.file,
                line=finding.line,
                evidence_refs=(report_ref,),
                recommendation=(f"Evidence: {finding.evidence}" if finding.evidence else None),
                introduced=True,
                actor=actor,
            )
            by_key[entry.finding_id] = finding
            recorded.append(entry)
        return recorded, by_key

    # ----- scoped auto-fix ---------------------------------------------------------------------
    def agent_files(self, execution: Execution, change_set: ChangeSet) -> set[str] | None:
        """The ChangeSet files only the agent wrote in this run (provenance), or ``None`` when
        provenance is not recorded."""
        engine = self.results.engine
        if not engine.provenance.snapshots_enabled:
            return None
        engine.provenance.attribute(execution, change_set, PhaseId.INDEPENDENT_REVIEW)
        records = [
            item
            for item in self.results.s.state.list(
                "component_provenance", ComponentProvenance, execution_id=execution.execution_id
            )
            if item.change_set_digest == change_set.digest
        ]
        if not records:
            return None
        return {item.path for item in records[-1].files if item.source == "AGENT"}

    def _attempts(self, execution: Execution) -> int:
        return sum(
            1
            for event in self.results.s.events.list(execution.execution_id)
            if event.event_type == "review.autofix.requested"
        )

    def _auto_fix(
        self,
        execution: Execution,
        change_set: ChangeSet,
        blocking: list[tuple[Finding, ReviewFinding | None]],
    ) -> tuple[Finding, ...]:
        settings = self.settings
        assert settings is not None
        config = settings.auto_fix
        if config is None:
            # Without autoFix the panel behaves as the single reviewer: every error goes back.
            return tuple(item for item, _ in blocking)
        if config.mode != "scoped":
            return ()
        attempts = self._attempts(execution)
        agent_files = self.agent_files(execution, change_set) or set()
        fixable = [
            finding
            for finding, source in blocking
            if source is not None and source.side == "new" and source.file in agent_files
        ]
        kept = [finding for finding, _ in blocking if finding not in fixable]
        key = f"{AUTOFIX_FLAG}:{execution.execution_id}"
        if not fixable or attempts >= config.attempts:
            self.results.s.events.append(
                execution.execution_id,
                "review.autofix.declined",
                {
                    "reason": "no error on a line the agent wrote"
                    if not fixable
                    else f"the {config.attempts} attempt(s) are used",
                    "attempts": attempts,
                    "notFixable": [item.finding_id for item in kept],
                },
            )
            self.results.set_flag_json(key, None)
            return ()
        self.results.set_flag_json(
            key,
            {
                "digest": change_set.digest,
                "fixable": [item.finding_id for item in fixable],
                "notFixable": [item.finding_id for item in kept],
            },
        )
        self.results.s.events.append(
            execution.execution_id,
            "review.autofix.requested",
            {
                "attempt": attempts + 1,
                "maxAttempts": config.attempts,
                "fixable": [item.finding_id for item in fixable],
                "notFixable": [item.finding_id for item in kept],
                "changeSetDigest": change_set.digest,
            },
        )
        return tuple(fixable)

    def scoped_findings(self, execution: Execution, findings: list[Finding]) -> list[Finding]:
        """The findings a correction carries back under scoped auto-fix: only the fixable ones
        of the current digest."""
        value = self.results.flag_json(f"{AUTOFIX_FLAG}:{execution.execution_id}")
        if not isinstance(value, dict) or value.get("digest") != execution.change_set_digest:
            return findings
        wanted = set(value.get("fixable") or [])
        return [item for item in findings if item.finding_id in wanted]


__all__ = ["AUTOFIX_FLAG", "PanelReview", "compact_task"]
