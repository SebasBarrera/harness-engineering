"""Onboarding (#53): --version, readable output on a terminal, init helpers, doctor checks and
error hints."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness import __version__
from governed_harness.application import HarnessApplication
from governed_harness.application.onboarding import sandbox_check
from governed_harness.application.task_loader import load_task_file
from governed_harness.cli.main import app
from governed_harness.cli.render import render_human, wants_json
from governed_harness.configuration import ConfigurationResolver
from governed_harness.runtime.sandbox import SandboxHost


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_version_flag() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"harness {__version__}"


def test_json_is_the_default_for_pipes_and_text_for_terminals() -> None:
    assert wants_json(None, io.StringIO()) is True
    assert wants_json(None, _Terminal()) is False
    assert wants_json(True, _Terminal()) is True
    assert wants_json(False, io.StringIO()) is False


def test_no_json_prints_text_not_a_python_repr(python_workspace: Path) -> None:
    runner = CliRunner()
    piped = runner.invoke(app, ["inspect", "--path", str(python_workspace)])
    assert json.loads(piped.stdout)["detections"]
    text = runner.invoke(app, ["inspect", "--path", str(python_workspace), "--no-json"])
    assert text.exit_code == 0
    assert "{'" not in text.stdout and "profile id: python_default" in text.stdout
    global_flag = runner.invoke(app, ["--no-json", "run", "list", "--path", str(python_workspace)])
    assert global_flag.stdout.startswith("No runs recorded.")
    forced = runner.invoke(app, ["--json", "run", "list", "--path", str(python_workspace)])
    assert json.loads(forced.stdout) == []


def test_human_execution_output_says_what_the_run_waits_for() -> None:
    text = render_human(
        {
            "executionId": "run_x",
            "taskId": "task_x",
            "status": "BLOCKED",
            "currentPhase": "DECISION",
            "changeSetDigest": "sha256:" + "a" * 64,
        },
        "execution",
    )
    assert "waiting for a human decision" in text
    assert "harness gate decide --run run_x" in text


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1'\n")
    return root


def test_cli_init_adds_gitignore_and_an_example_task(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".gitignore").write_text("dist/")
    result = CliRunner().invoke(app, ["init", "--path", str(root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["gitignore"] == "added"
    assert payload["profiles"][0]["profileId"] == "python_default"
    assert (root / ".gitignore").read_text() == "dist/\n.harness/\n"
    example = Path(payload["exampleTask"])
    assert example.parent == root / ".harness"
    task = load_task_file(example, project_id="p", criteria_policy="enforce")
    assert task.metadata["ownedPaths"] == ["src/", "tests/"]
    again = CliRunner().invoke(app, ["init", "--path", str(root), "--force"])
    assert json.loads(again.stdout)["gitignore"] == "present"
    assert (root / ".gitignore").read_text().count(".harness/") == 1


def test_init_opt_outs_and_the_python_api_leave_the_workspace_alone(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = CliRunner().invoke(
        app, ["init", "--path", str(root), "--no-gitignore", "--no-example-task"]
    )
    assert result.exit_code == 0
    assert not (root / ".gitignore").exists()
    assert not (root / ".harness" / "task.example.yaml").exists()
    other = _project(tmp_path / "api")
    HarnessApplication().init(other)
    assert not (other / ".gitignore").exists()


def test_doctor_reports_the_new_checks(python_workspace: Path) -> None:
    result = HarnessApplication().doctor(python_workspace)
    checks = result["checks"]
    assert result["status"] == "PASSED"
    for name in ("gitIdentity", "baseline", "agentProvider", "agentSandbox", "validators"):
        assert name in checks, name
    assert checks["agentProvider"]["provider"] == "simulated"
    assert {item["id"] for item in checks["validators"]["validators"]} >= {"python.pytest"}


def _set(workspace: Path, **changes: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value.update(changes)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def test_doctor_fails_when_the_agent_cli_is_missing(python_workspace: Path) -> None:
    _set(
        python_workspace,
        agentProvider="agent",
        agentProviders={"agent": {"command": ["definitely-not-an-agent-cli-53"]}},
    )
    result = CliRunner().invoke(app, ["doctor", "--path", str(python_workspace)])
    assert result.exit_code == 2
    check = json.loads(result.stdout)["checks"]["agentProvider"]
    assert check["status"] == "FAILED" and "definitely-not-an-agent-cli-53" in check["hint"]


def test_doctor_fails_on_a_missing_mandatory_validator(python_workspace: Path) -> None:
    path = python_workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value["validators"] = ["python.pytest"]
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    resolved = ConfigurationResolver().resolve(python_workspace)
    from governed_harness.application.onboarding import validator_checks

    broken = resolved.model_copy(
        update={
            "effective_validators": tuple(
                item.model_copy(update={"command": ("no-such-python-53", "-m", "pytest")})
                for item in resolved.effective_validators
            )
        }
    )
    check = validator_checks(broken)
    assert check["status"] == "FAILED"
    assert check["validators"][0]["hint"].startswith("Install 'no-such-python-53'")


@pytest.mark.parametrize(("provider", "expected"), [("simulated", "WARNING"), ("agent", "FAILED")])
def test_sandbox_without_mechanism(python_workspace: Path, provider: str, expected: str) -> None:
    _set(
        python_workspace,
        agentProvider=provider,
        agentProviders={"agent": {"command": ["true"]}},
    )
    resolved = ConfigurationResolver().resolve(python_workspace)
    host = SandboxHost(system="Linux", home=Path.home(), temp_dir=Path("/tmp"), bwrap=None)
    check = sandbox_check(resolved, host)
    assert check["status"] == expected
    assert "bubblewrap" in check["hint"]


def test_errors_carry_a_hint(python_workspace: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    missing = runner.invoke(app, ["status", "--path", str(python_workspace), "--run", "run_nope"])
    assert missing.exit_code == 3
    assert "harness run list" in json.loads(missing.stderr)["hint"]
    empty = tmp_path / "empty"
    empty.mkdir()
    unconfigured = runner.invoke(app, ["run", "list", "--path", str(empty)])
    assert unconfigured.exit_code == 2
    assert "harness init" in json.loads(unconfigured.stderr)["hint"]
