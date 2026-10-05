"""The ``feedback`` block of the command-provider request and the loop settings are public
contracts: ``provider-feedback.schema.json`` and ``project-config.schema.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind

SCHEMA_DIR = Path(__file__).parents[2] / "schemas" / "v1"

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
body = "subtotal * (1 - rate) if subtotal >= threshold else subtotal"
if "feedback" not in request:
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
print(json.dumps({"status": "PASSED", "summary": "done"}))
"""

TASK = (
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)


def _validate(schema_name: str, value: Any) -> None:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def test_project_schema_accepts_the_loop_settings_and_their_absence(
    python_workspace: Path,
) -> None:
    value = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    loop = {
        key: item
        for key, item in value["runtime"].items()
        if key not in {"agentSandbox", "sandboxWritePaths"}
    }
    assert loop == {
        "commandTimeoutSeconds": 900,
        "maxOutputBytes": 1000000,
        "maxParallel": 2,
        "allowNetwork": False,
        "verificationCorrections": 2,
        "providerFeedback": True,
        "unsupportedClaimSeverity": "MEDIUM",
        "providerRetries": 3,
        "providerRetryDelaySeconds": 60,
    }
    _validate("project-config.schema.json", value)
    _validate(
        "project-config.schema.json",
        {**value, "runtime": {**value["runtime"], "providerTransientPatterns": ["busy"]}},
    )
    for key in (
        "verificationCorrections",
        "providerFeedback",
        "unsupportedClaimSeverity",
        "providerRetries",
        "providerRetryDelaySeconds",
    ):
        del value["runtime"][key]
    _validate("project-config.schema.json", value)
    with pytest.raises(ValidationError):
        _validate(
            "project-config.schema.json",
            {**value, "runtime": {**value["runtime"], "verificationCorrections": -1}},
        )


def test_feedback_sent_to_the_provider_matches_its_schema(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = tmp_path / "requests.json"
    (python_workspace / "agent.py").write_text(AGENT.replace("LOG", repr(str(log))))
    config_path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"]["agentSandbox"] = "off"  # about the protocol; the sandbox has its own tests
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK)
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_path)
    run = application.start_run(python_workspace, task.task_id)
    assert run.change_set_digest
    application.decide_gate(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=run.change_set_digest,
        actor_id="human.reviewer",
        rationale="Name the rate parameter discount_rate.",
    )
    application.continue_run(python_workspace, run.execution_id)
    sent = json.loads(log.read_text())
    triggers = [item["feedback"]["trigger"] for item in sent if "feedback" in item]
    assert triggers == ["VERIFICATION_FAILED", "CHANGES_REQUESTED"]
    for item in sent[1:]:
        _validate("provider-feedback.schema.json", item["feedback"])
    with pytest.raises(ValidationError):
        _validate("provider-feedback.schema.json", {**sent[1]["feedback"], "attempt": 1})
