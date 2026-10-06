"""INTENT under the ladder settings (#55, items 7, 8 and 11): the operational contract in the
one clarification message, its confirmation bound to the task digest, the interruption budget
and its stop conditions, and the localisation call of M/L tasks (cached by task digest, cheapest
rung, its locations in the implement request and the context manifest)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.orchestration.engine import EngineServices
from tests.conftest import GIT_ENV, GIT_ISOLATION, without_agent_results

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
    assert contract["mode"] == "batch"
    assert contract["confirmed"] is False
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
    assert brief["contract"]["confirmed"] is True
    assert brief["contract"]["confirmedBy"] == "human.author"
    kinds = [item.event_type for item in events(python_workspace, execution.execution_id)]
    assert "contract.confirmed" in kinds
    assert kinds.count("contract.summarized") == 2


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
    assert execution.current_phase is PhaseId.INTENT
    assert execution.status is ResultStatus.BLOCKED
    request = application.list_clarifications(python_workspace, "task_clear")["openRequest"]
    assert request["questions"] == []
    assert request["contract"]["missing"]
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
    assert contract["missing"] == []
    assert contract["confirmed"] is False
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
    bad = tmp_path / "bad.yaml"
    bad.write_text("contract:\n  favourite: blue\n", encoding="utf-8")
    create(python_workspace, tmp_path, {**CLEAR, "taskId": "task_other"})
    other = application.start_run(python_workspace, "task_other")
    assert other.status is ResultStatus.BLOCKED
    with pytest.raises(ConfigurationError, match="unknown contract item"):
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
    assert interruptions["count"] == 1
    assert interruptions["overBudget"] is True
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


def test_a_change_outside_the_contract_scope_stops_the_run(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        {"interruptions": {"target": 2, "stopConditions": ["scope-contradiction"]}},
    )
    value = {**CLEAR, "taskId": "task_scoped", "contract": {"scopePaths": ["tests/**"]}}
    application = create(python_workspace, tmp_path, value)
    execution = application.start_run(python_workspace, "task_scoped")
    assert execution.current_phase is PhaseId.VERIFICATION
    assert execution.status is ResultStatus.BLOCKED
    rules = {
        item.rule_id for item in application.list_findings(python_workspace, execution.execution_id)
    }
    assert "contract.scope-contradiction" in rules
    stops = application.review(python_workspace, execution.execution_id)["interruptions"]["stops"]
    assert [item["condition"] for item in stops] == ["scope-contradiction"]
    metrics = application.status(python_workspace, execution.execution_id)["metrics"]
    assert metrics["human.interactions"]["value"] == 0


def _new_project(root: Path) -> Path:
    """A scaffold without sources: a new project, so intake.projectSetup asks its P1 questions."""
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='app'\nversion='0.1.0'\n\n[tool.pytest.ini_options]\npythonpath=['src']\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".harness/\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *GIT_ISOLATION, *args], cwd=root, check=True, env=GIT_ENV)

    git("init", "-q")
    git("config", "user.email", "fixture@example.com")
    git("config", "user.name", "Fixture")
    git("add", ".")
    git("commit", "-qm", "scaffold")
    HarnessApplication().init(root)
    without_agent_results(root)
    return root


def test_one_message_carries_intent_project_setup_and_contract(tmp_path: Path) -> None:
    """Wave 5 and wave 6 share one intake: the deterministic questions, the project setup
    questions (P1, #56) and the operational contract (#55) reach the person in one request,
    and one answers file settles all of them."""
    root = _new_project(tmp_path / "new")
    configure(root, {"operationalContract": "batch", "projectSetup": "ask"})
    value = {
        "taskId": "task_cart",
        "title": "Cart total",
        "intent": "Add a total.",
        "acceptanceCriteria": [{"criterionId": "AC-1", "text": "It works."}],
    }
    application = create(root, tmp_path, value)
    execution = application.start_run(root, "task_cart")
    assert (execution.current_phase, execution.status) == (PhaseId.INTENT, ResultStatus.BLOCKED)
    clarifications = application.list_clarifications(root, "task_cart")
    assert len(clarifications["requests"]) == 1
    request = clarifications["openRequest"]
    rules = [item["ruleId"] for item in request["questions"]]
    assert {"C1", "T1"} <= set(rules)
    assert rules.count("P1") == 3
    assert request["contract"]["mode"] == "batch"
    targets = {item["target"]: item["questionId"] for item in request["questions"]}
    answers = {
        item["questionId"]: "total([1.0, 2.0]) returns 3.0."
        for item in request["questions"]
        if item["ruleId"] != "P1"
    }
    answers[targets["project:architecture"]] = "Layered"
    answers[targets["project:testing"]] = "TDD"
    answers[targets["project:standards"]] = "default"
    source = tmp_path / "answers.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "answers": answers,
                "contract": {"verificationLevel": "L1", "createPullRequest": "no"},
                "confirmContract": True,
            }
        ),
        encoding="utf-8",
    )
    revised = application.clarify_task(
        root, task_id="task_cart", answers_file=source, actor_id="human.author"
    )["task"]
    assert revised["contract"]["verificationLevel"] == "L1"
    assert "Project setup (testing): TDD" in revised["constraints"]
    assert application.project(root)["projectSetup"]["testing"] == "tdd"
    kinds = [item.event_type for item in events(root, execution.execution_id)]
    assert "contract.confirmed" in kinds
    metrics = application.status(root, execution.execution_id)["metrics"]
    assert metrics["human.interactions"]["value"] == 1
