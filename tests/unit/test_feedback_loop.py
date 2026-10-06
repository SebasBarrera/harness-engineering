"""Units of the provider feedback loop (issue #36): bounds, transient patterns, settings."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver, RuntimeConfig
from governed_harness.configuration.models import DEFAULT_TRANSIENT_PATTERNS
from governed_harness.domain.enums import (
    ActorType,
    FindingSeverity,
    MetricQuality,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.models import (
    FEEDBACK_MAX_FINDINGS,
    FEEDBACK_STREAM_CHARS,
    FEEDBACK_TOTAL_CHARS,
    Actor,
    FeedbackGate,
    Finding,
    Provenance,
    ValidationResult,
)
from governed_harness.evidence import LocalArtifactStore
from governed_harness.orchestration import InvalidTransition, NormativeStateMachine
from governed_harness.orchestration.feedback import (
    FeedbackBuilder,
    failing_validations,
    head,
    tail,
    transient_cause,
    verification_reason_codes,
)
from governed_harness.retrospective import RetrospectiveEngine
from governed_harness.telemetry import MetricValue

PROVENANCE = Provenance(
    actor=Actor(actor_type=ActorType.HARNESS, actor_id="harness.core"), core_version="test"
)
DIGEST = "sha256:" + "a" * 64


def failed_validation(
    store: LocalArtifactStore, validator_id: str, stdout: str, stderr: str
) -> ValidationResult:
    out = store.put(stdout.encode(), media_type="text/plain")
    err = store.put(stderr.encode(), media_type="text/plain")
    report = store.put_json(
        {"exitCode": 1, "stdoutRef": out.uri, "stderrRef": err.uri, "stdoutTruncated": False}
    )
    now = datetime.now(UTC)
    return ValidationResult(
        validation_result_id=f"validation_{validator_id}",
        execution_id="run_1",
        validator_id=validator_id,
        change_set_digest=DIGEST,
        status=ResultStatus.FAILED,
        kind=ValidationKind.VALIDATION_FAILURE,
        summary=f"{validator_id} failed with exit code 1",
        evidence_refs=(report.uri, out.uri, err.uri),
        started_at=now,
        finished_at=now,
        provenance=PROVENANCE,
    )


def finding(index: int, severity: FindingSeverity) -> Finding:
    return Finding(
        finding_id=f"finding_{index}",
        execution_id="run_1",
        validator_id="python.pytest",
        rule_id=f"rule.{index}",
        category="validation",
        severity=severity,
        message="m" * 3000,
        provenance=PROVENANCE,
    )


def gate() -> FeedbackGate:
    return FeedbackGate(gate_id="verification", status=ResultStatus.FAILED, reason_codes=("x",))


def test_tail_and_head_bound_text() -> None:
    assert tail("abcdef", 4) == ("cdef", True)
    assert tail("abc", 4) == ("abc", False)
    assert tail("abc", 0) == ("", True)
    assert tail("", 0) == ("", False)
    assert head("a" * 10, 5) == "aaaa…"
    assert head("abc", 5) == "abc"


def test_feedback_keeps_the_end_of_each_stream_within_the_total(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    validations = [
        failed_validation(store, f"v{index}", "o" * 9000 + f"END-OUT-{index}", "e" * 9000)
        for index in range(4)
    ]
    feedback = FeedbackBuilder(store).build(
        trigger="VERIFICATION_FAILED",
        attempt=2,
        change_set_digest=DIGEST,
        gate=gate(),
        validations=validations,
        findings=[],
    )
    total = sum(len(item.stdout) + len(item.stderr) for item in feedback.validators)
    assert total == FEEDBACK_TOTAL_CHARS
    first = feedback.validators[0]
    assert len(first.stdout) == FEEDBACK_STREAM_CHARS and first.stdout.endswith("END-OUT-0")
    assert first.stdout_truncated and first.stderr_truncated
    assert first.exit_code == 1
    # The budget runs out after two validators: the others keep their status, not their output.
    assert [len(item.stdout) for item in feedback.validators] == [4000, 4000, 0, 0]
    assert all(item.stdout_truncated for item in feedback.validators)


def test_feedback_lists_the_most_severe_findings_first_and_counts_the_rest(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    severities = [FindingSeverity.LOW] * 20 + [FindingSeverity.CRITICAL] * 5
    feedback = FeedbackBuilder(store).build(
        trigger="VERIFICATION_FAILED",
        attempt=2,
        change_set_digest=DIGEST,
        gate=gate(),
        validations=[],
        findings=[finding(index, severity) for index, severity in enumerate(severities)],
    )
    assert len(feedback.findings) == FEEDBACK_MAX_FINDINGS
    assert feedback.omitted_findings == 5
    assert [item.severity for item in feedback.findings[:5]] == [FindingSeverity.CRITICAL] * 5
    assert all(len(item.message) == 1000 for item in feedback.findings)


def test_failing_validations_and_reason_codes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    failed = failed_validation(store, "python.pytest", "", "")
    optional = failed.model_copy(update={"validator_id": "python.ruff", "mandatory": False})
    passed = failed.model_copy(
        update={"validator_id": "python.mypy", "status": ResultStatus.PASSED}
    )
    assert failing_validations([failed, optional, passed]) == [failed]
    assert verification_reason_codes([failed]) == ("python.pytest_FAILED",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("HTTP 429 Too Many Requests", "429"),
        ("error at line 4290", None),
        ("Error 529: Overloaded", "overloaded"),
        ("Connection RESET by peer", "connection reset"),
        ("The machine went to sleep", "went to sleep"),
        ("You have reached your usage limit", "usage limit"),
        ("SyntaxError: invalid syntax", None),
    ],
)
def test_transient_patterns(text: str, expected: str | None) -> None:
    assert transient_cause([text], DEFAULT_TRANSIENT_PATTERNS) == expected


def test_transient_patterns_are_configurable() -> None:
    assert transient_cause(["quota exhausted"], ("Quota",)) == "Quota"
    assert transient_cause(["quota exhausted"], ()) is None


def test_absent_loop_settings_are_not_serialized() -> None:
    runtime = RuntimeConfig()
    assert runtime.model_dump(mode="json", by_alias=True) == {
        "commandTimeoutSeconds": 900,
        "maxOutputBytes": 1_000_000,
        "maxParallel": 2,
        "allowNetwork": False,
    }
    assert runtime.correction_limit == 0
    assert runtime.claim_check_enabled is False
    assert runtime.feedback_enabled is False
    assert runtime.retry_limit == 0
    assert runtime.retry_delay_seconds == 0
    assert runtime.transient_patterns == DEFAULT_TRANSIENT_PATTERNS
    assert runtime.claim_severity is FindingSeverity.MEDIUM


def test_loop_settings_are_serialized_when_present() -> None:
    runtime = RuntimeConfig.model_validate(
        {
            "verificationCorrections": 0,
            "providerFeedback": False,
            "providerRetries": 1,
            "providerRetryDelaySeconds": 2.5,
            "providerTransientPatterns": ["busy"],
            "unsupportedClaimSeverity": "LOW",
        }
    )
    dumped = runtime.model_dump(mode="json", by_alias=True)
    assert dumped["verificationCorrections"] == 0
    assert dumped["providerFeedback"] is False
    assert dumped["providerTransientPatterns"] == ["busy"]
    assert runtime.claim_check_enabled is True
    assert runtime.claim_severity is FindingSeverity.LOW
    assert runtime.retry_delay_seconds == 2.5


@pytest.mark.parametrize(
    "value",
    [
        {"verificationCorrections": -1},
        {"verificationCorrections": 11},
        {"providerRetries": -1},
        {"providerRetryDelaySeconds": -1},
        {"providerTransientPatterns": ["  "]},
        {"unsupportedClaimSeverity": "SEVERE"},
    ],
)
def test_invalid_loop_settings_are_rejected(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(value)


def test_init_enables_the_loop(python_workspace: Path) -> None:
    runtime = ConfigurationResolver().resolve(python_workspace).project.runtime
    assert runtime.correction_limit == 2
    assert runtime.feedback_enabled is True
    assert runtime.claim_severity is FindingSeverity.MEDIUM
    assert runtime.retry_limit == 3
    assert runtime.retry_delay_seconds == 60
    loop = HarnessApplication().validate_config(python_workspace)["feedbackLoop"]
    assert loop["verificationCorrections"] == 2
    assert loop["unsupportedClaimCheck"] is True


def test_project_without_loop_settings_keeps_its_snapshot_and_runs_without_loop(
    python_workspace: Path,
) -> None:
    path = python_workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value["runtime"] = {
        "commandTimeoutSeconds": 900,
        "maxOutputBytes": 1000000,
        "maxParallel": 2,
        "allowNetwork": False,
    }
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert resolved.model_dump(mode="json", by_alias=True)["project"]["runtime"] == value["runtime"]
    loop = HarnessApplication().validate_config(python_workspace)["feedbackLoop"]
    assert loop == {
        "verificationCorrections": 0,
        "providerFeedback": False,
        "unsupportedClaimCheck": False,
        "unsupportedClaimSeverity": "MEDIUM",
        "providerRetries": 0,
        "providerRetryDelaySeconds": 0.0,
        "providerTransientPatterns": list(DEFAULT_TRANSIENT_PATTERNS),
    }


def test_verification_correction_transition() -> None:
    machine = NormativeStateMachine()
    transition = machine.authorize_verification_correction(PhaseId.VERIFICATION)
    assert transition.target is PhaseId.IMPLEMENTATION
    assert PhaseId.VERIFICATION in transition.invalidated
    with pytest.raises(InvalidTransition):
        machine.authorize_verification_correction(PhaseId.DECISION)


def test_retrospective_separates_automatic_corrections() -> None:
    def metric(name: str, value: int) -> MetricValue:
        return MetricValue(name, value, "count", MetricQuality.OBSERVED, name, "test")

    metrics = {
        "validation.non_passed": metric("validation.non_passed", 1),
        "correction.cycles": metric("correction.cycles", 2),
        "correction.verification_cycles": metric("correction.verification_cycles", 2),
        "implementation.attempts": metric("implementation.attempts", 3),
        "review.cycles": metric("review.cycles", 1),
        "changeset.files": metric("changeset.files", 2),
    }
    retrospective = RetrospectiveEngine().generate(
        execution_id="run_1",
        metrics=metrics,
        evidence_refs=("artifact://sha256/abc",),
        provenance=PROVENANCE,
    )
    statements = [item.statement for item in retrospective.observations]
    assert "The execution recorded 0 human-authorized correction cycle(s)." in statements
    assert (
        "The execution recorded 2 automatic correction cycle(s) after a failed verification."
        in statements
    )
    assert all(item.category != "specification" for item in retrospective.recommendations)
