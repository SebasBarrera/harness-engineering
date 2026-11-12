"""A poor request is never refused: under the configuration of ``harness init`` the one-line
Uber request enters INTENT and asks, the basics first (intake.ambiguityReview.depth: adaptive),
with a fixture agent standing in for the provider; without the key the questions and the
clarify request are the earlier ones."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import PhaseId
from governed_harness.domain.models import ClarificationRequest

UBER = {
    "taskId": "task_uber",
    "title": "Plataforma de viajes y domicilios de comida",
    "intent": "haz una app como Uber con Uber Eats, viajes y domicilios de comida",
}
PARAGRAPH = {
    "taskId": "task_paragraph",
    "title": "Plataforma de viajes y domicilios de comida",
    "intent": (
        "Construye en Python el backend de una plataforma como Uber y Uber Eats. Pasajeros, "
        "conductores, repartidores y restaurantes tienen cuentas. Los pasajeros obtienen una "
        "cotización, piden un viaje y se les asigna un conductor cercano; también piden comida "
        "a un restaurante y un repartidor la entrega."
    ),
    "acceptanceCriteria": ["Funciona."],
}

# Every clarify call asks one question; the other read-only calls answer with nothing.
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
    result = {"questions": [{"category": "actors-and-roles", "target": "task",
                             "text": "Can a driver also order food?"}]}
elif kind == "locate":
    result = {"locations": [], "questions": []}
else:
    result = {}
print(json.dumps({"status": "PASSED", "summary": "ok", "result": result}))
"""


def configure(workspace: Path, tmp_path: Path, *, depth: bool) -> Path:
    HarnessApplication().init(workspace, force=True)
    log = tmp_path / "calls.json"
    (workspace / "agent.py").write_text(AGENT.replace("LOG", repr(str(log))), encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["intake"]["ambiguityReview"]["depth"] == "adaptive"
    if not depth:
        del config["intake"]["ambiguityReview"]["depth"]
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {"fixture": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"].update({"agentSandbox": "off", "providerRetries": 0})
    # Only INTENT's questions are under test: no project setup or architecture questions.
    config["intake"].pop("projectSetup", None)
    config.get("planning", {}).pop("architecture", None)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path, task: dict[str, Any]) -> tuple[HarnessApplication, str]:
    source = tmp_path / f"{task['taskId']}.yaml"
    source.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    application = HarnessApplication()
    created = application.create_task(workspace, source)
    assert created.criteria_pending is ("acceptanceCriteria" not in task)
    return application, application.start_run(workspace, task["taskId"]).execution_id


def open_request(application: HarnessApplication, workspace: Path, run: str) -> Any:
    with application._services(workspace) as services:
        requests = services.state.list(
            "clarification_request", ClarificationRequest, execution_id=run
        )
    return max(requests, key=lambda item: item.created_at)


def clarify_requests(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [item for item in json.loads(log.read_text()) if item.get("kind") == "clarify"]


def payload(request: dict[str, Any]) -> dict[str, Any]:
    """The clarify fields, wherever the request envelope keeps them."""
    for key in ("input", "payload", "context"):
        if isinstance(request.get(key), dict) and "categories" in request[key]:
            return dict(request[key])
    return request


def test_the_one_line_uber_request_enters_intent_and_asks_the_basics_first(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, depth=True)
    application, run = start(python_workspace, tmp_path, UBER)
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == PhaseId.INTENT
    request = open_request(application, python_workspace, run)
    targets = [question.target for question in request.questions]
    assert targets[:3] == ["task:users", "task:actions", "task:flows"]
    assert "task:results" in targets
    assert request.questions[0].text.startswith("¿Quiénes usan")
    [clarify] = clarify_requests(log)
    depth = payload(clarify)["depth"]
    assert depth["level"] == "very-poor" and depth["rounds"] == 6 and depth["questions"] == 12
    assert [item["topic"] for item in depth["topics"]] == ["users", "actions", "flows"]
    assert payload(clarify)["maxRounds"] == 6


def test_a_paragraph_asks_fewer_basics_and_plans_fewer_rounds(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, depth=True)
    application, run = start(python_workspace, tmp_path, PARAGRAPH)
    request = open_request(application, python_workspace, run)
    basics = [q for q in request.questions if q.category == "basics"]
    assert len(basics) == 5
    [clarify] = clarify_requests(log)
    assert payload(clarify)["depth"]["level"] == "poor"
    assert payload(clarify)["maxRounds"] == 4


def test_without_the_key_the_questions_and_the_fast_lane_are_the_earlier_ones(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, depth=False)
    application, run = start(python_workspace, tmp_path, UBER)
    request = open_request(application, python_workspace, run)
    assert not any(q.category == "basics" for q in request.questions)
    assert [q.rule_id for q in request.questions if q.rule_id == "C0"] == ["C0"] * 7
    # As before the key: the one-line wish is size S, the fast lane keeps the deterministic
    # intake only and no clarify call is made.
    assert clarify_requests(log) == []
    with application._services(python_workspace) as services:
        skipped = [
            item.payload
            for item in services.events.list(run)
            if item.event_type == "lane.step.skipped"
        ]
    assert skipped == [{"lane": "fast", "phaseId": "INTENT", "step": "ambiguityReview"}]
