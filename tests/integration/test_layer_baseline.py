"""Layer rules and the baseline (#93): an inferred rule the code already breaks is reported when
it is proposed and not enforced as written (``architecture.baselineConflicts: allow``), and the
layer check judges only the layers a change adds (``architecture.layerCheck: differential``).

The case is the one of the wave 9 pilot: ``encoding.py`` imports ``from .exc import BadData`` on
the baseline, the survey infers that ``encoding`` may depend on no other layer, and a change
that keeps that import was blamed for it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.checks.layers import LayerRules, check_layers, crossings
from governed_harness.domain.enums import DecisionKind, FindingSeverity
from governed_harness.domain.models import Finding
from governed_harness.orchestration.architecture import allowing, baseline_conflicts
from tests.conftest import GIT_ENV, GIT_ISOLATION

RULE = "architecture.layer-violation"
ENCODING = "src/sample/encoding.py"
BASELINE_ENCODING = (
    "from .exc import BadData\n\n\n"
    "def decode(value: str) -> str:\n"
    "    if not value:\n"
    "        raise BadData\n"
    "    return value\n"
)
CHANGED_ENCODING = BASELINE_ENCODING + "\n\ndef encode(value: str) -> str:\n    return value\n"
NEW_IMPORT_ENCODING = "from .pricing import apply_discount\n" + CHANGED_ENCODING
EXC = "class BadData(Exception):\n    pass\n"
PRICING = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
LAYERS = [
    {"name": "encoding", "paths": [ENCODING]},
    {"name": "exc", "paths": ["src/sample/exc.py"]},
    {"name": "core", "paths": ["src/sample/pricing.py"]},
]
RULES = LayerRules.from_mapping(LAYERS, {})

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
kind = request.get("kind", "implement")
if kind == "architecture":
    print(json.dumps({"status": "PASSED", "summary": kind, "result": {
        "style": "layered", "summary": "Encoding, errors and pricing.",
        "layers": LAYERS, "allow": {}}}))
    sys.exit(0)
for path, text in FILES.items():
    Path(path).write_text(text)
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""

TASK = (
    "taskId: task_layers\n"
    "title: Encode values\n"
    "intent: Add encode next to decode.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: encode returns its value.\n"
    "metadata:\n"
    f"  ownedPaths: [{ENCODING}, src/sample/pricing.py]\n"
)


# ----- the check ------------------------------------------------------------------------------
def test_an_import_the_baseline_has_is_not_judged() -> None:
    found = check_layers(
        {ENCODING: CHANGED_ENCODING},
        RULES,
        severity=FindingSeverity.HIGH,
        baseline={ENCODING: BASELINE_ENCODING}.get,
    )
    assert found == []


def test_without_a_baseline_every_import_is_judged() -> None:
    found = check_layers({ENCODING: CHANGED_ENCODING}, RULES, severity=FindingSeverity.HIGH)
    assert [(item.path, item.line) for item in found] == [(ENCODING, 1)]


def test_an_import_the_change_adds_is_judged() -> None:
    found = check_layers(
        {ENCODING: NEW_IMPORT_ENCODING},
        RULES,
        severity=FindingSeverity.HIGH,
        baseline={ENCODING: BASELINE_ENCODING}.get,
    )
    assert [item.line for item in found] == [1]
    assert "layer core" in found[0].message


def test_a_new_file_is_judged_whole() -> None:
    found = check_layers(
        {ENCODING: BASELINE_ENCODING},
        RULES,
        severity=FindingSeverity.HIGH,
        baseline={}.get,
    )
    assert len(found) == 1


def test_conflicts_of_rules_with_the_code_and_the_rules_that_allow_them() -> None:
    files = {ENCODING: BASELINE_ENCODING, "src/sample/exc.py": EXC}
    rules = {"layers": LAYERS, "allow": {}}
    conflicts = baseline_conflicts(files, rules)
    assert conflicts == [
        {
            "from": "encoding",
            "to": "exc",
            "imports": 1,
            "examples": [{"path": ENCODING, "line": 1, "target": "sample.exc"}],
        }
    ]
    enforced = allowing(rules, conflicts)
    assert enforced["allow"] == {"encoding": ["exc"]}
    assert crossings(files, LayerRules.from_mapping(enforced["layers"], enforced["allow"])) == []


# ----- the run --------------------------------------------------------------------------------
def prepare(workspace: Path, tmp_path: Path, encoding: str, keys: dict[str, Any]) -> str:
    """A project whose baseline has the import, with the survey agent; returns the task id."""
    (workspace / ENCODING).write_text(BASELINE_ENCODING, encoding="utf-8")
    (workspace / "src" / "sample" / "exc.py").write_text(EXC, encoding="utf-8")
    for arguments in (("add", "."), ("commit", "-qm", "encoding")):
        subprocess.run(["git", *GIT_ISOLATION, *arguments], cwd=workspace, check=True, env=GIT_ENV)
    files = {ENCODING: encoding, "src/sample/pricing.py": PRICING}
    script = AGENT.replace("LAYERS", repr(LAYERS)).replace("FILES", repr(files))
    (tmp_path / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", str(tmp_path / "agent.py")]}
    }
    config["runtime"].update(
        {"agentSandbox": "off", "providerRetries": 0, "verificationCorrections": 0}
    )
    config["architecture"] = {"mode": "agent", **keys}
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    return HarnessApplication().create_task(workspace, source).task_id


def approve(workspace: Path, run: str) -> dict[str, Any]:
    application = HarnessApplication()
    return application.decide_architecture(
        workspace,
        execution_id=run,
        digest=application.architecture(workspace)["state"]["digest"],
        rationale="Matches the code",
        decision=DecisionKind.APPROVE,
        actor_id="human.lead",
    )


def violations(workspace: Path, run: str) -> list[Finding]:
    with HarnessApplication()._services(workspace) as services:
        found = services.state.list("finding", Finding, execution_id=run)
    return [item for item in found if item.rule_id == RULE]


def test_without_the_keys_the_baseline_import_is_blamed(
    python_workspace: Path, tmp_path: Path
) -> None:
    """The defect of the pilot, kept for a project.yaml without the new keys."""
    task = prepare(python_workspace, tmp_path, CHANGED_ENCODING, {})
    run = HarnessApplication().start_run(python_workspace, task).execution_id
    state = HarnessApplication().architecture(python_workspace)["state"]
    assert "baselineConflicts" not in state
    approve(python_workspace, run)
    [blamed] = violations(python_workspace, run)
    assert "sample.exc" in blamed.message


def test_differential_check_judges_only_what_the_change_adds(
    python_workspace: Path, tmp_path: Path
) -> None:
    task = prepare(python_workspace, tmp_path, NEW_IMPORT_ENCODING, {"layerCheck": "differential"})
    run = HarnessApplication().start_run(python_workspace, task).execution_id
    approve(python_workspace, run)
    [added] = violations(python_workspace, run)
    assert "sample.pricing" in added.message
    assert added.severity is FindingSeverity.HIGH


def test_differential_check_does_not_blame_the_baseline_import(
    python_workspace: Path, tmp_path: Path
) -> None:
    task = prepare(python_workspace, tmp_path, CHANGED_ENCODING, {"layerCheck": "differential"})
    run = HarnessApplication().start_run(python_workspace, task).execution_id
    approve(python_workspace, run)
    assert violations(python_workspace, run) == []


def test_conflicts_are_listed_in_the_proposal_and_allowed_when_approved(
    python_workspace: Path, tmp_path: Path
) -> None:
    task = prepare(python_workspace, tmp_path, CHANGED_ENCODING, {"baselineConflicts": "allow"})
    application = HarnessApplication()
    started = application.start_run(python_workspace, task)
    run = started.execution_id
    assert (started.current_phase.value, started.status.value) == ("DISCOVERY", "BLOCKED")
    state = application.architecture(python_workspace)["state"]
    assert [(item["from"], item["to"]) for item in state["baselineConflicts"]] == [
        ("encoding", "exc")
    ]
    assert state["rules"]["allow"] == {}
    assert state["enforced"]["allow"] == {"encoding": ["exc"]}
    [entry] = [item for item in application.inbox(python_workspace) if item["executionId"] == run]
    assert entry["kind"] == "architecture"
    assert "encoding -> exc (1 import(s))" in entry["summary"]
    assert entry["baselineConflicts"] == state["baselineConflicts"]
    approve(python_workspace, run)
    assert violations(python_workspace, run) == []
    report = application.architecture(python_workspace)
    assert json.dumps(report["rules"]["allow"]) == '{"encoding": ["exc"]}'
