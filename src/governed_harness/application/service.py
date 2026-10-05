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
from governed_harness.configuration import ConfigurationResolver, RuntimeConfig, initialize_project
from governed_harness.configuration.models import ProjectConfiguration
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    MemoryLevel,
    RecommendationDecision,
)
from governed_harness.domain.errors import ConfigurationError, NotFoundError, PolicyViolationError
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
    PhaseExecution,
    Provenance,
    Retrospective,
    Task,
    ValidationResult,
)
from governed_harness.intake import task_digest
from governed_harness.memory import APPROVAL_REQUIRED, MemoryStore
from governed_harness.orchestration.engine import EngineServices, RunEngine
from governed_harness.profiles import detect_profiles
from governed_harness.reporting import TraceReporter
from governed_harness.telemetry import MetricsProjector

from .clarification_loader import load_clarification_file
from .hints import default_hint
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
        resolved = ConfigurationResolver().resolve(path)
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
        actor_id: str,
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
            record, task = RunEngine(services).clarify(
                task_id=task_id,
                clarification=clarification,
                actor=Actor(actor_type=actor_type, actor_id=actor_id),
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

    def start_run(self, path: Path, task_id: str, provider: str | None = None) -> Execution:
        with self._services(path) as services:
            engine = RunEngine(services)
            task = engine.get_task(task_id)
            execution = engine.create_execution(task, provider=provider)
            return engine.continue_execution(execution.execution_id)

    def continue_run(self, path: Path, execution_id: str) -> Execution:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            return RunEngine(services).continue_execution(execution_id)

    def cancel_run(self, path: Path, execution_id: str, actor_id: str) -> Execution:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            return RunEngine(services).cancel(execution_id, actor_id)

    def decide_gate(
        self,
        path: Path,
        *,
        execution_id: str,
        decision: DecisionKind,
        change_set_digest: str,
        actor_id: str,
        rationale: str,
        continue_after: bool = True,
    ) -> tuple[HumanDecision, Execution]:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
            engine = RunEngine(services)
            record = engine.decide(
                execution_id=execution_id,
                decision=decision,
                change_set_digest=change_set_digest,
                actor_id=actor_id,
                rationale=rationale,
            )
            execution = (
                engine.continue_execution(execution_id)
                if continue_after
                and decision in {DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION}
                else engine.get_execution(execution_id)
            )
            return record, execution

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
                "eventChainValid": services.events.verify_chain(execution_id),
                "metrics": {key: value.as_dict() for key, value in metrics.items()},
            }

    def list_runs(self, path: Path) -> list[Execution]:
        with self._services(path) as services:
            return services.state.list(
                "execution",
                Execution,
                project_id=services.resolved.project.project_id,
                newest_first=True,
            )

    def trace(self, path: Path, execution_id: str, format: str = "markdown") -> bytes:
        with self._services(path) as services:
            execution_id = self._run_id(services, execution_id)
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
            execution = services.state.get("execution", execution_id, Execution)
            events = services.events.list(execution_id)
            metrics = MetricsProjector(services.state).project(execution_id, events)
            trace_ref = services.artifacts.put(
                services.events.export_jsonl(execution_id),
                media_type="application/x-ndjson",
                metadata={"kind": "retrospective-input"},
            )
            retro = RunEngine(services).retrospective_engine.generate(
                execution_id=execution_id,
                metrics=metrics,
                evidence_refs=(trace_ref.uri,),
                provenance=RunEngine(services)._provenance(execution),
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
        actor_id: str,
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
                    actor_id, source_refs=(supersedes,) if supersedes else ()
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

    def approve_memory(self, path: Path, *, memory_id: str, actor_id: str) -> MemoryRecord:
        """Approve a normative, project or retrospective record. The approval is a new record
        that supersedes the proposal, so the proposal and its approver both stay on record."""
        with self._services(path) as services:
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
                    "provenance": self._human_provenance(actor_id, source_refs=(target.memory_id,)),
                }
            )
            store.put(record)
            return record

    def invalidate_memory(
        self, path: Path, *, memory_id: str, actor_id: str, reason: str
    ) -> MemoryRecord:
        """Invalidate a record without deleting it: an approved, already expired record
        supersedes it and keeps who invalidated it and why."""
        with self._services(path) as services:
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
                provenance=self._human_provenance(actor_id, source_refs=(target.memory_id,)),
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
        actor_id: str,
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
                    actor_id, source_refs=(retrospective.retrospective_id,)
                ),
                created_at=now,
                valid_until=now if rejected else None,
                approved=not rejected,
            )
            store.put(record)
            return record

    def doctor(self, path: Path | None = None) -> dict[str, Any]:
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

    def resolve_run(self, path: Path, reference: str) -> str:
        """The run id a reference names: an exact id, ``latest`` or a unique prefix."""
        with self._services(path) as services:
            return self._run_id(services, reference)

    def review(
        self, path: Path, execution_id: str, *, include_diff: bool = False
    ) -> dict[str, Any]:
        """The decision brief of a run (see ``application.review``)."""
        with self._services(path) as services:
            run_id = self._run_id(services, execution_id)
            return build_brief(services, run_id, include_diff=include_diff)

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
    def _retrospective(services: EngineServices, execution_id: str) -> Retrospective:
        services.state.get("execution", execution_id, Execution)
        records = services.state.list("retrospective", Retrospective, execution_id=execution_id)
        if not records:
            raise NotFoundError(f"run has no retrospective yet: {execution_id}")
        return records[-1]

    @staticmethod
    def _recommendation_key(recommendation_id: str) -> str:
        return f"recommendation/{recommendation_id}"

    @staticmethod
    def _human_provenance(actor_id: str, *, source_refs: tuple[str, ...] = ()) -> Provenance:
        return Provenance(
            actor=Actor(actor_type=ActorType.HUMAN, actor_id=actor_id),
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
