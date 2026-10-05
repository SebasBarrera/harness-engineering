"""Who may record a human decision (#45): agent, validator and harness actor ids are refused in
every human act, the decider defaults to the Git user under ``governance.deciderIdentity: git``
and an interactive decision can require the ChangeSet digest to be typed."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ProjectConfiguration
from governed_harness.domain.actors import actor_id_from_identity, is_non_human_actor_id
from governed_harness.domain.enums import DecisionKind, MemoryLevel
from governed_harness.domain.errors import NonHumanActorError, PolicyViolationError
from governed_harness.domain.models import HumanDecision

# The package re-exports the main() function under the name of its module.
cli = importlib.import_module("governed_harness.cli.main")

TASK = (
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
    "implementation:\n  mode: patch\n  patches:\n"
    "    - path: src/sample/pricing.py\n      operation: replace\n      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)


def _pending_run(workspace: Path, tmp_path: Path) -> tuple[str, str]:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_file)
    execution = application.start_run(workspace, task.task_id)
    assert execution.current_phase.value == "DECISION"
    assert execution.change_set_digest
    return execution.execution_id, execution.change_set_digest


def _decide(workspace: Path, run: str, digest: str, *extra: str) -> object:
    return CliRunner().invoke(
        cli.app,
        [
            "gate",
            "decide",
            "--run",
            run,
            "--decision",
            "APPROVE_EXCEPTION",
            "--change-set-digest",
            digest,
            "--rationale",
            "reviewed",
            "--path",
            str(workspace),
            *extra,
        ],
    )


def _drop_governance(workspace: Path) -> None:
    config = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    value.pop("governance", None)
    config.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("actor", ["agent.claude-code", "validator.python.pytest", "harness.core"])
def test_gate_decide_refuses_non_human_actor_ids(
    python_workspace: Path, tmp_path: Path, actor: str
) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    result = _decide(python_workspace, run, digest, "--actor", actor)
    assert result.exit_code == 5
    assert "only a person" in result.stderr
    status = HarnessApplication().status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "DECISION"
    assert status["humanDecision"] is None
    assert status["execution"]["status"] != "PASSED"


def test_actor_namespaces_are_refused_without_governance_settings(
    python_workspace: Path, tmp_path: Path
) -> None:
    """The namespace check is a defect fix: it applies to a 1.0.0 project.yaml as well."""
    _drop_governance(python_workspace)
    run, digest = _pending_run(python_workspace, tmp_path)
    with pytest.raises(NonHumanActorError):
        HarnessApplication().decide_gate(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE_EXCEPTION,
            change_set_digest=digest,
            actor_id="agent.claude-code",
            rationale="self-approval",
        )


def test_other_human_acts_refuse_non_human_actor_ids(python_workspace: Path) -> None:
    application = HarnessApplication()
    with pytest.raises(PolicyViolationError):
        application.add_memory(
            python_workspace,
            level=MemoryLevel.PROJECT,
            key="style",
            value={"text": "x"},
            actor_id="agent.claude-code",
            approved=True,
        )
    proposal = application.add_memory(
        python_workspace,
        level=MemoryLevel.PROJECT,
        key="style",
        value={"text": "x"},
        actor_id="agent.claude-code",
    )
    with pytest.raises(PolicyViolationError):
        application.approve_memory(
            python_workspace, memory_id=proposal.memory_id, actor_id="validator.review"
        )
    with pytest.raises(PolicyViolationError):
        application.invalidate_memory(
            python_workspace, memory_id=proposal.memory_id, actor_id="harness", reason="no"
        )


def test_decider_defaults_to_the_git_user(python_workspace: Path, tmp_path: Path) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    result = _decide(python_workspace, run, digest)
    assert result.exit_code == 0, result.stderr
    actor = json.loads(result.stdout)["decision"]["actor"]
    # tests/conftest.py configures the fixture repository as Fixture <fixture@example.com>.
    assert actor == {
        "actorType": "HUMAN",
        "actorId": "fixture-example.com",
        "displayName": "Fixture <fixture@example.com>",
        "version": None,
    }
    assert json.loads(result.stdout)["decision"]["identitySource"] == "git"
    assert "warning" not in result.stderr


def test_explicit_actor_is_recorded_as_explicit(python_workspace: Path, tmp_path: Path) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    result = _decide(python_workspace, run, digest, "--actor", "human.reviewer")
    assert result.exit_code == 0, result.stderr
    decision = json.loads(result.stdout)["decision"]
    assert decision["actor"]["actorId"] == "human.reviewer"
    assert decision["identitySource"] == "explicit"


def test_git_without_identity_falls_back_with_a_warning(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine without a Git identity (a CI runner) still decides: the 1.0.0 default actor
    is recorded with identitySource fallback and a warning says how to fix it."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("user.email", "user.name"):
        subprocess.run(["git", "config", "--unset", key], cwd=python_workspace, check=True)
    run, digest = _pending_run(python_workspace, tmp_path)
    result = _decide(python_workspace, run, digest)
    assert result.exit_code == 0, result.stderr
    decision = json.loads(result.stdout)["decision"]
    assert decision["actor"]["actorId"] == "human.local"
    assert decision["identitySource"] == "fallback"
    assert "warning: governance.deciderIdentity is git but Git has no usable" in result.stderr


def test_decider_without_the_setting_keeps_human_local(
    python_workspace: Path, tmp_path: Path
) -> None:
    _drop_governance(python_workspace)
    run, digest = _pending_run(python_workspace, tmp_path)
    result = _decide(python_workspace, run, digest)
    assert result.exit_code == 0, result.stderr
    decision = json.loads(result.stdout)["decision"]
    assert decision["actor"]["actorId"] == "human.local"
    assert "identitySource" not in decision


def test_interactive_decision_requires_the_digest(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    wrong = CliRunner().invoke(
        cli.app,
        [
            "gate",
            "decide",
            "--run",
            run,
            "--decision",
            "APPROVE_EXCEPTION",
            "--change-set-digest",
            digest,
            "--rationale",
            "reviewed",
            "--path",
            str(python_workspace),
        ],
        input="000000000000\n",
    )
    assert wrong.exit_code == 5
    assert "not confirmed" in wrong.stderr
    assert "src/sample/pricing.py" in wrong.stderr
    assert HarnessApplication().status(python_workspace, run)["humanDecision"] is None
    prefix = digest.removeprefix("sha256:")[: cli.CONFIRM_PREFIX_CHARS]
    right = CliRunner().invoke(
        cli.app,
        [
            "gate",
            "decide",
            "--run",
            run,
            "--decision",
            "APPROVE_EXCEPTION",
            "--change-set-digest",
            digest,
            "--rationale",
            "reviewed",
            "--path",
            str(python_workspace),
        ],
        input=f"{prefix}\n",
    )
    assert right.exit_code == 0, right.stderr
    # The test runner echoes the typed input on standard output before the JSON.
    output = right.stdout[right.stdout.index("{") :]
    decision = HumanDecision.model_validate(json.loads(output)["decision"])
    assert decision.change_set_digest == digest


def test_absent_governance_section_is_not_serialized() -> None:
    project = ProjectConfiguration.model_validate(
        {"configVersion": "1.0", "projectId": "p", "workspace": {"root": ".."}}
    )
    assert "governance" not in project.model_dump(mode="json", by_alias=True)
    configured = ProjectConfiguration.model_validate(
        {
            "configVersion": "1.0",
            "projectId": "p",
            "workspace": {"root": ".."},
            "governance": {"deciderIdentity": "git"},
        }
    )
    assert configured.model_dump(mode="json", by_alias=True)["governance"] == {
        "deciderIdentity": "git"
    }


def test_actor_ids_from_git_identities() -> None:
    assert actor_id_from_identity("Ana", "Ana.Perez@Example.org") == "ana.perez-example.org"
    assert actor_id_from_identity("Ana Perez", None) == "ana-perez"
    assert is_non_human_actor_id("Agent.X")
    assert not is_non_human_actor_id("human.agent")
