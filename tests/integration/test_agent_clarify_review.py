"""The agent review of a task in INTENT (#37): request kind ``clarify``, questions grouped by
category, once per task revision, answers by a person, malformed answers and failures, the
read-only contract, and the check of answers that cite what the task does not contain."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import (
    AgentInvocation,
    ClarificationRequest,
    ResourceUsage,
)

TASK = (
    "taskId: task_clarify\n"
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

# MODE decides the clarify answer; every request is logged.
AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
clarify_calls = sum(1 for item in calls if item.get("kind") == "clarify")

if kind == "clarify":
    if MODE == "fail":
        sys.stderr.write("model unavailable\\n")
        sys.exit(1)
    if MODE == "malformed":
        print(json.dumps({"status": "PASSED", "summary": "x", "result": {"questions": "none"}}))
        sys.exit(0)
    if MODE == "writes":
        Path("notes.txt").write_text("I was not supposed to write this")
    questions = []
    if MODE in {"questions", "writes"} and clarify_calls == 1:
        questions = [
            {"category": "edge-cases", "target": "AC-1", "text": "What happens below zero?"},
            {"category": "ambiguity", "target": "task", "text": "Is the threshold inclusive?"},
            {"category": "out-of-scope", "target": "task", "text": "What is out of scope?"},
        ]
    print(json.dumps({
        "status": "PASSED",
        "summary": f"{len(questions)} question(s)",
        "result": {"questions": questions},
        "usage": {"inputTokens": 100, "outputTokens": 20, "costUsd": 0.01},
    }))
    sys.exit(0)

Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
Path("tests/test_pricing.py").write_text(
    "from sample import apply_discount\\n\\n"
    "def test_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n"
)
print(json.dumps({"status": "PASSED", "summary": "Implemented"}))
"""


def configure(
    workspace: Path, tmp_path: Path, mode: str, intake: dict[str, Any] | None = None
) -> Path:
    log = tmp_path / f"requests-{mode}.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("MODE", repr(mode))
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"]["agentSandbox"] = "off"
    config["runtime"]["providerRetries"] = 0
    config["intake"] = intake or {
        "criteriaPolicy": "enforce",
        "ambiguityReview": "agent",
        "clarifyAgent": {"model": "cheap-model", "effort": "low"},
        "validateAnswers": True,
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def calls(log: Path, kind: str) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [item for item in json.loads(log.read_text()) if item.get("kind") == kind]


def start(workspace: Path, tmp_path: Path, task: str = TASK) -> tuple[HarnessApplication, str]:
    path = tmp_path / "task.yaml"
    path.write_text(task, encoding="utf-8")
    application = HarnessApplication()
    created = application.create_task(workspace, path)
    return application, application.start_run(workspace, created.task_id).execution_id


def requests_of(application: HarnessApplication, workspace: Path, run: str) -> list[Any]:
    with application._services(workspace) as services:
        return services.state.list("clarification_request", ClarificationRequest, execution_id=run)


def test_agent_questions_block_intent_until_a_person_answers(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "questions")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.INTENT
    assert status["execution"]["status"] == ResultStatus.BLOCKED
    [request] = calls(log, "clarify")
    assert request["readOnly"] is True and request["schemaVersion"] == "1.1"
    assert "explicitly out of scope" in request["instructions"]
    assert request["task"]["task_id"] == "task_clarify"
    assert (request["routing"]["model"], request["routing"]["effort"]) == ("cheap-model", "low")
    [clarification] = requests_of(application, python_workspace, run)
    agent = [item for item in clarification.questions if item.rule_id == "A1"]
    # Grouped by category in the documented order: ambiguity, edge-cases, out-of-scope.
    assert [item.category for item in agent] == ["ambiguity", "edge-cases", "out-of-scope"]
    with application._services(python_workspace) as services:
        [invocation] = [
            item
            for item in services.state.list("agent_invocation", AgentInvocation, execution_id=run)
            if item.call_kind == "clarify"
        ]
        assert invocation.phase_id is PhaseId.INTENT
        assert invocation.model == "cheap-model" and invocation.effort == "low"
        usage = services.state.list("resource_usage", ResourceUsage, execution_id=run)
        assert [item.cost_usd for item in usage] == [0.01]

    answers = tmp_path / "answers.yaml"
    by_category = {item.category: item.question_id for item in agent}
    answers.write_text(
        yaml.safe_dump(
            {
                "answers": {
                    by_category["ambiguity"]: "Yes, the threshold itself gets the discount.",
                    by_category["edge-cases"]: "A negative subtotal is rejected with ValueError.",
                    by_category["out-of-scope"]: "Rounding\nCurrencies",
                }
            }
        ),
        encoding="utf-8",
    )
    result = application.clarify_task(
        python_workspace, task_id="task_clarify", answers_file=answers
    )
    task = result["task"]
    assert any(item["source"] == "clarification" for item in task["requirements"])
    assert "Out of scope: Rounding" in task["constraints"]
    continued = application.continue_run(python_workspace, run)
    assert continued.current_phase is PhaseId.DECISION
    second = calls(log, "clarify")
    # A new revision gets a new review, with the answers to check for consistency.
    assert len(second) == 2 and second[1]["clarifications"]


def test_no_questions_is_evidence_and_the_review_runs_once_per_revision(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "none")
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )
    assert len(calls(log, "clarify")) == 1
    [listing] = application.list_evidence(python_workspace, run)
    assert any(
        "Agent review of the task: 0 question(s)" in str(item.get("summary"))
        for item in listing["evidence"]
    )
    # A second run of the same task revision reuses the stored answer.
    application.cancel_run(python_workspace, run, "human.reviewer")
    again = application.start_run(python_workspace, "task_clarify").execution_id
    assert len(calls(log, "clarify")) == 1
    with application._services(python_workspace) as services:
        reused = [
            item for item in services.events.list(again) if item.event_type.endswith("reused")
        ]
    assert reused and reused[0].payload["questions"] == 0


def test_malformed_answer_is_a_finding_and_blocks(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "malformed")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["status"] == ResultStatus.BLOCKED
    findings = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "intake.agent-review-malformed"
    ]
    assert findings and findings[0].severity is FindingSeverity.HIGH


def test_provider_failure_blocks_intent(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "fail")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == PhaseId.INTENT
    assert status["execution"]["status"] == ResultStatus.BLOCKED


def test_a_read_only_call_that_writes_is_undone(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "writes")
    application, run = start(python_workspace, tmp_path)
    assert not (python_workspace / "notes.txt").exists()
    findings = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "agent.read-only-violation"
    ]
    assert findings and findings[0].severity is FindingSeverity.HIGH
    assert application.status(python_workspace, run)["execution"]["status"] == (
        ResultStatus.BLOCKED
    )


def test_answers_that_cite_missing_documents_get_a_question(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        tmp_path,
        "none",
        intake={"criteriaPolicy": "enforce", "validateAnswers": True},
    )
    vague = TASK.replace("apply_discount(100, 100, 0.1) returns 90.", "It works.")
    application, run = start(python_workspace, tmp_path, vague)
    [request] = requests_of(application, python_workspace, run)
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "answers": {
                    request.questions[0].question_id: (
                        "apply_discount(100, 100, 0.1) returns 90 per A1, see SPEC.md"
                    )
                }
            }
        ),
        encoding="utf-8",
    )
    application.clarify_task(python_workspace, task_id="task_clarify", answers_file=answers)
    application.continue_run(python_workspace, run)
    latest = requests_of(application, python_workspace, run)[-1]
    texts = [item.text for item in latest.questions if item.rule_id == "A2"]
    assert any("SPEC.md" in text for text in texts)
    assert any("'A1'" in text for text in texts)
    # With the document in the workspace and the id in it, nothing is left dangling.
    (python_workspace / "SPEC.md").write_text("A1: the discount applies at the threshold.\n")
    with application._services(python_workspace) as services:
        from governed_harness.orchestration.engine import RunEngine

        engine = RunEngine(services)
        task = engine.get_task("task_clarify")
        answers_list = engine.results.intent._current_answers(engine.get_execution(run), task)
        assert engine.results.intent.dangling_references(task, answers_list) == []
