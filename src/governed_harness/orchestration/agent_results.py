"""The agent-results settings of a run (#37-#44, #52), wired into the engine's phases.

``RunEngine`` delegates every agent-results step to this class so that a project without any
of the settings runs exactly the 1.0.0 code path: ``active`` is false and the engine never calls
into it. Each step records what it did as evidence, events and findings on the run's chain."""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from governed_harness.agents import (
    AgentCallResult,
    AgentExecutionResult,
    AgentProvider,
    CommandAgentProvider,
    SimulatedAgentContext,
    render_instructions,
)
from governed_harness.agents.requests import READ_ONLY_KINDS, REQUEST_SCHEMA_VERSION, CallKind
from governed_harness.agents.routing import (
    RoutingHistory,
    TaskSignals,
    invoking_model,
    provider_family,
    select,
)
from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.agent_results import AgentCallConfig, BudgetConfig
from governed_harness.configuration.models import ProjectConfiguration
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    EvidenceKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    AgentInvocation,
    CapabilityGrant,
    ChangeRequestItem,
    Execution,
    Finding,
    FindingLocation,
    HumanDecision,
    PhaseExecution,
    ResourceUsage,
    Task,
    ValidationResult,
    utc_now,
)
from governed_harness.orchestration import budget as budget_rules
from governed_harness.orchestration.engine_types import READ_ONLY_RULE as READ_ONLY_RULE
from governed_harness.orchestration.engine_types import AgentCallOutcome as AgentCallOutcome
from governed_harness.orchestration.engine_types import PhaseOutcome
from governed_harness.orchestration.workspace_ops import (
    Contents,
    changes_since,
    restore_changes,
    snapshot_contents,
)
from governed_harness.runtime import (
    CancellationToken,
    PatchApplier,
    WorkspaceSnapshot,
    WorkspaceSnapshotter,
)
from governed_harness.runtime.snapshots import StoredSnapshot
from governed_harness.runtime.workspace import WorkspaceDiff

if TYPE_CHECKING:
    from governed_harness.orchestration.engine_types import EngineServices
    from governed_harness.orchestration.hosts import EngineHost

_SECURITY_WORDS = (
    "password",
    "secret",
    "token",
    "authentic",
    "authoriz",
    "credential",
    "card",
    "payment",
    "encrypt",
)

BUDGET_EXCEEDED_RULE = "budget.exceeded"
BUDGET_WARNING_RULE = "budget.warning"


class AgentResults:
    def __init__(self, engine: EngineHost) -> None:
        from governed_harness.orchestration.acceptance import AcceptanceTests
        from governed_harness.orchestration.agent_review import AgentReview
        from governed_harness.orchestration.corrections import Corrections
        from governed_harness.orchestration.decomposition import Decomposition
        from governed_harness.orchestration.differential import Differential
        from governed_harness.orchestration.gate_contract import GateContract
        from governed_harness.orchestration.intent_review import IntentReview
        from governed_harness.orchestration.lessons import Lessons
        from governed_harness.orchestration.stop_line import StopLine
        from governed_harness.orchestration.verification_checks import VerificationChecks

        self.engine = engine
        self.intent = IntentReview(self)
        self.verification = VerificationChecks(self)
        self.differential = Differential(self)
        self.stop_line = StopLine(self)
        self.gate = GateContract(self)
        self.corrections = Corrections(self)
        self.agent_review = AgentReview(self)
        # Wave 7 (#57): the review panel replaces the single reviewer under review.panel.
        from governed_harness.orchestration.review_panel import PanelReview

        self.panel = PanelReview(self)
        self.decomposition = Decomposition(self)
        self.lessons = Lessons(self)
        self.acceptance = AcceptanceTests(self)
        # Wave 6 (#56): standards, principles, testing strategy, architecture, project setup.
        from governed_harness.orchestration.architecture import ArchitectureFlow
        from governed_harness.orchestration.engineering import Engineering
        from governed_harness.orchestration.project_setup import ProjectSetup

        self.engineering = Engineering(self)
        self.architecture = ArchitectureFlow(self)
        self.project_setup = ProjectSetup(self)
        self._baselines: dict[str, StoredSnapshot | None] = {}

    def after_verification(
        self,
        execution: Execution,
        phase: PhaseExecution,
        change_set: Any,
        outputs: list[Any],
    ) -> list[Any]:
        """Reclassify failing validators against the baseline (``verification.differential``,
        ``verification.ratchet``)."""
        return self.differential.reclassify(execution, phase, change_set, outputs)

    def baseline_stored(self, execution: Execution) -> StoredSnapshot | None:
        """The workspace as DISCOVERY recorded it (1.0.0 text snapshot or manifest)."""
        key = execution.execution_id
        if key not in self._baselines:
            uri = self.s.state.get_flag(f"baseline:{key}")
            self._baselines[key] = self.engine.snapshots.load(uri) if uri else None
        return self._baselines[key]

    def baseline_snapshot(self, execution: Execution) -> WorkspaceSnapshot | None:
        stored = self.baseline_stored(execution)
        return stored.snapshot if stored else None

    def baseline_contents(self, execution: Execution) -> Contents | None:
        """The recorded bytes of the baseline's files (from the text it kept or from Git)."""
        stored = self.baseline_stored(execution)
        if stored is None:
            return None
        snapshots = self.engine.snapshots
        return Contents(
            lambda path: path in stored.snapshot.files,
            lambda path: snapshots.content(stored, path),
        )

    def baseline_text(self, execution: Execution) -> Callable[[str], str | None]:
        contents = self.baseline_contents(execution)

        def text(path: str) -> str | None:
            if contents is None or not contents.existed(path):
                return None
            data = contents.content(path)
            return data.decode("utf-8", "replace") if data is not None else None

        return text

    def baseline_changes(self, execution: Execution) -> WorkspaceDiff | None:
        """Every change since DISCOVERY (not only the owned paths), with a unified diff."""
        stored = self.baseline_stored(execution)
        if stored is None:
            return None
        snapshots = self.engine.snapshots
        return snapshots.diff(stored, snapshots.take())

    # ----- configuration -------------------------------------------------------------------
    @property
    def s(self) -> EngineServices:
        return self.engine.s

    @property
    def project(self) -> ProjectConfiguration:
        return self.s.resolved.project

    @property
    def active(self) -> bool:
        """Whether any agent-results setting is configured; without one the engine runs the
        1.0.0 path and never calls into this class."""
        project = self.project
        intake = project.intake
        verification = project.verification
        review = project.review
        runtime = project.runtime
        governance = project.governance_settings
        return any(
            (
                intake is not None
                and (
                    intake.ambiguity_review is not None
                    or intake.clarify_agent is not None
                    or intake.validate_answers is not None
                ),
                verification is not None
                and any(
                    getattr(verification, name) is not None
                    for name in (
                        "interface",
                        "architecture",
                        "security_patterns",
                        "constraints",
                        "ratchet",
                        "invariants",
                        "differential",
                        "weakened_controls",
                        "test_quality",
                        "secrets",
                        "sarif",
                        "risk_factors",
                        "acceptance_tests",
                    )
                ),
                review is not None
                and (
                    review.agent_review is not None
                    or review.reviewer is not None
                    or review.structured_changes is not None
                    or review.panel is not None
                ),
                runtime.gate_contract is not None or runtime.reproduce_first is not None,
                governance.stop_the_line is not None or governance.phase_permissions is not None,
                project.planning is not None,
                project.context is not None,
                project.budget is not None,
                project.memory is not None,
                project.agent_routing is not None,
                self.engineering.configured,
                bool(governance.apply_repository_policies),
            )
        )

    # ----- decisions: risk factors and structured change requests (#52) --------------------
    def required_acknowledgements(self, execution: Execution, digest: str) -> list[str]:
        from governed_harness.orchestration.verification_checks import RISK_ACK_FLAG

        value = self.flag_json(f"{RISK_ACK_FLAG}:{execution.execution_id}:{digest}")
        return [str(item) for item in value] if isinstance(value, list) else []

    def check_decision(
        self,
        execution: Execution,
        decision: DecisionKind,
        digest: str,
        acknowledged: tuple[str, ...],
        change_requests: tuple[ChangeRequestItem, ...],
    ) -> None:
        from governed_harness.configuration.agent_results import RISK_FACTORS

        unknown = sorted(set(acknowledged) - set(RISK_FACTORS))
        if unknown:
            raise ConfigurationError(
                f"unknown risk factor(s): {', '.join(unknown)}; known: {', '.join(RISK_FACTORS)}"
            )
        if change_requests:
            review = self.project.review
            if decision is not DecisionKind.REQUEST_CHANGES:
                raise ConfigurationError("change requests need the REQUEST_CHANGES decision")
            if not (review and review.structured_changes):
                raise ConfigurationError(
                    "change requests need review.structuredChanges: true in project.yaml"
                )
        if decision in {DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION}:
            missing = [
                item
                for item in self.required_acknowledgements(execution, digest)
                if item not in acknowledged
            ]
            if missing:
                raise PolicyViolationError(
                    "this ChangeSet has risk factors a person must acknowledge before approving: "
                    + ", ".join(missing)
                    + " (use --acknowledge-risk for each)"
                )

    def open_change_requests(self, execution: Execution, decision: HumanDecision) -> None:
        """Keep the blocking items of a structured REQUEST_CHANGES as temporary criteria that
        every later VERIFICATION of the run checks."""
        key = f"changerequests:{execution.execution_id}"
        current = self.flag_json(key) or []
        current.extend(
            {**item.model_dump(mode="json", by_alias=True), "decisionId": decision.decision_id}
            for item in decision.change_requests
        )
        self.set_flag_json(key, current)
        self.s.events.append(
            execution.execution_id,
            "change.requests.opened",
            {
                "decisionId": decision.decision_id,
                "items": [
                    item.model_dump(mode="json", by_alias=True) for item in decision.change_requests
                ],
            },
            actor=decision.actor,
        )

    def change_requests(self, execution: Execution) -> list[dict[str, Any]]:
        value = self.flag_json(f"changerequests:{execution.execution_id}")
        return value if isinstance(value, list) else []

    @property
    def secrets_in_context(self) -> bool:
        verification = self.project.verification
        return bool(verification and verification.secrets == "context")

    # ----- records -------------------------------------------------------------------------
    def record_finding(
        self,
        execution: Execution,
        *,
        validator_id: str,
        rule_id: str,
        category: str,
        severity: FindingSeverity,
        message: str,
        path: str | None = None,
        line: int | None = None,
        evidence_refs: tuple[str, ...] = (),
        recommendation: str | None = None,
        introduced: bool | None = None,
        actor: Actor | None = None,
    ) -> Finding:
        provenance = self.engine._provenance(execution)
        if actor is not None:
            provenance = provenance.model_copy(update={"actor": actor})
        finding = Finding(
            finding_id=new_id("finding"),
            execution_id=execution.execution_id,
            validator_id=validator_id,
            rule_id=rule_id,
            category=category,
            severity=severity,
            message=message,
            location=FindingLocation(path=path, start_line=line, end_line=line)
            if path or line
            else None,
            evidence_refs=evidence_refs,
            recommendation=recommendation,
            introduced=introduced,
            provenance=provenance,
        )
        self.s.state.put(
            "finding",
            finding.finding_id,
            finding,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "finding.recorded",
            finding.model_dump(mode="json"),
            actor=finding.provenance.actor,
        )
        return finding

    def record_validation(
        self,
        execution: Execution,
        *,
        validator_id: str,
        digest: str,
        status: ResultStatus,
        kind: ValidationKind,
        mandatory: bool,
        summary: str,
        findings: tuple[Finding, ...] = (),
        evidence_refs: tuple[str, ...],
        started_at: Any = None,
    ) -> ValidationResult:
        now = utc_now()
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{validator_id}", version="1")
        result = ValidationResult(
            validation_result_id=new_id("validation"),
            execution_id=execution.execution_id,
            validator_id=validator_id,
            change_set_digest=digest,
            status=status,
            kind=kind,
            mandatory=mandatory,
            summary=summary,
            finding_ids=tuple(item.finding_id for item in findings),
            evidence_refs=evidence_refs,
            started_at=started_at or now,
            finished_at=now,
            provenance=self.engine._provenance(execution).model_copy(update={"actor": actor}),
        )
        self.s.state.put(
            "validation",
            result.validation_result_id,
            result,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        self.s.events.append(
            execution.execution_id,
            "validation.completed",
            result.model_dump(mode="json"),
            actor=result.provenance.actor,
        )
        return result

    def record_json(
        self,
        execution: Execution,
        phase_id: PhaseId,
        value: Any,
        *,
        kind: str,
        summary: str,
        evidence_kind: EvidenceKind = EvidenceKind.OTHER,
        supports: tuple[str, ...] = (),
    ) -> str:
        """Store ``value`` as a JSON artifact and record it as evidence of ``phase_id``."""
        ref = self.s.artifacts.put_json(
            value, metadata={"kind": kind, "executionId": execution.execution_id}
        )
        evidence = self.engine._record_evidence(
            execution, phase_id, evidence_kind, ref, summary, supports=supports
        )
        return evidence.artifact_ref

    def flag_json(self, key: str) -> Any:
        raw = self.s.state.get_flag(key)
        return json.loads(raw) if raw else None

    def set_flag_json(self, key: str, value: Any) -> None:
        self.s.state.set_flag(key, json.dumps(value, sort_keys=True))

    # ----- budget (#42) --------------------------------------------------------------------
    def _usage(self, invocations: list[AgentInvocation], project_id: str) -> budget_rules.Usage:
        ids = {item.invocation_id for item in invocations}
        usages = [
            item
            for item in self.s.state.list("resource_usage", ResourceUsage, project_id=project_id)
            if item.invocation_id in ids
        ]
        return budget_rules.usage_of(invocations, usages)

    def run_usage(self, execution: Execution) -> budget_rules.Usage:
        invocations = self.s.state.list(
            "agent_invocation", AgentInvocation, execution_id=execution.execution_id
        )
        return self._usage(invocations, execution.project_id)

    def task_usage(self, execution: Execution) -> budget_rules.Usage:
        runs = {
            item.execution_id
            for item in self.s.state.list("execution", Execution, project_id=execution.project_id)
            if item.task_id == execution.task_id
        }
        invocations = [
            item
            for item in self.s.state.list(
                "agent_invocation", AgentInvocation, project_id=execution.project_id
            )
            if item.execution_id in runs
        ]
        return self._usage(invocations, execution.project_id)

    def raised_limits(self, execution: Execution) -> dict[str, dict[str, float]]:
        value = self.flag_json(f"budgetraise:{execution.execution_id}")
        return value if isinstance(value, dict) else {}

    def budget_check(
        self, execution: Execution, call: budget_rules.Usage | None = None
    ) -> budget_rules.BudgetCheck | None:
        config = self.project.budget
        if config is None:
            return None
        usage: dict[budget_rules.Scope, budget_rules.Usage] = {
            "run": self.run_usage(execution),
            "task": self.task_usage(execution),
        }
        if call is not None:
            usage["call"] = call
        return budget_rules.evaluate(config, usage, self.raised_limits(execution))

    def remaining_budget(self, execution: Execution) -> dict[str, Any] | None:
        check = self.budget_check(execution)
        if check is None:
            return None
        return {"remaining": check.remaining}

    def before_agent_call(
        self, execution: Execution, phase: PhaseExecution, kind: CallKind
    ) -> PhaseOutcome | None:
        """Fail closed before an agent call when a limit is already crossed (or a call crossed
        its per-call limit and no person raised it since)."""
        check = self.budget_check(execution)
        if check is None:
            return None
        crossings = list(check.exceeded)
        pending = self.flag_json(f"budgetblock:{execution.execution_id}")
        if isinstance(pending, list):
            raised = self.raised_limits(execution)
            for item in pending:
                limit = budget_rules.effective_limit(
                    self.project.budget or BudgetConfig(), item["scope"], item["metric"], raised
                )
                if limit is not None and item["used"] >= limit:
                    crossings.append(
                        budget_rules.Crossing(item["scope"], item["metric"], item["used"], limit)
                    )
        if not crossings:
            self.s.state.set_flag(f"budgetblock:{execution.execution_id}", "")
            return None
        self._record_crossings(execution, crossings, kind=kind, exceeded=True)
        reasons = "; ".join(item.describe() for item in crossings)
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"{BUDGET_EXCEEDED_RULE}: {reasons}; no {kind} call was made. Raise the limit with "
            "harness budget raise and continue the run.",
        )

    def after_agent_call(
        self, execution: Execution, phase: PhaseExecution, result: AgentExecutionResult
    ) -> None:
        """Account for a finished call: per-call limit, warnings at ``warnAt``, and a crossing
        that blocks the next call."""
        config = self.project.budget
        if config is None:
            return
        invocation = result.invocation
        usages = [result.usage] if result.usage is not None else []
        call = budget_rules.usage_of([invocation], usages)
        check = self.budget_check(execution, call)
        if check is None:
            return
        if check.warnings:
            self._record_crossings(execution, list(check.warnings), kind=None, exceeded=False)
        if check.exceeded:
            self._record_crossings(execution, list(check.exceeded), kind=None, exceeded=True)
            self.set_flag_json(
                f"budgetblock:{execution.execution_id}",
                [item.as_dict() for item in check.exceeded if item.scope == "call"],
            )

    def _record_crossings(
        self,
        execution: Execution,
        crossings: list[budget_rules.Crossing],
        *,
        kind: str | None,
        exceeded: bool,
    ) -> None:
        key = f"budgetseen:{execution.execution_id}"
        seen = set(self.flag_json(key) or [])
        rule = BUDGET_EXCEEDED_RULE if exceeded else BUDGET_WARNING_RULE
        for crossing in crossings:
            marker = f"{rule}:{crossing.key}"
            event = "budget.exceeded" if exceeded else "budget.warning"
            self.s.events.append(
                execution.execution_id,
                event,
                {**crossing.as_dict(), "callKind": kind},
            )
            if marker in seen:
                continue
            seen.add(marker)
            self.record_finding(
                execution,
                validator_id="harness.budget",
                rule_id=rule,
                category="budget",
                severity=FindingSeverity.HIGH if exceeded else FindingSeverity.LOW,
                message=(f"Budget {'exceeded' if exceeded else 'warning'}: {crossing.describe()}"),
                recommendation=(
                    "A person may raise the limit with harness budget raise (recorded) and "
                    "continue the run."
                    if exceeded
                    else "The run is close to a budget limit."
                ),
            )
        self.set_flag_json(key, sorted(seen))

    # ----- agent calls (#37, #38, #39) -----------------------------------------------------
    def call_config(self, kind: CallKind) -> AgentCallConfig | None:
        """The configuration of a read-only call kind (``None``: the provider's defaults)."""
        read = _CALL_CONFIGS.get(kind)
        return read(self) if read is not None else None

    def provider_for(self, execution: Execution, kind: CallKind) -> str:
        configured = self.call_config(kind)
        if configured is not None and configured.provider:
            return configured.provider
        provider = self.s.state.get_flag(f"provider:{execution.execution_id}") or "simulated"
        if provider == "session":
            # Embedded mode (#56): the session implements; read-only calls go to the project's
            # provider, so the author does not review itself.
            fallback = self.project.agent_provider
            return fallback if fallback != "session" else "simulated"
        return provider

    def call_agent(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        payload: dict[str, Any],
        *,
        task: Task,
        instruction_values: dict[str, Any] | None = None,
        instructions_suffix: str = "",
    ) -> AgentCallOutcome:
        """Send a read-only request of ``kind`` and return its structured result.

        The request, the provider's output and the routing decision are evidence; a call that
        changed the workspace is undone and answered with ``ERROR``."""
        engine = self.engine
        provider_id = self.provider_for(execution, kind)
        built = engine._build_provider(execution, phase, provider_id)
        if not isinstance(built, tuple):
            return AgentCallOutcome(built.status, built.summary, None, None, built.evidence_refs)
        provider, actor, _sandbox, sandbox_refs = built
        blocked = self.before_agent_call(execution, phase, kind)
        if blocked is not None:
            return AgentCallOutcome(blocked.status, blocked.summary, None, None, ())
        grants = grants_from_rules(
            execution.execution_id, actor, self.s.resolved.effective_capabilities
        )
        workspace = str(self.s.paths.workspace)
        request: dict[str, Any] = {
            "schemaVersion": REQUEST_SCHEMA_VERSION,
            "kind": kind,
            "readOnly": True,
            "instructions": render_instructions(
                kind, workspace=workspace, **(instruction_values or {})
            )
            + (f" {instructions_suffix}" if instructions_suffix else ""),
            "workspace": workspace,
            "task": task.model_dump(mode="json"),
        }
        request.update(payload)
        request.update(self.request_common(execution, phase, kind, task, provider_id, grants))
        repository = self.repository_extra(read_only=True)
        if repository:
            # #5: the notice and the names of the instruction files, never their directives.
            request.update(repository)
            if "untrustedContent" in repository:
                request["instructions"] += " " + repository["untrustedContent"]["notice"]
        request_ref = self.record_json(
            execution,
            phase.phase_id,
            request,
            kind=f"agent-{kind}-request",
            summary=f"Agent {kind} request to {provider_id}",
        )
        runtime = self.project.runtime
        before = WorkspaceSnapshotter(self.s.paths.workspace).snapshot()
        context = SimulatedAgentContext(
            execution_id=execution.execution_id,
            workspace=self.s.paths.workspace,
            grants=grants,
            artifact_store=self.s.artifacts,
            process_runner=engine._runner(execution),
            patch_applier=PatchApplier(self.s.paths.workspace),
            provenance=engine._provenance(execution).model_copy(update={"actor": actor}),
            timeout_seconds=engine._bounded_timeout(runtime.command_timeout_seconds),
            max_output_bytes=runtime.max_output_bytes,
            cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
        )
        answer = self._call_with_retries(execution, phase, provider, kind, request, context)
        if answer is None:
            return AgentCallOutcome(
                ResultStatus.CANCELLED, "Cancellation requested", None, None, ()
            )
        self.after_agent_call(execution, phase, answer.execution)
        refs = (request_ref, *sandbox_refs) + (
            (answer.execution.output_ref,) if answer.execution.output_ref else ()
        )
        diff = changes_since(self.s.paths.workspace, before)
        if diff.changes:
            restored, unrestorable = restore_changes(
                self.s.paths.workspace, snapshot_contents(before), diff
            )
            shown = ", ".join(item.path for item in diff.changes[:10])
            self.record_finding(
                execution,
                validator_id="harness.agent-calls",
                rule_id=READ_ONLY_RULE,
                category="agent-protocol",
                severity=FindingSeverity.HIGH,
                message=(
                    f"The {kind} call is read-only but changed {len(diff.changes)} path(s): "
                    f"{shown}; restored {len(restored)}, could not restore {len(unrestorable)}"
                ),
                path=diff.changes[0].path,
                evidence_refs=refs,
                recommendation="Use a provider that honours readOnly requests.",
            )
            return AgentCallOutcome(
                ResultStatus.ERROR,
                f"The {kind} call changed the workspace; its answer was discarded",
                None,
                answer.execution.invocation.invocation_id,
                refs,
            )
        return AgentCallOutcome(
            answer.execution.status,
            answer.execution.summary,
            answer.response if answer.execution.status is ResultStatus.PASSED else None,
            answer.execution.invocation.invocation_id,
            refs,
        )

    def _call_with_retries(
        self,
        execution: Execution,
        phase: PhaseExecution,
        provider: AgentProvider,
        kind: CallKind,
        request: dict[str, Any],
        context: SimulatedAgentContext,
    ) -> AgentCallResult | None:
        """The provider's answer, each one saved; a transient failure of a command provider is
        retried up to ``runtime.retryLimit`` times. ``None``: cancelled while waiting to retry."""
        engine = self.engine
        runtime = self.project.runtime
        retries = 0
        while True:
            answer: AgentCallResult = provider.call(kind, request, context, phase_id=phase.phase_id)
            engine._save_agent_result(execution, phase, answer.execution)
            cause = (
                engine._transient_cause(answer.execution)
                if isinstance(provider, CommandAgentProvider) and retries < runtime.retry_limit
                else None
            )
            if cause is None:
                return answer
            retries += 1
            engine._record_provider_retry(execution, phase, answer.execution, cause, retries)
            if not engine._wait_for_retry(execution.execution_id, runtime.retry_delay_seconds):
                return None

    def request_common(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        task: Task,
        provider_id: str,
        grants: list[CapabilityGrant],
    ) -> dict[str, Any]:
        """Keys every request of a configured run carries: routing, permissions and budget."""
        extra: dict[str, Any] = {}
        routing = self.routing_for(execution, phase, kind, task, provider_id)
        if routing is not None:
            extra["routing"] = routing
        if self.project.governance_settings.phase_permissions:
            extra["permissions"] = self.permissions(execution, phase, kind, grants)
        remaining = self.remaining_budget(execution)
        if remaining is not None:
            extra["budget"] = remaining
        return extra

    # ----- routing (#44) -------------------------------------------------------------------
    def task_signals(self, execution: Execution, task: Task) -> TaskSignals:
        """Size signals known before the call: requirements, the files the task owns or
        names, their lines, risk flags and whether PLANNING decomposed the task."""
        owned = [str(item) for item in task.metadata.get("ownedPaths") or []]
        interface = task.metadata.get("interface")
        files = list(dict.fromkeys(owned))
        loc = 0
        for path in files:
            target = self.s.paths.workspace / path
            try:
                if target.is_file():
                    loc += target.read_bytes().count(b"\n")
            except OSError:
                continue
        flags: list[str] = []
        if interface:
            flags.append("interface")
        policy = self.project.agent_routing
        risk_paths = policy.risk_paths if policy and policy.risk_paths else ()
        text = " ".join(
            [task.intent, *task.constraints, *(item.text for item in task.requirements)]
        ).lower()
        if any(word in text for word in _SECURITY_WORDS) or any(
            fnmatch.fnmatchcase(path, pattern) for path in files for pattern in risk_paths
        ):
            flags.append("security")
        return TaskSignals(
            requirements=len(task.requirements) or len(task.acceptance_criteria),
            files=len(files),
            loc=loc,
            risk_flags=tuple(flags),
            planned_large=bool(self.s.state.get_flag(f"decomposed:{execution.execution_id}")),
        )

    def escalations(self, execution: Execution) -> int:
        return int(self.s.state.get_flag(f"escalations:{execution.execution_id}") or 0)

    def provider_command(self, provider_id: str) -> tuple[str, ...]:
        configured = self.project.agent_providers.get(provider_id)
        if configured is None:
            return ()
        # A built-in adapter (kind claude-code, codex, ...) names its family by its kind.
        return (*(configured.command or ()), configured.kind)

    def invoking_model(self, provider_id: str) -> str | None:
        """The invoking model, ceiling of ``agentRouting.mode: anchored`` (#85), of a call that
        ``provider_id`` answers: ``agentRouting.anchorModel`` when set, else the provider's
        ``model``, else the ``--model``/``-m`` value of its command or ``args``. The embedded
        ``session`` provider stands for the project's ``agentProvider``, which answers the
        read-only calls of a session run. ``None`` when nothing says it."""
        return invoking_model(self.project, provider_id)

    def routing_for(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        task: Task,
        provider_id: str,
    ) -> dict[str, Any] | None:
        """The model and effort of a call: the call kind's own setting, else the router's
        decision under ``agentRouting``. The decision is evidence; ``None`` keeps the
        provider's own model."""
        override = self.call_config(kind)
        policy = self.project.agent_routing
        has_override = override is not None and (override.model or override.effort)
        if policy is None and not has_override:
            return None
        family = provider_family(
            provider_id, self.provider_command(provider_id), policy.families if policy else None
        )
        decision = select(
            kind,
            self.task_signals(execution, task),
            RoutingHistory(escalations=self.escalations(execution)),
            policy,
            family=family,
            override=override if has_override else None,
            anchor=self.invoking_model(provider_id),
        )
        record = {**decision.as_dict(), "provider": provider_id, "phase": phase.phase_id}
        ref = self.record_json(
            execution,
            phase.phase_id,
            record,
            kind="agent-routing",
            summary=(
                f"Routing of the {kind} call: {decision.rule} -> "
                f"{decision.model or 'provider default'}/{decision.effort or 'default'}"
            ),
        )
        self.s.events.append(
            execution.execution_id,
            "agent.routing.decided",
            {**record, "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        if decision.warning is not None:
            # #59: a call kind without its own routing entry runs on the implement rung.
            self.s.events.append(
                execution.execution_id,
                "agent.routing.fallback",
                {"callKind": kind, "warning": decision.warning, "evidenceRef": ref},
                phase_execution_id=phase.phase_execution_id,
            )
        if decision.model is None and decision.effort is None:
            return None
        value: dict[str, Any] = {
            "model": decision.model,
            "effort": decision.effort,
            "rule": decision.rule,
            "size": decision.size,
            "flags": list(decision.flags),
        }
        if decision.rung is not None:
            value["rung"] = decision.rung
        return value

    def permissions(
        self,
        execution: Execution,
        phase: PhaseExecution,
        kind: CallKind,
        grants: list[CapabilityGrant],
    ) -> dict[str, Any]:
        """The permissions of a call kind derived from the agent's capability grants, recorded
        as evidence (``governance.phasePermissions``)."""
        from governed_harness.orchestration.gate_contract import permissions

        value = permissions(
            kind,
            grants,
            kind in READ_ONLY_KINDS,
            self.engine._agent_network_allowed(),
        )
        self.record_json(
            execution,
            phase.phase_id,
            value,
            kind="agent-permissions",
            summary=f"Permissions of the {kind} call, derived from the capability grants",
        )
        return value

    def implementation_task(self, execution: Execution, task: Task) -> Task:
        """The current sub-task under an approved decomposition (#39), else the task."""
        return self.decomposition.subtask_task(execution, task)

    def implement_extras(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        provider_id: str,
        actor: Actor,
        grants: list[CapabilityGrant],
    ) -> dict[str, Any] | None:
        """The keys the agent-results settings add to the implement request; with any of
        them the request also carries its kind and rendered instructions (schema 1.1)."""
        extra = self.request_common(execution, phase, "implement", task, provider_id, grants)
        if self.gate.enabled:
            extra["gate"] = self.gate.contract(execution)
            self.gate.write_check_state(execution, task)
        extra.update(self.implement_context(execution, phase, task))
        extra.update(self.repository_extra(read_only=False))
        frozen = self.acceptance.request_extra(execution)
        if frozen is not None:
            extra["acceptanceTests"] = frozen
        suffix = ""
        if self.engineering.configured:
            # Standards cards for the files this call works on, testing strategy and layers
            # (#56), selected deterministically and cached by digest.
            since = self.baseline_changes(execution)
            changed = [item.path for item in since.changes] if since is not None else []
            manifest = extra.get("contextFiles")
            listed = (
                [str(item.get("path")) for item in manifest.get("files", []) if item.get("path")]
                if isinstance(manifest, dict)
                else []
            )
            paths = self.engineering.candidate_paths(task, changed, listed)
            engineering, suffix = self.engineering.implement_extra(execution, phase, paths)
            extra.update(engineering)
        if not extra:
            return None
        workspace = str(self.s.paths.workspace)
        extra.update(
            {
                "schemaVersion": REQUEST_SCHEMA_VERSION,
                "kind": "implement",
                "workspace": workspace,
                "instructions": render_instructions("implement", workspace=workspace)
                + (f" {suffix}" if suffix else ""),
            }
        )
        return extra

    def repository_extra(self, *, read_only: bool) -> dict[str, Any]:
        """The keys ``governance.applyRepositoryPolicies`` adds to a request (#5): the
        repository's instruction files as quoted, untrusted context (only their names for a
        read-only call) and the command policy."""
        from governed_harness.capabilities.repository import (
            repository_policies,
            untrusted_context,
        )

        policies = repository_policies(self.s.resolved)
        extra: dict[str, Any] = {}
        if policies.untrusted:
            extra["untrustedContent"] = untrusted_context(
                self.s.paths.workspace, policies.instruction_files, include_content=not read_only
            )
        if policies.deny_destructive:
            extra["commandPolicy"] = {"destructive": "deny"}
        return extra

    def implement_context(
        self, execution: Execution, phase: PhaseExecution, task: Task
    ) -> dict[str, Any]:
        """The context manifest (#41) and the active lessons (#43) of an implement call."""
        from governed_harness.agents.context_manifest import build_manifest
        from governed_harness.configuration.agent_results import (
            DEFAULT_CONTEXT_MAX_BYTES,
            DEFAULT_CONTEXT_MAX_FILES,
        )
        from governed_harness.orchestration.verification_checks import declared_interfaces

        extra: dict[str, Any] = {}
        context = self.project.context
        since = self.baseline_changes(execution)
        changed = [item.path for item in since.changes] if since is not None else []
        owned = [str(item) for item in task.metadata.get("ownedPaths") or []]
        lessons = self.lessons.for_request(execution, phase, task, [*owned, *changed])
        if lessons:
            extra["lessons"] = lessons
        if context is not None and context.enabled:
            interfaces = [path for pair in declared_interfaces(task) for path in pair]
            lesson_paths = [path for item in lessons for path in item.get("paths", [])]
            manifest = build_manifest(
                self.s.paths.workspace,
                task,
                changed_paths=changed,
                interface_paths=interfaces,
                lesson_paths=lesson_paths,
                located_paths=self.engine.ladder.located_paths(execution),
                max_files=context.max_files or DEFAULT_CONTEXT_MAX_FILES,
                max_bytes=context.max_bytes or DEFAULT_CONTEXT_MAX_BYTES,
            )
            if lessons:
                manifest["lessons"] = [item["memoryId"] for item in lessons]
            ref = self.record_json(
                execution,
                phase.phase_id,
                manifest,
                kind="context-files-manifest",
                summary=(
                    f"Context manifest: {len(manifest['files'])} file(s), "
                    f"{manifest['totalBytes']} bytes, {manifest['omitted']} candidate(s) left out"
                ),
            )
            self.s.events.append(
                execution.execution_id,
                "context.manifest.built",
                {
                    "files": len(manifest["files"]),
                    "totalBytes": manifest["totalBytes"],
                    "digest": manifest["digest"],
                    "evidenceRef": ref,
                },
                phase_execution_id=phase.phase_execution_id,
            )
            extra["contextFiles"] = manifest
        return extra

    def after_run(self, execution: Execution) -> Execution:
        """What happens when a ``run continue`` ends: stop the line for an unapproved stop,
        lessons from the corrections of a closed run."""
        execution = self.stop_line.after_continue(execution)
        if execution.status is ResultStatus.PASSED and not self.s.state.get_flag(
            f"lessons:{execution.execution_id}"
        ):
            self.s.state.set_flag(f"lessons:{execution.execution_id}", "1")
            self.lessons.propose(execution)
        return execution

    # ----- second reviewer corrections (#38) -----------------------------------------------
    def _corrections_used(self, execution: Execution, trigger: str) -> int:
        return sum(
            1
            for event in self.s.events.list(execution.execution_id)
            if event.event_type == "correction.authorized"
            and event.payload.get("trigger") == trigger
        )

    def review_correction_available(self, execution: Execution) -> bool:
        return self.engine._external_provider(execution.execution_id) and (
            self._corrections_used(execution, "REVIEW_FINDINGS")
            < self.project.runtime.correction_limit
        )

    def after_failed_review(self, execution: Execution, summary: str) -> bool:
        """Send blocking findings of the agent review back to IMPLEMENTATION (with feedback
        under ``runtime.providerFeedback``) while the correction budget lasts."""
        from governed_harness.domain.models import FeedbackGate
        from governed_harness.orchestration.agent_review import AGENT_REVIEW_ID

        engine = self.engine
        execution = engine.get_execution(execution.execution_id)
        if not execution.change_set_digest or not self.review_correction_available(execution):
            return False
        used = self._corrections_used(execution, "REVIEW_FINDINGS")
        limit = self.project.runtime.correction_limit
        validations = engine._latest_validations(
            execution.execution_id, execution.change_set_digest
        )
        review = [item for item in validations if item.validator_id == AGENT_REVIEW_ID]
        findings = [
            self.s.state.get("finding", finding_id, Finding)
            for item in review
            for finding_id in item.finding_ids
        ]
        if self.panel.configured:
            # #57: under scoped auto-fix only the errors on lines the agent wrote go back.
            findings = self.panel.scoped_findings(execution, findings)
        feedback_ref: str | None = None
        if engine._feedback_applies(execution.execution_id):
            feedback_ref = engine._record_feedback(
                execution,
                PhaseId.INDEPENDENT_REVIEW,
                trigger="REVIEW_FINDINGS",
                change_set_digest=execution.change_set_digest,
                gate=FeedbackGate(
                    gate_id="independent_review",
                    status=ResultStatus.FAILED,
                    reason_codes=(f"{AGENT_REVIEW_ID}_FINDINGS",),
                ),
                validations=[],
                findings=findings,
            )
        transition = engine.state_machine.authorize_review_correction(PhaseId.INDEPENDENT_REVIEW)
        engine._save_execution(
            execution.model_copy(
                update={
                    "status": ResultStatus.PENDING,
                    "current_phase": transition.target,
                    "gate_evaluation_id": None,
                    "human_decision_id": None,
                    "terminal_reason": None,
                    "updated_at": utc_now(),
                }
            )
        )
        self.s.events.append(
            execution.execution_id,
            "correction.authorized",
            {
                "trigger": "REVIEW_FINDINGS",
                "cycle": used + 1,
                "maxCycles": limit,
                "summary": summary,
                "findings": [item.finding_id for item in findings],
                "changeSetDigest": execution.change_set_digest,
                "invalidatedPhases": [phase.value for phase in transition.invalidated],
                "feedbackRef": feedback_ref,
            },
        )
        self.escalate(execution, "REVIEW_FINDINGS")
        return True

    # ----- escalation (#44) -----------------------------------------------------------------
    def escalate(self, execution: Execution, trigger: str) -> None:
        """A quality failure moves the next implement call one rung up the ladder (effort
        before model) while ``agentRouting.maxEscalations`` allows; environment failures are
        retried at the same rung and never get here."""
        from governed_harness.agents.routing import can_escalate

        policy = self.project.agent_routing
        if policy is None or policy.mode not in {"tiered", "anchored"}:
            return
        history = RoutingHistory(escalations=self.escalations(execution))
        if not can_escalate(history, policy):
            self.s.events.append(
                execution.execution_id,
                "agent.routing.escalation.capped",
                {"trigger": trigger, "escalations": history.escalations},
            )
            return
        self.s.state.set_flag(f"escalations:{execution.execution_id}", str(history.escalations + 1))
        self.s.events.append(
            execution.execution_id,
            "agent.routing.escalated",
            {"trigger": trigger, "escalations": history.escalations + 1},
        )

    # ----- decomposition hooks (#39) --------------------------------------------------------
    def after_passed_verification(self, execution: Execution) -> bool:
        return self.decomposition.after_passed_verification(execution)

    def replan_after_failure(self, execution: Execution) -> bool:
        return self.decomposition.replan_after_failure(execution)

    def implement_model(self, execution: Execution, task: Task) -> str | None:
        """The model an implement call would use (no record): the router's choice under
        ``agentRouting: tiered`` (or ``anchored``), else the provider's configured model."""
        provider_id = self.s.state.get_flag(f"provider:{execution.execution_id}") or "simulated"
        configured = self.project.agent_providers.get(provider_id)
        policy = self.project.agent_routing
        if policy is not None:
            family = provider_family(
                provider_id, self.provider_command(provider_id), policy.families
            )
            decision = select(
                "implement",
                self.task_signals(execution, task),
                RoutingHistory(escalations=self.escalations(execution)),
                policy,
                family=family,
                anchor=self.invoking_model(provider_id),
            )
            if decision.model:
                return decision.model
        return configured.model if configured else None


# ----- the configuration of each read-only call kind -----------------------------------------
def _clarify_config(results: AgentResults) -> AgentCallConfig | None:
    intake = results.project.intake
    return intake.clarify_agent if intake else None


def _review_config(results: AgentResults) -> AgentCallConfig | None:
    review = results.project.review
    return review.reviewer if review else None


def _plan_config(results: AgentResults) -> AgentCallConfig | None:
    planning = results.project.planning
    return planning.planner if planning else None


def _acceptance_config(results: AgentResults) -> AgentCallConfig | None:
    config = results.acceptance.config
    return config.author if config else None


def _locate_config(results: AgentResults) -> AgentCallConfig | None:
    context = results.project.context
    return context.locate.agent if context and context.locate else None


def _architecture_config(results: AgentResults) -> AgentCallConfig | None:
    architecture = results.project.architecture
    return architecture.agent if architecture else None


_CALL_CONFIGS: dict[str, Callable[[AgentResults], AgentCallConfig | None]] = {
    "clarify": _clarify_config,
    "review": _review_config,
    "plan": _plan_config,
    "acceptance": _acceptance_config,
    "locate": _locate_config,
    "architecture": _architecture_config,
}
