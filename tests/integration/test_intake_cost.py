"""The 2.0.1 cost settings of INTENT and of the agent contract, end to end with a fixture agent.

In the Sonnet pilots of 2.0.0 the rounds 2 and 3 of clarification were about 21 % of the cost:
``locate`` ran again for every answered revision (its cache is the task digest, which every
answer changes) and ``clarify`` ran a third round after a second round with no question or
after the ``unresolvable-ambiguity`` stop. ``context.locate.reuse: scope`` and
``intake.ambiguityReview.skipSettled`` remove those calls; without the keys the calls stay.
``agentContract.budgetProfile: by-kind`` gives implement and the read-only calls their own
token budget."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import PhaseId
from governed_harness.domain.models import ClarificationRequest

TASK = {
    "taskId": "task_cost",
    "title": "Threshold discount",
    "intent": "Apply the configured discount rate when the subtotal reaches the threshold.",
    "requirements": [f"R{index}. Rule number {index} of the discount." for index in range(1, 5)],
    "acceptanceCriteria": [
        {"criterionId": "AC-1", "text": "apply_discount(100, 100, 0.1) returns 90."}
    ],
    "metadata": {"ownedPaths": ["src/sample/pricing.py"]},
}

# CLARIFY: questions of each clarify call (the last entry again afterwards); KEEP: every call
# asks one new question. LOCATE_QUESTIONS: questions of the first locate call only.
AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
number = sum(1 for item in calls if item.get("kind", "implement") == kind)
if kind == "clarify":
    counts = CLARIFY
    count = number if KEEP else counts[min(number, len(counts)) - 1]
    questions = [{"category": "edge-cases", "target": "AC-1",
                  "text": f"Call {number}: what happens for edge case {index}?"}
                 for index in range(1, (1 if KEEP else count) + 1)]
    print(json.dumps({"status": "PASSED", "summary": "q", "result": {"questions": questions}}))
    sys.exit(0)
if kind == "locate":
    asked = LOCATE_QUESTIONS if number == 1 else []
    print(json.dumps({"status": "PASSED", "summary": "located", "result": {
        "locations": [{"path": "src/sample/pricing.py", "line": 2,
                       "evidence": "return subtotal", "reason": "the rule lives here"}],
        "questions": asked}}))
    sys.exit(0)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
print(json.dumps({"status": "PASSED", "summary": "done"}))
"""


def configure(
    workspace: Path,
    tmp_path: Path,
    *,
    clarify: list[int] | None = None,
    keep: bool = False,
    locate_questions: list[str] | None = None,
    review: dict[str, Any] | None = None,
    locate: dict[str, Any] | None = None,
    stop: bool = False,
    contract: dict[str, Any] | None = None,
) -> Path:
    log = tmp_path / "calls.json"
    script = (
        AGENT.replace("LOG", repr(str(log)))
        .replace("CLARIFY", repr(clarify or [0]))
        .replace("KEEP", repr(keep))
        .replace("LOCATE_QUESTIONS", repr(locate_questions or []))
    )
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {"fixture": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"].update({"agentSandbox": "off", "providerRetries": 0})
    config["intake"] = {
        "criteriaPolicy": "enforce",
        "ambiguityReview": {
            "mode": "agent",
            "maxRounds": 3,
            "maxQuestions": 8,
            "onExhausted": "assume",
            **(review or {}),
        },
    }
    if stop:
        config["intake"]["interruptions"] = {
            "target": 2,
            "stopConditions": ["unresolvable-ambiguity"],
        }
    config["context"] = {"manifest": "auto", "locate": {"mode": "agent", **(locate or {})}}
    config["agentRouting"] = {
        "mode": "tiered",
        "families": {"fixture": "claude-code"},
        "thresholds": {"requirements": [2, 15]},
    }
    if contract is not None:
        config["agentContract"] = contract
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(yaml.safe_dump(TASK), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_cost").execution_id


def answer(application: HarnessApplication, workspace: Path, run: str, tmp_path: Path) -> int:
    with application._services(workspace) as services:
        requests = services.state.list(
            "clarification_request", ClarificationRequest, execution_id=run
        )
    request = max(requests, key=lambda item: item.created_at)
    answers = {item.question_id: f"Decided for {item.question_id}." for item in request.questions}
    path = tmp_path / f"answers-{request.request_id}.yaml"
    path.write_text(yaml.safe_dump({"answers": answers}), encoding="utf-8")
    application.clarify_task(workspace, task_id="task_cost", answers_file=path)
    application.continue_run(workspace, run)
    return len(answers)


def kinds(log: Path) -> list[str]:
    return [item.get("kind", "implement") for item in json.loads(log.read_text())]


def events(application: HarnessApplication, workspace: Path, run: str) -> list[Any]:
    with application._services(workspace) as services:
        return services.events.list(run)


def phase(application: HarnessApplication, workspace: Path, run: str) -> Any:
    return application.status(workspace, run)["execution"]["currentPhase"]


# ----- locate once while the scope stays the same ---------------------------------------------
def run_two_rounds(workspace: Path, tmp_path: Path, **settings: Any) -> tuple[Any, str, Path]:
    log = configure(workspace, tmp_path, clarify=[1, 1, 0], **settings)
    application, run = start(workspace, tmp_path)
    assert answer(application, workspace, run, tmp_path) == 1
    assert answer(application, workspace, run, tmp_path) == 1
    assert phase(application, workspace, run) != PhaseId.INTENT
    return application, run, log


def test_without_the_key_locate_runs_for_every_revision(
    python_workspace: Path, tmp_path: Path
) -> None:
    _, _, log = run_two_rounds(python_workspace, tmp_path)
    assert kinds(log).count("locate") == 3


def test_locate_reuse_scope_calls_once_across_the_revisions(
    python_workspace: Path, tmp_path: Path
) -> None:
    application, run, log = run_two_rounds(python_workspace, tmp_path, locate={"reuse": "scope"})
    assert kinds(log).count("locate") == 1
    reused = [
        item.payload
        for item in events(application, python_workspace, run)
        if item.event_type == "locate.reused"
    ]
    assert len(reused) == 2 and all(item["scope"] for item in reused)
    assert reused[0]["fromTaskDigest"] != reused[0]["taskDigest"]
    # The implement request still carries the locations.
    [implement] = [item for item in json.loads(log.read_text()) if "locations" in item]
    assert implement["locations"][0]["path"] == "src/sample/pricing.py"


# ----- clarify not called again once the review settled -----------------------------------------
@pytest.mark.parametrize(("skip", "expected"), [(False, 2), (True, 1)])
def test_a_round_without_agent_questions_is_not_reviewed_again(
    python_workspace: Path, tmp_path: Path, skip: bool, expected: int
) -> None:
    """Round 1: the review asks nothing, the locate call asks one question; the answered
    revision is not reviewed again under skipSettled (and is without the key)."""
    log = configure(
        python_workspace,
        tmp_path,
        clarify=[0],
        locate_questions=["Which module holds the rule?"],
        review={"skipSettled": True} if skip else None,
    )
    application, run = start(python_workspace, tmp_path)
    assert answer(application, python_workspace, run, tmp_path) == 1
    assert phase(application, python_workspace, run) != PhaseId.INTENT
    assert kinds(log).count("clarify") == expected
    skipped = [
        item.payload
        for item in events(application, python_workspace, run)
        if item.event_type == "intent.agent-review.skipped"
    ]
    if skip:
        assert [item["reason"] for item in skipped] == ["no-new-questions"]
        assert skipped[0]["onExhausted"] == "assume"
    else:
        assert skipped == []


def test_after_the_ambiguity_stop_the_review_is_not_called_again(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, keep=True, stop=True, review={"skipSettled": True})
    application, run = start(python_workspace, tmp_path)
    assert answer(application, python_workspace, run, tmp_path) == 1
    recorded = events(application, python_workspace, run)
    assert [item.payload["condition"] for item in recorded if item.event_type == "stop.condition"]
    assert answer(application, python_workspace, run, tmp_path) == 1
    assert kinds(log).count("clarify") == 2
    assert phase(application, python_workspace, run) != PhaseId.INTENT
    skipped = [
        item.payload["reason"]
        for item in events(application, python_workspace, run)
        if item.event_type == "intent.agent-review.skipped"
    ]
    assert skipped == ["unresolvable-ambiguity"]


def test_without_the_key_the_review_runs_after_the_stop(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, keep=True, stop=True)
    application, run = start(python_workspace, tmp_path)
    answer(application, python_workspace, run, tmp_path)
    answer(application, python_workspace, run, tmp_path)
    assert kinds(log).count("clarify") == 3
    assert phase(application, python_workspace, run) == PhaseId.INTENT


# ----- token budgets by kind ------------------------------------------------------------------
def budgets(log: Path) -> dict[str, int]:
    return {
        item.get("kind", "implement"): item["agentContract"]["tokenBudget"]
        for item in json.loads(log.read_text())
        if "agentContract" in item
    }


def test_the_by_kind_profile_gives_implement_its_own_budget(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace, tmp_path, contract={"mode": "enforce", "budgetProfile": "by-kind"}
    )
    application, run = start(python_workspace, tmp_path)
    assert phase(application, python_workspace, run) != PhaseId.INTENT
    assert budgets(log) == {"clarify": 200_000, "locate": 200_000, "implement": 300_000}


def test_without_the_profile_every_call_has_the_size_budget(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, contract={"mode": "enforce"})
    start(python_workspace, tmp_path)
    assert budgets(log) == {"clarify": 200_000, "locate": 200_000, "implement": 200_000}
