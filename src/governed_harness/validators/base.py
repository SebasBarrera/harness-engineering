from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from governed_harness.configuration.models import ValidatorDefinition
from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import (
    CapabilityGrant,
    ChangeSet,
    Finding,
    Provenance,
    Task,
    ToolInvocation,
    ValidationResult,
)
from governed_harness.evidence.artifact_store import LocalArtifactStore
from governed_harness.runtime.cancellation import CancellationToken
from governed_harness.runtime.process_runner import SafeProcessRunner


@dataclass(frozen=True)
class ValidationContext:
    execution_id: str
    workspace: Path
    task: Task
    change_set: ChangeSet
    definition: ValidatorDefinition
    grants: list[CapabilityGrant]
    artifact_store: LocalArtifactStore
    process_runner: SafeProcessRunner
    provenance: Provenance
    cancellation: CancellationToken
    max_output_bytes: int
    # Unredacted diff exists only in memory for security checks.  It must never be
    # persisted as an artifact or event payload.
    raw_diff: str | None = None
    # Status of an unavailable mandatory validator (governance.applyProfilePolicies):
    # missingTestCommand for a missing executable or Python module, missingTestScript for a
    # missing package script. None keeps BLOCKED.
    missing_command_status: ResultStatus | None = None
    missing_script_status: ResultStatus | None = None


@dataclass(frozen=True)
class ValidatorOutput:
    result: ValidationResult
    findings: tuple[Finding, ...] = ()
    tool_invocations: tuple[ToolInvocation, ...] = ()


class Validator(Protocol):
    validator_id: str

    def execute(self, context: ValidationContext) -> ValidatorOutput: ...
