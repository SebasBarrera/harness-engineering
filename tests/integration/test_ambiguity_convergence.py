"""The converging agent review of a task in INTENT (#79).

In the 2.0.0 pilot two of four governed Haiku runs never left INTENT: every answered revision
got a new review that raised new questions (10, 8 and 8 in three rounds). These tests reproduce
that with a fixture agent that keeps asking (and with the questions the pilot's Haiku review
actually asked), show that the review of 1.1 (``ambiguityReview: agent``) still never converges,
and that the object form converges: the request carries the questions already asked and their
answers, repeated questions are dropped, a round is bounded, and after the last round the open
points become explicit assumptions (``assume``) or keep INTENT blocked (``block``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.models import ClarificationRequest, Task

PILOT_ROUNDS = Path(__file__).resolve().parents[1] / "fixtures" / "pilot-2-0-0-clarify-rounds.json"

TASK = (
    "taskId: task_converge\n"
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

# The brownfield task of the 2.0.0 pilot (evaluation/tasks/brownfield-itsdangerous.yaml).
PILOT_TASK = (
    "taskId: task_eval_brownfield_strict_base64\n"
    "title: Make base64_decode strict\n"
    "intent: >-\n"
    "  itsdangerous.encoding.base64_decode silently discards characters that are not part of\n"
    "  the URL-safe base64 alphabet (including non-ASCII characters), so several different\n"
    "  strings decode to the same bytes and a signature can be altered without being rejected.\n"
    "  Make the decoder strict.\n"
    "requirements:\n"
    "  - requirementId: req_alphabet\n"
    "    text: base64_decode raises BadData when the input contains any character outside A-Z,\n"
    "      a-z, 0-9, '-' and '_', except '=' padding at the end.\n"
    "  - requirementId: req_padding\n"
    "    text: Unpadded input and at most two trailing '=' padding characters keep being\n"
    "      accepted.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: ac_reject\n"
    "    text: Input with '+', '/', whitespace, non-ASCII characters, control characters or '='\n"
    "      before the end raises BadData, for both str and bytes.\n"
    "  - criterionId: ac_signatures\n"
    "    text: A signed value or serialized token whose signature has an inserted character\n"
    "      outside the alphabet is rejected.\n"
    "  - criterionId: ac_existing\n"
    "    text: Existing tests keep passing (python -m pytest -q) and the new behavior is covered\n"
    "      by tests.\n"
    "constraints:\n"
    "  - Keep the public signature of base64_decode and base64_encode.\n"
    "  - Do not add dependencies.\n"
)

# A clarify review that never converges. MODE "keeps": every call asks six new questions and
# repeats three earlier ones in other case and punctuation. MODE "replay": the questions the
# pilot's Haiku review asked in each round, the last round again afterwards.
AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
if kind == "clarify":
    number = sum(1 for item in calls if item.get("kind") == "clarify")
    if MODE == "replay":
        rounds = json.loads(Path(ROUNDS).read_text(encoding="utf-8"))["rounds"]
        questions = rounds[min(number, len(rounds)) - 1]
        if number > len(rounds):
            # Still asking after the recorded rounds, in other words.
            questions = [{**item, "text": "Again: " + item["text"]} for item in questions]
    else:
        questions = [
            {"category": "edge-cases", "target": "AC-1",
             "text": f"Round {number}: what happens for edge case {number}.{index}?"}
            for index in range(1, 7)
        ]
        for earlier in range(1, number):
            questions.append({"category": "ambiguity", "target": "task",
                              "text": f"ROUND {earlier}: What happens for edge case {earlier}.1??"})
    print(json.dumps({"status": "PASSED", "summary": "questions",
                      "result": {"questions": questions}}))
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

CONVERGING = {"mode": "agent", "maxRounds": 3, "maxQuestions": 8, "onExhausted": "assume"}


def configure(workspace: Path, tmp_path: Path, mode: str, review: Any, **extra: Any) -> Path:
    log = tmp_path / f"requests-{mode}.json"
    script = (
        AGENT.replace("LOG", repr(str(log)))
        .replace("MODE", repr(mode))
        .replace("ROUNDS", repr(str(PILOT_ROUNDS)))
    )
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"]["agentSandbox"] = "off"
    config["runtime"]["providerRetries"] = 0
    config["intake"] = {"criteriaPolicy": "enforce", "ambiguityReview": review}
    for section, values in extra.items():
        config.setdefault(section, {}).update(values)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def calls(log: Path, kind: str) -> list[dict[str, Any]]:
    # An implement request without agent-results keys keeps the 1.0 form, without a kind.
    return [item for item in json.loads(log.read_text()) if item.get("kind", "implement") == kind]


def start(workspace: Path, tmp_path: Path, task: str = TASK) -> tuple[HarnessApplication, str, str]:
    path = tmp_path / "task.yaml"
    path.write_text(task, encoding="utf-8")
    application = HarnessApplication()
    created = application.create_task(workspace, path)
    run = application.start_run(workspace, created.task_id).execution_id
    return application, run, created.task_id


def latest_request(application: HarnessApplication, workspace: Path, run: str) -> Any:
    with application._services(workspace) as services:
        requests = services.state.list(
            "clarification_request", ClarificationRequest, execution_id=run
        )
    return max(requests, key=lambda item: item.created_at)


def answer_round(
    application: HarnessApplication, workspace: Path, run: str, task_id: str, tmp_path: Path
) -> int:
    """A person answers every open question; the run continues. The questions answered."""
    request = latest_request(application, workspace, run)
    answers = {
        item.question_id: f"Decided: the simplest behaviour for {item.question_id}."
        for item in request.questions
    }
    path = tmp_path / f"answers-{request.request_id}.yaml"
    path.write_text(yaml.safe_dump({"answers": answers}), encoding="utf-8")
    application.clarify_task(workspace, task_id=task_id, answers_file=path)
    application.continue_run(workspace, run)
    return len(answers)


def phase_of(application: HarnessApplication, workspace: Path, run: str) -> Any:
    return application.status(workspace, run)["execution"]["currentPhase"]


def events(application: HarnessApplication, workspace: Path, run: str) -> list[Any]:
    with application._services(workspace) as services:
        return services.events.list(run)


def test_the_review_of_1_1_never_converges(python_workspace: Path, tmp_path: Path) -> None:
    """The defect: with the bare ``agent`` every answered revision brings new questions."""
    log = configure(python_workspace, tmp_path, "keeps", "agent")
    application, run, task_id = start(python_workspace, tmp_path)
    asked = [answer_round(application, python_workspace, run, task_id, tmp_path) for _ in range(4)]
    assert asked == [6, 7, 8, 9]
    assert phase_of(application, python_workspace, run) == PhaseId.INTENT
    assert application.status(python_workspace, run)["execution"]["status"] == (
        ResultStatus.BLOCKED
    )
    assert "previousQuestions" not in calls(log, "clarify")[-1]


def test_the_converging_review_records_assumptions_and_continues(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        "keeps",
        CONVERGING,
        runtime={"gateContract": True},
        governance={"pinTaskRevision": True},
    )
    application, run, task_id = start(python_workspace, tmp_path)
    asked = [answer_round(application, python_workspace, run, task_id, tmp_path) for _ in range(3)]
    # The repeated questions (other case and punctuation) are dropped: six new ones a round.
    assert asked == [6, 6, 6]
    assert phase_of(application, python_workspace, run) == PhaseId.DECISION
    clarify = calls(log, "clarify")
    assert len(clarify) == 4
    second = clarify[1]
    assert (second["round"], second["maxRounds"], second["maxQuestions"]) == (2, 3, 8)
    answered = [item for item in second["previousQuestions"] if item["ruleId"] == "A1"]
    assert len(answered) == 6
    assert all(item["answer"].startswith("Decided") for item in answered)
    assert "Never ask again" in second["instructions"]
    with application._services(python_workspace) as services:
        task = services.state.get("task", task_id, Task)
    assumptions = task.metadata["assumptions"]
    assert [item["assumptionId"] for item in assumptions] == [f"AS-{n}" for n in range(1, 7)]
    assert assumptions[0]["question"] == "Round 4: what happens for edge case 4.1?"
    assert assumptions[0]["rounds"] == 3
    # The implement request carries the revision and the gate contract shows the assumptions.
    [implement] = calls(log, "implement")
    assert implement["task"]["metadata"]["assumptions"] == assumptions
    assert implement["gate"]["assumptions"] == assumptions
    brief = application.review(python_workspace, run)
    assert brief["asked"]["assumptions"] == assumptions
    assert any("AS-1 was not answered" in line for line in brief["notVerified"])
    recorded = [
        item.event_type
        for item in events(application, python_workspace, run)
        if item.event_type.startswith(("intent.ambiguity", "intent.assumptions"))
    ]
    assert recorded == ["intent.ambiguity.exhausted", "intent.assumptions.recorded"]


def test_block_keeps_asking_at_the_cap(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, tmp_path, "keeps", {**CONVERGING, "onExhausted": "block"})
    application, run, task_id = start(python_workspace, tmp_path)
    for _ in range(3):
        answer_round(application, python_workspace, run, task_id, tmp_path)
    assert phase_of(application, python_workspace, run) == PhaseId.INTENT
    assert application.status(python_workspace, run)["execution"]["status"] == (
        ResultStatus.BLOCKED
    )
    exhausted = [
        item.payload
        for item in events(application, python_workspace, run)
        if item.event_type == "intent.ambiguity.exhausted"
    ]
    assert [item["onExhausted"] for item in exhausted] == ["block"]
    assert exhausted[0]["open"] == 6
    assert len(latest_request(application, python_workspace, run).questions) == 6


def test_a_round_asks_at_most_max_questions(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "keeps", {**CONVERGING, "maxQuestions": 4})
    application, run, _ = start(python_workspace, tmp_path)
    assert len(latest_request(application, python_workspace, run).questions) == 4
    [request] = calls(log, "clarify")
    assert "at most 4 questions" in request["instructions"]


def test_the_pilot_scenario_reaches_implementation(python_workspace: Path, tmp_path: Path) -> None:
    """The pilot's task and the questions its Haiku review asked, with the intake settings of
    the pilot's governed cells and the converging review that ``harness init`` now writes."""
    rounds = json.loads(PILOT_ROUNDS.read_text(encoding="utf-8"))["rounds"]
    log = configure(python_workspace, tmp_path, "replay", CONVERGING)
    path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["intake"].update(
        {
            "validateAnswers": True,
            "operationalContract": "batch",
            "interruptions": {
                "stopConditions": [
                    "unresolvable-ambiguity",
                    "scope-contradiction",
                    "destructive-collision",
                ],
                "target": 2,
            },
        }
    )
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    application, run, task_id = start(python_workspace, tmp_path, PILOT_TASK)
    asked = [answer_round(application, python_workspace, run, task_id, tmp_path) for _ in range(3)]
    # 10, 8 and 8 questions in the pilot; at most maxQuestions (8) a round here.
    assert [len(item) for item in rounds] == [10, 8, 8]
    assert asked == [8, 8, 8]
    # The pilot stopped here, still in INTENT; the converging review moves on.
    assert phase_of(application, python_workspace, run) != PhaseId.INTENT
    assert len(calls(log, "implement")) == 1
    with application._services(python_workspace) as services:
        task = services.state.get("task", task_id, Task)
    assert len(task.metadata["assumptions"]) == len(rounds[-1])
    contract = application.review(python_workspace, run)["contract"]
    assert len(contract["assumptions"]) == len(rounds[-1])
