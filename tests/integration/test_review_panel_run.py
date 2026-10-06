"""The review panel in INDEPENDENT_REVIEW of a governed run (review.panel, #57): reviewers by
domain, the report as evidence, scoped auto-fix by provenance and a reviewer that never answers.
The provider is a fixture command that calls no model.

The reviewers of a panel run in parallel, so the fixture records each call in a file of its own
(renamed into place when complete) instead of rewriting one shared log: a shared read-modify-write
lost a reviewer's call or left a torn file whenever two reviewers wrote at once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import Finding, ValidationResult

TASK = (
    "taskId: task_panel\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)

AGENT = """\
import json, os, sys, time
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
log.mkdir(exist_ok=True)
name = f"{time.time_ns():020d}-{os.getpid()}"
(log / f"{name}.tmp").write_text(json.dumps(request))
os.replace(log / f"{name}.tmp", log / f"{name}.json")
calls = [json.loads(path.read_text()) for path in sorted(log.glob("*.json"))]
kind = request.get("kind", "implement")
if kind == "review":
    reviewer = (request.get("reviewer") or {}).get("id")
    if MODE == "unknown":
        print(json.dumps({"status": "PASSED", "summary": "x", "result": {"findings": []}}))
        sys.exit(0)
    reviews = sum(1 for item in calls if (item.get("reviewer") or {}).get("id") == "quality")
    findings = []
    if reviewer == "quality" and reviews == 1 and MODE in {"agent-line", "removed-line"}:
        findings = [{
            "file": "src/sample/pricing.py",
            "side": "new" if MODE == "agent-line" else "old",
            "line": 2,
            "rule": "quality.wrong-logic",
            "severity": "error",
            "issue": "The discount ignores the threshold",
            "evidence": "return subtotal",
        }]
    result = {"verdict": "FAIL" if findings else "PASS", "findings": findings, "summary": "ok"}
    print(json.dumps({"status": "PASSED", "summary": "reviewed", "result": result}))
    sys.exit(0)
fixed = "feedback" in request
body = "subtotal * (1 - rate) if subtotal >= threshold else subtotal" if fixed else (
    "subtotal * (1 - rate) if subtotal >= 0 else subtotal"
)
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


def configure(workspace: Path, tmp_path: Path, mode: str, **panel: Any) -> Path:
    log = tmp_path / f"calls-{mode}"
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
            "verificationCorrections": 2,
            "providerFeedback": True,
            "providerRetries": 0,
        }
    )
    config["provenance"] = {"agentSnapshots": True}
    config["review"] = {
        "panel": {
            "mode": "enforce",
            "autoFix": {"mode": "scoped", "maxAttempts": 1},
            "cache": {"enabled": False},
            **panel,
        }
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def requests(log: Path, kind: str) -> list[dict[str, Any]]:
    """The recorded calls of ``kind``, in the order they were made."""
    calls = [json.loads(path.read_text()) for path in sorted(log.glob("*.json"))]
    return [item for item in calls if item.get("kind", "implement") == kind]


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_panel").execution_id


def events(application: HarnessApplication, workspace: Path, run: str) -> list[Any]:
    with application._services(workspace) as services:
        return services.events.list(run)


def test_panel_reviews_by_domain_and_records_its_report(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "clean")
    application, run = start(python_workspace, tmp_path)
    assert (
        application.status(python_workspace, run)["execution"]["currentPhase"] == PhaseId.DECISION
    )
    reviews = requests(log, "review")
    reviewers = sorted(item["reviewer"]["id"] for item in reviews)
    # A body line and a test changed: quality and tests review; no declaration, import,
    # concurrency primitive, external call or pipeline file activates the others.
    assert reviewers == ["quality", "tests"]
    for request in reviews:
        assert request["readOnly"] is True
        assert request["isolation"]["tools"] == ["Read", "Grep", "Glob"]
        assert request["task"]["intent"].startswith("Apply the configured discount")
        assert "tests.tautological-assertion" not in request["instructions"]
    completed = [
        item
        for item in events(application, python_workspace, run)
        if item.event_type == "review.panel.completed"
    ]
    assert completed
    assert completed[-1].payload["verdict"] == "PASS"
    with application._services(python_workspace) as services:
        validation = [
            item
            for item in services.state.list("validation", ValidationResult, execution_id=run)
            if item.validator_id == "review.agent"
        ][-1]
        assert validation.status is ResultStatus.PASSED
        assert validation.mandatory


def test_errors_on_agent_lines_go_back_scoped(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "agent-line")
    application, run = start(python_workspace, tmp_path)
    recorded = events(application, python_workspace, run)
    requested = [item for item in recorded if item.event_type == "review.autofix.requested"]
    assert len(requested) == 1
    assert requested[0].payload["fixable"]
    corrections = [item for item in recorded if item.event_type == "correction.authorized"]
    assert [item.payload["trigger"] for item in corrections] == ["REVIEW_FINDINGS"]
    implements = requests(log, "implement")
    assert len(implements) == 2
    assert "feedback" in implements[1]
    feedback = json.dumps(implements[1]["feedback"])
    assert "quality.wrong-logic" in feedback
    assert (
        application.status(python_workspace, run)["execution"]["currentPhase"] == PhaseId.DECISION
    )


def test_errors_on_lines_the_agent_did_not_write_stay_for_a_person(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "removed-line")
    application, run = start(python_workspace, tmp_path)
    recorded = events(application, python_workspace, run)
    assert not [item for item in recorded if item.event_type == "review.autofix.requested"]
    declined = [item for item in recorded if item.event_type == "review.autofix.declined"]
    assert declined
    assert declined[0].payload["reason"] == "no error on a line the agent wrote"
    assert len(requests(log, "implement")) == 1
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    with application._services(python_workspace) as services:
        findings = [
            item
            for item in services.state.list("finding", Finding, execution_id=run)
            if item.rule_id == "review.panel.quality.wrong-logic"
        ]
    assert findings
    assert findings[0].severity is FindingSeverity.HIGH
    assert "(a removed line)" in findings[0].message


def test_reviewer_without_a_valid_answer_blocks(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "unknown")
    application, run = start(python_workspace, tmp_path)
    with application._services(python_workspace) as services:
        validation = [
            item
            for item in services.state.list("validation", ValidationResult, execution_id=run)
            if item.validator_id == "review.agent"
        ][-1]
        findings = services.state.list("finding", Finding, execution_id=run)
    assert validation.status is ResultStatus.BLOCKED
    assert any(item.rule_id == "review.panel.unknown" for item in findings)
    assert not any(
        item.event_type == "correction.authorized"
        for item in events(application, python_workspace, run)
    )


def test_every_reviewer_records_its_routing_decision(
    python_workspace: Path, tmp_path: Path
) -> None:
    """#85: each reviewer call records agent.routing.decided, capped at the invoking model."""
    log = configure(python_workspace, tmp_path, "clean")
    path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProviders"]["fixture_agent"]["model"] = "claude-sonnet-5-5"
    config["agentRouting"] = {"mode": "anchored", "families": {"fixture_agent": "claude-code"}}
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    application, run = start(python_workspace, tmp_path)
    reviewed = sorted(item["reviewer"]["id"] for item in requests(log, "review"))
    decided = [
        item.payload
        for item in events(application, python_workspace, run)
        if item.event_type == "agent.routing.decided" and "reviewer" in item.payload
    ]
    assert sorted(item["reviewer"] for item in decided) == reviewed
    assert {item["model"] for item in decided} == {"claude-sonnet-5-5"}
    assert {item["anchor"] for item in decided} == {"claude-sonnet-5-5"}
    assert {item["phase"] for item in decided} == {PhaseId.INDEPENDENT_REVIEW}
    report = application.routing_calibration(python_workspace)
    review_rows = [item for item in report["groups"] if item["callKind"] == "review"]
    assert len(review_rows) == 1


def test_reviewers_record_no_routing_without_agent_routing(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, tmp_path, "clean")
    application, run = start(python_workspace, tmp_path)
    assert not [
        item
        for item in events(application, python_workspace, run)
        if item.event_type == "agent.routing.decided"
    ]
