from __future__ import annotations

from datetime import UTC, datetime

from governed_harness.domain.enums import ActorType, FindingSeverity, ResultStatus, ValidationKind
from governed_harness.domain.models import (
    Actor,
    Finding,
    Provenance,
    ValidationResult,
)
from governed_harness.gates import GateEngine, GatePolicy


def provenance() -> Provenance:
    return Provenance(
        actor=Actor(actor_type=ActorType.HARNESS, actor_id="harness.core"), core_version="test"
    )


def validation(status: ResultStatus, *, mandatory: bool = True) -> ValidationResult:
    now = datetime.now(UTC)
    return ValidationResult(
        validation_result_id=f"validation_{status.lower()}",
        execution_id="run_001",
        validator_id="validator.test",
        change_set_digest="sha256:" + "a" * 64,
        status=status,
        kind=ValidationKind.SUCCESS
        if status is ResultStatus.PASSED
        else ValidationKind.VALIDATION_FAILURE,
        mandatory=mandatory,
        summary="x",
        evidence_refs=("artifact://sha256/" + "b" * 64,),
        started_at=now,
        finished_at=now,
        provenance=provenance(),
    )


def evaluate(validations, findings=()):
    return GateEngine().evaluate(
        execution_id="run_001",
        gate_id="delivery",
        change_set_digest="sha256:" + "a" * 64,
        policy_digest="sha256:" + "c" * 64,
        validations=validations,
        findings=findings,
        policy=GatePolicy(),
        provenance=provenance(),
    )


def test_optional_not_applicable_does_not_block() -> None:
    gate = evaluate(
        [validation(ResultStatus.PASSED), validation(ResultStatus.NOT_APPLICABLE, mandatory=False)]
    )
    assert gate.status is ResultStatus.PASSED


def test_mandatory_timeout_blocks() -> None:
    gate = evaluate([validation(ResultStatus.TIMED_OUT)])
    assert gate.status is ResultStatus.BLOCKED


def test_high_finding_fails_passed_validations() -> None:
    finding = Finding(
        finding_id="finding_001",
        execution_id="run_001",
        validator_id="review.independent",
        rule_id="review.security",
        category="security",
        severity=FindingSeverity.HIGH,
        message="risk",
        provenance=provenance(),
    )
    gate = evaluate([validation(ResultStatus.PASSED)], [finding])
    assert gate.status is ResultStatus.FAILED
    assert any(code.startswith("BLOCKING_FINDING") for code in gate.reason_codes)
