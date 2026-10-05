"""Model and effort routing of agent calls (agentRouting, #44): tiered selection, escalation
after a failed verification (effort before model), the cap, evidence and calibration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, PhaseId
from governed_harness.domain.models import AgentInvocation

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
fixed = len(calls) > FAILURES
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    + ("    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n" if fixed
       else "    return subtotal\\n")
)
Path("tests/test_pricing.py").write_text(
    "from sample import apply_discount\\n\\n"
    "def test_r1_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n"
)
print(json.dumps({"status": "PASSED", "summary": "done", "usage": {"costUsd": 0.25}}))
"""

TASK = (
    "taskId: task_route\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "requirements:\n"
    "  - requirementId: R1\n"
    "    text: Apply the discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)


def configure(
    workspace: Path, tmp_path: Path, failures: int, routing: dict[str, Any], corrections: int = 2
) -> Path:
    log = tmp_path / "calls.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("FAILURES", str(failures))
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"].update(
        {
            "agentSandbox": "off",
            "verificationCorrections": corrections,
            "providerFeedback": True,
            "providerRetries": 0,
        }
    )
    config["agentRouting"] = routing
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_route").execution_id


TIERED = {"mode": "tiered", "families": {"fixture_agent": "claude-code"}, "maxEscalations": 2}


def test_escalation_raises_effort_before_model(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, failures=2, routing=TIERED)
    application, run = start(python_workspace, tmp_path)
    calls = json.loads(log.read_text())
    rungs = [(item["routing"]["model"], item["routing"]["effort"]) for item in calls]
    assert rungs == [
        ("claude-sonnet-5-5", "medium"),
        ("claude-sonnet-5-5", "high"),
        ("claude-opus-5-5", "high"),
    ]
    assert calls[0]["routing"]["flags"] == ["--model", "claude-sonnet-5-5", "--effort", "medium"]
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )
    with application._services(python_workspace) as services:
        decided = [
            item.payload
            for item in services.events.list(run)
            if item.event_type == "agent.routing.decided"
        ]
        invocations = services.state.list("agent_invocation", AgentInvocation, execution_id=run)
    assert [item["rule"] for item in decided][:2] == [
        "tier:implement:S",
        "escalation:1:implement:S",
    ]
    assert all(item["policyDigest"] == decided[0]["policyDigest"] for item in decided)
    assert [item.effort for item in invocations] == ["medium", "high", "high"]


def test_escalations_are_capped(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        failures=2,
        routing={**TIERED, "maxEscalations": 1},
    )
    application, run = start(python_workspace, tmp_path)
    rungs = [item["routing"]["effort"] for item in json.loads(log.read_text())]
    assert rungs == ["medium", "high", "high"]
    with application._services(python_workspace) as services:
        assert [item for item in services.events.list(run) if item.event_type.endswith("capped")]


def test_fixed_mode_keeps_the_provider_model(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, failures=0, routing={"mode": "fixed"})
    start(python_workspace, tmp_path)
    [call] = json.loads(log.read_text())
    assert "routing" not in call


def test_calibration_reports_cost_per_approved_task(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, failures=0, routing=TIERED)
    application, run = start(python_workspace, tmp_path)
    digest = application.status(python_workspace, run)["execution"]["changeSetDigest"]
    application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="ok",
    )
    report = application.routing_calibration(python_workspace)
    [group] = [item for item in report["groups"] if item["callKind"] == "implement"]
    assert group["approvedRuns"] == 1
    assert group["costPerApprovedTask"] == 0.25
