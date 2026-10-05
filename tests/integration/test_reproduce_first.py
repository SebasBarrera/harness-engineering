"""Reproduce-first corrections and empty corrections (runtime.reproduceFirst, #52)."""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, FindingSeverity, PhaseId
from governed_harness.domain.models import Finding

TASK = (
    "taskId: task_repro\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py, tests/test_negative.py]\n"
)

# The first call writes an implementation that ignores negative subtotals; the correction
# (a call with feedback) behaves as MODE says.
AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
pricing = Path("src/sample/pricing.py")
base = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
if "feedback" not in request:
    if MODE == "empty":
        pricing.write_text(base.replace("1 - rate", "1 + rate"))
    else:
        pricing.write_text(base)
    Path("tests/test_pricing.py").write_text(
        "from sample import apply_discount\\n\\n"
        "def test_at_threshold() -> None:\\n"
        "    assert apply_discount(100, 100, 0.1) == 90\\n"
    )
elif MODE == "reproduced":
    pricing.write_text(
        base.replace("    return", "    if subtotal < 0:\\n        raise ValueError(subtotal)\\n    return")
    )
    Path("tests/test_negative.py").write_text(
        "import pytest\\n\\nfrom sample import apply_discount\\n\\n"
        "def test_negative() -> None:\\n"
        "    with pytest.raises(ValueError):\\n"
        "        apply_discount(-1, 100, 0.1)\\n"
    )
elif MODE == "untested":
    pricing.write_text(
        base.replace("    return", "    if subtotal < 0:\\n        raise ValueError(subtotal)\\n    return")
    )
print(json.dumps({"status": "PASSED", "summary": "The code already complies" if MODE == "empty" else "done"}))
"""


def configure(workspace: Path, mode: str) -> None:
    (workspace / "agent.py").write_text(AGENT.replace("MODE", repr(mode)), encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    runtime = config["runtime"]
    runtime.update(
        {
            "agentSandbox": "off",
            "verificationCorrections": 1,
            "providerFeedback": True,
            "providerRetries": 0,
            "reproduceFirst": True,
        }
    )
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_repro").execution_id


def finding(application: HarnessApplication, workspace: Path, run: str, rule: str) -> list[Finding]:
    return [item for item in application.list_findings(workspace, run) if item.rule_id == rule]


def request_changes(application: HarnessApplication, workspace: Path, run: str) -> None:
    digest = application.status(workspace, run)["execution"]["changeSetDigest"]
    application.decide_gate(
        workspace,
        execution_id=run,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="A negative subtotal must raise ValueError",
    )
    application.continue_run(workspace, run)


def test_an_empty_correction_is_a_finding(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, "empty")
    application, run = start(python_workspace, tmp_path)
    [empty] = finding(application, python_workspace, run, "agent.empty-correction")
    assert empty.severity is FindingSeverity.MEDIUM
    assert "already complies" in empty.message


def test_a_reproduced_correction_passes(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, "reproduced")
    application, run = start(python_workspace, tmp_path)
    request_changes(application, python_workspace, run)
    assert not finding(application, python_workspace, run, "correction.not-reproduced")
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == (
        PhaseId.DECISION
    )


def test_a_correction_without_a_reproducing_test_blocks(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, "untested")
    application, run = start(python_workspace, tmp_path)
    request_changes(application, python_workspace, run)
    [missing] = finding(application, python_workspace, run, "correction.not-reproduced")
    assert missing.severity is FindingSeverity.HIGH
