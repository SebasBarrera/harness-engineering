"""Frozen acceptance tests the agent cannot rewrite and may dispute (#94).

``verification.acceptanceTests.protect`` keeps the frozen files from the implementing agent and
``disputes: person`` makes a VERIFICATION that fails only on frozen tests the agent disputes
wait for a person, who upholds the tests or has them written again. The agent is a fixture
command provider: its first acceptance proposal expects a wrong value (as in the wave 9 pilot)
and the one it writes for a revision expects the right one."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.agents.self_report import parse_self_report
from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, FindingSeverity
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.orchestration.acceptance_dispute import DisputeDecision
from governed_harness.orchestration.engine import RunEngine
from governed_harness.runtime.sandbox import SandboxHost, build_sandbox

FROZEN = "tests/acceptance/test_ac_1.py"
REASON = "apply_discount(100, 100, 0.1) is 90, the test expects 91"
TASK = (
    "taskId: task_dispute\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    f"  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py, {FROZEN}]\n"
)

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
if request.get("kind") == "acceptance":
    expected = 90 if (request.get("revision") or MODE in ("tamper", "right")) else 91
    content = (
        "from sample import apply_discount\\n\\n\\n"
        f"def test_ac_1() -> None:\\n    assert apply_discount(100, 100, 0.1) == {expected}\\n"
    )
    print(json.dumps({"status": "PASSED", "summary": "1 file",
                      "result": {"tests": [{"path": FROZEN, "content": content}]}}))
    sys.exit(0)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
if MODE == "broken":
    Path("tests/test_pricing.py").write_text("def test_broken() -> None:\\n    assert 1 == 2\\n")
if MODE == "tamper":
    Path(FROZEN).write_text("def test_ac_1() -> None:\\n    assert True\\n")
report = {"assumptions": [], "alternativesDiscarded": [], "lowConfidenceAreas": [],
          "unrequestedChanges": []}
if MODE in ("dispute", "broken"):
    report["disputedAcceptanceTests"] = [{"test": FROZEN + "::test_ac_1", "reason": REASON}]
print(json.dumps({"status": "PASSED", "summary": "implemented", "selfReport": report}))
"""


def configure(
    workspace: Path,
    tmp_path: Path,
    mode: str,
    acceptance: dict[str, Any],
    corrections: int = 0,
) -> Path:
    log = tmp_path / "calls.json"
    script = (
        AGENT.replace("LOG", repr(str(log)))
        .replace("MODE", repr(mode))
        .replace("FROZEN", repr(FROZEN))
        .replace("REASON", repr(REASON))
    )
    (tmp_path / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", str(tmp_path / "agent.py")]}
    }
    config["runtime"].update(
        {"agentSandbox": "off", "providerRetries": 0, "verificationCorrections": corrections}
    )
    config["provenance"] = {"selfReport": True}
    config["verification"] = {
        "requirementTraceability": "off",
        "outputParsers": True,
        "acceptanceTests": {"mode": "agent", **acceptance},
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    """Create the task, start the run and approve the proposed acceptance tests."""
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    run = application.start_run(workspace, "task_dispute").execution_id
    approve(application, workspace, run)
    return application, run


def approve(application: HarnessApplication, workspace: Path, run: str) -> dict[str, Any]:
    return application.decide_acceptance(
        workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        digest=application.acceptance(workspace, run)["digest"],
        rationale="They test the criterion",
        actor_id="human.reviewer",
    )


def where(application: HarnessApplication, workspace: Path, run: str) -> tuple[str, str]:
    execution = application.status(workspace, run)["execution"]
    return execution["currentPhase"], execution["status"]


def decide(
    application: HarnessApplication, workspace: Path, run: str, decision: DisputeDecision
) -> dict[str, Any]:
    return application.decide_acceptance_dispute(
        workspace,
        execution_id=run,
        decision=decision,
        digest=application.acceptance(workspace, run)["dispute"]["digest"],
        rationale="The criterion says 90",
        actor_id="human.reviewer",
    )


def calls(log: Path, kind: str) -> list[dict[str, Any]]:
    return [item for item in json.loads(log.read_text()) if item.get("kind", "implement") == kind]


def events(application: HarnessApplication, workspace: Path, run: str) -> list[str]:
    lines = application.trace(workspace, run, "jsonl").decode().splitlines()
    return [json.loads(line)["eventType"] for line in lines if line.strip()]


DISPUTES = {"disputes": "person"}


# ----- the dispute ----------------------------------------------------------------------------
def test_without_the_key_a_run_with_a_wrong_frozen_test_only_fails(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "dispute", {})
    application, run = start(python_workspace, tmp_path)
    assert where(application, python_workspace, run) == ("VERIFICATION", "FAILED")
    assert "dispute" not in application.acceptance(python_workspace, run)


def test_a_disputed_frozen_test_waits_for_a_person(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "dispute", DISPUTES)
    application, run = start(python_workspace, tmp_path)
    assert where(application, python_workspace, run) == ("VERIFICATION", "BLOCKED")
    [entry] = [item for item in application.inbox(python_workspace) if item["executionId"] == run]
    assert entry["kind"] == "acceptance-dispute"
    assert entry["disputed"] == [{"test": f"{FROZEN}::test_ac_1", "reason": REASON}]
    assert REASON in entry["summary"]
    assert f"harness acceptance dispute --run {run}" in entry["next"]
    dispute = application.acceptance(python_workspace, run)["dispute"]
    assert dispute["status"] == "OPEN"
    assert entry["digest"] == dispute["digest"]
    # Continuing the run does not verify again: it still waits.
    application.continue_run(python_workspace, run)
    assert where(application, python_workspace, run) == ("VERIFICATION", "BLOCKED")
    assert events(application, python_workspace, run).count("acceptance.dispute.opened") == 1


def test_the_implement_request_says_how_to_dispute(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "dispute", DISPUTES)
    start(python_workspace, tmp_path)
    [implement] = calls(log, "implement")
    assert "disputedAcceptanceTests" in implement["acceptanceTests"]["dispute"]


def test_only_a_person_decides_and_the_digest_binds(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "dispute", DISPUTES)
    application, run = start(python_workspace, tmp_path)
    digest = application.acceptance(python_workspace, run)["dispute"]["digest"]
    with pytest.raises(PolicyViolationError):
        application.decide_acceptance_dispute(
            python_workspace,
            execution_id=run,
            decision=DisputeDecision.REVISE,
            digest=digest,
            rationale="x",
            actor_id="agent.fixture_agent",
        )
    with pytest.raises(PolicyViolationError):
        application.decide_acceptance_dispute(
            python_workspace,
            execution_id=run,
            decision=DisputeDecision.REVISE,
            digest="sha256:" + "0" * 64,
            rationale="x",
            actor_id="human.reviewer",
        )
    assert where(application, python_workspace, run) == ("VERIFICATION", "BLOCKED")


def test_no_dispute_without_a_report_of_one(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "silent", DISPUTES)
    application, run = start(python_workspace, tmp_path)
    assert where(application, python_workspace, run) == ("VERIFICATION", "FAILED")
    with pytest.raises(NotFoundError):
        application.decide_acceptance_dispute(
            python_workspace,
            execution_id=run,
            decision=DisputeDecision.UPHOLD,
            digest="sha256:" + "0" * 64,
            rationale="x",
            actor_id="human.reviewer",
        )


def test_no_dispute_when_something_else_fails_too(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "broken", DISPUTES)
    application, run = start(python_workspace, tmp_path)
    assert where(application, python_workspace, run) == ("VERIFICATION", "FAILED")
    assert "dispute" not in application.acceptance(python_workspace, run)


def test_uphold_goes_on_to_the_corrections_and_fails_as_before(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "dispute", DISPUTES, corrections=1)
    application, run = start(python_workspace, tmp_path)
    result = decide(application, python_workspace, run, DisputeDecision.UPHOLD)
    assert result["dispute"]["status"] == "UPHELD"
    assert where(application, python_workspace, run) == ("VERIFICATION", "FAILED")
    seen = events(application, python_workspace, run)
    assert seen.count("correction.authorized") == 1
    assert seen.count("acceptance.dispute.opened") == 1
    first, second = calls(log, "implement")
    assert "upheld" not in first["acceptanceTests"]
    assert second["acceptanceTests"]["upheld"]["rationale"] == "The criterion says 90"
    assert (python_workspace / FROZEN).is_file()


def test_revise_returns_to_specification_and_verifies_the_same_change(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "dispute", DISPUTES)
    application, run = start(python_workspace, tmp_path)
    first = application.acceptance(python_workspace, run)
    result = decide(application, python_workspace, run, DisputeDecision.REVISE)
    assert result["dispute"]["status"] == "REVISED"
    assert where(application, python_workspace, run) == ("SPECIFICATION", "BLOCKED")
    assert not (python_workspace / FROZEN).exists()
    proposal = application.acceptance(python_workspace, run)
    assert proposal["status"] == "PROPOSED"
    assert proposal["digest"] != first["digest"]
    revision = calls(log, "acceptance")[1]["revision"]
    assert revision["disputed"] == [{"test": f"{FROZEN}::test_ac_1", "reason": REASON}]
    assert revision["rationale"] == "The criterion says 90"
    approved = approve(application, python_workspace, run)
    assert approved["execution"]["currentPhase"] == "DECISION"
    state = approved["acceptanceTests"]
    assert state["revision"] == 1
    assert state["previous"] == [
        {
            "digest": first["digest"],
            "frozen": first["frozen"],
            "decidedBy": "human.reviewer",
            "rationale": "They test the criterion",
            "ref": first["ref"],
        }
    ]
    assert state["frozen"] != first["frozen"]
    # The new tests ran on a copy of the baseline, where the discount is not applied yet.
    assert state["failBefore"]["status"] == "FAILED"
    assert len(calls(log, "implement")) == 1
    seen = events(application, python_workspace, run)
    assert seen.count("acceptance.revision.resumed") == 1
    assert not [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "acceptance.passes-before"
    ]


# ----- write protection -----------------------------------------------------------------------
def test_without_protect_a_rewritten_frozen_test_stays_rewritten(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "tamper", {})
    application, run = start(python_workspace, tmp_path)
    assert "assert True" in (python_workspace / FROZEN).read_text()
    rules = [item.rule_id for item in application.list_findings(python_workspace, run)]
    assert "acceptance.modified" in rules


def test_protect_puts_a_rewritten_frozen_test_back(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "tamper", {"protect": True})
    application, run = start(python_workspace, tmp_path)
    assert "== 90" in (python_workspace / FROZEN).read_text()
    found = application.list_findings(python_workspace, run)
    [undone] = [item for item in found if item.rule_id == "acceptance.write-undone"]
    assert undone.severity is FindingSeverity.HIGH
    assert f"{FROZEN} (modified)" in undone.message
    assert "acceptance.modified" not in [item.rule_id for item in found]
    assert where(application, python_workspace, run)[0] == "DECISION"


def test_protect_adds_the_frozen_files_to_the_sandbox_deny_paths(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "right", {"protect": True})
    application, run = start(python_workspace, tmp_path)
    frozen = (python_workspace / FROZEN).resolve()
    with application._services(python_workspace) as services:
        engine = RunEngine(services)
        protected = engine._protected_paths(engine.get_execution(run))
    assert frozen in [item.resolve() for item in protected]
    host = SandboxHost(
        system="Darwin", home=tmp_path, temp_dir=tmp_path, sandbox_exec="/usr/bin/sandbox-exec"
    )
    plan = build_sandbox(python_workspace, (), host, protected=protected)
    denied = plan.profile.split("(deny file-write*\n")[1]
    # The profile escapes backslashes, which a Windows path has.
    quoted = str(frozen).replace("\\", "\\\\")
    assert f'(subpath "{quoted}")' in denied


def test_without_protect_the_frozen_files_are_not_deny_paths(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "right", {})
    application, run = start(python_workspace, tmp_path)
    with application._services(python_workspace) as services:
        engine = RunEngine(services)
        protected = engine._protected_paths(engine.get_execution(run))
    assert (python_workspace / FROZEN) not in protected


# ----- the self-report and the configuration --------------------------------------------------
def test_the_self_report_reads_disputed_acceptance_tests() -> None:
    parsed = parse_self_report(
        {
            "assumptions": [],
            "disputedAcceptanceTests": [
                {"test": "tests/acceptance/test_a.py::test_x", "reason": "expects a typo"},
                {"path": "tests/acceptance/test_b.py", "description": "rejects valid input"},
                {"test": "tests/acceptance/test_c.py"},
                "not an object",
            ],
        }
    )
    assert parsed is not None
    assert [(item.test, item.reason) for item in parsed.disputed_acceptance_tests] == [
        ("tests/acceptance/test_a.py::test_x", "expects a typo"),
        ("tests/acceptance/test_b.py", "rejects valid input"),
    ]
    assert parsed.problems == (
        "disputedAcceptanceTests has an entry without a test or a reason",
        "disputedAcceptanceTests has an entry without a test or a reason",
    )


def test_init_writes_the_keys_and_config_validate_shows_them(tmp_path: Path) -> None:
    application = HarnessApplication()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    application.init(tmp_path)
    written = yaml.safe_load((tmp_path / ".harness" / "project.yaml").read_text())
    assert written["verification"]["acceptanceTests"] == {
        "mode": "agent",
        "protect": True,
        "disputes": "person",
    }
    assert written["architecture"]["layerCheck"] == "differential"
    assert written["architecture"]["baselineConflicts"] == "allow"
    shown = application.validate_config(tmp_path)
    assert shown["agentResults"]["checks"]["acceptanceTests"]["disputes"] == "person"
    assert shown["agentResults"]["checks"]["acceptanceTests"]["protect"] is True
    assert shown["engineering"]["architecture"]["layerCheck"] == "differential"
    assert shown["engineering"]["architecture"]["baselineConflicts"] == "allow"
