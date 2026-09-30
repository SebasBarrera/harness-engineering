import pytest

from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.orchestration.state_machine import InvalidTransition, NormativeStateMachine


def test_pass_advances_to_next_phase() -> None:
    decision = NormativeStateMachine().advance(PhaseId.INTENT, ResultStatus.PASSED)
    assert decision.target is PhaseId.DISCOVERY


@pytest.mark.parametrize(
    "status",
    [ResultStatus.BLOCKED, ResultStatus.ERROR, ResultStatus.SKIPPED, ResultStatus.INCONCLUSIVE],
)
def test_non_success_does_not_advance(status: ResultStatus) -> None:
    with pytest.raises(InvalidTransition):
        NormativeStateMachine().advance(PhaseId.VERIFICATION, status)


def test_correction_invalidates_downstream() -> None:
    decision = NormativeStateMachine().authorize_correction(PhaseId.DECISION)
    assert decision.target is PhaseId.IMPLEMENTATION
    assert PhaseId.VERIFICATION in decision.invalidated


def test_approval_is_digest_bound() -> None:
    machine = NormativeStateMachine()
    assert machine.approval_is_current("sha256:a", "sha256:a")
    assert not machine.approval_is_current("sha256:a", "sha256:b")
