"""The wave 4 commands through the CLI: exit codes of export, verify --bundle and
verify-approval."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind

TASK = (
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - A subtotal of 100 with a ten percent rate returns 90.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)


def test_export_verify_and_verify_approval_exit_codes(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    run = application.start_run(python_workspace, task.task_id)
    base = application.status(python_workspace, run.execution_id)["execution"]["baselineRevision"]
    application.decide_gate(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=run.change_set_digest or "",
        actor_id="human.reviewer",
        rationale="No test names the new threshold yet; tracked separately",
    )
    runner = CliRunner()
    bundle = tmp_path / "evidence.tar.gz"
    path = ["--path", str(python_workspace)]
    exported = runner.invoke(
        app, ["--json", "export", "--run", run.execution_id, "--bundle", str(bundle), *path]
    )
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["eventChainHead"].startswith("sha256:")
    verified = runner.invoke(app, ["--json", "verify", "--bundle", str(bundle)])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["valid"] is True
    both = runner.invoke(app, ["verify", "--run", run.execution_id, "--bundle", str(bundle)])
    assert both.exit_code == 2
    missing = runner.invoke(app, ["verify", "--bundle", str(tmp_path / "absent.tar.gz")])
    assert missing.exit_code == 3
    head = f"harness/{run.execution_id}"
    approved = runner.invoke(
        app,
        [
            "--json",
            "verify-approval",
            "--base",
            base,
            "--head",
            head,
            "--bundle",
            str(bundle),
            "--no-workspace",
            *path,
        ],
    )
    assert approved.exit_code == 0, approved.output
    report = json.loads(approved.output)
    assert report["matchedApproval"]["decision"] == "APPROVE_EXCEPTION"
    assert report["trailers"][0]["Harness-Exception"].startswith("No test names")
    unapproved = runner.invoke(
        app, ["--json", "verify-approval", "--base", base, "--head", base, *path]
    )
    assert unapproved.exit_code == 5
    assert any("changes no file" in item for item in json.loads(unapproved.output)["reasons"])
