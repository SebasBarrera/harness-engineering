"""The agent contract in a run (wave 11, #97): the minimal request and its evidence, the cap of
iterations, the response limits and the token budget and time limit of a call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.agents.contract import TASK_FIELDS
from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import PhaseId, ResultStatus

TASK = (
    "taskId: task_contract\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "constraints: [Keep the public signature.]\n"
    "requirements:\n"
    "  - requirementId: R-1\n"
    "    text: The discount applies at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/acceptance/test_ac_1.py]\n"
)

# MODE: "good" answers well; "notes" adds a key outside the contract to the first BROKEN answers;
# "usage" reports more tokens than any budget; "sleep" does not answer within the time limit.
AGENT = """\
import json, sys, time
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
mine = [item for item in calls if item.get("kind", "implement") == kind]
if kind == "acceptance":
    if MODE == "sleep":
        time.sleep(30)
    content = (
        "from sample import apply_discount\\n\\n\\n"
        "def test_ac_1() -> None:\\n    assert apply_discount(100, 100, 0.1) == 90\\n"
    )
    result = {"tests": [{"path": "tests/acceptance/test_ac_1.py", "content": content}]}
    if MODE == "notes" and len(mine) <= BROKEN:
        result["notes"] = "my reasoning"
    answer = {"status": "PASSED", "summary": "answered", "result": result}
    if MODE == "usage":
        answer["usage"] = {"inputTokens": 5000, "outputTokens": 10}
    print(json.dumps(answer))
    sys.exit(0)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def configure(
    workspace: Path,
    tmp_path: Path,
    *,
    mode: str = "good",
    broken: int = 0,
    contract: dict[str, Any] | None = None,
    retry: bool = False,
    acceptance: bool = True,
) -> Path:
    log = tmp_path / "calls.json"
    agent = workspace / "fixture_agent.py"
    agent.write_text(
        AGENT.replace("LOG", repr(str(log)))
        .replace("MODE", repr(mode))
        .replace("BROKEN", str(broken)),
        encoding="utf-8",
    )
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", agent.name]}
    }
    config["runtime"].update(
        {"agentSandbox": "off", "providerRetries": 0, "verificationCorrections": 0}
    )
    if retry:
        config["runtime"]["contractRetry"] = {"mode": "once"}
    if acceptance:
        config["verification"] = {
            "requirementTraceability": "off",
            "acceptanceTests": {"mode": "agent"},
        }
    if contract is not None:
        config["agentContract"] = contract
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_contract").execution_id


def requests_of(log: Path, kind: str) -> list[dict[str, Any]]:
    return [item for item in json.loads(log.read_text()) if item.get("kind") == kind]


def rules(application: HarnessApplication, workspace: Path, run: str) -> list[str]:
    return [item.rule_id for item in application.list_findings(workspace, run)]


def final_statuses(
    application: HarnessApplication, workspace: Path, run: str, kind: str
) -> list[str]:
    calls = application.agent_calls(workspace, run, kind)["calls"]
    return [item["finalStatus"] for item in calls]


# ----- item 2: the minimal request and its evidence ------------------------------------------
def test_without_the_section_the_request_is_the_earlier_one(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path)
    application, run = start(python_workspace, tmp_path)
    [request] = requests_of(log, "acceptance")
    assert "agentContract" not in request
    assert "metadata" in request["task"]
    assert application.agent_calls(python_workspace, run)["calls"] == []


def test_a_call_receives_only_the_task_fields_of_its_kind(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, contract={"mode": "enforce"})
    start(python_workspace, tmp_path)
    [request] = requests_of(log, "acceptance")
    assert set(request["task"]) <= set(TASK_FIELDS["acceptance"])


def test_the_request_carries_its_contract(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, contract={"mode": "enforce"})
    start(python_workspace, tmp_path)
    [request] = requests_of(log, "acceptance")
    contract = request["agentContract"]
    assert contract["iteration"] == {"number": 1, "max": 5}
    assert contract["sizeClass"] == "S"
    assert contract["constraints"]["readOnly"] is True


def test_the_command_shows_what_was_sent_and_received(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, contract={"mode": "enforce"})
    application, run = start(python_workspace, tmp_path)
    shown = application.agent_calls(python_workspace, run, "acceptance", 1)
    verification = shown["call"]["verification"]
    assert verification["digestMatches"] is True
    assert verification["keysMatchContract"] is True
    assert verification["extraKeys"] == []
    assert shown["call"]["parentReceived"]["finalStatus"] == "PASSED"
    assert shown["call"]["request"]["agentContract"]["callKind"] == "acceptance"


# ----- item 3: the response limits -----------------------------------------------------------
def test_an_answer_with_a_key_outside_the_contract_fails_closed(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, mode="notes", broken=9, contract={"mode": "enforce"})
    application, run = start(python_workspace, tmp_path)
    assert final_statuses(application, python_workspace, run, "acceptance") == [
        "CONTRACT_VIOLATION"
    ]
    assert "agent.contract.violation" in rules(application, python_workspace, run)


def test_a_violation_is_retried_and_the_parent_gets_only_the_good_answer(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        tmp_path,
        mode="notes",
        broken=1,
        contract={"mode": "enforce"},
        retry=True,
    )
    application, run = start(python_workspace, tmp_path)
    assert final_statuses(application, python_workspace, run, "acceptance") == [
        "CONTRACT_VIOLATION",
        "PASSED",
    ]
    assert application.acceptance(python_workspace, run)["status"] == "PROPOSED"


# ----- item 5: the cap of iterations ---------------------------------------------------------
def test_the_iterations_of_an_agent_are_counted(python_workspace: Path, tmp_path: Path) -> None:
    configure(
        python_workspace,
        tmp_path,
        mode="notes",
        broken=1,
        contract={"mode": "enforce"},
        retry=True,
    )
    application, run = start(python_workspace, tmp_path)
    used = application.agent_calls(python_workspace, run)["iterations"]["acceptance"]
    assert used == {"used": 2, "max": 5}


def test_an_agent_that_used_its_cap_blocks_the_run_with_a_restructure_advice(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        mode="notes",
        broken=9,
        contract={"mode": "enforce", "maxIterations": 1},
        retry=True,
    )
    application, run = start(python_workspace, tmp_path)
    assert len(requests_of(log, "acceptance")) == 1
    status = application.status(python_workspace, run)["execution"]
    assert status["status"] == ResultStatus.BLOCKED
    assert status["currentPhase"] == PhaseId.SPECIFICATION
    [finding] = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "agent.iterations.exhausted"
    ]
    assert "badly structured" in finding.message
    assert "Restructure the input" in finding.message


# ----- item 6: the token budget and the time limit -------------------------------------------
def test_a_call_over_its_token_budget_ends_with_a_structured_result(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        tmp_path,
        mode="usage",
        contract={"mode": "enforce", "budgets": {"S": {"tokens": 1000}}},
    )
    application, run = start(python_workspace, tmp_path)
    shown = application.agent_calls(python_workspace, run, "acceptance", 1)
    received = shown["call"]["parentReceived"]
    assert received["finalStatus"] == "LIMIT_REACHED"
    assert received["limitReached"] == "tokens"
    assert received["result"] is None


def test_a_call_that_reached_a_limit_counts_as_an_iteration(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        tmp_path,
        mode="usage",
        contract={"mode": "enforce", "budgets": {"S": {"tokens": 1000}}},
    )
    application, run = start(python_workspace, tmp_path)
    assert application.agent_calls(python_workspace, run)["iterations"]["acceptance"]["used"] == 1
    assert "agent.limit.reached" in rules(application, python_workspace, run)


def test_a_call_that_does_not_answer_in_time_is_stopped(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        tmp_path,
        mode="sleep",
        contract={"mode": "enforce", "budgets": {"S": {"wallSeconds": 1}}},
    )
    application, run = start(python_workspace, tmp_path)
    shown = application.agent_calls(python_workspace, run, "acceptance", 1)
    assert shown["call"]["parentReceived"]["limitReached"] == "time"
    status = application.status(python_workspace, run)["execution"]
    assert status["status"] == ResultStatus.BLOCKED


# ----- implement -----------------------------------------------------------------------------
def test_an_implement_call_carries_the_contract_and_is_recorded(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, contract={"mode": "enforce"}, acceptance=False)
    application, run = start(python_workspace, tmp_path)
    [request] = requests_of(log, "implement")
    contract = request["agentContract"]
    assert contract["constraints"]["readOnly"] is False
    assert contract["resources"]["writePaths"][0] == "src/sample/pricing.py"
    shown = application.agent_calls(python_workspace, run, "implement", 1)
    assert shown["call"]["parentReceived"]["finalStatus"] == "PASSED"
