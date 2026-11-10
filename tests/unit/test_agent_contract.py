"""The agent contract (wave 11, #97): configuration, request and response contracts, response
limits and the registry of script contracts."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from governed_harness.agents.contract import (
    TASK_FIELDS,
    check_answer,
    limit_summary,
    minimal_task,
    validate_request_contract,
    validate_response_contract,
)
from governed_harness.agents.script_contracts import brief, registry
from governed_harness.configuration.agent_contract import (
    DEFAULT_SIZE_BUDGETS,
    AgentContractConfig,
)
from governed_harness.configuration.models import ProjectConfiguration, ValidatorDefinition

RAW = {"configVersion": "1.0", "projectId": "p", "workspace": {"root": ".."}}
SECTION = "agentContract"


def request_contract(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "contractVersion": "1.0",
        "callKind": "plan",
        "task": {
            "taskId": "task_1",
            "callKind": "plan",
            "objective": "Discount. Apply it.",
            "fields": ["task_id", "title"],
        },
        "allowedContext": ["agentContract", "task"],
        "resources": {"readPaths": ["/w"], "writePaths": []},
        "constraints": {"readOnly": True, "workspace": "/w"},
        "tokenBudget": 50000,
        "timeLimitSeconds": 600,
        "sizeClass": "S",
        "iteration": {"number": 1, "max": 5},
        "expectedResponse": {
            "resultKeys": ["subtasks"],
            "limits": {"summaryChars": 600, "resultBytes": 32768, "fieldChars": 4000},
            "fileContentsAllowed": False,
        },
        "returnToParent": {},
    }
    value.update(changes)
    return value


# ----- configuration -------------------------------------------------------------------------
def test_a_file_without_the_section_keeps_its_serialized_form() -> None:
    dumped = ProjectConfiguration.model_validate(RAW).model_dump(mode="json", by_alias=True)
    assert SECTION not in dumped


def test_the_section_is_serialized_when_present() -> None:
    raw = {**RAW, SECTION: {"mode": "enforce", "maxIterations": 5}}
    dumped = ProjectConfiguration.model_validate(raw).model_dump(mode="json", by_alias=True)
    assert dumped[SECTION]["maxIterations"] == 5


@pytest.mark.parametrize("cap", [0, 7, 100])
def test_a_cap_above_six_or_below_one_is_rejected(cap: int) -> None:
    with pytest.raises(ValidationError):
        AgentContractConfig.model_validate({"maxIterations": cap})


def test_the_cap_is_five_by_default_and_six_is_allowed() -> None:
    assert AgentContractConfig().iterations == 5
    assert AgentContractConfig.model_validate({"maxIterations": 6}).iterations == 6


def test_the_budget_of_a_size_is_the_default_or_the_configured_one() -> None:
    config = AgentContractConfig.model_validate({"budgets": {"M": {"tokens": 123456}}})
    assert config.budget_for("S") == DEFAULT_SIZE_BUDGETS["S"]
    assert config.budget_for("M") == (123456, DEFAULT_SIZE_BUDGETS["M"][1])


def test_bare_off_turns_the_contract_off() -> None:
    assert AgentContractConfig.model_validate({"mode": False}).enabled is False


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentContractConfig.model_validate({"maxIteration": 3})


# ----- the minimal request -------------------------------------------------------------------
TASK = {
    "task_id": "t",
    "project_id": "p",
    "title": "Title",
    "intent": "Intent",
    "constraints": ["c"],
    "requirements": [{"requirement_id": "R-1", "text": "x"}],
    "acceptance_criteria": [{"criterion_id": "AC-1", "text": "y"}],
    "implementation": {"mode": "none"},
    "metadata": {"ownedPaths": ["a.py"]},
    "created_at": "2026-01-01T00:00:00Z",
    "probes": [],
    "checklist": [],
}


@pytest.mark.parametrize("kind", sorted(TASK_FIELDS))
def test_a_read_only_kind_gets_only_its_task_fields(kind: str) -> None:
    sent = minimal_task(kind, TASK)
    assert set(sent) <= set(TASK_FIELDS[kind])


@pytest.mark.parametrize("kind", sorted(TASK_FIELDS))
def test_a_read_only_kind_never_gets_the_internal_fields(kind: str) -> None:
    sent = minimal_task(kind, TASK)
    assert not {"metadata", "created_at", "implementation", "project_id"} & set(sent)


def test_implement_keeps_the_whole_task() -> None:
    assert minimal_task("implement", TASK) == TASK


# ----- the request contract ------------------------------------------------------------------
def test_a_valid_request_contract_passes() -> None:
    assert validate_request_contract(request_contract()).iteration.max == 5


def test_a_cap_above_six_in_the_contract_is_invalid() -> None:
    with pytest.raises(ValueError, match="iteration"):
        validate_request_contract(request_contract(iteration={"number": 1, "max": 7}))


def test_an_iteration_above_the_cap_is_invalid() -> None:
    with pytest.raises(ValueError, match="above the cap"):
        validate_request_contract(request_contract(iteration={"number": 6, "max": 5}))


def test_a_missing_required_field_is_named() -> None:
    value = request_contract()
    del value["tokenBudget"]
    with pytest.raises(ValueError, match="tokenBudget"):
        validate_request_contract(value)


def test_a_read_only_call_cannot_be_authorized_to_write() -> None:
    with pytest.raises(ValueError, match="read-only"):
        validate_request_contract(
            request_contract(resources={"readPaths": ["/w"], "writePaths": ["src"]})
        )


def test_an_unknown_field_is_invalid() -> None:
    with pytest.raises(ValueError, match="extra"):
        validate_request_contract(request_contract(extra="x"))


def test_the_task_and_the_contract_must_name_the_same_kind() -> None:
    value = request_contract()
    value["task"]["callKind"] = "review"
    with pytest.raises(ValueError, match="different call kinds"):
        validate_request_contract(value)


# ----- the response contract -----------------------------------------------------------------
def response(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "contractVersion": "1.0",
        "callKind": "plan",
        "iteration": 1,
        "status": "PASSED",
        "finalStatus": "PASSED",
        "summary": "Two sub-tasks.",
        "result": {"subtasks": []},
    }
    value.update(changes)
    return value


def test_a_valid_response_passes() -> None:
    assert validate_response_contract(response()).final_status == "PASSED"


def test_a_limit_status_needs_the_limit() -> None:
    with pytest.raises(ValueError, match="limitReached"):
        validate_response_contract(response(finalStatus="LIMIT_REACHED", result=None))


def test_only_a_passed_call_returns_a_result() -> None:
    with pytest.raises(ValueError, match="only a passed call"):
        validate_response_contract(response(finalStatus="BLOCKED"))


def test_a_violation_status_needs_the_violations() -> None:
    with pytest.raises(ValueError, match="violations"):
        validate_response_contract(response(finalStatus="CONTRACT_VIOLATION", result=None))


def test_a_limit_response_names_the_limit() -> None:
    value = validate_response_contract(
        response(finalStatus="LIMIT_REACHED", limitReached="tokens", result=None, status=None)
    )
    assert value.limit_reached == "tokens"


# ----- the limits of a response --------------------------------------------------------------
CONFIG = AgentContractConfig.model_validate({"limits": {"resultBytes": 2000, "fieldChars": 100}})


def test_a_conclusion_within_the_limits_has_no_problem() -> None:
    assert check_answer("plan", {"subtasks": [{"title": "a"}]}, CONFIG) == []


def test_a_result_above_its_cap_is_a_problem() -> None:
    problems = check_answer("plan", {"subtasks": ["x" * 90] * 40}, CONFIG)
    assert any("above the cap of 2000" in item for item in problems)


def test_a_long_text_field_is_a_problem() -> None:
    problems = check_answer("review", {"findings": [{"evidence": "x" * 101}]}, CONFIG)
    assert any("101 characters" in item for item in problems)


def test_file_content_is_a_problem_when_a_conclusion_is_due() -> None:
    problems = check_answer("locate", {"locations": [{"content": "def f(): ..."}]}, CONFIG)
    assert any("carries file content" in item for item in problems)


def test_acceptance_may_return_the_content_of_its_tests() -> None:
    result = {"tests": [{"path": "tests/acceptance/test_a.py", "content": "x" * 500}]}
    assert check_answer("acceptance", result, AgentContractConfig()) == []


def test_a_key_outside_the_contract_is_a_problem() -> None:
    problems = check_answer("plan", {"subtasks": [], "notes": "my reasoning"}, CONFIG)
    assert any("outside the plan contract" in item for item in problems)


def test_no_result_has_no_problem() -> None:
    assert check_answer("plan", None, CONFIG) == []


def test_a_summary_is_cut_to_its_cap() -> None:
    cut, was_cut = limit_summary("x" * 700, 600)
    assert (len(cut), was_cut) == (600, True)


def test_a_short_summary_is_kept() -> None:
    assert limit_summary("short", 600) == ("short", False)


# ----- scripts as black boxes ----------------------------------------------------------------
def test_the_registry_has_harness_check_with_its_errors() -> None:
    contract = registry()["harness.check"]
    assert {item.code for item in contract.errors} >= {"6", "2", "3"}


def test_a_validator_command_gets_a_contract() -> None:
    definition = ValidatorDefinition(id="unit", command=("pytest", "-q"))
    contract = registry([definition])["validator.unit"]
    assert contract.invocation == ("pytest", "-q")


def test_a_validator_without_a_command_gets_none() -> None:
    assert "validator.nocommand" not in registry([ValidatorDefinition(id="nocommand")])


def test_an_mcp_tool_contract_comes_from_its_schema() -> None:
    tool = {
        "name": "harness_check",
        "description": "Run the checks.",
        "inputSchema": {
            "type": "object",
            "properties": {"run": {"type": "string", "description": "Run id"}},
            "required": ["run"],
        },
    }
    contract = registry(tools=[tool])["mcp.harness_check"]
    assert [(item.name, item.required) for item in contract.inputs] == [("run", True)]


def test_the_brief_of_a_script_is_small_and_json() -> None:
    value = brief(registry()["harness.check"])
    assert len(json.dumps(value)) < 1500


def test_a_script_contract_always_declares_its_source_opaque() -> None:
    assert registry()["harness.check"].source_is_opaque is True
