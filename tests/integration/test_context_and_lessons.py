"""The bounded context manifest (context.manifest, #41) and lessons from findings that caused
a correction (memory.learnFromFindings, #43)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, PhaseId

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
secret = "feedback" not in request and MODE == "secret"
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    + ('    api_token = "Zq81-real-Token-77"\\n' if secret else "")
    + "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
Path("tests/test_pricing.py").write_text(
    "from sample import apply_discount\\n\\n"
    "def test_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n"
)
print(json.dumps({"status": "PASSED", "summary": "done"}))
"""


def task_yaml(task_id: str) -> str:
    return (
        f"taskId: {task_id}\n"
        "title: Threshold discount\n"
        "intent: Apply the discount described in docs/SPEC.md at or above the threshold.\n"
        "requirements:\n"
        "  - requirementId: REQ-7\n"
        "    text: Apply the discount at or above the threshold.\n"
        "acceptanceCriteria:\n"
        "  - criterionId: AC-1\n"
        "    text: apply_discount(100, 100, 0.1) returns 90.\n"
        "metadata:\n"
        "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
    )


def configure(workspace: Path, tmp_path: Path, mode: str, **sections: Any) -> Path:
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
    config.update(sections)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def requests(log: Path) -> list[dict[str, Any]]:
    return list(json.loads(log.read_text()))


def run(application: HarnessApplication, workspace: Path, tmp_path: Path, task_id: str) -> str:
    source = tmp_path / f"{task_id}.yaml"
    source.write_text(task_yaml(task_id), encoding="utf-8")
    application.create_task(workspace, source)
    return application.start_run(workspace, task_id).execution_id


def test_the_manifest_ranks_what_the_task_references(
    python_workspace: Path, tmp_path: Path
) -> None:
    (python_workspace / "docs").mkdir()
    (python_workspace / "docs" / "SPEC.md").write_text("# Spec\n\nREQ-7: discount rules.\n")
    (python_workspace / "src" / "sample" / "unrelated.py").write_text("VALUE = 1\n")
    log = configure(
        python_workspace, tmp_path, "plain", context={"manifest": "auto", "maxFiles": 5}
    )
    application = HarnessApplication()
    run_id = run(application, python_workspace, tmp_path, "task_manifest")
    [request] = requests(log)
    manifest = request["contextFiles"]
    first = manifest["files"][0]
    assert first["path"] == "docs/SPEC.md"
    assert "referenced by the task" in first["reasons"]
    assert any("REQ-7" in reason for reason in first["reasons"])
    assert "unrelated.py" not in {Path(item["path"]).name for item in manifest["files"]}
    assert "You may read any other file" in manifest["guidance"]
    assert request["kind"] == "implement"
    with application._services(python_workspace) as services:
        built = [
            item
            for item in services.events.list(run_id)
            if item.event_type == "context.manifest.built"
        ]
    assert built and built[0].payload["digest"] == manifest["digest"]


def test_without_the_settings_the_request_keeps_the_1_0_form(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "plain")
    application = HarnessApplication()
    run(application, python_workspace, tmp_path, "task_plain")
    [request] = requests(log)
    assert set(request) <= {"schemaVersion", "task", "plan", "context", "feedback"}
    assert request["schemaVersion"] == "1.0"


def test_a_lesson_from_one_run_reaches_the_next(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        "secret",
        verification={"requirementTraceability": "off", "secrets": "context"},
        memory={"learnFromFindings": "auto"},
    )
    application = HarnessApplication()
    first = run(application, python_workspace, tmp_path, "task_one")
    status = application.status(python_workspace, first)
    assert status["execution"]["currentPhase"] == PhaseId.DECISION
    application.decide_gate(
        python_workspace,
        execution_id=first,
        decision=DecisionKind.APPROVE,
        change_set_digest=status["execution"]["changeSetDigest"],
        actor_id="human.reviewer",
        rationale="Corrected",
    )
    proposals = [
        item["record"]
        for item in application.list_memory(python_workspace)
        if item["record"]["key"].startswith("lesson:")
    ]
    assert [item["key"] for item in proposals] == ["lesson:harness.secrets:secrets.credential"]
    assert proposals[0]["approved"] is False
    assert "src/sample/pricing.py" in proposals[0]["value"]["paths"]
    application.approve_memory(
        python_workspace, memory_id=proposals[0]["memoryId"], actor_id="human.reviewer"
    )
    second = run(application, python_workspace, tmp_path, "task_two")
    implement = [item for item in requests(log) if item["task"]["task_id"] == "task_two"][0]
    assert [item["rule"] for item in implement["lessons"]] == ["secrets.credential"]
    with application._services(python_workspace) as services:
        applied = [
            item for item in services.events.list(second) if item.event_type == "lesson.applied"
        ]
    assert applied
