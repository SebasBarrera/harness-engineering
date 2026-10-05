"""Line coverage threshold of a Python project (``policies.coverage.minimumPercent`` under
``governance.applyProfilePolicies``).

The Python profile declares ``coverage: optional_for_research_prototype``, which no component
read. A project that sets ``coverage: {minimumPercent: N}`` gets a mandatory validator,
``python.coverage``, that runs the test suite under ``coverage.py`` and fails when the total line
coverage is below ``N`` percent. It runs the tests a second time (after ``python.pytest``) and
needs the ``coverage`` package in the project's interpreter; without it the validator is
``BLOCKED`` (or the ``missingTestCommand`` status).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from governed_harness.configuration.models import ValidatorDefinition
from governed_harness.configuration.policies import coverage_minimum
from governed_harness.validators.base import ValidationContext, ValidatorOutput
from governed_harness.validators.command import CommandValidator

COVERAGE_VALIDATOR_ID = "python.coverage"
COVERAGE_TIMEOUT_SECONDS = 900


__all__ = ["COVERAGE_VALIDATOR_ID", "CoverageValidator", "coverage_minimum"]


class CoverageValidator:
    validator_id = COVERAGE_VALIDATOR_ID

    def __init__(self, minimum: float) -> None:
        self.minimum = minimum

    def definition(self) -> ValidatorDefinition:
        return ValidatorDefinition(
            id=self.validator_id,
            command=("python", "-m", "coverage", "run", "-m", "pytest", "-q"),
            mandatory=True,
            timeoutSeconds=COVERAGE_TIMEOUT_SECONDS,
        )

    def execute(self, context: ValidationContext, *, data_file: Path) -> ValidatorOutput:
        """Run the tests under coverage, then the report with ``--fail-under``. The result is
        the report's (or the first step's when the tests themselves did not pass)."""
        data = str(data_file)
        run_definition = context.definition.model_copy(
            update={
                "command": (
                    "python",
                    "-m",
                    "coverage",
                    "run",
                    f"--data-file={data}",
                    "-m",
                    "pytest",
                    "-q",
                )
            }
        )
        first = CommandValidator(self.validator_id).execute(
            replace(context, definition=run_definition)
        )
        if not first.result.status.successful:
            return first
        report_definition = context.definition.model_copy(
            update={
                "command": (
                    "python",
                    "-m",
                    "coverage",
                    "report",
                    f"--data-file={data}",
                    f"--fail-under={self.minimum:g}",
                )
            }
        )
        second = CommandValidator(self.validator_id).execute(
            replace(context, definition=report_definition)
        )
        result = second.result.model_copy(
            update={
                "summary": (
                    f"line coverage reached {self.minimum:g}%"
                    if second.result.status.successful
                    else f"line coverage below {self.minimum:g}% ({second.result.summary})"
                ),
                "evidence_refs": first.result.evidence_refs + second.result.evidence_refs,
                "tool_invocation_ids": first.result.tool_invocation_ids
                + second.result.tool_invocation_ids,
                "started_at": first.result.started_at,
            }
        )
        return ValidatorOutput(
            result, second.findings, first.tool_invocations + second.tool_invocations
        )
