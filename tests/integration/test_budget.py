"""Governed budget of agent calls (budget, #42): accounting, warning, fail-closed block and a
person raising the limit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration.agent_results import BudgetConfig
from governed_harness.domain.enums import FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.orchestration.budget import Usage, evaluate

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
fixed = "feedback" in request
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    + ("    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n" if fixed
       else "    return subtotal\\n")
)
Path("tests/test_pricing.py").write_text(
    "from sample import apply_discount\\n\\n"
    "def test_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n"
)
print(json.dumps({"status": "PASSED", "summary": "done",
                  "usage": {"inputTokens": 1000, "outputTokens": 100, "costUsd": 0.5}}))
"""

TASK = (
    "taskId: task_budget\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)


def configure(workspace: Path, tmp_path: Path, budget: dict[str, Any]) -> Path:
    log = tmp_path / "calls.json"
    (workspace / "agent.py").write_text(AGENT.replace("LOG", repr(str(log))), encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"].update(
        {
            "agentSandbox": "off",
            "verificationCorrections": 1,
            "providerFeedback": True,
            "providerRetries": 0,
        }
    )
    config["budget"] = budget
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_budget").execution_id


def rules(application: HarnessApplication, workspace: Path, run: str) -> list[Any]:
    return [
        item
        for item in application.list_findings(workspace, run)
        if item.validator_id == "harness.budget"
    ]


def test_thresholds() -> None:
    config = BudgetConfig.model_validate({"perRun": {"costUsd": 1.0, "tokens": 100}, "warnAt": 0.5})
    check = evaluate(config, {"run": Usage(cost_usd=0.6, tokens=10)})
    assert [item.metric for item in check.warnings] == ["costUsd"]
    assert check.exceeded == ()
    assert check.remaining["run"]["tokens"] == 90
    over = evaluate(config, {"run": Usage(cost_usd=1.2, tokens=10)}, {"run": {"costUsd": 2.0}})
    assert over.exceeded == ()  # a person raised the limit
    assert evaluate(config, {"run": Usage(cost_usd=1.2)}).exceeded[0].metric == "costUsd"


def test_crossing_the_run_limit_blocks_the_next_call_until_raised(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, {"perRun": {"costUsd": 0.4}, "warnAt": 0.8})
    application, run = start(python_workspace, tmp_path)
    [first] = json.loads(log.read_text())
    assert first["budget"]["remaining"]["run"]["costUsd"] == 0.4
    status = application.status(python_workspace, run)["execution"]
    assert status["currentPhase"] == PhaseId.IMPLEMENTATION
    assert status["status"] == ResultStatus.BLOCKED
    assert len(json.loads(log.read_text())) == 1  # no further agent call
    exceeded = [
        item
        for item in rules(application, python_workspace, run)
        if item.rule_id == "budget.exceeded"
    ]
    assert exceeded and exceeded[0].severity is FindingSeverity.HIGH
    with pytest.raises(PolicyViolationError):
        application.raise_budget(
            python_workspace,
            execution_id=run,
            scope="run",
            metric="costUsd",
            limit=0.3,
            rationale="lower",
            actor_id="human.reviewer",
        )
    with pytest.raises(PolicyViolationError):
        application.raise_budget(
            python_workspace,
            execution_id=run,
            scope="run",
            metric="costUsd",
            limit=5,
            rationale="raise",
            actor_id="agent.fixture_agent",
        )
    state = application.raise_budget(
        python_workspace,
        execution_id=run,
        scope="run",
        metric="costUsd",
        limit=5,
        rationale="The correction is worth it",
        actor_id="human.reviewer",
    )
    assert state["raised"] == {"run": {"costUsd": 5.0}}
    continued = application.continue_run(python_workspace, run)
    assert continued.current_phase is PhaseId.DECISION
    assert len(json.loads(log.read_text())) == 2
    assert application.budget(python_workspace, run)["usage"]["run"]["costUsd"] == 1.0


def test_a_call_over_its_own_limit_blocks_the_next_one(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, {"perCall": {"costUsd": 0.3}})
    application, run = start(python_workspace, tmp_path)
    assert len(json.loads(log.read_text())) == 1
    assert application.status(python_workspace, run)["execution"]["status"] == ResultStatus.BLOCKED


def test_warning_is_recorded_once(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, {"perRun": {"costUsd": 10}, "warnAt": 0.05})
    application, run = start(python_workspace, tmp_path)
    warnings = [
        item
        for item in rules(application, python_workspace, run)
        if item.rule_id == "budget.warning"
    ]
    assert len(warnings) == 1 and warnings[0].severity is FindingSeverity.LOW
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )
