from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from governed_harness import __version__
from governed_harness.configuration import ConfigurationResolver, initialize_project
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import ConfigurationError, NotFoundError
from governed_harness.domain.models import (
    Artifact,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Retrospective,
    Task,
    ValidationResult,
)
from governed_harness.orchestration.engine import EngineServices, RunEngine
from governed_harness.profiles import detect_profiles
from governed_harness.reporting import TraceReporter
from governed_harness.telemetry import MetricsProjector

from .task_loader import load_task_file


class HarnessApplication:
    def init(self, path: Path, *, force: bool = False) -> dict[str, Any]:
        config = initialize_project(path, force=force)
        return {"status": "PASSED", "configuration": str(config)}

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
            "capabilities": [item.model_dump(mode="json", by_alias=True) for item in resolved.effective_capabilities],
            "policies": resolved.effective_policies,
        }

    def create_task(self, path: Path, source: Path) -> Task:
        with self._services(path) as services:
            task = load_task_file(source, project_id=services.resolved.project.project_id)
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

    def start_run(self, path: Path, task_id: str, provider: str | None = None) -> Execution:
        with self._services(path) as services:
            engine = RunEngine(services)
            task = engine.get_task(task_id)
            execution = engine.create_execution(task, provider=provider)
            return engine.continue_execution(execution.execution_id)

    def continue_run(self, path: Path, execution_id: str) -> Execution:
        with self._services(path) as services:
            return RunEngine(services).continue_execution(execution_id)

    def cancel_run(self, path: Path, execution_id: str, actor_id: str) -> Execution:
        with self._services(path) as services:
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
                if continue_after and decision in {DecisionKind.APPROVE, DecisionKind.APPROVE_EXCEPTION}
                else engine.get_execution(execution_id)
            )
            return record, execution

    def status(self, path: Path, execution_id: str) -> dict[str, Any]:
        with self._services(path) as services:
            execution = services.state.get("execution", execution_id, Execution)
            phases = services.state.list("phase", PhaseExecution, execution_id=execution_id)
            validations = services.state.list("validation", ValidationResult, execution_id=execution_id)
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
                "humanDecision": decision.model_dump(mode="json", by_alias=True) if decision else None,
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
            execution = services.state.get("execution", execution_id, Execution)
            task = services.state.get("task", execution.task_id, Task)
            phases = services.state.list("phase", PhaseExecution, execution_id=execution_id)
            validations = services.state.list("validation", ValidationResult, execution_id=execution_id)
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
            evidence = services.state.list_dicts("evidence", execution_id=execution_id)
            artifacts = services.state.list_dicts("artifact", execution_id=execution_id)
            return [{"evidence": evidence, "artifacts": artifacts}]

    def list_findings(self, path: Path, execution_id: str) -> list[Finding]:
        with self._services(path) as services:
            return services.state.list("finding", Finding, execution_id=execution_id)

    def retrospect(self, path: Path, execution_id: str) -> Retrospective:
        with self._services(path) as services:
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

    def doctor(self, path: Path | None = None) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "python": {"status": "PASSED", "version": sys.version.split()[0]},
            "git": {"status": "PASSED" if shutil.which("git") else "FAILED"},
            "node": {"status": "PASSED" if shutil.which("node") else "NOT_APPLICABLE"},
            "npm": {"status": "PASSED" if shutil.which("npm") else "NOT_APPLICABLE"},
        }
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
            except Exception as error:
                checks["configuration"] = {"status": "FAILED", "message": str(error)}
        status = "PASSED" if all(item["status"] not in {"FAILED", "ERROR"} for item in checks.values()) else "FAILED"
        return {"status": status, "version": __version__, "checks": checks}

    @contextmanager
    def _services(self, path: Path) -> Iterator[EngineServices]:
        resolved = ConfigurationResolver().resolve(path)
        services = EngineServices.open(resolved)
        try:
            yield services
        finally:
            services.close()

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
            value = [item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item for item in value]
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
