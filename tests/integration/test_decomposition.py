"""Decomposition of large tasks into governed sub-tasks in PLANNING (planning.decomposition,
#39), including adaptive granularity (split on failure)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError

TASK = (
    "taskId: task_large\n"
    "title: Four small functions\n"
    "intent: Provide four small functions.\n"
    "requirements:\n"
    + "".join(
        f"  - requirementId: R{number}\n    text: Function f{number} returns {number}.\n"
        for number in range(1, 5)
    )
    + "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: f1() returns 1 and f4() returns 4.\n"
    "constraints:\n"
    "  - Keep every function pure.\n"
)

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
if kind == "plan":
    ids = [item["requirement_id"] for item in request["task"]["requirements"]]
    if MODE == "bad":
        subtasks = [{"title": "Only some", "requirements": ids[:2], "criteria": [], "constraints": []}]
    else:
        subtasks = [
            {"title": "First half", "requirements": ids[:2], "criteria": [], "constraints": ["Use no globals."]},
            {"title": "Second half", "requirements": ids[2:], "criteria": ["AC-1"], "constraints": []},
        ]
    print(json.dumps({"status": "PASSED", "summary": "planned", "result": {"subtasks": subtasks}}))
    sys.exit(0)
implements = [item for item in calls if item.get("kind", "implement") == "implement"]
for item in request["task"]["requirements"]:
    number = item["requirement_id"][1:]
    value = number
    if MODE in {"failfirst", "adaptive"} and len(implements) == 1:
        value = "0"
    Path(f"src/sample/f{number}.py").write_text(f"def f{number}() -> int:\\n    return {value}\\n")
    Path(f"tests/test_req_{number}.py").write_text(
        f"from sample.f{number} import f{number}\\n\\n"
        f"def test_r{number}() -> None:\\n    assert f{number}() == {number}\\n"
    )
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def configure(
    workspace: Path, tmp_path: Path, mode: str, planning: dict[str, Any] | None = None
) -> Path:
    log = tmp_path / f"calls-{mode}.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("MODE", repr(mode))
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {
            "kind": "command",
            "command": ["python", "agent.py"],
            "model": "claude-sonnet-5-5",
        }
    }
    config["runtime"].update({"agentSandbox": "off", "providerRetries": 0})
    config["planning"] = planning or {"decomposition": "agent", "threshold": 2}
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def calls(log: Path, kind: str) -> list[dict[str, Any]]:
    return [item for item in json.loads(log.read_text()) if item.get("kind", "implement") == kind]


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_large").execution_id


def events(application: HarnessApplication, workspace: Path, run: str, kind: str) -> list[Any]:
    with application._services(workspace) as services:
        return [item.payload for item in services.events.list(run) if item.event_type == kind]


def test_an_approved_plan_runs_each_subtask_with_its_own_gate(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "good")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)["execution"]
    assert status["currentPhase"] == PhaseId.PLANNING and status["status"] == ResultStatus.BLOCKED
    plan = application.plan(python_workspace, run)
    assert plan["status"] == "PROPOSED" and len(plan["subtasks"]) == 2
    with pytest.raises(PolicyViolationError, match="digest"):
        application.decide_plan(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE,
            digest="sha256:stale",
            rationale="ok",
            actor_id="human.reviewer",
        )
    result = application.decide_plan(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        digest=plan["digest"],
        rationale="Two halves are fine",
        actor_id="human.reviewer",
    )
    assert result["execution"]["currentPhase"] == PhaseId.DECISION
    implements = calls(log, "implement")
    assert [[r["requirement_id"] for r in item["task"]["requirements"]] for item in implements] == [
        ["R1", "R2"],
        ["R3", "R4"],
    ]
    # The task's constraints reach every sub-task; the sub-task's own are added.
    assert "Keep every function pure." in implements[1]["task"]["constraints"]
    assert "Use no globals." in implements[0]["task"]["constraints"]
    completed = events(application, python_workspace, run, "subtask.completed")
    assert [item["gateStatus"] for item in completed] == ["PASSED", "PASSED"]
    assert len(calls(log, "plan")) == 1
    assert application.status(python_workspace, run)["gate"]["status"] == ResultStatus.PASSED


def test_a_rejected_plan_keeps_the_task_whole(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "good")
    application, run = start(python_workspace, tmp_path)
    plan = application.plan(python_workspace, run)
    application.decide_plan(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.REJECT,
        digest=plan["digest"],
        rationale="Small enough",
        actor_id="human.reviewer",
    )
    [implement] = calls(log, "implement")
    assert len(implement["task"]["requirements"]) == 4


def test_a_plan_that_is_not_a_partition_is_a_finding(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "bad")
    application, run = start(python_workspace, tmp_path)
    [finding] = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "planning.plan-malformed"
    ]
    assert finding.severity is FindingSeverity.HIGH
    assert "R3" in finding.message


def no_corrections(workspace: Path) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["runtime"]["verificationCorrections"] = 0
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_a_failing_subtask_stops_the_dependent_ones(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "failfirst")
    no_corrections(python_workspace)
    application, run = start(python_workspace, tmp_path)
    plan = application.plan(python_workspace, run)
    result = application.decide_plan(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        digest=plan["digest"],
        rationale="ok",
        actor_id="human.reviewer",
    )
    assert result["execution"]["currentPhase"] == PhaseId.VERIFICATION
    assert len(calls(log, "implement")) == 1
    assert [
        item["index"] for item in events(application, python_workspace, run, "subtask.started")
    ] == [1]


def test_adaptive_granularity_splits_only_after_a_failure(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        "adaptive",
        planning={"decomposition": "agent", "threshold": 2, "granularity": "adaptive"},
    )
    no_corrections(python_workspace)
    application, run = start(python_workspace, tmp_path)
    # The capable model started with the whole task; its failure sent the run back to PLANNING.
    assert len(calls(log, "implement")) == 1
    assert events(application, python_workspace, run, "planning.decomposition.deferred")
    assert events(application, python_workspace, run, "planning.split-on-failure")
    status = application.status(python_workspace, run)["execution"]
    assert status["currentPhase"] == PhaseId.PLANNING
    assert len(calls(log, "plan")) == 1
