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
    created = runner.invoke(app, ["task", "create", "--path", str(python_workspace), "--file", str(task)])
    assert created.exit_code == 0
    task_id = json.loads(created.stdout)["taskId"]
    listed = runner.invoke(app, ["task", "list", "--path", str(python_workspace)])
    assert listed.exit_code == 0
    assert any(item["taskId"] == task_id for item in json.loads(listed.stdout))
