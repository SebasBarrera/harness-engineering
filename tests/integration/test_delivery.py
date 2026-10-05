"""Approval valid for what gets merged: the closure commit with trailers, verify-approval, the
portable evidence bundle and the pull-request publisher (with a fake transport: no network)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration.models import PublisherConfig
from governed_harness.delivery.publisher import GitHubPublisher, marker, transport_for
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import ConfigurationError

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
    "    - path: tests/test_pricing.py\n"
    "      operation: append\n"
    "      content: |\n"
    "\n"
    "        def test_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """The closure commit honours the repository's Git configuration; the tests must not
    inherit the developer's global one (signing, hooks)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def configure(root: Path, **delivery: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["delivery"] = delivery
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def run_to_decision(root: Path) -> tuple[HarnessApplication, str, str]:
    task_file = root.parent / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(root, task_file)
    run = application.start_run(root, task.task_id)
    assert run.current_phase.value == "DECISION"
    digest = application.status(root, run.execution_id)["execution"]["changeSetDigest"]
    return application, run.execution_id, digest


def approve(application: HarnessApplication, root: Path, run: str, digest: str) -> Any:
    _, execution = application.decide_gate(
        root,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="Checked the discount rule",
    )
    return execution


def test_branch_closure_commit_carries_trailers_and_verifies(python_workspace: Path) -> None:
    configure(python_workspace, closureCommit="branch")
    head_before = git(python_workspace, "rev-parse", "HEAD")
    application, run, digest = run_to_decision(python_workspace)
    execution = approve(application, python_workspace, run, digest)
    assert execution.status.value == "PASSED"
    branch = f"harness/{run}"
    commit = git(python_workspace, "rev-parse", f"refs/heads/{branch}")
    message = git(python_workspace, "log", "-1", "--format=%B", commit)
    assert f"Harness-Run: {run}" in message
    assert f"Harness-ChangeSet: {digest}" in message
    assert "Harness-Decision: decision_" in message and " APPROVE" in message
    assert "Harness-Exception" not in message
    # The current branch, the index and the working tree are untouched.
    assert git(python_workspace, "rev-parse", "HEAD") == head_before
    assert git(python_workspace, "rev-parse", f"{commit}~1") == head_before
    assert "src/sample/pricing.py" in git(python_workspace, "status", "--porcelain")
    files = git(python_workspace, "show", "--name-only", "--format=", commit).splitlines()
    assert sorted(files) == ["src/sample/pricing.py", "tests/test_pricing.py"]
    report = HarnessApplication.verify_approval(python_workspace, base=head_before, head=branch)
    assert report["status"] == "PASSED", report["reasons"]
    assert report["computedDigest"] == digest
    assert report["trailers"][0]["Harness-Run"] == run
    events = [item.event_type for item in _events(application, python_workspace, run)]
    assert "delivery.commit.created" in events


def _events(application: HarnessApplication, root: Path, run: str) -> list[Any]:
    with application._services(root) as services:
        return services.events.list(run)


def test_a_later_commit_breaks_the_approval(python_workspace: Path) -> None:
    configure(python_workspace, closureCommit="head")
    base = git(python_workspace, "rev-parse", "HEAD")
    application, run, digest = run_to_decision(python_workspace)
    approve(application, python_workspace, run, digest)
    closure = git(python_workspace, "rev-parse", "HEAD")
    assert closure != base
    # head mode refreshes the index: the ChangeSet paths are committed and clean.
    status = git(python_workspace, "status", "--porcelain", "-uno", "--", "src", "tests")
    assert status == ""
    assert HarnessApplication.verify_approval(python_workspace, base=base)["status"] == "PASSED"
    (python_workspace / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return 0\n",
        encoding="utf-8",
    )
    git(python_workspace, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qam", "later")
    report = HarnessApplication.verify_approval(python_workspace, base=base)
    assert report["status"] == "FAILED"
    assert report["computedDigest"] != digest
    assert any("no valid, unexpired" in item for item in report["reasons"])


def test_closure_blocks_on_a_dirty_baseline(python_workspace: Path) -> None:
    configure(python_workspace, closureCommit="branch")
    pricing = python_workspace / "src" / "sample" / "pricing.py"
    pricing.write_text(pricing.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8")
    application, run, digest = run_to_decision(python_workspace)
    execution = approve(application, python_workspace, run, digest)
    assert execution.status.value == "BLOCKED"
    assert execution.current_phase.value == "CLOSURE"
    assert (
        "does not hold the baseline of src/sample/pricing.py"
        in (application.status(python_workspace, run)["phases"][-1]["summary"])
    )
    assert git(python_workspace, "branch", "--list", f"harness/{run}") == ""


def test_closure_commit_without_a_git_identity(python_workspace: Path) -> None:
    git(python_workspace, "config", "--unset", "user.name")
    git(python_workspace, "config", "--unset", "user.email")
    configure(python_workspace, closureCommit="branch", branch="review/{taskId}")
    application, run, digest = run_to_decision(python_workspace)
    assert approve(application, python_workspace, run, digest).status.value == "PASSED"
    branches = git(python_workspace, "branch", "--list", "review/*")
    assert branches.strip().startswith("review/task_")
    author = git(python_workspace, "log", "-1", "--format=%an <%ae>", branches.strip())
    assert author == "Governed Agent Harness <harness@localhost.invalid>"


def test_no_delivery_key_never_commits(python_workspace: Path) -> None:
    head = git(python_workspace, "rev-parse", "HEAD")
    application, run, digest = run_to_decision(python_workspace)
    approve(application, python_workspace, run, digest)
    assert git(python_workspace, "rev-parse", "HEAD") == head
    assert git(python_workspace, "branch", "--list", "harness/*") == ""


def test_bundle_round_trip_and_tampering(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, closureCommit="branch")
    base = git(python_workspace, "rev-parse", "HEAD")
    application, run, digest = run_to_decision(python_workspace)
    approve(application, python_workspace, run, digest)
    bundle = tmp_path / "run.tar.gz"
    exported = application.export_bundle(python_workspace, run, bundle)
    assert exported["missingArtifacts"] == []
    report = HarnessApplication.verify_bundle(bundle)
    assert report["valid"], report["problems"]
    assert report["changeSetDigest"] == digest
    assert [item["decision"] for item in report["decisions"]] == ["APPROVE"]
    # CI: the workspace record is not there, only the bundle.
    approval = HarnessApplication.verify_approval(
        python_workspace, base=base, head=f"harness/{run}", bundles=(bundle,), use_workspace=False
    )
    assert approval["status"] == "PASSED"
    assert approval["matchedApproval"]["source"] == f"bundle:{bundle}"
    without = HarnessApplication.verify_approval(
        python_workspace, base=base, head=f"harness/{run}", use_workspace=False
    )
    assert without["status"] == "FAILED"

    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(bundle, "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
        for member in source:
            data = source.extractfile(member).read()  # type: ignore[union-attr]
            if member.name == "events.jsonl":
                data = data.replace(b"Checked the discount rule", b"Forged rationale here!!")
            info = tarfile.TarInfo(member.name)
            info.size = len(data)
            target.addfile(info, io.BytesIO(data))
    broken = HarnessApplication.verify_bundle(tampered)
    assert not broken["valid"]
    assert any("events.jsonl does not match" in item for item in broken["problems"])
    assert any("event digest mismatch" in item for item in broken["problems"])
    rejected = HarnessApplication.verify_approval(
        python_workspace,
        base=base,
        head=f"harness/{run}",
        bundles=(tampered,),
        use_workspace=False,
    )
    assert rejected["status"] == "FAILED"


class FakeTransport:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.existing = existing or []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, body))
        if method == "GET" and "/comments" in path:
            return self.existing if path.endswith("page=1") else []
        if method == "GET" and "/pulls/" in path:
            return {"head": {"sha": "abc123"}}
        if method in {"POST", "PATCH"} and "comments" in path:
            return {"id": 7, "html_url": "https://github.example/pr/1#c7"}
        if "code-scanning" in path:
            return {"id": "sarif-1"}
        return None


def test_publisher_posts_then_updates_one_comment(python_workspace: Path) -> None:
    application, run, digest = run_to_decision(python_workspace)
    transport = FakeTransport()
    report = application.publish_pull_request(
        python_workspace,
        run,
        pull_request=12,
        repository="owner/repo",
        transport_override=transport,
    )
    assert report["comment"]["action"] == "created"
    assert report["sarif"]["status"] == "UPLOADED"
    method, path, body = transport.calls[1]
    assert (method, path) == ("POST", "repos/owner/repo/issues/12/comments")
    assert marker(run) in body["body"]
    assert digest in body["body"]
    assert "| File | Status | + | - |" in body["body"]
    upload = [call for call in transport.calls if "code-scanning" in call[1]][0]
    assert upload[2]["commit_sha"] == "abc123"
    assert upload[2]["ref"] == "refs/pull/12/head"

    again = FakeTransport(existing=[{"id": 99, "body": f"{marker(run)}\nold"}])
    report = application.publish_pull_request(
        python_workspace,
        run,
        pull_request=12,
        repository="owner/repo",
        sarif=False,
        transport_override=again,
    )
    assert report["comment"]["action"] == "updated"
    assert again.calls[1][:2] == ("PATCH", "repos/owner/repo/issues/comments/99")
    assert report["sarif"]["status"] == "SKIPPED"


def test_publisher_needs_a_repository(python_workspace: Path) -> None:
    application, run, _ = run_to_decision(python_workspace)
    with pytest.raises(ConfigurationError, match="no repository"):
        application.publish_pull_request(
            python_workspace, run, pull_request=1, transport_override=FakeTransport()
        )


def test_api_transport_reads_the_token_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HARNESS_TEST_TOKEN", raising=False)
    config = PublisherConfig(transport="api", tokenEnv="HARNESS_TEST_TOKEN")
    with pytest.raises(ConfigurationError, match="HARNESS_TEST_TOKEN"):
        transport_for(config)
    monkeypatch.setenv("HARNESS_TEST_TOKEN", "t0ken")
    transport = transport_for(config)
    assert transport.token == "t0ken"
    assert GitHubPublisher(transport, "o/r").repository == "o/r"
    assert json.dumps({"ok": True})
