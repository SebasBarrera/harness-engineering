from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import EvidenceKind
from governed_harness.domain.models import Evidence
from governed_harness.orchestration.engine import EngineServices
from governed_harness.validators import RequirementTraceabilityReport

SCHEMA_DIR = Path(__file__).parents[2] / "schemas" / "v1"
RESOURCE_DIR = (
    Path(__file__).parents[2] / "src" / "governed_harness" / "resources" / "schemas" / "v1"
)
NAME = "requirement-traceability.schema.json"


def _schema(name: str) -> dict[str, object]:
    value: dict[str, object] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return value


def _validate(name: str, value: object) -> None:
    Draft202012Validator(_schema(name), format_checker=FormatChecker()).validate(value)


def test_report_schema_is_published_and_packaged() -> None:
    Draft202012Validator.check_schema(_schema(NAME))
    assert (RESOURCE_DIR / NAME).read_bytes() == (SCHEMA_DIR / NAME).read_bytes()


def test_project_schema_accepts_the_verification_section_and_its_absence(
    python_workspace: Path,
) -> None:
    value = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    _validate("project-config.schema.json", value)
    assert value["verification"] == {"requirementTraceability": "enforce", "outputParsers": True}
    del value["verification"]
    _validate("project-config.schema.json", value)
    with pytest.raises(ValidationError):
        _validate(
            "project-config.schema.json",
            {**value, "verification": {"requirementTraceability": "sometimes"}},
        )


def test_persisted_report_matches_the_public_schema(python_workspace: Path, tmp_path: Path) -> None:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(
        "title: Discount\n"
        "intent: Apply a percentage discount only when the subtotal reaches the threshold.\n"
        "requirements:\n"
        "  - 'A1. A subtotal below the threshold is unchanged.'\n"
        "  - Keep the signature.\n"
        "acceptanceCriteria:\n"
        "  - apply_discount(99, 100, 0.1) returns 99.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "\n"
        "        def test_a1_unchanged_below() -> None:\n"
        "            assert apply_discount(99, 100, 0.1) == 99\n",
        encoding="utf-8",
    )
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    run = application.start_run(python_workspace, task.task_id)
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        (evidence,) = [
            item
            for item in services.state.list("evidence", Evidence, execution_id=run.execution_id)
            if item.kind is EvidenceKind.TEST_REPORT
        ]
        report = json.loads(services.artifacts.get(evidence.artifact_ref))
    finally:
        services.close()
    _validate(NAME, report)
    assert RequirementTraceabilityReport.model_validate(report).traced_count == 1
    assert report["skippedCount"] == 1
