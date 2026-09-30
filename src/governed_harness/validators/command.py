from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import UTC, datetime

from governed_harness.domain.enums import (
    ActorType,
    ErrorKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    Finding,
    FindingLocation,
    HarnessErrorRecord,
    ToolInvocation,
    ValidationResult,
)
from governed_harness.runtime.process_runner import CommandSpec
from governed_harness.validators.base import ValidationContext, ValidatorOutput


class CommandValidator:
    def __init__(self, validator_id: str) -> None:
        self.validator_id = validator_id

    def execute(self, context: ValidationContext) -> ValidatorOutput:
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{self.validator_id}", version="1")
        started = datetime.now(UTC)
        availability = self._availability(context)
        if availability is not None:
            status, reason = availability
            evidence = context.artifact_store.put_json(
                {"validatorId": self.validator_id, "status": status, "reason": reason},
                metadata={"kind": "validator-availability"},
            )
            result = ValidationResult(
                validation_result_id=new_id("validation"),
                execution_id=context.execution_id,
                validator_id=self.validator_id,
                change_set_digest=context.change_set.digest,
                status=status,
                kind=(
                    ValidationKind.CONFIGURATION_ERROR
                    if status is ResultStatus.BLOCKED
                    else ValidationKind.INCONCLUSIVE
                ),
                mandatory=context.definition.mandatory,
                summary=reason,
                evidence_refs=(evidence.uri,),
                started_at=started,
                finished_at=datetime.now(UTC),
                provenance=context.provenance.model_copy(update={"actor": actor}),
            )
            return ValidatorOutput(result)
        assert context.definition.command is not None
        process = context.process_runner.run(
            CommandSpec(
                argv=context.definition.command,
                cwd=context.workspace,
                timeout_seconds=float(context.definition.timeout_seconds or 900),
                max_output_bytes=context.max_output_bytes,
            ),
            actor=actor,
            grants=context.grants,
            cancellation=context.cancellation,
        )
        stdout = context.artifact_store.put(
            process.stdout,
            media_type="text/plain",
            metadata={"validatorId": self.validator_id, "stream": "stdout"},
        )
        stderr = context.artifact_store.put(
            process.stderr,
            media_type="text/plain",
            metadata={"validatorId": self.validator_id, "stream": "stderr"},
        )
        report = context.artifact_store.put_json(
            {
                "validatorId": self.validator_id,
                "argv": list(context.definition.command),
                "exitCode": process.exit_code,
                "status": process.status,
                "durationMs": process.duration_ms,
                "timedOut": process.timed_out,
                "cancelled": process.cancelled,
                "stdoutRef": stdout.uri,
                "stderrRef": stderr.uri,
                "stdoutTruncated": process.stdout_truncated,
                "stderrTruncated": process.stderr_truncated,
            },
            metadata={"kind": "validation-report", "validatorId": self.validator_id},
        )
        tool = ToolInvocation(
            invocation_id=new_id("tool"),
            execution_id=context.execution_id,
            phase_id=PhaseId.VERIFICATION,
            actor=actor,
            tool_id=self.validator_id,
            argv=context.definition.command,
            cwd=".",
            started_at=started,
            finished_at=datetime.now(UTC),
            status=process.status,
            exit_code=process.exit_code,
            timed_out=process.timed_out,
            cancelled=process.cancelled,
            stdout_ref=stdout.uri,
            stderr_ref=stderr.uri,
            provenance=context.provenance.model_copy(update={"actor": actor}),
        )
        errors: tuple[HarnessErrorRecord, ...] = ()
        findings: tuple[Finding, ...] = ()
        if process.status is ResultStatus.PASSED:
            kind = ValidationKind.SUCCESS
            summary = f"{self.validator_id} passed"
        elif process.status is ResultStatus.TIMED_OUT:
            kind = ValidationKind.TOOL_ERROR
            summary = f"{self.validator_id} timed out"
            errors = (
                HarnessErrorRecord(
                    error_id=new_id("err"),
                    kind=ErrorKind.TOOL_ERROR,
                    message=summary,
                    actor=actor,
                ),
            )
        elif process.status is ResultStatus.CANCELLED:
            kind = ValidationKind.INCONCLUSIVE
            summary = f"{self.validator_id} was cancelled"
        else:
            kind = ValidationKind.VALIDATION_FAILURE
            summary = f"{self.validator_id} failed with exit code {process.exit_code}"
            finding = Finding(
                finding_id=new_id("finding"),
                execution_id=context.execution_id,
                validator_id=self.validator_id,
                rule_id=f"{self.validator_id}.failed",
                category="validation",
                severity=FindingSeverity.HIGH if context.definition.mandatory else FindingSeverity.MEDIUM,
                message=summary,
                location=FindingLocation(),
                evidence_refs=(report.uri, stdout.uri, stderr.uri),
                recommendation="Inspect the validator output and correct the project before approval.",
                introduced=None,
                provenance=context.provenance.model_copy(update={"actor": actor}),
            )
            findings = (finding,)
        result = ValidationResult(
            validation_result_id=new_id("validation"),
            execution_id=context.execution_id,
            validator_id=self.validator_id,
            change_set_digest=context.change_set.digest,
            status=process.status,
            kind=kind,
            mandatory=context.definition.mandatory,
            summary=summary,
            finding_ids=tuple(item.finding_id for item in findings),
            evidence_refs=(report.uri, stdout.uri, stderr.uri),
            tool_invocation_ids=(tool.invocation_id,),
            started_at=started,
            finished_at=datetime.now(UTC),
            errors=errors,
            provenance=context.provenance.model_copy(update={"actor": actor}),
        )
        return ValidatorOutput(result, findings, (tool,))

    def _availability(self, context: ValidationContext) -> tuple[ResultStatus, str] | None:
        definition = context.definition
        if definition.command is None:
            status = ResultStatus.BLOCKED if definition.mandatory else ResultStatus.NOT_APPLICABLE
            return status, f"validator {self.validator_id} has no configured command"
        if definition.script:
            package_json = context.workspace / "package.json"
            try:
                package = json.loads(package_json.read_text(encoding="utf-8"))
                scripts = package.get("scripts", {})
            except (OSError, json.JSONDecodeError):
                scripts = {}
            if definition.script not in scripts:
                status = ResultStatus.BLOCKED if definition.mandatory else ResultStatus.NOT_APPLICABLE
                return status, f"package script {definition.script!r} is not defined"
        argv0 = definition.command[0]
        if shutil.which(argv0) is None:
            status = ResultStatus.BLOCKED if definition.mandatory else ResultStatus.NOT_APPLICABLE
            return status, f"executable {argv0!r} is not available"
        if definition.when_available and len(definition.command) >= 3 and definition.command[1] == "-m":
            module = definition.command[2]
            if importlib.util.find_spec(module) is None:
                return ResultStatus.NOT_APPLICABLE, f"optional Python module {module!r} is not installed"
        return None
