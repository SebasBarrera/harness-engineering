"""Delivery hygiene (#55, items 9, 10, 12, 13 and 14): the environment preflight, the run
registry outside the workspace, worktree isolation per run, complete delivery (staging, a push
that honours the hooks, the pull request and its comment through the forge layer of #56) and the
lint of agent instruction files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.delivery.approval import local_database
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import PolicyViolationError
from governed_harness.orchestration import ladder_delivery
from governed_harness.orchestration.engine import EngineServices, RunEngine

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GIT_ISOLATION = (
    "-c",
    "commit.gpgsign=false",
    "-c",
    "user.email=fixture@example.com",
    "-c",
    "user.name=Fixture",
)
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX hooks and permissions")

TASK = {
    "taskId": "task_hygiene",
    "title": "Discount at the threshold",
    "intent": "Apply the configured discount at or above the threshold.",
    "acceptanceCriteria": [
        {"criterionId": "AC-1", "text": "apply_discount(100, 100, 0.1) returns 90."}
    ],
    "implementation": {
        "mode": "patch",
        "patches": [
            {
                "path": "src/sample/pricing.py",
                "operation": "replace",
                "content": "def apply_discount(subtotal: float, threshold: float, rate: float) "
                "-> float:\n    return subtotal * (1 - rate) if subtotal >= threshold else "
                "subtotal\n",
            }
        ],
    },
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *GIT_ISOLATION, *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    ).stdout.strip()


def configure(root: Path, **sections: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(workspace: Path, tmp_path: Path, **kwargs: Any) -> tuple[HarnessApplication, Any]:
    source = tmp_path / "task.yaml"
    value = {**TASK, **kwargs.pop("task", {})}
    source.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, value["taskId"], **kwargs)


def approve(application: HarnessApplication, workspace: Path, run: str, **extra: Any) -> Any:
    digest = application.status(workspace, run)["execution"]["changeSetDigest"]
    return application.decide_gate(
        workspace,
        execution_id=run,
        decision=extra.pop("decision", DecisionKind.APPROVE),
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="reviewed",
        **extra,
    )


# ----- environment preflight (item 10) ------------------------------------------------------
def test_a_missing_variable_or_tool_stops_discovery(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HYGIENE_TOKEN", raising=False)
    configure(
        python_workspace,
        environment={
            "variables": ["HYGIENE_TOKEN"],
            "tools": [
                {"name": "python", "command": ["python", "--version"], "pattern": "Python 3"}
            ],
        },
    )
    application, execution = start(python_workspace, tmp_path)
    assert execution.current_phase is PhaseId.DISCOVERY
    assert execution.status is ResultStatus.BLOCKED
    phases = application.status(python_workspace, execution.execution_id)["phases"]
    assert "HYGIENE_TOKEN is not set" in phases[-1]["summary"]
    monkeypatch.setenv("HYGIENE_TOKEN", "value-for-test")
    resumed = application.continue_run(python_workspace, execution.execution_id)
    assert resumed.current_phase is PhaseId.DECISION
    configure(
        python_workspace,
        environment={
            "tools": [
                {"name": "python", "command": ["python", "--version"], "pattern": "^Python 2"}
            ]
        },
    )
    _application, other = start(python_workspace, tmp_path, task={"taskId": "task_tool"})
    assert other.current_phase is PhaseId.DISCOVERY and other.status is ResultStatus.BLOCKED


@POSIX
def test_required_hooks_and_the_dirty_tree(python_workspace: Path, tmp_path: Path) -> None:
    configure(
        python_workspace,
        environment={
            "gitHooks": {"required": ["pre-commit"], "install": ["sh", "install-hooks.sh"]},
            "dirtyTree": "block",
        },
    )
    application, execution = start(python_workspace, tmp_path)
    reason = application.status(python_workspace, execution.execution_id)["phases"][-1]["summary"]
    assert "Git hook pre-commit is missing; install it with: sh install-hooks.sh" in reason
    hook = python_workspace / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    (python_workspace / "notes.txt").write_text("unrelated\n", encoding="utf-8")
    blocked = application.continue_run(python_workspace, execution.execution_id)
    assert blocked.status is ResultStatus.BLOCKED
    reason = application.status(python_workspace, execution.execution_id)["phases"][-1]["summary"]
    assert "uncommitted change(s): notes.txt" in reason
    (python_workspace / "notes.txt").unlink()
    (python_workspace / "task.yaml").unlink(missing_ok=True)
    resumed = application.continue_run(python_workspace, execution.execution_id)
    assert resumed.current_phase is PhaseId.DECISION
    doctor = application.doctor(python_workspace)
    assert doctor["checks"]["environment"]["gitHooks"] == [
        {"name": "pre-commit", "present": True, "executable": True}
    ]


def test_the_baseline_is_run_before_the_change(python_workspace: Path, tmp_path: Path) -> None:
    broken = python_workspace / "tests" / "test_legacy.py"
    broken.write_text("def test_legacy() -> None:\n    assert 1 + 1 == 3\n", encoding="utf-8")
    git(python_workspace, "add", ".")
    git(python_workspace, "commit", "-qm", "legacy")
    configure(python_workspace, environment={"baseline": "require"})
    application, execution = start(python_workspace, tmp_path)
    assert execution.current_phase is PhaseId.DISCOVERY
    reason = application.status(python_workspace, execution.execution_id)["phases"][-1]["summary"]
    assert "the baseline does not pass: python.pytest FAILED" in reason
    configure(
        python_workspace,
        environment={"baseline": "report"},
        verification={"requirementTraceability": "off", "differential": True},
    )
    _application, reported = start(python_workspace, tmp_path, task={"taskId": "task_report"})
    assert reported.current_phase is PhaseId.DECISION
    rules = {
        item.rule_id for item in application.list_findings(python_workspace, reported.execution_id)
    }
    assert "environment.baseline-failing" in rules
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        baseline_runs = [
            event
            for event in services.events.list(reported.execution_id)
            if event.event_type == "verification.baseline.completed"
        ]
    finally:
        services.close()
    # The comparison with the baseline reused the result DISCOVERY recorded.
    assert baseline_runs == []


# ----- run registry outside the workspace (item 12) ------------------------------------------
def test_the_registry_lives_outside_the_workspace(python_workspace: Path, tmp_path: Path) -> None:
    state = tmp_path / "registry"
    configure(python_workspace, runtime={"stateDir": str(state)})
    application, execution = start(python_workspace, tmp_path)
    assert execution.current_phase is PhaseId.DECISION
    assert not (python_workspace / ".harness" / "state.db").exists()
    databases = list(state.glob("*/state.db"))
    assert len(databases) == 1 and (databases[0].parent / "registry.json").is_file()
    assert local_database(python_workspace) == databases[0]
    services = EngineServices.open(ConfigurationResolver().resolve(python_workspace))
    try:
        assert services.paths.state_root == databases[0].parent
        assert databases[0].parent in RunEngine(services)._protected_paths()
    finally:
        services.close()
    settings = application.validate_config(python_workspace)["ladder"]["state"]
    assert settings == {
        "stateDir": str(state),
        "database": str(databases[0]),
        "external": True,
    }


def test_the_registry_lists_the_runs_of_several_repositories(
    python_workspace: Path, node_workspace: Path, tmp_path: Path
) -> None:
    for workspace in (python_workspace, node_workspace):
        configure(workspace, runtime={"stateDir": "auto"})
    start(python_workspace, tmp_path)
    registry = HarnessApplication().registry()
    projects = {item["projectId"]: item for item in registry["projects"]}
    assert set(projects) == {"project_python_project"}
    assert projects["project_python_project"]["runs"][0]["currentPhase"] == "DECISION"
    from fastapi.testclient import TestClient

    from governed_harness.api import create_app

    client = TestClient(create_app(python_workspace), base_url="http://localhost")
    assert (
        client.get("/api/registry").json()["projects"][0]["projectId"] == "project_python_project"
    )


# ----- worktree isolation (item 9) -----------------------------------------------------------
def test_an_isolated_run_works_in_its_own_worktree(python_workspace: Path, tmp_path: Path) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=GIT_ENV)
    git(python_workspace, "remote", "add", "origin", str(remote))
    branch = git(python_workspace, "rev-parse", "--abbrev-ref", "HEAD")
    git(python_workspace, "push", "-q", "origin", branch)
    application, execution = start(python_workspace, tmp_path, isolate="worktree")
    run = execution.execution_id
    worktree = Path(execution.workspace)
    assert worktree.parent == python_workspace / ".harness" / "worktrees"
    assert execution.current_phase is PhaseId.DECISION
    assert "return subtotal\n" in (python_workspace / "src" / "sample" / "pricing.py").read_text()
    assert "1 - rate" in (worktree / "src" / "sample" / "pricing.py").read_text()
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"harness/task_hygiene-{run}"
    events = application.status(python_workspace, run)
    assert events["execution"]["workspace"] == str(worktree)
    record, closed = approve(application, python_workspace, run)
    assert closed.status is ResultStatus.PASSED
    (worktree / "scratch.txt").write_text("keep me\n", encoding="utf-8")
    with pytest.raises(PolicyViolationError, match="uncommitted"):
        application.cleanup_run(python_workspace, run)
    (worktree / "scratch.txt").unlink()
    removed = application.cleanup_run(python_workspace, run)
    assert removed["status"] == "REMOVED" and not worktree.exists()
    assert git(python_workspace, "branch", "--list", f"harness/task_hygiene-{run}")


def test_a_collision_blocks_and_nothing_is_reset(python_workspace: Path, tmp_path: Path) -> None:
    configure(
        python_workspace, workspace={"isolation": {"mode": "worktree", "branch": "work/{taskId}"}}
    )
    git(python_workspace, "branch", "work/task_hygiene")
    application, execution = start(python_workspace, tmp_path)
    assert execution.status is ResultStatus.BLOCKED
    assert "branch work/task_hygiene already exists" in (execution.terminal_reason or "")
    assert git(python_workspace, "branch", "--list", "work/task_hygiene")
    git(python_workspace, "branch", "-D", "work/task_hygiene")
    resumed = application.continue_run(python_workspace, execution.execution_id)
    assert resumed.current_phase is PhaseId.DECISION
    assert Path(resumed.workspace).name == execution.execution_id


# ----- complete delivery (item 13) -----------------------------------------------------------
class FakeForge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, body))
        if method == "GET":
            return []
        if path.endswith("/pulls"):
            return {"number": 7, "html_url": "https://forge.invalid/pull/7"}
        return {"id": 1}


def test_an_unauthorised_push_leaves_the_change_staged(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace, delivery={"closureCommit": "branch", "stage": True, "push": False})
    application, execution = start(python_workspace, tmp_path)
    approve(application, python_workspace, execution.execution_id)
    staged = git(python_workspace, "diff", "--cached", "--name-only").splitlines()
    assert staged == ["src/sample/pricing.py"]


@POSIX
def test_push_pull_request_and_comment_follow_the_contract(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=GIT_ENV)
    git(python_workspace, "remote", "add", "origin", str(remote))
    (python_workspace / ".github").mkdir()
    (python_workspace / ".github" / "pull_request_template.md").write_text("## Why\n")
    git(python_workspace, "add", ".")
    git(python_workspace, "commit", "-qm", "template")
    forge = FakeForge()
    monkeypatch.setitem(ladder_delivery.TRANSPORT, "override", forge)
    configure(
        python_workspace,
        delivery={
            "closureCommit": "branch",
            "pullRequest": {"create": True, "draft": True},
            "comment": "notClean",
            # The forge layer of #56: the remote is a local path, so the forge is named.
            "forge": {
                "kind": "github",
                "repository": "owner/name",
                "baseBranch": "main",
                "labels": ["governed"],
            },
        },
    )
    application, execution = start(
        python_workspace, tmp_path, task={"contract": {"push": True, "branch": "feature/discount"}}
    )
    approve(
        application,
        python_workspace,
        execution.execution_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
    )
    assert git(python_workspace, "ls-remote", "--heads", str(remote), "feature/discount")
    posted = [(method, path) for method, path, _ in forge.calls if method == "POST"]
    assert ("POST", "repos/owner/name/pulls") in posted
    assert ("POST", "repos/owner/name/issues/7/labels") in posted
    assert ("POST", "repos/owner/name/issues/7/comments") in posted
    pull = next(body for method, path, body in forge.calls if path == "repos/owner/name/pulls")
    assert pull["head"] == "feature/discount" and pull["base"] == "main"
    assert pull["body"].startswith("## Why\n") and pull["draft"] is True


@POSIX
def test_a_hook_that_refuses_the_push_stops_closure(python_workspace: Path, tmp_path: Path) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=GIT_ENV)
    git(python_workspace, "remote", "add", "origin", str(remote))
    hook = python_workspace / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho 'pre-push says no' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    configure(python_workspace, delivery={"closureCommit": "branch", "push": True})
    application, execution = start(python_workspace, tmp_path)
    _record, closed = approve(application, python_workspace, execution.execution_id)
    assert closed.current_phase is PhaseId.CLOSURE and closed.status is ResultStatus.BLOCKED
    reason = application.status(python_workspace, execution.execution_id)["phases"][-1]["summary"]
    assert "Push of harness/" in reason and "pre-push says no" in reason
    assert not git(python_workspace, "ls-remote", "--heads", str(remote))


# ----- harness config lint (item 14) ---------------------------------------------------------
def test_config_lint_reports_contradictions(python_workspace: Path) -> None:
    configure(
        python_workspace,
        instructions={"files": ["AGENTS.md", "CLAUDE.md"], "precedence": ["harness", "AGENTS.md"]},
        verification={"requirementTraceability": "off", "testQuality": {"diffCoverage": 80}},
    )
    runner = CliRunner()
    clean = runner.invoke(app, ["config", "lint", "--path", str(python_workspace), "--json"])
    assert clean.exit_code == 0, clean.output
    (python_workspace / "AGENTS.md").write_text("Keep coverage above 95%.\n", encoding="utf-8")
    (python_workspace / "CLAUDE.md").write_text("Use git push --force to sync.\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "lint", "--path", str(python_workspace), "--json"])
    assert result.exit_code == 6
    report = json.loads(result.stdout)
    kinds = sorted(item["kind"] for item in report["issues"])
    assert kinds == ["coverage", "forbidden-flag"]
    coverage = next(item for item in report["issues"] if item["kind"] == "coverage")
    assert coverage["winner"] == "harness"
    assert os.path.basename(report["files"][0]) == "AGENTS.md"
