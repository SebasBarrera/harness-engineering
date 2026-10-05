#!/usr/bin/env python3
"""Generate public JSON Schema contracts from the typed boundary models.

The event envelope and common shared definitions are maintained manually because the
SQLite event representation is deliberately not a Pydantic domain entity.  Every
other generated schema is deterministic and copied into package resources so source
checkouts and installed wheels expose the same contracts.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from governed_harness.configuration.models import ProjectConfiguration, WorkflowDefinition
from governed_harness.domain.models import (
    AgentInvocation,
    Artifact,
    CapabilityGrant,
    ChangeSet,
    ClarificationRecord,
    ClarificationRequest,
    Evidence,
    ExceptionRecord,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    MemoryRecord,
    PhaseExecution,
    Plan,
    ProviderFeedback,
    Recommendation,
    ResourceUsage,
    Retrospective,
    Task,
    ToolInvocation,
    ValidationResult,
)
from governed_harness.plugins.protocol import PluginDescriptor, PluginRequest, PluginResponse
from governed_harness.validators.traceability import RequirementTraceabilityReport

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "v1"
RESOURCE_ROOT = ROOT / "src" / "governed_harness" / "resources" / "schemas" / "v1"
type SchemaModel = type[BaseModel]

MODELS: dict[str, SchemaModel] = {
    "agent-invocation.schema.json": AgentInvocation,
    "artifact.schema.json": Artifact,
    "capability-grant.schema.json": CapabilityGrant,
    "change-set.schema.json": ChangeSet,
    "clarification-record.schema.json": ClarificationRecord,
    "clarification-request.schema.json": ClarificationRequest,
    "evidence.schema.json": Evidence,
    "exception.schema.json": ExceptionRecord,
    "execution.schema.json": Execution,
    "finding.schema.json": Finding,
    "gate-evaluation.schema.json": GateEvaluation,
    "human-decision.schema.json": HumanDecision,
    "memory-record.schema.json": MemoryRecord,
    "phase-result.schema.json": PhaseExecution,
    "plan.schema.json": Plan,
    "plugin-descriptor.schema.json": PluginDescriptor,
    "plugin-request.schema.json": PluginRequest,
    "plugin-response.schema.json": PluginResponse,
    "project-config.schema.json": ProjectConfiguration,
    "provider-feedback.schema.json": ProviderFeedback,
    "requirement-traceability.schema.json": RequirementTraceabilityReport,
    "resource-usage.schema.json": ResourceUsage,
    "retrospective-recommendation.schema.json": Recommendation,
    "retrospective.schema.json": Retrospective,
    "task.schema.json": Task,
    "tool-invocation.schema.json": ToolInvocation,
    "validation-result.schema.json": ValidationResult,
    "workflow.schema.json": WorkflowDefinition,
}


def generate() -> None:
    SCHEMA_ROOT.mkdir(parents=True, exist_ok=True)
    RESOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    for filename, model in MODELS.items():
        schema = model.model_json_schema(by_alias=True, ref_template="#/$defs/{model}")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://example.org/governed-harness/schemas/v1/{filename}"
        data = (json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
        (SCHEMA_ROOT / filename).write_bytes(data)
        (RESOURCE_ROOT / filename).write_bytes(data)
    for filename in ("common.schema.json", "event.schema.json"):
        data = (SCHEMA_ROOT / filename).read_bytes()
        (RESOURCE_ROOT / filename).write_bytes(data)


if __name__ == "__main__":
    generate()
