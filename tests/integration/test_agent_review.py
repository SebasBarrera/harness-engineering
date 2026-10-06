"""Second-agent review in INDEPENDENT_REVIEW (review.agentReview, #38)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import PhaseExecution, ValidationResult

TASK = (
    "taskId: task_review\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
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
if kind == "review":
    if MODE == "fail":
        sys.exit(3)
    reviews = sum(1 for item in calls if item.get("kind") == "review")
    findings = []
    if MODE in {"blocking", "warn"} and reviews == 1:
        findings = [{
            "severity": "HIGH", "rule": "Wrong Formula", "path": "src/sample/pricing.py",
            "line": 2, "message": "The discount ignores the threshold",
            "evidence": "return subtotal * (1 - rate)",
        }]
    print(json.dumps({"status": "PASSED", "summary": "reviewed", "result": {"findings": findings}}))
    sys.exit(0)
fixed = "feedback" in request or MODE == "clean"
body = "subtotal * (1 - rate) if subtotal >= threshold else subtotal" if fixed else (
    "subtotal * (1 - rate) if subtotal >= 0 else subtotal"
)
if MODE == "red":
    body = "subtotal"
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    f"    return {body}\\n"
)
Path("tests/test_pricing.py").write_text(
    "from sample import apply_discount\\n\\n"
    "def test_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n"
)
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def configure(workspace: Path, tmp_path: Path, mode: str, policy: str = "enforce") -> Path:
    log = tmp_path / f"calls-{mode}.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("MODE", repr(mode))
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
            "verificationCorrections": 1,
            "providerFeedback": True,
            "providerRetries": 0,
        }
    )
    config["review"] = {"agentReview": policy, "reviewer": {"model": "reviewer-model"}}
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def calls(log: Path, kind: str) -> list[dict[str, Any]]:
    return [item for item in json.loads(log.read_text()) if item.get("kind", "implement") == kind]


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_review").execution_id


def review_validations(
    application: HarnessApplication, workspace: Path, run: str
) -> list[ValidationResult]:
    with application._services(workspace) as services:
        return [
            item
            for item in services.state.list("validation", ValidationResult, execution_id=run)
            if item.validator_id == "review.agent"
        ]


def test_blocking_findings_return_to_the_agent_and_pass_after_correction(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "blocking")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    assert status["gate"]["status"] == ResultStatus.PASSED
    reviews = calls(log, "review")
    assert len(reviews) == 2
    assert reviews[0]["readOnly"] is True
    assert "+    return subtotal * (1 - rate)" in reviews[0]["changeSet"]["diff"]
    assert reviews[0]["routing"]["model"] == "reviewer-model"
    implement = calls(log, "implement")
    assert len(implement) == 2
    feedback = implement[1]["feedback"]
    assert feedback["trigger"] == "REVIEW_FINDINGS"
    assert feedback["findings"][0]["ruleId"] == "review.agent.wrong-formula"
    findings = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "review.agent.wrong-formula"
    ]
    assert findings[0].severity is FindingSeverity.HIGH
    assert findings[0].location is not None and findings[0].location.start_line == 2


def test_the_review_runs_once_per_changeset(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "clean")
    application, run = start(python_workspace, tmp_path)
    assert len(calls(log, "review")) == 1
    with application._services(python_workspace) as services:
        from governed_harness.orchestration.engine import RunEngine

        engine = RunEngine(services)
        execution = engine.get_execution(run)
        phase = services.state.list("phase", PhaseExecution, execution_id=run)[-1]
        outcome = engine.results.agent_review.run(execution, phase, engine.current_change_set(run))
    assert outcome.ran is False
    assert len(calls(log, "review")) == 1


def test_warn_caps_severity_and_never_blocks(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "warn", policy="warn")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["gate"]["status"] == ResultStatus.PASSED
    [finding] = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.validator_id == "review.agent"
    ]
    assert finding.severity is FindingSeverity.MEDIUM


def test_no_review_while_a_deterministic_validator_fails(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "red")
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.VERIFICATION
    )
    assert calls(log, "review") == []


def test_a_failing_reviewer_blocks_the_gate_under_enforce(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "fail")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    assert status["gate"]["status"] == ResultStatus.BLOCKED
    [validation] = review_validations(application, python_workspace, run)
    assert validation.status is ResultStatus.BLOCKED and validation.mandatory
