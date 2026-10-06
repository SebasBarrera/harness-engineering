"""Forges end to end (#56): a GitLab origin is detected, the brief is published as a merge
request note, a merge request is opened from the closure branch with the template and the
labels, a commit status follows the run and the Code Quality report is exported. Every request
goes to a fake transport: no network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.cli.main import app
from governed_harness.delivery.publisher import marker
from governed_harness.domain.errors import ConfigurationError
from tests.conftest import GIT_ENV
from tests.integration.test_delivery import run_to_decision


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        if method == "GET":
            return []
        if path.endswith("/merge_requests"):
            return {"iid": 42, "web_url": "https://gitlab.example.invalid/mr/42"}
        return {"id": 1}


def _origin(root: Path, url: str) -> None:
    subprocess.run(["git", "remote", "add", "origin", url], cwd=root, check=True, env=GIT_ENV)


def _forge(root: Path, **values: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.setdefault("delivery", {})["forge"] = values
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_gitlab_origin_publish_create_and_status(python_workspace: Path) -> None:
    _origin(python_workspace, "https://gitlab.example.com/team/shop.git")
    _forge(python_workspace, baseBranch="main", labels=["governed"])
    template = python_workspace / ".gitlab" / "merge_request_templates"
    template.mkdir(parents=True)
    (template / "Default.md").write_text("## Review checklist\n", encoding="utf-8")
    application, run, digest = run_to_decision(python_workspace)
    assert application.forge(python_workspace)["kind"] == "gitlab"

    transport = Recorder()
    report = application.publish_pull_request(
        python_workspace, run, pull_request=7, transport_override=transport
    )
    assert report["publisher"] == "gitlab"
    assert report["comment"]["action"] == "created"
    method, path, body = transport.calls[1]
    assert (method, path) == ("POST", "projects/team%2Fshop/merge_requests/7/notes")
    assert marker(run) in body["body"]
    assert digest in body["body"]
    assert report["reports"]["status"] == "SKIPPED"

    transport = Recorder()
    created = application.create_pull_request(
        python_workspace, run, labels=("extra",), transport_override=transport
    )
    assert created["pullRequest"]["number"] == 42
    assert created["head"] == f"harness/{run}"
    assert created["template"] == ".gitlab/merge_request_templates/Default.md"
    _, path, body = transport.calls[-1]
    assert path == "projects/team%2Fshop/merge_requests"
    assert body["labels"] == "governed,extra"
    assert body["description"].startswith("## Review checklist")
    events = application.trace(python_workspace, run, "jsonl").decode().splitlines()
    assert any('"delivery.pull-request.created"' in line for line in events)

    transport = Recorder()
    status = application.pull_request_status(
        python_workspace, run, commit_sha="abc123", transport_override=transport
    )
    assert status["state"] == "pending"
    assert transport.calls[-1][2]["state"] == "pending"
    assert transport.calls[-1][1] == "projects/team%2Fshop/statuses/abc123"


def test_create_needs_a_base_branch(python_workspace: Path) -> None:
    _origin(python_workspace, "https://gitlab.example.com/team/shop.git")
    application, run, _ = run_to_decision(python_workspace)
    with pytest.raises(ConfigurationError, match="no base branch"):
        application.create_pull_request(python_workspace, run, transport_override=Recorder())


def test_unknown_origin_needs_the_forge(python_workspace: Path) -> None:
    _origin(python_workspace, "https://code.example.com/team/shop.git")
    application, run, _ = run_to_decision(python_workspace)
    assert application.forge(python_workspace)["status"] == "UNKNOWN"
    report = application.publish_pull_request(
        python_workspace,
        run,
        pull_request=1,
        forge="gitea",
        transport_override=Recorder(),
    )
    assert report["forge"]["apiUrl"] == "https://code.example.com/api/v1"


def test_trace_code_quality_and_forge_command(python_workspace: Path) -> None:
    _origin(python_workspace, "https://bitbucket.org/team/shop.git")
    _, run, _ = run_to_decision(python_workspace)
    runner = CliRunner()
    path = ["--path", str(python_workspace)]
    result = runner.invoke(app, ["trace", "--run", run, "--format", "codequality", *path])
    assert result.exit_code == 0, result.output
    issues = json.loads(result.output)
    assert isinstance(issues, list)
    assert all(
        {"check_name", "fingerprint", "severity", "location"} <= set(item) for item in issues
    )
    shown = runner.invoke(app, ["--json", "pr", "forge", *path])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["kind"] == "bitbucket"
    assert json.loads(shown.output)["tokenEnv"] == "BITBUCKET_TOKEN"
