from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governed_harness import __version__
from governed_harness.configuration import (
    ConfigurationResolver,
    GovernanceConfig,
    RuntimeConfig,
    initialize_project,
)
from governed_harness.configuration.declared import declared_settings_report
from governed_harness.configuration.loader import find_project_config, load_yaml
from governed_harness.configuration.models import ProjectConfiguration, PublisherConfig
from governed_harness.delivery.approval import verify_approval
from governed_harness.delivery.bundle import export_bundle, verify_bundle
from governed_harness.delivery.publisher import (
    GitHubPublisher,
    Transport,
    render_brief_markdown,
    transport_for,
)
from governed_harness.delivery.vcs import Git, repository_from_remote
from governed_harness.domain.actors import (
    DEFAULT_CLI_ACTOR,
    IdentitySource,
    actor_id_from_identity,
    identity_display_name,
    require_human_actor,
)
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    MemoryLevel,
    RecommendationDecision,
    ResultStatus,
)
from governed_harness.domain.errors import (
    ConfigurationError,
    IntegrityError,
    NotFoundError,
    PolicyViolationError,
)
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    ClarificationRecord,
    ClarificationRequest,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    MemoryRecord,
    OutcomeRecord,
    PhaseExecution,
    Provenance,
    Retrospective,
    RetrospectiveTrigger,
    Task,
    ValidationResult,
    utc_now,
)
from governed_harness.intake import task_digest
from governed_harness.memory import APPROVAL_REQUIRED, MemoryStore
from governed_harness.orchestration.engine import EngineServices, RunEngine, run_is_open
from governed_harness.orchestration.gate_contract import run_check
from governed_harness.orchestration.retention import RetentionCollector
from governed_harness.orchestration.verification import RunVerifier
from governed_harness.profiles import detect_profiles
from governed_harness.reporting import TraceReporter
from governed_harness.runtime import GitAdapter
from governed_harness.runtime.lease import WorkspaceLease, interruptible
from governed_harness.telemetry import MetricsProjector

from .agent_results import (
    acceptance_state,
    agent_results_summary,
    budget_state,
    decide_acceptance,
    decide_plan,
    parse_change_requests,
    plan_state,
    quarantine_run,
    raise_budget,
    routing_calibration,
)
from .clarification_loader import load_clarification_file
from .exceptions import (
    ExceptionOptions,
    brief_exceptions,
    list_exceptions,
    parse_expiry,
    parse_scope,
    record_exception,
)
from .health import list_outcomes, record_outcome, rule_health
from .hints import default_hint
from .isolation import (
    cleanup_worktree,
    isolation_record,
    retry_isolation,
    start_isolated,
)
from .ladder import (
    attach_evidence,
    config_lint,
    confirm_contract,
    decide_preflight,
    inbox_entries,
    verification_state,
)
from .notifications import inbox, notify, notify_transition
from .onboarding import (
    EXAMPLE_TASK_NAME,
    ensure_gitignore,
    git_identity,
    provider_checks,
    repository_checks,
    sandbox_check,
    validator_checks,
    write_example_task,
)
from .review import build_brief
from .task_loader import load_task_file


class HarnessApplication:
    def __init__(self) -> None:
        self.notices: list[str] = []
        """Warnings for the person (the CLI prints them on standard error)."""
        self.last_identity_source: IdentitySource = "default"

    def init(
        self,
        path: Path,
        *,
        force: bool = False,
        gitignore: bool = False,
        example_task: bool = False,
    ) -> dict[str, Any]:
        """Write .harness/project.yaml. The CLI also asks for the .gitignore entry and the
        example task (``gitignore``/``example_task``); the Python API leaves the workspace
        untouched beyond .harness/ unless asked."""
        config = initialize_project(path, force=force)
        workspace = config.parent.parent
        detections = [item for item in detect_profiles(workspace) if item.confidence > 0]
        result: dict[str, Any] = {
            "status": "PASSED",
            "configuration": str(config),
            "profiles": [
                {"profileId": item.profile_id, "confidence": item.confidence} for item in detections
            ],
        }
        if gitignore:
            result["gitignore"] = ensure_gitignore(workspace)
        example: Path | None = None
        if example_task:
            example = write_example_task(
                config.parent, [item.technology for item in detections], force=force
            )
            result["exampleTask"] = str(example) if example else None
        task_file = f".harness/{EXAMPLE_TASK_NAME}" if example or example_task else "task.yaml"
        result["next"] = [
            "harness doctor --path .",
            f"harness task create --file {task_file}   (after editing it)",
            "harness run start --task <taskId>",
        ]
        return result

    def inspect(self, path: Path) -> dict[str, Any]:
        workspace = path.resolve(strict=True)
        detections = detect_profiles(workspace)
        return {
            "workspace": str(workspace),
            "detections": [
                {
                    "profileId": item.profile_id,
                    "technology": item.technology,
                    "confidence": item.confidence,
                    "evidence": list(item.evidence),
                    "warnings": list(item.warnings),
                }
                for item in detections
                if item.confidence > 0
            ],
        }

    def validate_config(self, path: Path) -> dict[str, Any]:
        """The resolved configuration, the settings that are declared but not applied
        (``declarative``) and a warning for each one this project relies on."""
        resolved = ConfigurationResolver().resolve(path)
        declarative, warnings = declared_settings_report(
            resolved, load_yaml(find_project_config(path))
        )
        return {
            "status": "PASSED",
            "projectId": resolved.project.project_id,
            "workspace": str(resolved.workspace_root),
            "profiles": [item.profile_id for item in resolved.profiles],
            "workflow": resolved.workflow.workflow_id,
            "validators": [item.validator_id for item in resolved.effective_validators],
            "capabilities": [
                item.model_dump(mode="json", by_alias=True)
                for item in resolved.effective_capabilities
            ],
            "policies": resolved.effective_policies,
            "intake": {"criteriaPolicy": resolved.project.criteria_policy},
            "verification": self._verification(resolved.project),
            "review": self._review(resolved.project),
            "agentSandbox": {
                "mode": resolved.project.runtime.effective_agent_sandbox,
                "writePaths": list(resolved.project.runtime.sandbox_write_paths or ()),
            },
            "feedbackLoop": self._feedback_loop(resolved.project.runtime),
            "governance": self._governance(resolved.project.governance_settings),
            "agentResults": agent_results_summary(resolved.project),
            **self._wave4_settings(resolved),
            "ladder": self._ladder_settings(resolved),
            "declarative": declarative,
            "warnings": warnings,
        }

    @staticmethod
    def _wave4_settings(resolved: Any) -> dict[str, Any]:
        """Effective workspace, toolchain, provenance and delivery settings (since 1.1;
        absent keys resolve to the 1.0.0 behaviour)."""
        project = resolved.project
        workspace = project.workspace
        toolchain = project.toolchain_settings
        provenance = project.provenance_settings
        delivery = project.delivery_settings
        return {
            "snapshots": {
                "snapshot": workspace.snapshot or "walk",
                "baseline": workspace.baseline or "text",
                "snapshotCache": bool(workspace.snapshot_cache),
            },
            "toolchain": {
                "profileDetection": toolchain.profile_detection or "best",
                "interpreter": toolchain.interpreter or "system",
                "projectProfiles": [
                    item for item in resolved.source_files if not item.startswith("builtin:")
                ][1:],
                "commands": {
                    item.validator_id: list(item.command or ())
                    for item in resolved.effective_validators
                },
            },
            "provenance": {
                "agentSnapshots": bool(provenance.agent_snapshots),
                "selfReport": bool(provenance.self_report),
            },
            "delivery": {
                "closureCommit": delivery.mode,
                "branch": delivery.branch_template if delivery.mode == "branch" else None,
            },
        }

    @staticmethod
    def _ladder_settings(resolved: Any) -> dict[str, Any]:
        """Effective settings of the verification ladder and delivery hygiene (#55; absent
        keys resolve to the earlier behaviour)."""
        from governed_harness.runtime.state_location import resolve_state_location

        project = resolved.project
        verification = project.verification
        ladder = verification.ladder if verification else None
        mutation = verification.mutation if verification else None
        intake = project.intake
        delivery = project.delivery_settings
        isolation = project.workspace.isolation
        location = resolve_state_location(
            resolved.workspace_root, project.project_id, project.runtime.state_dir, create=False
        )
        context = project.context
        return {
            "ladder": {
                "mode": (ladder.mode if ladder else None) or "off",
                "defaultLevel": ladder.required_default.value if ladder else None,
                "deferredExpiryDays": ladder.expiry_days if ladder else None,
                "preflight": bool(ladder and ladder.preflight),
                "capabilityDetection": bool(ladder and ladder.capability_detection),
            },
            "probes": [
                item.probe_id for item in (verification.probes if verification else None) or ()
            ],
            "mutation": (mutation.mode if mutation else None) or "off",
            "manualChecklist": bool(project.review and project.review.manual_checklist),
            "operationalContract": (intake.operational_contract if intake else None) or "off",
            "interruptions": intake.interruptions.model_dump(mode="json", by_alias=True)
            if intake and intake.interruptions
            else None,
            "isolation": isolation.effective_mode if isolation else "none",
            "environment": project.environment.model_dump(mode="json", by_alias=True)
            if project.environment
            else None,
            "locate": bool(context and context.locate and context.locate.enabled),
            "delivery": {
                "stage": bool(delivery.stage),
                "push": bool(delivery.push),
                "pullRequest": bool(delivery.pull_request and delivery.pull_request.create),
                "comment": delivery.comment or "never",
            },
            "state": {
                "stateDir": project.runtime.state_dir,
                "database": str(location.database),
                "external": location.external,
            },
        }

    @staticmethod
    def _governance(settings: GovernanceConfig) -> dict[str, Any]:
        """Effective governance settings (absent keys resolve to the 1.0.0 behaviour)."""
        return {
            "deciderIdentity": settings.decider_identity or "default",
            "confirmDecisionDigest": bool(settings.confirm_decision_digest),
            "trustedHosts": list(settings.trusted_hosts) if settings.trusted_hosts else None,
            "verifyRecords": bool(settings.verify_records),
            "chainAnchor": settings.chain_anchor or "off",
            "pinTaskRevision": bool(settings.pin_task_revision),
            "protectExcludedPaths": bool(settings.protect_excluded_paths),
            "workspaceLease": bool(settings.workspace_lease),
            "applyWorkflowSettings": bool(settings.apply_workflow_settings),
            "decisionExpiryHours": settings.decision_expiry_hours,
            "applyProfilePolicies": bool(settings.apply_profile_policies),
            "applyNetworkPolicy": bool(settings.apply_network_policy),
        }

    @staticmethod
    def _verification(project: ProjectConfiguration) -> dict[str, Any]:
        value: dict[str, Any] = {"requirementTraceability": project.requirement_traceability}
        if project.output_parsers_enabled:
            value["outputParsers"] = True
        return value

    @staticmethod
    def _review(project: ProjectConfiguration) -> dict[str, Any]:
        """Effective review settings; the URLs of webhooks are never printed."""
        return {
            "exceptions": project.exceptions_enabled,
            "exceptionDays": project.exception_days if project.exceptions_enabled else None,
            "causalRetrospective": project.causal_retrospective,
            "webhooks": [
                {
                    "target": f"env:{item.url_env}" if item.url_env else "url",
                    "events": list(item.events),
                    "retries": item.retries,
                }
                for item in project.webhooks
            ],
        }

    @staticmethod
    def _feedback_loop(runtime: RuntimeConfig) -> dict[str, Any]:
        """Effective values of the provider feedback loop (absent keys resolve to 'off')."""
        return {
            "verificationCorrections": runtime.correction_limit,
            "providerFeedback": runtime.feedback_enabled,
            "unsupportedClaimCheck": runtime.claim_check_enabled,
            "unsupportedClaimSeverity": runtime.claim_severity.value,
            "providerRetries": runtime.retry_limit,
            "providerRetryDelaySeconds": runtime.retry_delay_seconds,
            "providerTransientPatterns": list(runtime.transient_patterns),
        }

    def create_task(self, path: Path, source: Path) -> Task:
        with self._services(path) as services:
            project = services.resolved.project
            task = load_task_file(
                source, project_id=project.project_id, criteria_policy=project.criteria_policy
            )
            if project.governance_settings.pin_task_revision:
                open_runs = [
                    item.execution_id
                    for item in services.state.list(
                        "execution", Execution, project_id=project.project_id
                    )
                    if item.task_id == task.task_id and run_is_open(item)
                ]
                if open_runs:
                    raise PolicyViolationError(
                        f"task {task.task_id} has open run(s) {', '.join(open_runs)}; its task "
                        "cannot be replaced while they run. Create the task under a new id, "
                        "cancel the run, or revise it with harness task clarify during INTENT"
                    )
            services.state.put(
                "task",
                task.task_id,
                task,
                project_id=task.project_id,
            )
            return task

    def list_tasks(self, path: Path) -> list[Task]:
        with self._services(path) as services:
            return services.state.list(
                "task", Task, project_id=services.resolved.project.project_id, newest_first=True
            )

    def get_task(self, path: Path, task_id: str) -> Task:
        with self._services(path) as services:
            return services.state.get("task", task_id, Task)

    def clarify_task(
        self,
        path: Path,
        *,
        task_id: str,
        answers_file: Path,
        actor_id: str | None = None,
        actor_type: ActorType = ActorType.HUMAN,
    ) -> dict[str, Any]:
        """Answer the clarification questions INTENT asked about a task.

        The answers file maps question ids to answers and may replace criteria, add criteria
        and add requirements. The harness stores the revised task and a clarification record
        (actor, questions, answers, previous and new task digest) on the event chain of the
        run that asked; ``continue_run`` then assesses the revised task in INTENT.

        Only a human actor may answer. Unknown question ids and empty answers are rejected
        (exit code 2), as is a task with a run past INTENT (exit code 5)."""
        clarification = load_clarification_file(answers_file)
        with self._services(path) as services:
            decider, display_name = self._decider(services, actor_id)
            with self._leased(services, "task clarify"):
                record, task = RunEngine(services).clarify(
                    task_id=task_id,
                    clarification=clarification,
                    actor=Actor(actor_type=actor_type, actor_id=decider, display_name=display_name),
                )
            return {
                "clarification": record.model_dump(mode="json", by_alias=True),
                "task": task.model_dump(mode="json", by_alias=True),
                "next": f"harness run continue --run {record.execution_id}",
            }

    def list_clarifications(self, path: Path, task_id: str) -> dict[str, Any]:
        """The clarification requests asked about a task, the answers recorded for them and
        the open request: the latest one asked about the current revision of the task."""
        with self._services(path) as services:
            task = services.state.get("task", task_id, Task)
            requests = sorted(
                (
                    item
                    for item in services.state.list(
                        "clarification_request", ClarificationRequest, project_id=task.project_id
                    )
                    if item.task_id == task_id
                ),
                key=lambda item: item.created_at,
            )
            current = task_digest(task)
            open_requests = [item for item in requests if item.task_digest == current]
            records = [
                item
                for item in services.state.list(
                    "clarification", ClarificationRecord, project_id=task.project_id
                )
                if item.task_id == task_id
            ]
            return {
                "taskId": task_id,
                "taskDigest": current,
                "openRequest": open_requests[-1].model_dump(mode="json", by_alias=True)
                if open_requests
                else None,
                "requests": [item.model_dump(mode="json", by_alias=True) for item in requests],
                "clarifications": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in sorted(records, key=lambda item: item.recorded_at)
                ],
            }

    def start_run(
        self,
        path: Path,
        task_id: str,
        provider: str | None = None,
        *,
        isolate: str | None = None,
    ) -> Execution:
        """Create a run and execute the phases. With ``isolate`` ``worktree`` (or
        ``workspace.isolation.mode: worktree``) the run happens in a Git worktree of its own on a
        new branch (#55)."""
        from governed_harness.configuration.ladder import IsolationConfig

        if isolate not in {None, "none", "worktree"}:
            raise ConfigurationError(f"--isolate is none or worktree, got {isolate!r}")
        with self._services(path) as services:
            settings = services.resolved.project.workspace.isolation
            mode = isolate or (settings.effective_mode if settings else "none")
            if mode == "worktree":
                engine = RunEngine(services)
                task = engine.get_task(task_id)
                execution, isolated = start_isolated(
                    self._open_services,
                    services,
                    task,
                    provider,
                    settings or IsolationConfig(mode="worktree"),
                )
                if isolated is None:
                    return execution
                try:
                    with self._leased(isolated, "run start") as lease:
                        if lease is not None:
                            lease.bind(execution.execution_id)
                        result = RunEngine(isolated).continue_execution(execution.execution_id)
                        return self._after(isolated, result)
                finally:
                    isolated.close()
        with self._services(path) as services, self._leased(services, "run start") as lease:
            engine = RunEngine(services)
            task = engine.get_task(task_id)
            execution = engine.create_execution(task, provider=provider)
            if lease is not None:
                lease.bind(execution.execution_id)
            return self._after(services, engine.continue_execution(execution.execution_id))

    def continue_run(self, path: Path, execution_id: str) -> Execution:
        with self._run_services(path, execution_id) as (services, execution_id):
            record = isolation_record(services, execution_id)
            if record is not None and record.get("status") == "COLLISION":
                # The run waits for its worktree (#55): try again.
                execution, isolated = retry_isolation(
                    self._open_services,
                    services,
                    services.state.get("execution", execution_id, Execution),
                )
                if isolated is None:
                    return execution
                try:
                    with self._leased(isolated, "run continue") as lease:
                        if lease is not None:
                            lease.bind(execution_id)
                        result = RunEngine(isolated).continue_execution(execution_id)
                        return self._after(isolated, result)
                finally:
                    isolated.close()
            with self._leased(services, "run continue") as lease:
                if lease is not None:
                    lease.bind(execution_id)
                return self._after(services, RunEngine(services).continue_execution(execution_id))

    def cancel_run(self, path: Path, execution_id: str, actor_id: str | None = None) -> Execution:
        with self._run_services(path, execution_id) as (services, execution_id):
            decider = self._decider(services, actor_id)[0]
            return self._after(services, RunEngine(services).cancel(execution_id, decider))

    def cleanup_run(self, path: Path, execution_id: str) -> dict[str, Any]:
        """Remove the worktree of a finished isolated run (never its branch, #55)."""
        with self._services(path) as services:
            run_id = self._run_id(services, execution_id)
            return cleanup_worktree(services, services.state.get("execution", run_id, Execution))

    # ----- the verification ladder (#55) -----------------------------------------------------
    def verification(self, path: Path, execution_id: str) -> dict[str, Any]:
        """The verification plan, preflight, certification, deferred items and checklist of a
        run."""
        with self._services(path) as services:
            return verification_state(services, self._run_id(services, execution_id))

    def decide_verification(
        self,
        path: Path,
        *,
        execution_id: str,
        rationale: str,
        actor_id: str | None = None,
        continue_after: bool = True,
    ) -> dict[str, Any]:
        """A person decides to continue a run whose preflight is UNAVAILABLE, uncertified for
        what cannot be verified here."""
        with self._run_services(path, execution_id) as (services, execution_id):
            decider, display_name = self._decider(services, actor_id)
            require_human_actor(decider, "continue a run uncertified")
            actor = Actor(actor_type=ActorType.HUMAN, actor_id=decider, display_name=display_name)
            with self._leased(services, "verification decide") as lease:
                if lease is not None:
                    lease.bind(execution_id)
                decision = decide_preflight(
                    services, execution_id, actor=actor, rationale=rationale
                )
                result: dict[str, Any] = {"decision": decision}
                if continue_after:
                    execution = self._after(
                        services, RunEngine(services).continue_execution(execution_id)
                    )
                    result["execution"] = execution.model_dump(mode="json", by_alias=True)
                return result

    def confirm_contract(
        self, path: Path, *, task_id: str, digest: str, actor_id: str | None = None
    ) -> dict[str, Any]:
        """A person confirms the operational contract of a task's revision (#55)."""
        with self._services(path) as services:
            decider, display_name = self._decider(services, actor_id)
            require_human_actor(decider, "confirm the operational contract")
            actor = Actor(actor_type=ActorType.HUMAN, actor_id=decider, display_name=display_name)
            return confirm_contract(services, task_id=task_id, digest=digest, actor=actor)

    def attach_evidence(
        self,
        path: Path,
        *,
        file: Path,
        execution_id: str | None = None,
        task_id: str | None = None,
        item: str | None = None,
        manual: bool = False,
        evidence_format: str = "auto",
        case: str | None = None,
        commit: str | None = None,
        note: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Close a deferred verification with external evidence, or attach a person's evidence
        to a run (checklist) or a task (intake context) (#55)."""
        with self._services(path) as services:
            decider, display_name = self._decider(services, actor_id)
            require_human_actor(decider, "attach evidence")
            actor = Actor(actor_type=ActorType.HUMAN, actor_id=decider, display_name=display_name)
            run_id = self._run_id(services, execution_id) if execution_id else None
            return attach_evidence(
                services,
                file=file,
                actor=actor,
                execution_id=run_id,
                task_id=task_id,
                item=item,
                manual=manual,
                evidence_format=evidence_format,
                case=case,
                commit=commit,
                note=note,
            )

    def config_lint(self, path: Path) -> dict[str, Any]:
        """Contradictions between the configuration and the agent instruction files (#55)."""
        return config_lint(ConfigurationResolver().resolve(path))

    def registry(self, path: Path | None = None) -> dict[str, Any]:
        """The projects whose run registry lives in the state directory (``runtime.stateDir:
        auto``), with their latest runs: one dashboard for several repositories (#55)."""
        from governed_harness.runtime.state_location import default_state_root, registered_projects
        from governed_harness.storage import SQLiteStateStore

        projects = []
        for entry in registered_projects():
            database = Path(entry["database"])
            runs: list[dict[str, Any]] = []
            if database.is_file():
                store = SQLiteStateStore(database)
                try:
                    executions = store.list("execution", Execution, newest_first=True)
                finally:
                    store.close()
                runs = [
                    {
                        "executionId": item.execution_id,
                        "taskId": item.task_id,
                        "status": item.status.value,
                        "currentPhase": item.current_phase.value,
                        "updatedAt": item.updated_at.isoformat(),
                    }
                    for item in executions[:20]
                ]
            projects.append({**entry, "runs": runs})
        return {"stateRoot": str(default_state_root()), "projects": projects}

    def routing_calibration(self, path: Path) -> dict[str, Any]:
        """Cost per approved task of the routing decisions recorded in the project (#44)."""
        with self._services(path) as services:
            return routing_calibration(services)

    def budget(self, path: Path, execution_id: str) -> dict[str, Any]:
        """Usage of a run and its task against the budget limits (#42)."""
        with self._services(path) as services:
            return budget_state(services, self._run_id(services, execution_id))

    def raise_budget(
        self,
        path: Path,
        *,
        execution_id: str,
        scope: str,
        metric: str,
        limit: float,
        rationale: str,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Raise a budget limit of a run (a person, recorded); ``run continue`` resumes it."""
        with self._services(path) as services, self._leased(services, "budget raise"):
            execution_id = self._run_id(services, execution_id)
            decider = self._decider(services, actor_id)[0]
            return raise_budget(
                services,
                execution_id,
                scope=scope,
                metric=metric,
                limit=limit,
                actor_id=decider,
                rationale=rationale,
            )

    def acceptance(self, path: Path, execution_id: str) -> dict[str, Any]:
        """The acceptance tests proposed or frozen for a run (#52)."""
        with self._services(path) as services:
            return acceptance_state(services, self._run_id(services, execution_id))

    def decide_acceptance(
        self,
        path: Path,
        *,
        execution_id: str,
        decision: DecisionKind,
        digest: str,
        rationale: str,
        actor_id: str | None = None,
        continue_after: bool = True,
    ) -> dict[str, Any]:
        """Approve or reject the proposed acceptance tests (digest-bound, a person)."""
        with self._services(path) as services, self._leased(services, "acceptance decide") as lease:
            execution_id = self._run_id(services, execution_id)
            if lease is not None:
                lease.bind(execution_id)
            decider = self._decider(services, actor_id)[0]
            return decide_acceptance(
                services,
                execution_id,
                decision=decision,
                digest=digest,
                actor_id=decider,
                rationale=rationale,
                continue_after=continue_after,
            )

    def plan(self, path: Path, execution_id: str) -> dict[str, Any]:
        """The decomposition of a run and the progress of its sub-tasks (#39)."""
        with self._services(path) as services:
            return plan_state(services, self._run_id(services, execution_id))

    def decide_plan(
        self,
        path: Path,
        *,
        execution_id: str,
        decision: DecisionKind,
        digest: str,
        rationale: str,
        actor_id: str | None = None,
        continue_after: bool = True,
    ) -> dict[str, Any]:
        """Approve or reject the decomposition PLANNING proposed (digest-bound, a person)."""
        with self._services(path) as services, self._leased(services, "plan decide") as lease:
            execution_id = self._run_id(services, execution_id)
            if lease is not None:
                lease.bind(execution_id)
            decider = self._decider(services, actor_id)[0]
            return decide_plan(
                services,
                execution_id,
                decision=decision,
                digest=digest,
                actor_id=decider,
                rationale=rationale,
                continue_after=continue_after,
            )

    def check(self, path: Path, execution_id: str | None = None) -> dict[str, Any]:
        """``harness check``: the gate's validators and diff checks on the workspace, with
        nothing recorded (it reads the configuration and the run's check state only)."""
        return run_check(path, execution_id)

    def quarantine_run(
        self, path: Path, execution_id: str, actor_id: str | None = None
    ) -> dict[str, Any]:
        """Quarantine the changes of a stopped run and restore the baseline
        (``governance.stopTheLine``); a person only."""
        with self._services(path) as services, self._leased(services, "run quarantine"):
            execution_id = self._run_id(services, execution_id)
            decider = self._decider(services, actor_id)[0]
            return quarantine_run(services, execution_id, decider)

    @classmethod
    def _after(cls, services: EngineServices, execution: Execution) -> Execution:
        """Side effects of reaching a state a person cares about: the webhooks of
        ``notifications`` (a delivery failure is recorded, not raised) and, under
        ``retrospective.causal``, the retrospective of a rejected or cancelled run. They never
        change the run."""
        if services.resolved.project.causal_retrospective:
            trigger: RetrospectiveTrigger | None = None
            if execution.status is ResultStatus.CANCELLED:
                trigger = "CANCELLED"
            elif execution.status is ResultStatus.FAILED and execution.human_decision_id:
                decision = services.state.get(
                    "decision", execution.human_decision_id, HumanDecision
                )
                trigger = "REJECTED" if decision.decision is DecisionKind.REJECT else None
            existing = services.state.list(
                "retrospective", Retrospective, execution_id=execution.execution_id
            )
            if trigger and not existing:
                cls._generate_retrospective(services, execution.execution_id, trigger)
        notify_transition(services, execution)
        return execution

    def rule_health(self, path: Path, *, since_days: int | None = None) -> dict[str, Any]:
        """How each rule and validator behaved across the runs of the project."""
        with self._services(path) as services:
            return rule_health(services, since_days)

    def record_outcome(
        self,
        path: Path,
        *,
        execution_id: str,
        kind: str,
        summary: str,
        actor_id: str | None = None,
        reference: str | None = None,
        observed_at: str | None = None,
    ) -> OutcomeRecord:
        """Link an incident, revert, hotfix or regression to a run (a person only)."""
        with self._services(path) as services:
            actor_id = self._decider(services, actor_id)[0]
            return record_outcome(
                services,
                execution_id=self._run_id(services, execution_id),
                kind=kind,
                summary=summary,
                reference=reference,
                observed_at=observed_at,
                actor_id=actor_id,
            )

    def list_outcomes(self, path: Path, execution_id: str | None = None) -> list[OutcomeRecord]:
        with self._services(path) as services:
            run_id = self._run_id(services, execution_id) if execution_id else None
            return list_outcomes(services, run_id)

    def inbox(self, path: Path) -> list[dict[str, Any]]:
        """Runs of the project waiting for a person (decision or clarification answers) and,
        since #55, deferred verifications waiting for evidence and preflights waiting for a
        decision."""
        with self._services(path) as services:
            entries = inbox(services) + inbox_entries(services)
            return sorted(entries, key=lambda item: item["waitingSince"])

    def decide_gate(
        self,
        path: Path,
        *,
        execution_id: str,
        decision: DecisionKind,
        change_set_digest: str,
        actor_id: str | None = None,
        rationale: str,
        continue_after: bool = True,
        default_actor: str = DEFAULT_CLI_ACTOR,
        exception: ExceptionOptions | None = None,
        acknowledged_risks: tuple[str, ...] = (),
        change_requests: tuple[str, ...] = (),
        checked_items: tuple[str, ...] = (),
    ) -> tuple[HumanDecision, Execution]:
        """Record a human decision. Under ``review.exceptions`` an ``APPROVE_EXCEPTION`` also
        records an exception with an expiry (``expires_in``/``expires_at``, else
        ``review.exceptionDays``), a scope (``rule[:path]`` entries, else the blocking findings
        of the gate), alternative evidence and a follow-up (``exception``).

        Without ``actor_id`` the decider is the Git user under ``governance.deciderIdentity:
        git`` (``default_actor`` with a notice when Git has no identity) and ``default_actor``
        otherwise; an actor id of an agent, a validator or the harness is refused (exit 5)."""
        options = exception or ExceptionOptions()
        with (
            self._run_services(path, execution_id) as (services, execution_id),
            self._leased(services, "gate decide") as lease,
        ):
            if lease is not None:
                lease.bind(execution_id)
            engine = RunEngine(services)
            decider, display_name = self._decider(services, actor_id, default_actor)
            project = services.resolved.project
            records_exception = (
                decision is DecisionKind.APPROVE_EXCEPTION and project.exceptions_enabled
            )
            if options.given and not records_exception:
                raise ConfigurationError(
                    "exception options (--expires-in, --expires-at, --scope, "
                    "--alternative-evidence, --follow-up) need APPROVE_EXCEPTION and "
                    "review.exceptions: true in project.yaml"
                )
            expiry = (
                parse_expiry(
                    expires_in=options.expires_in,
                    expires_at=options.expires_at,
                    default_days=project.exception_days,
                    now=utc_now(),
                )
                if records_exception
                else None
            )
            scopes = parse_scope(options.scope)
            record = engine.decide(
                execution_id=execution_id,
                decision=decision,
                change_set_digest=change_set_digest,
                actor_id=decider,
                rationale=rationale,
                actor_display_name=display_name,
                identity_source=self.last_identity_source
                if services.resolved.project.governance_settings.git_decider
                else None,
                expires_at=expiry,
                acknowledged_risks=acknowledged_risks,
                change_requests=parse_change_requests(change_requests),
                checked_items=checked_items,
            )
            if records_exception:
                granted = record_exception(
                    services,
                    decision=record,
                    scope=scopes,
                    alternative_evidence=options.alternative_evidence,
                    follow_up=options.follow_up,
                )
                notify(
                    services,
                    engine.get_execution(execution_id),
                    "exception.granted",
                    exceptionId=granted.exception_id,
                    expiresAt=granted.expires_at.isoformat(),
                )
            execution = (
                engine.continue_execution(execution_id)
                if continue_after
                and decision in {DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION}
                else engine.get_execution(execution_id)
            )
            return record, self._after(services, execution)

    def decision_summary(self, path: Path, execution_id: str) -> dict[str, Any]:
        """What a person is about to decide on: the phase, the gate result and the ChangeSet the
        decision would be bound to (read-only; used by the interactive confirmation)."""
        with self._services(path) as services:
            execution = services.state.get("execution", execution_id, Execution)
            gate = (
                services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
                if execution.gate_evaluation_id
                else None
            )
            files: list[dict[str, Any]] = []
            if execution.change_set_digest:
                try:
                    change_set = RunEngine(services).current_change_set(execution_id)
                    files = [
                        item.model_dump(mode="json", by_alias=True) for item in change_set.files
                    ]
                except NotFoundError:
                    files = []
            return {
                "executionId": execution_id,
                "taskId": execution.task_id,
                "currentPhase": execution.current_phase.value,
                "changeSetDigest": execution.change_set_digest,
                "gateStatus": gate.status.value if gate else None,
                "gateReasonCodes": list(gate.reason_codes) if gate else [],
                "files": files,
                "confirmDigest": bool(
                    services.resolved.project.governance_settings.confirm_decision_digest
                ),
            }

    def status(self, path: Path, execution_id: str) -> dict[str, Any]:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            execution = services.state.get("execution", execution_id, Execution)
            phases = services.state.list("phase", PhaseExecution, execution_id=execution_id)
            validations = services.state.list(
                "validation", ValidationResult, execution_id=execution_id
            )
            findings = services.state.list("finding", Finding, execution_id=execution_id)
            gate = (
                services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
                if execution.gate_evaluation_id
                else None
            )
            decision = (
                services.state.get("decision", execution.human_decision_id, HumanDecision)
                if execution.human_decision_id
                else None
            )
            events = services.events.list(execution_id)
            metrics = MetricsProjector(services.state).project(execution_id, events)
            # A broken chain is reported, not raised: status is how a person finds out.
            chain = services.events.check_chain(execution_id)
            integrity: dict[str, Any] = {}
            if not chain.valid:
                integrity["eventChainError"] = chain.error
            if services.resolved.project.governance_settings.verify_records:
                verification = RunVerifier(services).verify(execution_id)
                integrity["recordsValid"] = verification.valid
                if not verification.valid:
                    integrity["verificationSummary"] = verification.summary()
            return {
                "execution": execution.model_dump(mode="json", by_alias=True),
                "phases": [item.model_dump(mode="json", by_alias=True) for item in phases],
                "validationSummary": {
                    "total": len(validations),
                    "byStatus": self._count_by(validations, "status"),
                },
                "findings": {
                    "total": len(findings),
                    "bySeverity": self._count_by(findings, "severity"),
                },
                "gate": gate.model_dump(mode="json", by_alias=True) if gate else None,
                "humanDecision": decision.model_dump(mode="json", by_alias=True)
                if decision
                else None,
                "eventCount": len(events),
                "eventChainValid": chain.valid,
                **integrity,
                "metrics": {key: value.as_dict() for key, value in metrics.items()},
            }

    def verify(self, path: Path, execution_id: str | None = None) -> dict[str, Any]:
        """Verify one run, or every run of the workspace when ``execution_id`` is ``None``:
        the event chain, the anchor of its head, the records against the events and the
        artifacts against their digests. Never raises on a failed check; ``valid`` is false."""
        with self._services(path) as services:
            verifier = RunVerifier(services)
            ids = [execution_id] if execution_id else verifier.execution_ids()
            runs = [verifier.verify(item).as_dict() for item in ids]
            return {
                "valid": all(item["valid"] for item in runs),
                "runCount": len(runs),
                "runs": runs,
            }

    def export_bundle(self, path: Path, execution_id: str, output: Path) -> dict[str, Any]:
        """Write the portable evidence bundle of a run (``delivery.bundle``). Under
        ``governance.verifyRecords`` the run is verified first, as for ``trace``."""
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            if services.resolved.project.governance_settings.verify_records:
                verification = RunVerifier(services).verify(execution_id)
                if not verification.valid:
                    raise IntegrityError(
                        f"run {execution_id} does not verify ({verification.summary()}); "
                        "inspect it with harness verify --run"
                    )
            return export_bundle(services, execution_id, output)

    @staticmethod
    def verify_bundle(bundle: Path) -> dict[str, Any]:
        """Verify a bundle without a workspace or a project configuration."""
        if not bundle.is_file():
            raise NotFoundError(f"bundle not found: {bundle}")
        return verify_bundle(bundle)

    @staticmethod
    def verify_approval(
        path: Path,
        *,
        base: str,
        head: str = "HEAD",
        bundles: tuple[Path, ...] = (),
        use_workspace: bool = True,
    ) -> dict[str, Any]:
        """Whether the range ``base..head`` is a ChangeSet a person approved
        (``delivery.approval``); needs a Git repository, not a project configuration."""
        for bundle in bundles:
            if not bundle.is_file():
                raise NotFoundError(f"bundle not found: {bundle}")
        root = path.resolve()
        if not Git(root).is_repository():
            raise ConfigurationError(f"not a Git repository: {root}")
        top = Path(Git(root).text("rev-parse", "--show-toplevel"))
        return verify_approval(top, base, head, bundles=bundles, use_workspace=use_workspace)

    def publish_pull_request(
        self,
        path: Path,
        execution_id: str,
        *,
        pull_request: int,
        repository: str | None = None,
        transport: str | None = None,
        sarif: bool | None = None,
        commit_sha: str | None = None,
        transport_override: Transport | None = None,
    ) -> dict[str, Any]:
        """Post the decision brief of a run on a pull request and upload its SARIF report."""
        with self._services(path) as services:
            run_id = self._run_id(services, execution_id)
            settings = services.resolved.project.delivery_settings.publisher or PublisherConfig()
            updates: dict[str, Any] = {}
            if repository is not None:
                updates["repository"] = repository
            if transport is not None:
                updates["transport"] = transport
            if sarif is not None:
                updates["sarif"] = sarif
            settings = PublisherConfig.model_validate(
                settings.model_dump(by_alias=True)
                | {
                    PublisherConfig.model_fields[key].alias or key: value
                    for key, value in updates.items()
                }
            )
            name = settings.repository or repository_from_remote(services.paths.workspace)
            if name is None:
                raise ConfigurationError(
                    "no repository: pass --repository owner/name or set "
                    "delivery.publisher.repository (no GitHub remote named origin was found)"
                )
            execution = services.state.get("execution", run_id, Execution)
            brief = build_brief(services, run_id, exceptions=brief_exceptions(services, execution))
            report = None
            if settings.sarif:
                findings = services.state.list("finding", Finding, execution_id=run_id)
                report = TraceReporter().render_sarif(findings)
            publisher = GitHubPublisher(transport_override or transport_for(settings), name)
            return publisher.publish(
                pull_request=pull_request,
                run_id=run_id,
                brief_markdown=render_brief_markdown(brief),
                sarif=report,
                commit_sha=commit_sha,
            )

    def list_runs(self, path: Path) -> list[Execution]:
        with self._services(path) as services:
            return services.state.list(
                "execution",
                Execution,
                project_id=services.resolved.project.project_id,
                newest_first=True,
            )

    def trace(self, path: Path, execution_id: str, format: str = "markdown") -> bytes:
        """Export the trace of a run. Under ``governance.verifyRecords`` the run is verified
        first and a run that does not verify is not exported (exit code 6)."""
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            if services.resolved.project.governance_settings.verify_records:
                verification = RunVerifier(services).verify(execution_id)
                if not verification.valid:
                    raise IntegrityError(
                        f"run {execution_id} does not verify ({verification.summary()}); "
                        "inspect it with harness verify --run"
                    )
            execution = services.state.get("execution", execution_id, Execution)
            task = services.state.get("task", execution.task_id, Task)
            phases = services.state.list("phase", PhaseExecution, execution_id=execution_id)
            validations = services.state.list(
                "validation", ValidationResult, execution_id=execution_id
            )
            findings = services.state.list("finding", Finding, execution_id=execution_id)
            gate = (
                services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
                if execution.gate_evaluation_id
                else None
            )
            decision = (
                services.state.get("decision", execution.human_decision_id, HumanDecision)
                if execution.human_decision_id
                else None
            )
            retrospectives = services.state.list(
                "retrospective", Retrospective, execution_id=execution_id
            )
            events = services.events.list(execution_id)
            metrics = MetricsProjector(services.state).project(execution_id, events)
            reporter = TraceReporter()
            kwargs = dict(
                execution=execution,
                task=task,
                phases=phases,
                validations=validations,
                findings=findings,
                gate=gate,
                decision=decision,
                events=events,
                metrics=metrics,
                retrospective=retrospectives[-1] if retrospectives else None,
            )
            if format == "json":
                return reporter.render_json(**kwargs)
            if format == "sarif":
                return reporter.render_sarif(findings)
            if format == "jsonl":
                return services.events.export_jsonl(execution_id)
            if format != "markdown":
                raise ConfigurationError(f"unsupported trace format: {format}")
            return reporter.render_markdown(**kwargs)

    def gc(self, path: Path, *, apply: bool = False) -> dict[str, Any]:
        """Apply ``retention.artifactDays`` and ``retention.eventDays`` to the runs that
        ended; without ``apply`` only report what would be removed."""
        with self._services(path) as services, self._leased(services, "gc"):
            collector = RetentionCollector(services)
            plan = collector.plan()
            if apply:
                collector.apply(plan)
            return plan.as_dict(applied=apply)

    def list_evidence(self, path: Path, execution_id: str) -> list[dict[str, Any]]:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            evidence = services.state.list_dicts("evidence", execution_id=execution_id)
            artifacts = services.state.list_dicts("artifact", execution_id=execution_id)
            return [{"evidence": evidence, "artifacts": artifacts}]

    def list_findings(self, path: Path, execution_id: str) -> list[Finding]:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            return services.state.list("finding", Finding, execution_id=execution_id)

    def retrospect(self, path: Path, execution_id: str) -> Retrospective:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            records = services.state.list("retrospective", Retrospective, execution_id=execution_id)
            if records:
                return records[-1]
            return self._generate_retrospective(services, execution_id, "ON_DEMAND")

    @staticmethod
    def _generate_retrospective(
        services: EngineServices, execution_id: str, trigger: RetrospectiveTrigger
    ) -> Retrospective:
        """A retrospective outside CLOSURE: on demand, or (``retrospective.causal``) when a run
        is rejected or cancelled. Stored as a record; the run's event chain is not extended."""
        engine = RunEngine(services)
        execution = services.state.get("execution", execution_id, Execution)
        events = services.events.list(execution_id)
        metrics = MetricsProjector(services.state).project(execution_id, events)
        trace_ref = services.artifacts.put(
            services.events.export_jsonl(execution_id),
            media_type="application/x-ndjson",
            metadata={"kind": "retrospective-input"},
        )
        analysis = engine.cause_analysis(execution)
        retro = engine.retrospective_engine.generate(
            execution_id=execution_id,
            metrics=metrics,
            evidence_refs=(trace_ref.uri,),
            provenance=engine._provenance(execution),
            analysis=analysis,
            trigger=trigger if analysis is not None else None,
        )
        services.state.put(
            "retrospective",
            retro.retrospective_id,
            retro,
            execution_id=execution_id,
            project_id=execution.project_id,
        )
        return retro

    def add_memory(
        self,
        path: Path,
        *,
        level: MemoryLevel,
        key: str,
        value: dict[str, Any],
        actor_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        valid_until: datetime | None = None,
        supersedes: str | None = None,
        sensitive: bool = False,
        approved: bool = False,
    ) -> MemoryRecord:
        """Record a memory entry. Normative, project and retrospective entries only enter a
        context once approved; the approval is an explicit act of the recorded actor."""
        with self._services(path) as services:
            actor_id, display_name = self._decider(services, actor_id)
            if approved:
                require_human_actor(actor_id, "approve a memory record")
            store = MemoryStore(services.state)
            project_id = services.resolved.project.project_id
            if not key.strip():
                raise ConfigurationError("a memory record requires a non-empty key")
            if level is MemoryLevel.TASK and not task_id:
                raise ConfigurationError("task memory requires the task it belongs to")
            if level is MemoryLevel.EPHEMERAL and not execution_id:
                raise ConfigurationError("ephemeral memory requires the run it belongs to")
            if task_id:
                services.state.get("task", task_id, Task)
            if execution_id:
                services.state.get("execution", execution_id, Execution)
            if supersedes:
                self._project_memory(store, supersedes, project_id)
            record = MemoryRecord(
                memory_id=new_id("mem"),
                project_id=project_id,
                task_id=task_id,
                execution_id=execution_id,
                level=level,
                key=key.strip(),
                value=value,
                provenance=self._human_provenance(
                    actor_id,
                    source_refs=(supersedes,) if supersedes else (),
                    display_name=display_name,
                ),
                valid_until=valid_until,
                supersedes=supersedes,
                sensitive=sensitive,
                approved=approved,
            )
            store.put(record)
            return record

    def list_memory(
        self, path: Path, *, task_id: str | None = None, execution_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List every memory record of the project with its status for the given task and run:
        active, superseded, expired, unapproved, limit or out_of_scope."""
        with self._services(path) as services:
            store = MemoryStore(services.state)
            project_id = services.resolved.project.project_id
            selection = store.select(
                project_id=project_id, task_id=task_id, execution_id=execution_id
            )
            active = {item.memory_id for item in selection.records}
            excluded = {item.memory_id: item for item in selection.exclusions}
            listing: list[dict[str, Any]] = []
            for record in sorted(
                store.list_project(project_id), key=lambda item: (item.created_at, item.memory_id)
            ):
                entry: dict[str, Any] = {"record": record.model_dump(mode="json", by_alias=True)}
                if record.memory_id in active:
                    entry["status"] = "active"
                elif record.memory_id in excluded:
                    exclusion = excluded[record.memory_id]
                    entry["status"] = exclusion.reason
                    if exclusion.superseded_by:
                        entry["supersededBy"] = exclusion.superseded_by
                else:
                    entry["status"] = "out_of_scope"
                listing.append(entry)
            return listing

    def memory_manifest(self, path: Path, execution_id: str) -> dict[str, Any]:
        """Return the context manifest recorded for a run: the memory it applied and the
        candidates it left out, as they were when the run was planned."""
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            services.state.get("execution", execution_id, Execution)
            reference = services.state.get_flag(f"context:{execution_id}")
            if not reference:
                raise NotFoundError(f"run has no context manifest: {execution_id}")
            manifest: dict[str, Any] = json.loads(services.artifacts.get(reference))
            return {"executionId": execution_id, "manifestRef": reference, **manifest}

    def approve_memory(
        self, path: Path, *, memory_id: str, actor_id: str | None = None
    ) -> MemoryRecord:
        """Approve a normative, project or retrospective record. The approval is a new record
        that supersedes the proposal, so the proposal and its approver both stay on record."""
        with self._services(path) as services:
            actor_id, display_name = self._decider(services, actor_id)
            require_human_actor(actor_id, "approve a memory record")
            store = MemoryStore(services.state)
            target = self._project_memory(store, memory_id, services.resolved.project.project_id)
            if target.level not in APPROVAL_REQUIRED:
                raise PolicyViolationError(
                    f"{target.level.value} memory does not require approval: {memory_id}"
                )
            if target.approved:
                raise PolicyViolationError(f"memory record is already approved: {memory_id}")
            record = target.model_copy(
                update={
                    "memory_id": new_id("mem"),
                    "created_at": datetime.now(UTC),
                    "supersedes": target.memory_id,
                    "approved": True,
                    "provenance": self._human_provenance(
                        actor_id, source_refs=(target.memory_id,), display_name=display_name
                    ),
                }
            )
            store.put(record)
            return record

    def invalidate_memory(
        self, path: Path, *, memory_id: str, actor_id: str | None = None, reason: str
    ) -> MemoryRecord:
        """Invalidate a record without deleting it: an approved, already expired record
        supersedes it and keeps who invalidated it and why."""
        with self._services(path) as services:
            actor_id, display_name = self._decider(services, actor_id)
            require_human_actor(actor_id, "invalidate a memory record")
            store = MemoryStore(services.state)
            target = self._project_memory(store, memory_id, services.resolved.project.project_id)
            if not reason.strip():
                raise ConfigurationError("invalidating a memory record requires a reason")
            now = datetime.now(UTC)
            record = MemoryRecord(
                memory_id=new_id("mem"),
                project_id=target.project_id,
                task_id=target.task_id,
                execution_id=target.execution_id,
                level=target.level,
                key=target.key,
                value={"invalidated": True, "reason": reason.strip()},
                provenance=self._human_provenance(
                    actor_id, source_refs=(target.memory_id,), display_name=display_name
                ),
                created_at=now,
                valid_until=now,
                supersedes=target.memory_id,
                approved=True,
            )
            store.put(record)
            return record

    def list_recommendations(self, path: Path, execution_id: str) -> list[dict[str, Any]]:
        """List the recommendations of a run with the decision recorded for each, if any."""
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            retrospective = self._retrospective(services, execution_id)
            decided = {
                item.key: item
                for item in MemoryStore(services.state).list_project(
                    services.resolved.project.project_id
                )
                if item.level is MemoryLevel.RETROSPECTIVE
            }
            listing: list[dict[str, Any]] = []
            for recommendation in retrospective.recommendations:
                record = decided.get(self._recommendation_key(recommendation.recommendation_id))
                listing.append(
                    {
                        "recommendation": recommendation.model_dump(mode="json", by_alias=True),
                        "decision": record.value.get("decision") if record else None,
                        "memoryId": record.memory_id if record else None,
                    }
                )
            return listing

    def decide_recommendation(
        self,
        path: Path,
        *,
        execution_id: str,
        recommendation_id: str,
        decision: RecommendationDecision,
        actor_id: str | None = None,
        rationale: str,
        statement: str | None = None,
    ) -> MemoryRecord:
        """Record the decision of a person on a retrospective recommendation.

        The decision is kept as retrospective memory. An accepted or edited recommendation is
        approved and enters the context of later runs; a rejected one is kept as history and
        never enters a context. Nothing else changes: rules, gates and configuration are only
        modified by a person through a versioned change."""
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            actor_id, display_name = self._decider(services, actor_id)
            require_human_actor(actor_id, "decide on a recommendation")
            retrospective = self._retrospective(services, execution_id)
            recommendation = next(
                (
                    item
                    for item in retrospective.recommendations
                    if item.recommendation_id == recommendation_id
                ),
                None,
            )
            if recommendation is None:
                raise NotFoundError(f"recommendation not found in run: {recommendation_id}")
            if not rationale.strip():
                raise ConfigurationError("a decision on a recommendation requires a rationale")
            edited = statement.strip() if statement else ""
            if decision is RecommendationDecision.EDIT and not edited:
                raise ConfigurationError("EDIT requires the edited statement")
            if decision is not RecommendationDecision.EDIT and edited:
                raise ConfigurationError("only EDIT takes an edited statement")
            store = MemoryStore(services.state)
            project_id = services.resolved.project.project_id
            key = self._recommendation_key(recommendation_id)
            if any(item.key == key for item in store.list_project(project_id)):
                raise PolicyViolationError(
                    f"recommendation already has a decision: {recommendation_id}"
                )
            now = datetime.now(UTC)
            value: dict[str, Any] = {
                "recommendationId": recommendation.recommendation_id,
                "executionId": execution_id,
                "category": recommendation.category,
                "statement": edited or recommendation.statement,
                "rationale": recommendation.rationale,
                "decision": decision.value,
                "decisionRationale": rationale.strip(),
            }
            if edited:
                value["originalStatement"] = recommendation.statement
            rejected = decision is RecommendationDecision.REJECT
            record = MemoryRecord(
                memory_id=new_id("mem"),
                project_id=project_id,
                level=MemoryLevel.RETROSPECTIVE,
                key=key,
                value=value,
                provenance=self._human_provenance(
                    actor_id,
                    source_refs=(retrospective.retrospective_id,),
                    display_name=display_name,
                ),
                created_at=now,
                valid_until=now if rejected else None,
                approved=not rejected,
            )
            store.put(record)
            return record

    def doctor(self, path: Path | None = None, *, install_hooks: bool = False) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "python": {"status": "PASSED", "version": sys.version.split()[0]},
            "git": {"status": "PASSED" if shutil.which("git") else "FAILED"},
            "node": {"status": "PASSED" if shutil.which("node") else "NOT_APPLICABLE"},
            "npm": {"status": "PASSED" if shutil.which("npm") else "NOT_APPLICABLE"},
        }
        if checks["git"]["status"] == "FAILED":
            checks["git"]["hint"] = "Install Git: the harness records baselines from Git."
        checks["gitIdentity"] = git_identity(path if path and path.is_dir() else None)
        if path is not None:
            try:
                resolved = ConfigurationResolver().resolve(path)
                checks["configuration"] = {
                    "status": "PASSED",
                    "projectId": resolved.project.project_id,
                    "profiles": [item.profile_id for item in resolved.profiles],
                }
                harness_dir = resolved.workspace_root / ".harness"
                checks["filesystem"] = {
                    "status": "PASSED" if os.access(harness_dir, os.W_OK) else "FAILED",
                    "path": str(harness_dir),
                }
                if checks["filesystem"]["status"] == "FAILED":
                    checks["filesystem"]["hint"] = f"Make {harness_dir} writable by this user."
                checks.update(repository_checks(resolved.workspace_root))
                checks.update(provider_checks(resolved))
                checks["agentSandbox"] = sandbox_check(resolved)
                checks["validators"] = validator_checks(resolved)
                checks.update(self._ladder_checks(resolved, install_hooks=install_hooks))
            except Exception as error:
                checks["configuration"] = {
                    "status": "FAILED",
                    "message": str(error),
                    "hint": default_hint(error)
                    or "Fix .harness/project.yaml; `harness config validate` shows the error.",
                }
        status = (
            "PASSED"
            if all(item["status"] not in {"FAILED", "ERROR"} for item in checks.values())
            else "FAILED"
        )
        return {"status": status, "version": __version__, "checks": checks}

    @staticmethod
    def _ladder_checks(resolved: Any, *, install_hooks: bool) -> dict[str, Any]:
        """The run registry, the environment preflight and the instruction files (#55)."""
        from governed_harness.application.ladder import config_lint
        from governed_harness.orchestration.ladder_environment import environment_checks
        from governed_harness.runtime.state_location import resolve_state_location

        checks: dict[str, Any] = {}
        project = resolved.project
        workspace = resolved.workspace_root
        if project.runtime.state_dir is not None:
            location = resolve_state_location(
                workspace, project.project_id, project.runtime.state_dir, create=False
            )
            root = location.root
            writable = (
                os.access(root if root.exists() else root.parent, os.W_OK)
                or not root.parent.exists()
            )
            checks["stateDir"] = {
                "status": "PASSED" if writable else "FAILED",
                "path": str(root),
            }
            if not writable:
                checks["stateDir"]["hint"] = f"Make {root} writable, or set runtime.stateDir."
        environment = project.environment
        if environment is not None:
            hooks = environment.git_hooks
            if install_hooks and hooks and hooks.install:
                import subprocess

                completed = subprocess.run(  # nosec B603 - the project's own declared command
                    list(hooks.install),
                    cwd=workspace,
                    capture_output=True,
                    check=False,
                    timeout=600,
                    stdin=subprocess.DEVNULL,
                )
                checks["hooksInstall"] = {
                    "status": "PASSED" if completed.returncode == 0 else "FAILED",
                    "command": list(hooks.install),
                    "message": completed.stderr.decode("utf-8", "replace").strip()[-300:],
                }
            report = environment_checks(environment, workspace)
            problems = report["problems"]
            checks["environment"] = {
                "status": "FAILED" if problems else "PASSED",
                "message": "; ".join(problems[:3]) if problems else None,
                "tools": report["tools"],
                "variables": report["variables"],
                "gitHooks": report["gitHooks"],
                "dirtyTree": report["dirtyTree"],
            }
            if problems:
                checks["environment"]["hint"] = (
                    "Install the tools and set the variables the project declares in "
                    "environment; `harness doctor --install-hooks` runs the declared hook "
                    "installation."
                )
        if project.instructions is not None:
            lint = config_lint(resolved)
            checks["instructions"] = {
                "status": "PASSED" if lint["status"] == "PASSED" else "WARNING",
                "message": f"{len(lint['issues'])} issue(s) in {len(lint['files'])} file(s)",
            }
            if lint["issues"]:
                checks["instructions"]["hint"] = "`harness config lint` lists them."
        return checks

    @staticmethod
    @contextmanager
    def _leased(services: EngineServices, command: str) -> Iterator[WorkspaceLease | None]:
        """Hold the workspace lease while a command executes phases or records a decision
        (``governance.workspaceLease``); a second process gets exit code 5. ``SIGTERM`` then
        stops the command like Ctrl-C, so the runner terminates the agent's process group and
        the phase is recorded as interrupted."""
        if not services.resolved.project.governance_settings.workspace_lease:
            yield None
            return
        lease = WorkspaceLease(services.paths.harness_dir, command).acquire()
        try:
            with interruptible():
                yield lease
        finally:
            lease.release()

    def resolve_run(self, path: Path, reference: str) -> str:
        """The run id a reference names: an exact id, ``latest`` or a unique prefix."""
        with self._services(path) as services:
            return self._run_id(services, reference)

    def review(
        self, path: Path, execution_id: str, *, include_diff: bool = False
    ) -> dict[str, Any]:
        """The decision brief of a run (see ``application.review``)."""
        with self._run_services(path, execution_id) as (services, run_id):
            execution = services.state.get("execution", run_id, Execution)
            return build_brief(
                services,
                run_id,
                include_diff=include_diff,
                exceptions=brief_exceptions(services, execution),
            )

    def list_exceptions(
        self, path: Path, *, status: str = "all", expiring_within: int | None = None
    ) -> list[dict[str, Any]]:
        """The exceptions of the project with their status (ACTIVE or EXPIRED), days left and
        the runs whose gate relied on them."""
        if status.lower() not in {"all", "active", "expired"}:
            raise ConfigurationError(f"unknown exception status: {status}")
        with self._services(path) as services:
            return list_exceptions(services, status=status.lower(), expiring_within=expiring_within)

    def show_artifact(self, path: Path, reference: str) -> tuple[dict[str, Any], bytes]:
        """An artifact's descriptor and its content, read with digest verification. The
        reference is ``artifact://sha256/<hex>``, ``sha256:<hex>`` or a unique hex prefix of at
        least 6 characters."""
        with self._services(path) as services:
            uri = self._artifact_uri(services, reference.strip())
            descriptor = services.artifacts.describe(uri)
            data = services.artifacts.get(uri)
            return {
                "uri": descriptor.uri,
                "digest": descriptor.digest,
                "sizeBytes": descriptor.size_bytes,
                "mediaType": descriptor.media_type,
                "redacted": descriptor.redacted,
                "verified": True,
                "metadata": descriptor.metadata or {},
            }, data

    @staticmethod
    def _artifact_uri(services: EngineServices, reference: str) -> str:
        hex_part = reference
        for prefix in ("artifact://sha256/", "sha256:"):
            if reference.startswith(prefix):
                hex_part = reference[len(prefix) :]
        hex_part = hex_part.lower()
        if len(hex_part) < 6 or any(char not in "0123456789abcdef" for char in hex_part):
            raise ConfigurationError(
                f"not an artifact reference or digest prefix (6+ hex characters): {reference}"
            )
        meta_root = services.artifacts.meta_root
        if len(hex_part) == 64:
            matches = [hex_part] if (meta_root / f"{hex_part}.json").exists() else []
        else:
            matches = sorted(item.stem for item in meta_root.glob(f"{hex_part}*.json"))
        if not matches:
            raise NotFoundError(f"artifact not found: {reference}")
        if len(matches) > 1:
            raise ConfigurationError(
                f"artifact prefix {reference} matches {len(matches)} artifacts; use more characters"
            )
        return f"artifact://sha256/{matches[0]}"

    @staticmethod
    def _run_id(services: EngineServices, reference: str) -> str:
        reference = reference.strip()
        try:
            services.state.get("execution", reference, Execution)
            return reference
        except NotFoundError:
            pass
        runs = services.state.list(
            "execution", Execution, project_id=services.resolved.project.project_id
        )
        if reference == "latest":
            if not runs:
                raise NotFoundError("execution not found: latest (the project has no runs)")
            return max(runs, key=lambda item: item.created_at).execution_id
        prefixes = (reference, f"run_{reference}")
        matches = sorted(
            item.execution_id for item in runs if item.execution_id.startswith(prefixes)
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ConfigurationError(
                f"run prefix {reference} matches {len(matches)} runs: {', '.join(matches[:5])}"
            )
        raise NotFoundError(f"execution not found: {reference}")

    @contextmanager
    def _services(self, path: Path) -> Iterator[EngineServices]:
        resolved = ConfigurationResolver().resolve(path)
        services = EngineServices.open(resolved)
        try:
            yield services
        finally:
            services.close()

    @staticmethod
    def _open_services(path: Path) -> EngineServices:
        return EngineServices.open(ConfigurationResolver().resolve(path))

    @contextmanager
    def _run_services(self, path: Path, reference: str) -> Iterator[tuple[EngineServices, str]]:
        """The services of a run's own workspace: the worktree of an isolated run (#55), else
        the workspace of ``path``."""
        with self._services(path) as services:
            run_id = self._run_id(services, reference)
            record = isolation_record(services, run_id)
            target = Path(services.state.get("execution", run_id, Execution).workspace)
            if (
                record is not None
                and record.get("status") == "CREATED"
                and target.is_dir()
                and target.resolve() != services.paths.workspace
            ):
                isolated = self._open_services(target)
                try:
                    yield isolated, run_id
                finally:
                    isolated.close()
                return
            yield services, run_id

    @staticmethod
    def _retrospective(services: EngineServices, execution_id: str) -> Retrospective:
        services.state.get("execution", execution_id, Execution)
        records = services.state.list("retrospective", Retrospective, execution_id=execution_id)
        if not records:
            raise NotFoundError(f"run has no retrospective yet: {execution_id}")
        return records[-1]

    @staticmethod
    def _recommendation_key(recommendation_id: str) -> str:
        return f"recommendation/{recommendation_id}"

    def _decider(
        self, services: EngineServices, actor_id: str | None, default: str = DEFAULT_CLI_ACTOR
    ) -> tuple[str, str | None]:
        """The actor id (and display name) of the person acting: the explicit ``--actor``, else
        the Git user under ``governance.deciderIdentity: git``, else ``default``.

        Where it came from is kept in ``last_identity_source``: ``explicit``, ``git``,
        ``fallback`` (the Git user was asked for but Git has no usable ``user.email`` or
        ``user.name``: the 1.0.0 default is used and a notice says so) or ``default``."""
        if actor_id:
            self.last_identity_source = "explicit"
            return actor_id, None
        if services.resolved.project.governance_settings.git_decider:
            name, email = GitAdapter(services.paths.workspace).user_identity()
            try:
                decider = actor_id_from_identity(name, email)
            except ConfigurationError:
                self.last_identity_source = "fallback"
                self.notices.append(
                    f"governance.deciderIdentity is git but Git has no usable user.email or "
                    f"user.name; recorded the actor as {default}. Set them with git config "
                    f"user.email / user.name, or pass --actor"
                )
                return default, None
            self.last_identity_source = "git"
            return decider, identity_display_name(name, email)
        self.last_identity_source = "default"
        return default, None

    @staticmethod
    def _human_provenance(
        actor_id: str, *, source_refs: tuple[str, ...] = (), display_name: str | None = None
    ) -> Provenance:
        return Provenance(
            actor=Actor(actor_type=ActorType.HUMAN, actor_id=actor_id, display_name=display_name),
            core_version=__version__,
            source_refs=source_refs,
        )

    @staticmethod
    def _project_memory(store: MemoryStore, memory_id: str, project_id: str) -> MemoryRecord:
        record = store.get(memory_id)
        if record.project_id != project_id:
            raise NotFoundError(f"memory not found: {memory_id}")
        return record

    @staticmethod
    def _count_by(items: list[Any], field: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            value = getattr(item, field)
            key = value.value if hasattr(value, "value") else str(value)
            counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def render_json(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", by_alias=True)
        elif isinstance(value, list):
            value = [
                item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item
                for item in value
            ]
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
