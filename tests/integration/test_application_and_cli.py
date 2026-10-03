from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app


def test_application_init_and_inspect(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1'\n")
    application = HarnessApplication()
    result = application.init(project)
    assert Path(result["configuration"]).exists()
    inspection = application.inspect(project)
    assert inspection["detections"][0]["profileId"] == "python_default"


def test_cli_doctor_json(python_workspace: Path) -> None:
    result = CliRunner().invoke(app, ["doctor", "--path", str(python_workspace), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "PASSED"


def test_cli_task_create_and_list(python_workspace: Path, tmp_path: Path) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        "title: Example\nintent: Do work\nacceptanceCriteria:\n  - It works\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    created = runner.invoke(
        app, ["task", "create", "--path", str(python_workspace), "--file", str(task)]
    )
    assert created.exit_code == 0
    task_id = json.loads(created.stdout)["taskId"]
    listed = runner.invoke(app, ["task", "list", "--path", str(python_workspace)])
    assert listed.exit_code == 0
    assert any(item["taskId"] == task_id for item in json.loads(listed.stdout))


def test_cli_gate_decide_prints_camel_case_like_every_other_command(
    python_workspace: Path, tmp_path: Path
) -> None:
    """gate decide used snake_case keys while the rest of the CLI and the API use camelCase (#30)."""
    task = tmp_path / "task.yaml"
    task.write_text(
        "title: Discount\n"
        "intent: Apply a discount at or above the threshold.\n"
        "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
        "implementation:\n  mode: patch\n  patches:\n"
        "    - path: src/sample/pricing.py\n      operation: replace\n      content: |\n"
        "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    path = ["--path", str(python_workspace)]
    created = json.loads(runner.invoke(app, ["task", "create", "--file", str(task), *path]).stdout)
    started = runner.invoke(app, ["run", "start", "--task", created["taskId"], *path])
    assert started.exit_code == 4
    run = json.loads(started.stdout)
    decided = runner.invoke(
        app,
        [
            "gate",
            "decide",
            "--run",
            run["executionId"],
            "--decision",
            "APPROVE",
            "--change-set-digest",
            run["changeSetDigest"],
            "--actor",
            "human.reviewer",
            "--rationale",
            "reviewed",
            *path,
        ],
    )
    assert decided.exit_code == 0
    output = json.loads(decided.stdout)
    assert output["decision"]["changeSetDigest"] == run["changeSetDigest"]
    assert output["execution"]["currentPhase"] == "CLOSURE"
    assert "change_set_digest" not in decided.stdout
