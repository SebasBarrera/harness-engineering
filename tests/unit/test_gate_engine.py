from governed_harness.domain.enums import ResultStatus
from governed_harness.gates.engine import GateEngine, GateInput


def test_all_mandatory_pass() -> None:
    decision = GateEngine().evaluate([GateInput("tests", ResultStatus.PASSED)])
    assert decision.status is ResultStatus.PASSED


def test_skipped_mandatory_is_failure() -> None:
    decision = GateEngine().evaluate([GateInput("tests", ResultStatus.SKIPPED)])
    assert decision.status is ResultStatus.FAILED


def test_missing_inputs_is_inconclusive() -> None:
    assert GateEngine().evaluate([]).status is ResultStatus.INCONCLUSIVE
