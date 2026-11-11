"""Low friction for small changes (#58) and the plan-approval checkpoint (#8): the fast lane
and its escalation, the pre-authorised approval, batch decisions, ``harness do``, change types
and the faster verification of the fast lane."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.application.friction import BatchItem
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.domain.models import AgentInvocation, HumanDecision, utc_now
from governed_harness.orchestration import friction as friction_module
from governed_harness.orchestration.engine import EngineServices
from governed_harness.telemetry.metrics import human_interactions

GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
TEST = (
    "from sample import apply_discount\n\n\n"
    "def test_below_threshold() -> None:\n"
    "    assert apply_discount(99, 100, 0.1) == 99\n\n\n"
    "def test_at_threshold() -> None:\n"
    "    assert apply_discount(100, 100, 0.1) == 90\n"
)
FRICTION: dict[str, Any] = {
    "fastLane": {
        "mode": "auto",
        "verification": {"affectedTestsFirst": True, "parallel": True, "cache": True},
    },
    "preAuthorization": {"mode": "allow", "defaultHours": 24, "maxHours": 72},
    "changeTypes": True,
    "planApproval": "risk",
    "targets": {"S": {"interactions": 1, "minutes": 30}},
}
AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
kind = request.get("kind", "implement")
results = {"clarify": {"questions": []}, "review": {"findings": []}, "plan": {"subtasks": []},
           "acceptance": {"tests": []}, "locate": {"locations": []}}
if kind == "implement":
    for path, text in json.loads(Path("agent-writes.json").read_text()).items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    answer = {"status": "PASSED", "summary": "written"}
else:
    answer = {"status": "PASSED", "summary": kind, "result": results.get(kind, {})}
answer["usage"] = {"inputTokens": 100, "outputTokens": 10}
print(json.dumps(answer))
"""


def configure(workspace: Path, friction: dict[str, Any] | None = None, **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["friction"] = FRICTION if friction is None else friction
    for key, value in sections.items():
        config[key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def command_agent(workspace: Path, writes: dict[str, str]) -> None:
    """A command provider that writes ``writes`` on implement and answers the read-only kinds
    with nothing; ignored by Git so the ChangeSet holds only what it writes."""
    (workspace / "agent.py").write_text(AGENT, encoding="utf-8")
    (workspace / "agent-writes.json").write_text(json.dumps(writes), encoding="utf-8")
    ignore = workspace / ".gitignore"
    previous = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    ignore.write_text(previous + "agent.py\nagent-writes.json\n.harness/\n", encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {"fixture": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"]["agentSandbox"] = "off"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def patch_task(task_id: str, patches: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "taskId": task_id,
        "title": "Discount at the threshold",
        "intent": "Apply the configured discount at or above the threshold.",
        "acceptanceCriteria": [
            {"criterionId": "ac_at", "text": "apply_discount(100, 100, 0.1) returns 90."}
        ],
        "implementation": {"mode": "patch", "patches": patches},
    }
    value.update(extra)
    return value


def replace(path: str, content: str) -> dict[str, Any]:
    return {"path": path, "operation": "replace", "content": content}


def create(path: str, content: str) -> dict[str, Any]:
    return {"path": path, "operation": "create", "content": content}


def start(workspace: Path, tmp_path: Path, value: dict[str, Any]) -> tuple[HarnessApplication, Any]:
    source = tmp_path / f"{value['taskId']}.yaml"
    source.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    application = HarnessApplication()
    created = application.create_task(workspace, source)
    return application, application.start_run(workspace, created.task_id)


def events(workspace: Path, run: str) -> list[Any]:
    services = EngineServices.open(ConfigurationResolver().resolve(workspace))
    try:
        return services.events.list(run)
    finally:
        services.close()


def records(workspace: Path, kind: str, model: Any, run: str) -> list[Any]:
    services = EngineServices.open(ConfigurationResolver().resolve(workspace))
    try:
        return services.state.list(kind, model, execution_id=run)
    finally:
        services.close()


def of_type(items: list[Any], event_type: str) -> list[Any]:
    return [item for item in items if item.event_type == event_type]


# ----- the lane ---------------------------------------------------------------------------------
def test_small_task_takes_the_fast_lane_and_skips_agent_steps(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        intake={"criteriaPolicy": "warn", "ambiguityReview": "agent"},
        review={"agentReview": "enforce"},
    )
    application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_fast",
            [replace("src/sample/pricing.py", GOOD), replace("tests/test_pricing.py", TEST)],
        ),
    )
    assert run.current_phase is PhaseId.DECISION
    assert run.status is ResultStatus.BLOCKED
    chain = events(python_workspace, run.execution_id)
    classified = of_type(chain, "lane.classified")
    assert classified[0].payload["lane"] == "fast"
    assert classified[0].payload["reasons"] == ["size S and no risk flag"]
    skipped = {item.payload["step"] for item in of_type(chain, "lane.step.skipped")}
    assert {"ambiguityReview", "decomposition"} <= skipped
    assert of_type(chain, "review.agent.skipped")[0].payload["reason"].startswith("fast lane")
    kinds = [
        item.call_kind
        for item in records(python_workspace, "agent_invocation", AgentInvocation, run.execution_id)
    ]
    assert "clarify" not in kinds
    assert "review" not in kinds
    assert of_type(chain, "plan.approval.skipped")[0].payload["reason"] == (
        "size S and no risk flag"
    )
    brief = application.review(python_workspace, run.execution_id)
    assert brief["friction"]["lane"]["lane"] == "fast"
    assert brief["friction"]["changeType"] == "code"


def test_large_task_takes_the_full_lane(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, friction={"fastLane": {"mode": "auto"}})
    requirements = [
        {"requirementId": f"req_{index}", "text": f"Requirement {index} holds."}
        for index in range(6)
    ]
    _application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_full",
            [replace("src/sample/pricing.py", GOOD)],
            requirements=requirements,
        ),
    )
    chain = events(python_workspace, run.execution_id)
    lane = of_type(chain, "lane.classified")[0].payload
    assert lane["lane"] == "full"
    assert lane["size"] == "M"
    assert lane["reasons"][0].startswith("size M (requirements=6>5")
    assert not of_type(chain, "lane.step.skipped")


def test_a_task_asking_for_stronger_evidence_takes_the_full_lane(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, friction={"fastLane": {"mode": "auto"}})
    _application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_checked",
            [replace("src/sample/pricing.py", GOOD)],
            checklist=[{"id": "look", "text": "The receipt shows the discount line"}],
        ),
    )
    lane = of_type(events(python_workspace, run.execution_id), "lane.classified")[0].payload
    assert lane["lane"] == "full"
    assert lane["reasons"] == [
        "the task declares verification beyond L1 (probes, deferred or manual)"
    ]


def test_a_risk_factor_takes_the_run_out_of_the_fast_lane(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, review={"agentReview": "enforce"})
    auth = "def check_password(stored: str, given: str) -> bool:\n    return stored == given\n"
    _application, run = start(
        python_workspace,
        tmp_path,
        patch_task("task_risky", [create("src/sample/auth.py", auth)]),
    )
    chain = events(python_workspace, run.execution_id)
    escalated = of_type(chain, "lane.escalated")
    assert escalated
    assert escalated[0].payload["reasons"] == ["risk factor authentication"]
    assert of_type(chain, "review.agent.signals")
    kinds = [
        item.call_kind
        for item in records(python_workspace, "agent_invocation", AgentInvocation, run.execution_id)
    ]
    assert "review" in kinds


# ----- the pre-authorised approval -----------------------------------------------------------------
def test_harness_do_with_a_pre_authorised_approval_closes_the_run(python_workspace: Path) -> None:
    configure(python_workspace)
    command_agent(
        python_workspace,
        {"README.md": "# Sample\n\nThe discount applies at or above the threshold.\n"},
    )
    application = HarnessApplication()
    result = application.do(
        python_workspace,
        "Document the discount rule in the README",
        criteria=("README.md states that the discount applies at or above the threshold",),
        pre_approve=True,
        actor_id="human.tester",
    )
    assert result["run"]["status"] == "PASSED"
    assert result["run"]["currentPhase"] == "CLOSURE"
    assert result["lane"]["lane"] == "fast"
    run = result["run"]["executionId"]
    decisions = records(python_workspace, "decision", HumanDecision, run)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.actor.actor_id == "human.tester"
    assert decision.pre_authorization_id == result["preAuthorization"]["preAuthorizationId"]
    assert decision.decision is DecisionKind.APPROVE
    chain = events(python_workspace, run)
    # Confirming the contract and approving in advance is one act: one interaction.
    assert human_interactions(chain) == {"preauthorization": 1}
    assert of_type(chain, "decision.preauthorization.applied")
    # A documentation-only change needs no requirement traceability.
    assert of_type(chain, "change-type.exempted")


def test_a_pre_authorisation_is_not_applied_on_a_risk_factor(python_workspace: Path) -> None:
    configure(python_workspace)
    command_agent(
        python_workspace,
        {"src/sample/auth.py": "def login(password: str) -> bool:\n    return bool(password)\n"},
    )
    application = HarnessApplication()
    result = application.do(
        python_workspace,
        "Add a login helper",
        criteria=("login('x') returns True",),
        pre_approve=True,
        actor_id="human.tester",
    )
    assert result["run"]["currentPhase"] == "DECISION"
    assert result["run"]["status"] == "BLOCKED"
    run = result["run"]["executionId"]
    refused = of_type(events(python_workspace, run), "decision.preauthorization.not-applied")
    assert "risk factor(s) authentication" in refused[0].payload["reasons"]
    assert not records(python_workspace, "decision", HumanDecision, run)


def test_an_expired_pre_authorisation_asks_the_person(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(python_workspace, intake={"criteriaPolicy": "warn", "operationalContract": "enforce"})
    application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_expiring",
            [replace("src/sample/pricing.py", GOOD), replace("tests/test_pricing.py", TEST)],
            contract={
                "objective": "Discount at the threshold",
                "examples": ["apply_discount(100, 100, 0.1) returns 90"],
                "scope": ["src/sample/pricing.py"],
                "definitionOfDone": ["the unit test passes"],
                "verificationLevel": "L1",
                "branch": "harness/discount",
                "push": False,
                "createPullRequest": False,
                "comment": False,
                "coverageThreshold": 0,
            },
        ),
    )
    assert run.current_phase is PhaseId.INTENT
    assert run.status is ResultStatus.BLOCKED
    contract = application.list_clarifications(python_workspace, "task_expiring")
    digest = next(
        event.payload["digest"]
        for event in events(python_workspace, run.execution_id)
        if event.event_type == "contract.summarized"
    )
    with pytest.raises(PolicyViolationError):
        application.confirm_contract(
            python_workspace,
            task_id="task_expiring",
            digest="sha256:" + "0" * 64,
            actor_id="human.tester",
            pre_approve=True,
        )
    confirmed = application.confirm_contract(
        python_workspace,
        task_id="task_expiring",
        digest=digest,
        actor_id="human.tester",
        pre_approve=True,
        hours=1,
    )
    assert confirmed["preAuthorization"]["contractDigest"] == digest
    assert contract["taskId"] == "task_expiring"
    later = utc_now() + timedelta(hours=2)
    monkeypatch.setattr(friction_module, "utc_now", lambda: later)
    execution = application.continue_run(python_workspace, run.execution_id)
    assert execution.current_phase is PhaseId.DECISION
    assert execution.status is ResultStatus.BLOCKED
    refused = of_type(
        events(python_workspace, run.execution_id), "decision.preauthorization.not-applied"
    )
    assert any(reason.startswith("it expired at") for reason in refused[0].payload["reasons"])


def test_pre_authorisation_needs_the_setting(python_workspace: Path) -> None:
    configure(python_workspace, friction={"fastLane": {"mode": "auto"}})
    command_agent(python_workspace, {"README.md": "# Sample\n\nMore.\n"})
    application = HarnessApplication()
    with pytest.raises(PolicyViolationError, match="preAuthorization"):
        application.do(python_workspace, "Document more", pre_approve=True, actor_id="human.tester")


def test_an_agent_cannot_pre_authorise(python_workspace: Path) -> None:
    configure(python_workspace)
    command_agent(python_workspace, {"README.md": "# Sample\n\nMore.\n"})
    application = HarnessApplication()
    with pytest.raises(PolicyViolationError):
        application.do(python_workspace, "Document more", pre_approve=True, actor_id="agent.codex")


# ----- the plan-approval checkpoint (#8) ------------------------------------------------------------
def risky_task(task_id: str) -> dict[str, Any]:
    return patch_task(
        task_id,
        [replace("src/sample/pricing.py", GOOD)],
        intent="Check the password before applying the discount.",
    )


def test_a_risky_task_waits_for_its_plan_to_be_approved(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    application, run = start(python_workspace, tmp_path, risky_task("task_plan"))
    assert run.current_phase is PhaseId.PLANNING
    assert run.status is ResultStatus.BLOCKED
    shown = application.plan(python_workspace, run.execution_id)
    approval = shown["approval"]
    assert approval["status"] == "PENDING"
    assert approval["reasons"] == ["risk flag(s) security"]
    with pytest.raises(PolicyViolationError):
        application.decide_plan(
            python_workspace,
            execution_id=run.execution_id,
            decision=DecisionKind.APPROVE,
            digest="sha256:" + "1" * 64,
            rationale="wrong digest",
            actor_id="human.tester",
        )
    decided = application.decide_plan(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.APPROVE,
        digest=approval["digest"],
        rationale="The plan touches only pricing",
        actor_id="human.tester",
    )
    assert decided["execution"]["currentPhase"] == "DECISION"
    chain = events(python_workspace, run.execution_id)
    assert human_interactions(chain)["plan"] == 1


def test_a_rejected_plan_ends_the_run(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace)
    application, run = start(python_workspace, tmp_path, risky_task("task_plan_rejected"))
    digest = application.plan(python_workspace, run.execution_id)["approval"]["digest"]
    decided = application.decide_plan(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.REJECT,
        digest=digest,
        rationale="Not now",
        actor_id="human.tester",
    )
    assert decided["execution"]["status"] == "FAILED"


def test_a_pre_authorisation_covers_the_plan_approval(python_workspace: Path) -> None:
    configure(python_workspace)
    command_agent(
        python_workspace,
        {"src/sample/pricing.py": GOOD},
    )
    result = HarnessApplication().do(
        python_workspace,
        "Check the password rule before the discount",
        criteria=("apply_discount(100, 100, 0.1) returns 90",),
        pre_approve=True,
        actor_id="human.tester",
    )
    run = result["run"]["executionId"]
    chain = events(python_workspace, run)
    covered = of_type(chain, "plan.approval.covered")
    assert covered
    assert covered[0].payload["actorId"] == "human.tester"
    # The task carries a risk flag, so the final approval is the person's as usual.
    assert result["run"]["currentPhase"] == "DECISION"


# ----- batch decisions ------------------------------------------------------------------------------
def test_batch_decisions_are_each_bound_to_their_digest(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, friction={"fastLane": {"mode": "auto"}})
    application, first = start(
        python_workspace,
        tmp_path,
        patch_task("task_one", [replace("src/sample/pricing.py", GOOD)]),
    )
    digest = application.status(python_workspace, first.execution_id)["execution"][
        "changeSetDigest"
    ]
    result = application.decide_batch(
        python_workspace,
        [
            BatchItem(first.execution_id, DecisionKind.APPROVE, digest),
            BatchItem(first.execution_id, DecisionKind.APPROVE, "sha256:" + "2" * 64),
        ],
        rationale="Reviewed in the inbox",
        actor_id="human.tester",
    )
    assert result["recorded"] == 1
    assert result["refused"] == 1
    assert result["results"][0]["execution"]["status"] == "PASSED"


def test_inbox_approve_from_the_command_line(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, friction={"fastLane": {"mode": "auto"}})
    application, run = start(
        python_workspace,
        tmp_path,
        patch_task("task_cli", [replace("src/sample/pricing.py", GOOD)]),
    )
    digest = application.status(python_workspace, run.execution_id)["execution"]["changeSetDigest"]
    runner = CliRunner()
    stale = runner.invoke(
        app,
        [
            "inbox",
            "--path",
            str(python_workspace),
            "--approve",
            f"{run.execution_id}=sha256:{'3' * 64}",
            "--rationale",
            "ok",
            "--actor",
            "human.tester",
            "--json",
        ],
    )
    assert stale.exit_code == 5
    recorded = runner.invoke(
        app,
        [
            "inbox",
            "--path",
            str(python_workspace),
            "--approve",
            f"{run.execution_id}={digest}",
            "--rationale",
            "ok",
            "--actor",
            "human.tester",
            "--json",
        ],
    )
    assert recorded.exit_code == 0, recorded.output
    assert json.loads(recorded.output)["recorded"] == 1
    missing = runner.invoke(
        app, ["inbox", "--path", str(python_workspace), "--approve", "run_x=nodigest"]
    )
    assert missing.exit_code == 2


# ----- change types and faster verification ---------------------------------------------------------
def test_documentation_change_needs_no_traceability(python_workspace: Path, tmp_path: Path) -> None:
    configure(
        python_workspace,
        verification={"requirementTraceability": "enforce"},
        friction={"changeTypes": True},
    )
    application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_docs",
            [create("src/sample/DISCOUNT.md", "# Discount\n\nAt or above the threshold.\n")],
            requirements=[{"requirementId": "req_docs", "text": "Document the discount."}],
        ),
    )
    assert run.current_phase is PhaseId.DECISION
    status = application.status(python_workspace, run.execution_id)
    assert status["gate"]["status"] == "PASSED"
    validations = {
        item["validatorId"]: item
        for item in application.review(python_workspace, run.execution_id)["verified"][
            "validations"
        ]
    }
    assert validations["traceability.requirements"]["status"] == "NOT_APPLICABLE"


def test_a_failing_affected_test_stops_the_attempt_early(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    broken = TEST + "\n\ndef test_broken() -> None:\n    assert apply_discount(1, 2, 0.1) == 5\n"
    _application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_broken",
            [replace("src/sample/pricing.py", GOOD), replace("tests/test_pricing.py", broken)],
        ),
    )
    assert run.current_phase is PhaseId.VERIFICATION
    chain = events(python_workspace, run.execution_id)
    affected = of_type(chain, "verification.affected-tests")
    assert affected[0].payload["tests"] == ["tests/test_pricing.py"]
    assert affected[0].payload["status"] == "FAILED"
    completed = [
        item
        for item in of_type(chain, "validation.completed")
        if item.payload["validator_id"] == "python.pytest"
    ]
    assert not completed


def test_validators_run_side_by_side_and_are_reused_for_the_same_digest(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    application, run = start(
        python_workspace,
        tmp_path,
        patch_task(
            "task_reuse",
            [replace("src/sample/pricing.py", GOOD), replace("tests/test_pricing.py", TEST)],
        ),
    )
    chain = events(python_workspace, run.execution_id)
    assert of_type(chain, "verification.parallel")
    digest = application.status(python_workspace, run.execution_id)["execution"]["changeSetDigest"]
    application.decide_gate(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=digest,
        actor_id="human.tester",
        rationale="Run it again",
    )
    again = application.continue_run(python_workspace, run.execution_id)
    assert again.change_set_digest == digest
    reused = of_type(events(python_workspace, run.execution_id), "validator.reused")
    assert {item.payload["validatorId"] for item in reused} >= {"python.pytest"}


def test_without_the_section_nothing_changes(python_workspace: Path, tmp_path: Path) -> None:
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert "friction" not in resolved.project.model_dump(mode="json", by_alias=True)
    _application, run = start(
        python_workspace,
        tmp_path,
        patch_task("task_plain", [replace("src/sample/pricing.py", GOOD)]),
    )
    chain = events(python_workspace, run.execution_id)
    assert not [item for item in chain if item.event_type.startswith(("lane.", "plan.approval"))]
