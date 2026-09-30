from __future__ import annotations

from pathlib import Path

from governed_harness.configuration.models import ValidatorDefinition
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import (
    AcceptanceCriterion,
    Actor,
    ChangedFile,
    ChangeSet,
    Provenance,
    Task,
)
from governed_harness.evidence import LocalArtifactStore
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.validators import IndependentReviewValidator, ValidationContext


def context(tmp_path: Path, diff: bytes) -> ValidationContext:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    ref = artifacts.put(diff, media_type="text/x-diff", redact=False)
    changeset = ChangeSet(
        change_set_id="changeset_001",
        execution_id="run_001",
        files=(ChangedFile(path="src/a.py", status="MODIFIED", additions=1),),
        diff_ref=ref.uri,
        digest="sha256:" + "a" * 64,
    )
    task = Task(
        task_id="task_001",
        project_id="project_001",
        title="x",
        intent="y",
        acceptance_criteria=(AcceptanceCriterion(criterion_id="ac_001", text="works"),),
    )
    actor = Actor(actor_type=ActorType.TOOL, actor_id="validator.independent-review")
    return ValidationContext(
        execution_id="run_001",
        workspace=tmp_path,
        task=task,
        change_set=changeset,
        definition=ValidatorDefinition(id="review.independent", mandatory=True),
        grants=[],
        artifact_store=artifacts,
        process_runner=SafeProcessRunner(tmp_path),
        provenance=Provenance(actor=actor, core_version="test"),
        cancellation=CancellationToken(),
        max_output_bytes=10000,
    )


def test_review_detects_possible_secret(tmp_path: Path) -> None:
    output = IndependentReviewValidator().execute(
        context(
            tmp_path, b'--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n+password = "supersecret"\n'
        )
    )
    assert any(finding.rule_id == "review.possible-secret" for finding in output.findings)


def test_review_detects_source_without_tests(tmp_path: Path) -> None:
    output = IndependentReviewValidator().execute(
        context(tmp_path, b"--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n+x = 2\n")
    )
    assert any(
        finding.rule_id == "review.source-without-test-change" for finding in output.findings
    )
