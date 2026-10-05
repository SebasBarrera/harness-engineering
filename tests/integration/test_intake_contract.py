"""INTENT under the ladder settings (#55, items 7, 8 and 11): the operational contract in the
one clarification message, its confirmation bound to the task digest, the interruption budget
and its stop conditions, and the localisation call of M/L tasks (cached by task digest, cheapest
rung, its locations in the implement request and the context manifest)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.orchestration.engine import EngineServices

VAGUE = {
    "taskId": "task_vague",
    "title": "Discount",
    "intent": "Add a discount.",
    "acceptanceCriteria": [{"criterionId": "AC-1", "text": "It works."}],
    "implementation": {
        "mode": "patch",
        "patches": [
            {
                "path": "src/sample/pricing.py",
                "operation": "replace",
                "content": "def apply_discount(subtotal: float, threshold: float, rate: float) "
                "-> float:\n    return subtotal * (1 - rate) if subtotal >= threshold else "
                "subtotal\n",
            }
        ],
    },
}
CLEAR = {
    **VAGUE,
    "taskId": "task_clear",
    "intent": "Apply the configured discount rate when the subtotal reaches the threshold.",
    "acceptanceCriteria": [
        {"criterionId": "AC-1", "text": "apply_discount(100, 100, 0.1) returns 90."}
    ],
}


def configure(workspace: Path, intake: dict[str, Any], **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["intake"] = {"criteriaPolicy": "enforce", **intake}
    config.update(sections)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def create(workspace: Path, tmp_path: Path, value: dict[str, Any]) -> HarnessApplication:
    source = tmp_path / f"{value['taskId']}.yaml"
    source.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application


def events(workspace: Path, run: str) -> list[Any]:
    services = EngineServices.open(ConfigurationResolver().resolve(workspace))
    try:
        return services.events.list(run)
    finally:
        services.close()


def test_batch_puts_the_contract_in_the_one_clarification_message(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, {"operationalContract": "batch"})
    application = create(python_workspace, tmp_path, VAGUE)
    execution = application.start_run(python_workspace, "task_vague")
    assert execution.status is ResultStatus.BLOCKED
    request = application.list_clarifications(python_workspace, "task_vague")["openRequest"]
    assert [item["ruleId"] for item in request["questions"]] == ["C1", "T1"]
    contract = request["contract"]
    assert contract["mode"] == "batch" and contract["confirmed"] is False
    assert "verificationLevel" in contract["missing"]
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "answers": {
                    "Q-1": "apply_discount(100, 100, 0.1) returns 90.",
                    "Q-2": "Only the threshold rule.",
                },
                "contract": {
                    "verificationLevel": "L1",
                    "createPullRequest": "no",
                    "examples": "apply_discount(100, 100, 0.1) -> 90\napply_discount(99, 100, 0.1) -> 99",
                },
                "confirmContract": True,
            }
        ),
        encoding="utf-8",
    )
    revised = application.clarify_task(
        python_workspace, task_id="task_vague", answers_file=answers, actor_id="human.author"
    )["task"]
    assert revised["contract"]["verificationLevel"] == "L1"
    assert revised["contract"]["createPullRequest"] is False
    assert len(revised["contract"]["examples"]) == 2
    resumed = application.continue_run(python_workspace, execution.execution_id)
    assert resumed.current_phase is PhaseId.DECISION
    brief = application.review(python_workspace, execution.execution_id)
    assert (
        brief["contract"]["confirmed"] is True
        and brief["contract"]["confirmedBy"] == "human.author"
    )
    kinds = [item.event_type for item in events(python_workspace, execution.execution_id)]
    assert "contract.confirmed" in kinds and kinds.count("contract.summarized") == 2


def test_batch_asks_nothing_when_intent_asks_nothing(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, {"operationalContract": "batch"})
    application = create(python_workspace, tmp_path, CLEAR)
    execution = application.start_run(python_workspace, "task_clear")
    assert execution.current_phase is PhaseId.DECISION
    assert application.list_clarifications(python_workspace, "task_clear")["openRequest"] is None
    assert (
        application.review(python_workspace, execution.execution_id)["contract"]["confirmed"]
        is False
    )


def test_enforce_waits_for_a_complete_and_confirmed_contract(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, {"operationalContract": "enforce"})
    application = create(python_workspace, tmp_path, CLEAR)
    execution = application.start_run(python_workspace, "task_clear")
    assert execution.current_phase is PhaseId.INTENT and execution.status is ResultStatus.BLOCKED
    request = application.list_clarifications(python_workspace, "task_clear")["openRequest"]
    assert request["questions"] == [] and request["contract"]["missing"]
    answers = tmp_path / "contract.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "contract": {
                    "verificationLevel": "L1",
                    "branch": "feature/discount",
                    "push": "no",
                    "createPullRequest": "no",
                    "comment": "yes",
                    "coverageThreshold": 80,
                    "scope": "src/sample/pricing.py: the discount rule",
                }
            }
        ),
        encoding="utf-8",
    )
    application.clarify_task(
        python_workspace, task_id="task_clear", answers_file=answers, actor_id="human.author"
    )
    blocked = application.continue_run(python_workspace, execution.execution_id)
    assert blocked.status is ResultStatus.BLOCKED
    contract = application.review(python_workspace, execution.execution_id)["contract"]
    assert contract["missing"] == [] and contract["confirmed"] is False
    with pytest.raises(PolicyViolationError, match="does not match"):
        application.confirm_contract(
            python_workspace, task_id="task_clear", digest="sha256:0", actor_id="human.author"
        )
    with pytest.raises(PolicyViolationError):
        application.confirm_contract(
            python_workspace, task_id="task_clear", digest=contract["digest"], actor_id="agent.x"
        )
    confirmed = application.confirm_contract(
        python_workspace, task_id="task_clear", digest=contract["digest"], actor_id="human.author"
    )
    assert confirmed["digest"] == contract["digest"]
    resumed = application.continue_run(python_workspace, execution.execution_id)
    assert resumed.current_phase is PhaseId.DECISION
    with pytest.raises(ConfigurationError, match="unknown contract item"):
        bad = tmp_path / "bad.yaml"
        bad.write_text("contract:\n  favourite: blue\n", encoding="utf-8")
        create(python_workspace, tmp_path, {**CLEAR, "taskId": "task_other"})
        other = application.start_run(python_workspace, "task_other")
        assert other.status is ResultStatus.BLOCKED
        application.clarify_task(
            python_workspace, task_id="task_other", answers_file=bad, actor_id="human.author"
        )


def test_interruptions_are_counted_and_stop_conditions_recorded(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        {"interruptions": {"target": 0, "stopConditions": ["unresolvable-ambiguity"]}},
    )
    application = create(python_workspace, tmp_path, VAGUE)
    execution = application.start_run(python_workspace, "task_vague")
    answers = tmp_path / "answers.yaml"
    answers.write_text("answers:\n  Q-1: It should be fine.\n", encoding="utf-8")
    application.clarify_task(
        python_workspace, task_id="task_vague", answers_file=answers, actor_id="human.author"
    )
    again = application.continue_run(python_workspace, execution.execution_id)
    assert again.status is ResultStatus.BLOCKED
    interruptions = application.review(python_workspace, execution.execution_id)["interruptions"]
    assert interruptions["count"] == 1 and interruptions["overBudget"] is True
    assert interruptions["byKind"] == {"clarification": 1}
    assert [item["condition"] for item in interruptions["stops"]] == ["unresolvable-ambiguity"]


LOCATING_AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
if request.get("kind") == "locate":
    print(json.dumps({"status": "PASSED", "summary": "located", "result": {
        "locations": [{"path": "src/sample/pricing.py", "line": 2,
                       "evidence": "return subtotal", "reason": "the rule lives here"}],
        "questions": []}}))
    sys.exit(0)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
print(json.dumps({"status": "PASSED", "summary": "done"}))
"""


def locate_project(workspace: Path, tmp_path: Path) -> Path:
    log = tmp_path / "calls.json"
    (workspace / "agent.py").write_text(
        LOCATING_AGENT.replace("LOG", repr(str(log))), encoding="utf-8"
    )
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "locator"
    config["agentProviders"] = {"locator": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"].update({"agentSandbox": "off"})
    config["context"] = {"manifest": "auto", "locate": {"mode": "agent"}}
    config["agentRouting"] = {
        "mode": "tiered",
        "families": {"locator": "claude-code"},
        "thresholds": {"requirements": [2, 15]},
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def test_locate_runs_once_per_task_revision_for_larger_tasks(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = locate_project(python_workspace, tmp_path)
    large = {
        **CLEAR,
        "taskId": "task_large",
        "requirements": [
            f"R{index}. Rule number {index} of the discount." for index in range(1, 5)
        ],
    }
    application = create(python_workspace, tmp_path, large)
    first = application.start_run(python_workspace, "task_large")
    calls = json.loads(log.read_text())
    kinds = [item.get("kind", "implement") for item in calls]
    assert kinds == ["locate", "implement"]
    assert calls[0]["routing"]["model"] == "claude-sonnet-5-5"  # the bottom rung
    assert calls[0]["readOnly"] is True
    implement = calls[1]
    assert implement["locations"][0]["path"] == "src/sample/pricing.py"
    located = [
        item
        for item in implement["contextFiles"]["files"]
        if "located by the locate call" in item["reasons"]
    ]
    assert [item["path"] for item in located] == ["src/sample/pricing.py"]
    assert first.current_phase is PhaseId.DECISION
    second = application.start_run(python_workspace, "task_large")
    kinds = [item.get("kind", "implement") for item in json.loads(log.read_text())]
    assert kinds == ["locate", "implement", "implement"]
    reused = [item.event_type for item in events(python_workspace, second.execution_id)]
    assert "locate.reused" in reused


def test_locate_is_skipped_for_small_tasks(python_workspace: Path, tmp_path: Path) -> None:
    log = locate_project(python_workspace, tmp_path)
    application = create(python_workspace, tmp_path, CLEAR)
    execution = application.start_run(python_workspace, "task_clear")
    calls = json.loads(log.read_text())
    assert [item.get("kind", "implement") for item in calls] == ["implement"]
    assert "locations" not in calls[0]
    assert "locate.skipped" in [
        item.event_type for item in events(python_workspace, execution.execution_id)
    ]
