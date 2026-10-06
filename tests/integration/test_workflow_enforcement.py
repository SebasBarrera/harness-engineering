"""Runs under governance.enforceWorkflow: exit gates, dependsOn and parallel validators (#3)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.configuration import resolver as resolver_module
from governed_harness.configuration.loader import load_builtin_workflow
from governed_harness.configuration.models import WorkflowDefinition
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Execution, PhaseExecution, utc_now
from governed_harness.orchestration import PHASE_ORDER
from governed_harness.orchestration.engine import PhaseOutcome, RunEngine

TASK = """\
taskId: {task_id}
title: Apply a discount at the threshold
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - requirementId: req_discount
    text: A subtotal at or above the threshold is reduced by the rate.
acceptanceCriteria:
  - criterionId: ac_at_threshold
    text: A subtotal of 100 with threshold 100 and rate 0.1 returns 90.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |


        def test_req_discount_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""


def configure(workspace: Path, **changes: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for dotted, value in changes.items():
        target = config
        *parents, last = dotted.split("__")
        for part in parents:
            target = target.setdefault(part, {})
        target[last] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def enforce(workspace: Path, **changes: Any) -> None:
    configure(workspace, governance__enforceWorkflow=True, **changes)


def start(workspace: Path, tmp_path: Path, task_id: str = "task_enforced") -> Execution:
    task_file = tmp_path / f"{task_id}.yaml"
    task_file.write_text(TASK.format(task_id=task_id), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, task_file)
    return application.start_run(workspace, task_id)


def events(workspace: Path, run_id: str, event_type: str | None = None) -> list[dict[str, Any]]:
    with HarnessApplication()._services(workspace) as services:
        items = [item.as_dict() for item in services.events.list(run_id)]
    return [item for item in items if event_type is None or item["eventType"] == event_type]


def decide(workspace: Path, run: Execution, decision: DecisionKind) -> Execution:
    _, execution = HarnessApplication().decide_gate(
        workspace,
        execution_id=run.execution_id,
        decision=decision,
        change_set_digest=run.change_set_digest or "",
        actor_id="human.reviewer",
        rationale="reviewed",
    )
    return execution


def test_approved_run_meets_every_exit_gate(python_workspace: Path, tmp_path: Path) -> None:
    enforce(python_workspace)
    pending = start(python_workspace, tmp_path)
    assert pending.current_phase is PhaseId.DECISION
    assert pending.status is ResultStatus.BLOCKED
    closed = decide(python_workspace, pending, DecisionKind.APPROVE_EXCEPTION)
    assert closed.status is ResultStatus.PASSED
    [schedule] = events(python_workspace, pending.execution_id, "workflow.schedule")
    assert schedule["payload"]["order"] == [item.value for item in PHASE_ORDER]
    assert schedule["payload"]["phases"] == "sequential"
    completed = [
        item["payload"]
        for item in events(python_workspace, pending.execution_id)
        if item["eventType"] == "phase.completed"
    ]
    passed = [item for item in completed if item["status"] == "PASSED"]
    assert [item["phaseId"] for item in passed] == [item.value for item in PHASE_ORDER]
    assert all(item["exitGateMet"] for item in passed)
    by_phase = {item["phaseId"]: item for item in passed}
    assert by_phase["DECISION"]["exitGate"] == "decision_recorded"
    assert [item["condition"] for item in by_phase["DECISION"]["exitConditions"]] == [
        "decision_approved"
    ]
    assert by_phase["CLOSURE"]["exitConditions"][0]["condition"] == "run_closed"
    assert not events(python_workspace, pending.execution_id, "phase.exit_gate.unmet")


def test_request_changes_and_reject_still_work(python_workspace: Path, tmp_path: Path) -> None:
    enforce(python_workspace)
    first = start(python_workspace, tmp_path, "task_changes")
    changed = decide(python_workspace, first, DecisionKind.REQUEST_CHANGES)
    assert changed.current_phase is PhaseId.IMPLEMENTATION
    again = HarnessApplication().continue_run(python_workspace, first.execution_id)
    assert again.current_phase is PhaseId.DECISION
    assert again.status is ResultStatus.BLOCKED
    assert again.change_set_digest != first.change_set_digest
    assert decide(python_workspace, again, DecisionKind.APPROVE_EXCEPTION).status is (
        ResultStatus.PASSED
    )
    second = start(python_workspace, tmp_path, "task_rejected")
    rejected = decide(python_workspace, second, DecisionKind.REJECT)
    assert rejected.status is ResultStatus.FAILED
    assert rejected.current_phase is PhaseId.DECISION
    for run in (first, second):
        assert not events(python_workspace, run.execution_id, "phase.exit_gate.unmet")


def _without_contract(self: RunEngine, execution: Execution, phase: PhaseExecution) -> PhaseOutcome:
    # A SPECIFICATION that reports success without freezing the acceptance contract.
    return PhaseOutcome(ResultStatus.PASSED, "Nothing recorded")


def test_unmet_exit_gate_blocks_with_its_reason(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enforce(python_workspace)
    original = RunEngine._phase_specification
    monkeypatch.setattr(RunEngine, "_phase_specification", _without_contract)
    blocked = start(python_workspace, tmp_path)
    assert blocked.current_phase is PhaseId.SPECIFICATION
    assert blocked.status is ResultStatus.BLOCKED
    summary = HarnessApplication().status(python_workspace, blocked.execution_id)["phases"][-1][
        "summary"
    ]
    assert "did not meet its exit gate specification_approved" in summary
    assert "no acceptance contract" in summary
    [unmet] = events(python_workspace, blocked.execution_id, "phase.exit_gate.unmet")
    assert unmet["payload"]["phaseId"] == "SPECIFICATION"
    assert unmet["payload"]["condition"] == "specification_approved"
    completed = events(python_workspace, blocked.execution_id, "phase.completed")[-1]["payload"]
    assert completed["status"] == "BLOCKED"
    assert completed["exitGateMet"] is False
    # The phase that leaves its record passes on the next attempt.
    monkeypatch.setattr(RunEngine, "_phase_specification", original)
    resumed = HarnessApplication().continue_run(python_workspace, blocked.execution_id)
    assert resumed.current_phase is PhaseId.DECISION


def test_without_the_key_the_gates_stay_labels(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    assert "enforceWorkflow" not in config.get("governance", {})
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert (
        "enforceWorkflow"
        not in resolved.model_dump(mode="json", by_alias=True)["project"]["governance"]
    )
    monkeypatch.setattr(RunEngine, "_phase_specification", _without_contract)
    run = start(python_workspace, tmp_path)
    # As in 1.0.0: the phase's own result decides; nothing is evaluated after it.
    assert run.current_phase is PhaseId.DECISION
    assert not events(python_workspace, run.execution_id, "workflow.schedule")
    assert all(
        "exitConditions" not in item["payload"]
        for item in events(python_workspace, run.execution_id, "phase.completed")
    )


def test_a_phase_waits_for_its_dependencies(python_workspace: Path, tmp_path: Path) -> None:
    enforce(python_workspace)
    run = start(python_workspace, tmp_path)
    application = HarnessApplication()
    with application._services(python_workspace) as services:
        # A later VERIFICATION attempt that did not pass: INDEPENDENT_REVIEW depends on it.
        failed = PhaseExecution(
            phase_execution_id=new_id("phase"),
            execution_id=run.execution_id,
            phase_id=PhaseId.VERIFICATION,
            status=ResultStatus.FAILED,
            attempt=99,
            started_at=utc_now(),
            finished_at=utc_now(),
        )
        services.state.put(
            "phase",
            failed.phase_execution_id,
            failed,
            execution_id=run.execution_id,
            project_id=run.project_id,
        )
        current = services.state.get("execution", run.execution_id, Execution)
        services.state.put(
            "execution",
            run.execution_id,
            current.model_copy(
                update={
                    "current_phase": PhaseId.INDEPENDENT_REVIEW,
                    "status": ResultStatus.PENDING,
                }
            ),
            execution_id=run.execution_id,
            project_id=run.project_id,
        )
    waiting = application.continue_run(python_workspace, run.execution_id)
    assert waiting.status is ResultStatus.BLOCKED
    assert waiting.current_phase is PhaseId.INDEPENDENT_REVIEW
    assert waiting.terminal_reason == (
        "INDEPENDENT_REVIEW cannot start: dependsOn VERIFICATION has no passed attempt"
    )
    [unmet] = events(python_workspace, run.execution_id, "phase.dependencies.unmet")
    assert unmet["payload"]["unmet"] == ["VERIFICATION"]


def test_unknown_exit_gate_is_a_configuration_error(
    python_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(workflow_id: str) -> WorkflowDefinition:
        workflow = load_builtin_workflow(workflow_id)
        phases = tuple(
            item.model_copy(update={"exit_gate": "looks_fine"})
            if item.phase_id is PhaseId.PLANNING
            else item
            for item in workflow.phases
        )
        return workflow.model_copy(update={"phases": phases})

    monkeypatch.setattr(resolver_module, "load_builtin_workflow", broken)
    ConfigurationResolver().resolve(python_workspace)  # without the key the name is a label
    enforce(python_workspace)
    resolver = ConfigurationResolver()
    with pytest.raises(ConfigurationError, match="unknown exitGate 'looks_fine'"):
        resolver.resolve(python_workspace)


PROBE = """\
import sys, time
with open(sys.argv[1], "a", encoding="utf-8") as handle:
    handle.write(f"{sys.argv[2]} start {time.time()}\\n")
time.sleep(1.5)
with open(sys.argv[1], "a", encoding="utf-8") as handle:
    handle.write(f"{sys.argv[2]} end {time.time()}\\n")
"""


def test_parallel_safe_validators_run_at_once(python_workspace: Path, tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    log = tmp_path / "probe.log"
    validators = [
        {
            "id": f"project.probe{name}",
            "command": [sys.executable, str(probe), str(log), name],
            "mandatory": False,
            "parallelSafe": True,
        }
        for name in ("a", "b")
    ]
    enforce(python_workspace, toolchain__validators=validators, runtime__maxParallel=2)
    run = start(python_workspace, tmp_path)
    assert run.current_phase is PhaseId.DECISION
    times: dict[str, dict[str, float]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        name, edge, value = line.split()
        times.setdefault(name, {})[edge] = float(value)
    # Both probes started before either ended.
    assert times["a"]["start"] < times["b"]["end"]
    assert times["b"]["start"] < times["a"]["end"]
    [parallel] = events(python_workspace, run.execution_id, "verification.validators.parallel")
    assert parallel["payload"] == {
        "validators": ["project.probea", "project.probeb"],
        "maxParallel": 2,
    }
    [schedule] = events(python_workspace, run.execution_id, "workflow.schedule")
    assert ["project.probea", "project.probeb"] in schedule["payload"]["validatorBatches"]
    # The results are recorded in the declared order.
    recorded = [
        item["payload"]["validator_id"]
        for item in events(python_workspace, run.execution_id, "validation.completed")
        if item["payload"]["validator_id"].startswith("project.probe")
    ]
    assert recorded == ["project.probea", "project.probeb"]


def test_parallel_safe_validators_wait_without_the_key(
    python_workspace: Path, tmp_path: Path
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE.replace("1.5", "0.2"), encoding="utf-8")
    log = tmp_path / "probe.log"
    validators = [
        {
            "id": f"project.probe{name}",
            "command": [sys.executable, str(probe), str(log), name],
            "mandatory": False,
            "parallelSafe": True,
        }
        for name in ("a", "b")
    ]
    configure(python_workspace, toolchain__validators=validators, runtime__maxParallel=2)
    run = start(python_workspace, tmp_path)
    lines = [line.split()[:2] for line in log.read_text(encoding="utf-8").splitlines()]
    assert lines == [["a", "start"], ["a", "end"], ["b", "start"], ["b", "end"]]
    assert not events(python_workspace, run.execution_id, "verification.validators.parallel")
