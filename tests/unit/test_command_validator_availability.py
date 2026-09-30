"""Availability of `python -m <module>` validators is probed with the target interpreter (#1)."""

from __future__ import annotations

import sys
from pathlib import Path

from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.models import CapabilityRule, ValidatorDefinition
from governed_harness.domain.enums import ActorType, ResultStatus, ValidationKind
from governed_harness.domain.models import (
    AcceptanceCriterion,
    Actor,
    ChangeSet,
    Provenance,
    Task,
)
from governed_harness.evidence import LocalArtifactStore
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.validators import CommandValidator, ValidationContext

MISSING_MODULE = "governed_harness_test_module_that_does_not_exist"


def run_validator(
    tmp_path: Path, command: tuple[str, ...], *, mandatory: bool
) -> ValidationContext:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    diff = artifacts.put(b"", media_type="text/x-diff", redact=False)
    actor = Actor(actor_type=ActorType.TOOL, actor_id="validator.python.check", version="1")
    return ValidationContext(
        execution_id="run_001",
        workspace=tmp_path,
        task=Task(
            task_id="task_001",
            project_id="project_001",
            title="x",
            intent="y",
            acceptance_criteria=(AcceptanceCriterion(criterion_id="ac_001", text="works"),),
        ),
        change_set=ChangeSet(
            change_set_id="changeset_001",
            execution_id="run_001",
            files=(),
            diff_ref=diff.uri,
            digest="sha256:" + "a" * 64,
        ),
        definition=ValidatorDefinition(id="python.check", command=command, mandatory=mandatory),
        grants=grants_from_rules(
            "run_001",
            actor,
            [CapabilityRule(capability="process.execute", scope=(sys.executable,))],
        ),
        artifact_store=artifacts,
        process_runner=SafeProcessRunner(tmp_path),
        provenance=Provenance(actor=actor, core_version="test"),
        cancellation=CancellationToken(),
        max_output_bytes=10000,
    )


def test_mandatory_validator_with_missing_module_is_blocked(tmp_path: Path) -> None:
    context = run_validator(tmp_path, (sys.executable, "-m", MISSING_MODULE), mandatory=True)
    result = CommandValidator("python.check").execute(context).result
    assert result.status is ResultStatus.BLOCKED
    assert result.kind is ValidationKind.CONFIGURATION_ERROR
    assert MISSING_MODULE in result.summary


def test_optional_validator_with_missing_module_is_not_applicable(tmp_path: Path) -> None:
    context = run_validator(tmp_path, (sys.executable, "-m", MISSING_MODULE), mandatory=False)
    result = CommandValidator("python.check").execute(context).result
    assert result.status is ResultStatus.NOT_APPLICABLE


def test_validator_with_available_module_runs(tmp_path: Path) -> None:
    context = run_validator(tmp_path, (sys.executable, "-m", "json.tool", "--help"), mandatory=True)
    result = CommandValidator("python.check").execute(context).result
    assert result.status is ResultStatus.PASSED
