from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.models import (
    AgentInvocation,
    Artifact,
    ChangeSet,
    Evidence,
    Execution,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Plan,
    Retrospective,
    Task,
    ToolInvocation,
    ValidationResult,
)
from governed_harness.orchestration.engine import EngineServices

SCHEMA_DIR = Path(__file__).parents[2] / "schemas" / "v1"


def _validate(schema_name: str, value: dict[str, object]) -> None:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _first[T: BaseModel](
    services: EngineServices, record_type: str, model: type[T], execution_id: str
) -> T:
    records = services.state.list(record_type, model, execution_id=execution_id)
    assert records, f"missing {record_type} record"
    return records[0]


def test_persisted_vertical_slice_records_match_public_schemas(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(
        "title: Add threshold behavior\n"
        "intent: Apply the configured discount at the threshold.\n"
        "requirements:\n"
        "  - A subtotal equal to the threshold is reduced by the rate.\n"
        "acceptanceCriteria:\n"
        "  - The threshold is inclusive.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/sample/pricing.py\n"
        "      operation: replace\n"
        "      content: |\n"
        "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
        "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        def test_threshold_is_inclusive() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file)
    pending = app.start_run(python_workspace, task.task_id)
    assert pending.change_set_digest
    _, final = app.decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=pending.change_set_digest,
        actor_id="human.contract-test",
        rationale="Contract fixture validations passed",
    )

    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        execution_id = final.execution_id
        records: list[tuple[str, BaseModel]] = [
            ("task.schema.json", services.state.get("task", task.task_id, Task)),
            ("execution.schema.json", services.state.get("execution", execution_id, Execution)),
            ("phase-result.schema.json", _first(services, "phase", PhaseExecution, execution_id)),
            ("plan.schema.json", _first(services, "plan", Plan, execution_id)),
            ("change-set.schema.json", _first(services, "change_set", ChangeSet, execution_id)),
            (
                "validation-result.schema.json",
                _first(services, "validation", ValidationResult, execution_id),
            ),
            ("gate-evaluation.schema.json", _first(services, "gate", GateEvaluation, execution_id)),
            (
                "human-decision.schema.json",
                _first(services, "decision", HumanDecision, execution_id),
            ),
            (
                "agent-invocation.schema.json",
                _first(services, "agent_invocation", AgentInvocation, execution_id),
            ),
            (
                "tool-invocation.schema.json",
                _first(services, "tool_invocation", ToolInvocation, execution_id),
            ),
            ("evidence.schema.json", _first(services, "evidence", Evidence, execution_id)),
            ("artifact.schema.json", _first(services, "artifact", Artifact, execution_id)),
            (
                "retrospective.schema.json",
                _first(services, "retrospective", Retrospective, execution_id),
            ),
        ]
        for schema_name, record in records:
            _validate(schema_name, record.model_dump(mode="json", by_alias=True))
        for event in services.events.list(execution_id):
            _validate("event.schema.json", event.as_dict())
    finally:
        services.close()
