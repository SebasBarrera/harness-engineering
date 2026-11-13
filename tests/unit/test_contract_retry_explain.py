"""``runtime.contractRetry.explainProblem`` (2.1): the second attempt of a broken read-only call
is told why the first was rejected; without the key the serialized configuration (and so its
digest) is the earlier one."""

from __future__ import annotations

from governed_harness.configuration.agent_results import ContractRetryConfig
from governed_harness.orchestration.agent_results import PROBLEM_LIMIT, retry_suffix

PROBLEM = "option layered needs a 'fit' reason and 'supports'"


def test_without_the_key_the_serialized_section_is_unchanged() -> None:
    config = ContractRetryConfig.model_validate({"mode": "once"})
    assert config.explain_problem is None
    assert config.model_dump(by_alias=True, mode="json") == {"mode": "once"}


def test_the_key_is_serialized_when_set() -> None:
    config = ContractRetryConfig.model_validate({"mode": "once", "explainProblem": True})
    assert config.explain_problem is True
    assert config.model_dump(by_alias=True, mode="json") == {
        "mode": "once",
        "explainProblem": True,
    }


def test_the_suffix_carries_the_problem_and_asks_for_a_correction() -> None:
    suffix = retry_suffix("", PROBLEM)
    assert PROBLEM in suffix
    assert suffix.startswith("Your previous answer to this request was rejected")
    assert "Correct that problem" in suffix


def test_the_suffix_keeps_the_original_suffix_first() -> None:
    suffix = retry_suffix("Expert prompt.", PROBLEM)
    assert suffix.startswith("Expert prompt. Your previous answer")


def test_a_long_problem_is_cut() -> None:
    suffix = retry_suffix("", "x" * (PROBLEM_LIMIT + 500))
    assert "x" * PROBLEM_LIMIT in suffix
    assert "x" * (PROBLEM_LIMIT + 1) not in suffix
