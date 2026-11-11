"""What the gate contract tells the agent about requirement traceability and the commands it may
run (#84): the examples of the naming rule are the ones the check accepts, and a command is
suggested only when the agent's grants allow it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

import pytest

from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor, CapabilityGrant, Task
from governed_harness.orchestration.gate_contract import (
    TRACEABILITY_RULE,
    GateContract,
    runnable,
    traceability_terms,
)
from governed_harness.validators.traceability import is_test_file, name_mentions

AGENT = Actor(actor_type=ActorType.AGENT, actor_id="agent.fixture", version="1")


def _grant(*scope: str) -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        grant_id="grant_1",
        execution_id="run_1",
        actor=AGENT,
        capability="process.execute",
        scope=scope,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


@pytest.mark.parametrize(
    ("name", "identifier"),
    [
        ("test_a1_rounding", "A1"),
        ("test_A1", "A1"),
        ("TestA1Rounding", "A1"),
        ("test_req_alphabet_rejects", "req_alphabet"),
    ],
)
def test_the_rule_examples_are_names_the_check_accepts(name: str, identifier: str) -> None:
    assert name in TRACEABILITY_RULE
    assert name_mentions(name, identifier)


@pytest.mark.parametrize(
    ("path", "technology"),
    [
        ("tests/test_pricing.py", "python"),
        ("pricing_test.py", "python"),
        ("src/pricing.test.js", "node"),
        ("src/pricing.spec.ts", "node"),
        ("__tests__/pricing.js", "node"),
    ],
)
def test_the_rule_names_the_test_files_the_check_reads(path: str, technology: str) -> None:
    assert is_test_file(PurePosixPath(path), (technology,))


def test_the_terms_list_identified_requirements_and_the_rest() -> None:
    task = Task.model_validate(
        {
            "taskId": "t",
            "projectId": "p",
            "title": "x",
            "intent": "x",
            "requirements": [
                {"requirementId": "req_a", "text": "A1. Round half up."},
                {"requirementId": "req_alphabet", "text": "Reject other characters."},
                # A generated id (the task file gave none) identifies nothing.
                {"requirementId": "req_" + "0" * 32, "text": "Keep the signature."},
            ],
            "acceptanceCriteria": [{"criterionId": "AC-1", "text": "x"}],
        }
    )
    terms = traceability_terms(task, "warn")["traceability"]
    assert [item["identifier"] for item in terms["requirements"]] == ["A1", "req_alphabet"]
    assert len(terms["notChecked"]) == 1
    assert traceability_terms(task, "off") == {}


def test_a_command_is_runnable_only_under_a_grant() -> None:
    grants = [_grant("python")]
    assert runnable(["python", "-m", "pytest"], grants)
    assert not runnable(["harness", "check"], grants)
    assert not runnable(["python"], [])
    assert runnable(["harness", "check", "--run", "r"], [_grant("harness check")])


def test_the_instructions_say_what_to_run() -> None:
    assert GateContract.instructions_note({"checkCommand": ["harness", "check"]}) == ""
    some = GateContract.instructions_note({"checkCommands": [{"id": "x", "command": ["y"]}]})
    assert "gate.checkCommands" in some
    none = GateContract.instructions_note({"checkCommands": []})
    assert "do not try to run" in none
