from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from governed_harness.domain.enums import FindingSeverity, ResultStatus
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Finding, GateEvaluation, Provenance, ValidationResult


@dataclass(frozen=True)
class GateInput:
    check_id: str
    status: ResultStatus
    mandatory: bool = True


@dataclass(frozen=True)
class LegacyGateDecision:
    status: ResultStatus
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class GatePolicy:
    require_human_decision: bool = True
    blocking_severities: tuple[FindingSeverity, ...] = (
        FindingSeverity.HIGH,
        FindingSeverity.CRITICAL,
    )


class GateEngine:
    """Fail-closed consolidation. It evaluates evidence; it never executes checks."""

    def evaluate(
        self,
        inputs: list[GateInput] | None = None,
        *,
        execution_id: str | None = None,
        gate_id: str | None = None,
        change_set_digest: str | None = None,
        policy_digest: str | None = None,
        validations: Iterable[ValidationResult] = (),
        findings: Iterable[Finding] = (),
        policy: GatePolicy | None = None,
        provenance: Provenance | None = None,
    ) -> GateEvaluation | LegacyGateDecision:
        if inputs is not None:
            mandatory_inputs = [item for item in inputs if item.mandatory]
            if not mandatory_inputs:
                return LegacyGateDecision(ResultStatus.INCONCLUSIVE, ("NO_MANDATORY_INPUTS",))
            non_passed = [item for item in mandatory_inputs if item.status is not ResultStatus.PASSED]
            if not non_passed:
                return LegacyGateDecision(ResultStatus.PASSED, ("ALL_MANDATORY_PASSED",))
            if any(item.status is ResultStatus.ERROR for item in non_passed):
                status = ResultStatus.ERROR
            elif any(item.status in {ResultStatus.BLOCKED, ResultStatus.TIMED_OUT, ResultStatus.CANCELLED} for item in non_passed):
                status = ResultStatus.BLOCKED
            elif any(item.status is ResultStatus.INCONCLUSIVE for item in non_passed):
                status = ResultStatus.INCONCLUSIVE
            else:
                status = ResultStatus.FAILED
            return LegacyGateDecision(status, tuple(f"{item.check_id}_{item.status}" for item in non_passed))
        if None in {execution_id, gate_id, change_set_digest, policy_digest, policy, provenance}:
            raise TypeError("structured gate evaluation requires execution, gate, digests, policy and provenance")
        validation_list = list(validations)
        finding_list = list(findings)
        mandatory = [item for item in validation_list if item.mandatory]
        input_refs = tuple(
            [f"record://validation/{item.validation_result_id}" for item in validation_list]
            + [f"record://finding/{item.finding_id}" for item in finding_list]
        )
        reasons: list[str] = []
        if not mandatory:
            status = ResultStatus.INCONCLUSIVE
            reasons.append("NO_MANDATORY_VALIDATIONS")
        else:
            status = self._status(mandatory, reasons)
        blocking_findings = [
            finding for finding in finding_list if finding.severity in policy.blocking_severities
        ]
        if blocking_findings and status is ResultStatus.PASSED:
            status = ResultStatus.FAILED
        for finding in blocking_findings:
            reasons.append(f"BLOCKING_FINDING_{finding.finding_id}")
        if not reasons and status is ResultStatus.PASSED:
            reasons.append("ALL_MANDATORY_VALIDATIONS_PASSED")
        assert execution_id is not None and gate_id is not None and change_set_digest is not None and policy_digest is not None and policy is not None and provenance is not None
        return GateEvaluation(
            gate_evaluation_id=new_id("gateeval"),
            execution_id=execution_id,
            gate_id=gate_id,
            status=status,
            change_set_digest=change_set_digest,
            policy_digest=policy_digest,
            input_refs=input_refs,
            reason_codes=tuple(reasons),
            requires_human_decision=policy.require_human_decision,
            provenance=provenance,
        )

    @staticmethod
    def _status(mandatory: list[ValidationResult], reasons: list[str]) -> ResultStatus:
        non_passed = [item for item in mandatory if item.status is not ResultStatus.PASSED]
        if not non_passed:
            return ResultStatus.PASSED
        precedence = (
            (ResultStatus.ERROR, ResultStatus.ERROR),
            (ResultStatus.TIMED_OUT, ResultStatus.BLOCKED),
            (ResultStatus.BLOCKED, ResultStatus.BLOCKED),
            (ResultStatus.CANCELLED, ResultStatus.BLOCKED),
            (ResultStatus.INCONCLUSIVE, ResultStatus.INCONCLUSIVE),
            (ResultStatus.SKIPPED, ResultStatus.INCONCLUSIVE),
            (ResultStatus.NOT_APPLICABLE, ResultStatus.INCONCLUSIVE),
            (ResultStatus.FAILED, ResultStatus.FAILED),
        )
        for source, gate_status in precedence:
            matching = [item for item in non_passed if item.status is source]
            if matching:
                reasons.extend(f"{item.validator_id}_{source}" for item in matching)
                return gate_status
        reasons.append("UNKNOWN_MANDATORY_STATUS")
        return ResultStatus.ERROR
