"""Embedded mode with frozen acceptance tests (#81): the files the harness writes on approval are
not the session's edits, a failed verification does not quarantine them, and a later
verification does not report them as modified."""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from tests.integration import test_acceptance_tests as acceptance
from tests.integration.test_stop_line_and_contract import GOOD, ORIGINAL, WRONG

FROZEN = Path("tests") / "acceptance" / "test_ac_1.py"


def session_run(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    """A run of the session provider whose read-only calls go to the fixture agent, with
    frozen acceptance tests and stop the line restoring a stopped run's changes."""
    acceptance.configure(workspace, tmp_path, "good")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.setdefault("governance", {})["stopTheLine"] = "restore"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    source = tmp_path / "task.yaml"
    source.write_text(acceptance.TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    run = application.start_run(workspace, "task_acceptance", provider="session")
    return application, run.execution_id


def findings(application: HarnessApplication, workspace: Path, run: str, rule: str) -> list[str]:
    return [
        item.finding_id
        for item in application.list_findings(workspace, run)
        if item.rule_id == rule
    ]


def test_frozen_files_are_not_the_session_edits(python_workspace: Path, tmp_path: Path) -> None:
    application, run = session_run(python_workspace, tmp_path)
    decided = acceptance.approve(application, python_workspace, run)
    # IMPLEMENTATION waits for the session: the frozen file alone is not its change.
    assert decided["execution"]["currentPhase"] == "IMPLEMENTATION"
    assert decided["execution"]["status"] == "BLOCKED"
    frozen = (python_workspace / FROZEN).read_bytes()
    pricing = python_workspace / "src" / "sample" / "pricing.py"
    pricing.write_text(WRONG, encoding="utf-8")
    failed = application.continue_run(python_workspace, run)
    assert failed.current_phase.value == "VERIFICATION"
    assert failed.status.value == "FAILED"
    # Stop the line restored the session's edit, not the frozen acceptance test.
    assert pricing.read_text(encoding="utf-8") == ORIGINAL
    assert (python_workspace / FROZEN).read_bytes() == frozen
    pricing.write_text(GOOD, encoding="utf-8")
    passed = application.continue_run(python_workspace, run)
    assert passed.current_phase.value == "DECISION"
    assert not findings(application, python_workspace, run, "acceptance.modified")
