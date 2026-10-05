"""Forge providers (#56): detection from the remote, the requests of each provider (through a
recording fake transport: no network), the Code Quality report and the token handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governed_harness.configuration.engineering import ForgeConfig
from governed_harness.delivery.publisher import marker
from governed_harness.domain.enums import FindingSeverity
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import Actor, Finding, FindingLocation, Provenance
from governed_harness.forges import (
    auth_headers,
    build_transport,
    code_quality_issues,
    forge_for,
    parse_remote,
    pull_request_body,
    read_template,
    status_for,
)

SSH_GITLAB = "git" + "@gitlab.example.com:group/sub/app.git"
SSH_AZURE = "git" + "@ssh.dev.azure.com:v3/org/proj/repo"


@pytest.mark.parametrize(
    ("url", "kind", "repository", "api"),
    [
        ("https://github.com/o/r.git", "github", "o/r", "https://api.github.com"),
        ("https://gitlab.com/g/sg/p.git", "gitlab", "g/sg/p", "https://gitlab.com/api/v4"),
        (SSH_GITLAB, "gitlab", "group/sub/app", "https://gitlab.example.com/api/v4"),
        (
            "https://bitbucket.org/ws/repo.git",
            "bitbucket",
            "ws/repo",
            "https://api.bitbucket.org/2.0",
        ),
        (
            "https://org@dev.azure.com/org/proj/_git/repo",
            "azure-devops",
            "org/proj/repo",
            "https://dev.azure.com/org",
        ),
        (SSH_AZURE, "azure-devops", "org/proj/repo", "https://dev.azure.com/org"),
        (
            "https://org.visualstudio.com/proj/_git/repo",
            "azure-devops",
            "org/proj/repo",
            "https://org.visualstudio.com",
        ),
        ("https://codeberg.org/o/r.git", "gitea", "o/r", "https://codeberg.org/api/v1"),
    ],
)
def test_remote_detection(url: str, kind: str, repository: str, api: str) -> None:
    location = parse_remote(url)
    assert location is not None
    assert (location.kind, location.repository, location.api_url) == (kind, repository, api)


def test_unknown_host_needs_the_kind() -> None:
    assert parse_remote("https://code.example.com/team/app.git") is None
    forced = parse_remote("https://code.example.com/team/app.git", "gitea")
    assert forced is not None and forced.api_url == "https://code.example.com/api/v1"


class Recorder:
    """A transport that records requests and answers from a table of (method, fragment)."""

    def __init__(self, answers: dict[tuple[str, str], Any] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.answers = answers or {}

    def request(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        for (verb, fragment), answer in self.answers.items():
            if verb == method and fragment in path:
                return answer
        return {}


def test_gitlab_merge_request_notes_status_and_creation() -> None:
    run = "run_1"
    transport = Recorder(
        {
            ("GET", "notes?per_page=100&page=1"): [{"id": 5, "body": f"{marker(run)}\nold"}],
            ("POST", "merge_requests"): {"iid": 9, "web_url": "https://gitlab.example/mr/9"},
        }
    )
    forge = forge_for("gitlab", transport, "group/sub/app")
    comment = forge.upsert_comment(3, run, f"{marker(run)}\nnew")
    assert comment["action"] == "updated"
    assert transport.calls[-1][:2] == (
        "PUT",
        "projects/group%2Fsub%2Fapp/merge_requests/3/notes/5",
    )
    created = forge.create_pull_request(
        head="harness/run_1", base="main", title="T", body="B", labels=("a", "b"), draft=True
    )
    assert created == {"number": 9, "url": "https://gitlab.example/mr/9"}
    method, path, body = transport.calls[-1]
    assert (method, path) == ("POST", "projects/group%2Fsub%2Fapp/merge_requests")
    assert body == {
        "source_branch": "harness/run_1",
        "target_branch": "main",
        "title": "Draft: T",
        "description": "B",
        "labels": "a,b",
    }
    status = forge.set_status(commit_sha="abc", state="failure", description="blocked")
    assert status["state"] == "failed"
    assert transport.calls[-1][1] == "projects/group%2Fsub%2Fapp/statuses/abc"
    assert (
        forge.upload_reports(3, sarif=b"{}", code_quality=b"[]", commit_sha=None)["status"]
        == "SKIPPED"
    )


def test_bitbucket_comments_code_insights_and_status() -> None:
    transport = Recorder({("GET", "pullrequests/4/comments"): {"values": []}})
    forge = forge_for("bitbucket", transport, "ws/repo")
    assert forge.upsert_comment(4, "run_2", "brief")["action"] == "created"
    assert transport.calls[-1] == (
        "POST",
        "repositories/ws/repo/pullrequests/4/comments",
        {"content": {"raw": "brief"}},
    )
    report = json.dumps(
        [
            {
                "description": "bad",
                "fingerprint": "f1",
                "severity": "critical",
                "location": {"path": "a.py", "lines": {"begin": 3}},
            }
        ]
    ).encode()
    uploaded = forge.upload_reports(4, sarif=None, code_quality=report, commit_sha="c0ffee")
    assert uploaded["status"] == "UPLOADED"
    put = [call for call in transport.calls if call[0] == "PUT"][0]
    assert put[1] == "repositories/ws/repo/commit/c0ffee/reports/governed-harness"
    assert put[2]["result"] == "FAILED"
    annotations = transport.calls[-1][2]
    assert annotations[0]["severity"] == "HIGH" and annotations[0]["line"] == 3
    created = forge.create_pull_request(head="h", base="main", title="T", body="B", labels=("x",))
    assert created["labelsIgnored"] == ["x"]
    assert forge.set_status(commit_sha="c", state="success", description="ok")["state"] == (
        "SUCCESSFUL"
    )


def test_azure_threads_pull_request_and_status() -> None:
    transport = Recorder(
        {
            ("GET", "threads"): {
                "value": [{"id": 11, "comments": [{"id": 1, "content": f"{marker('r')}\nx"}]}]
            },
            ("POST", "pullrequests?"): {"pullRequestId": 77, "url": "https://dev.azure/pr/77"},
        }
    )
    forge = forge_for("azure-devops", transport, "org/proj/repo")
    assert forge.upsert_comment(5, "r", "new")["action"] == "updated"
    assert transport.calls[-1][1] == (
        "proj/_apis/git/repositories/repo/pullRequests/5/threads/11/comments/1?api-version=7.1"
    )
    created = forge.create_pull_request(head="h", base="main", title="T", body="B", labels=("l",))
    assert created["number"] == 77
    assert transport.calls[-1][2]["sourceRefName"] == "refs/heads/h"
    assert transport.calls[-1][2]["labels"] == [{"name": "l"}]
    assert forge.set_status(commit_sha="s", state="pending", description="d")["state"] == "pending"


def test_gitea_labels_are_resolved_to_ids() -> None:
    transport = Recorder(
        {
            ("GET", "labels"): [{"id": 3, "name": "harness"}],
            ("POST", "pulls"): {"number": 8, "html_url": "https://gitea.example/pulls/8"},
        }
    )
    forge = forge_for("gitea", transport, "o/r")
    created = forge.create_pull_request(
        head="h", base="main", title="T", body="B", labels=("harness", "missing")
    )
    assert created["labelsUnknown"] == ["missing"]
    assert transport.calls[-1][2]["labels"] == [3]


def test_github_forge_creates_and_labels() -> None:
    transport = Recorder({("POST", "/pulls"): {"number": 4, "html_url": "u"}})
    forge = forge_for("github", transport, "o/r")
    forge.create_pull_request(head="h", base="main", title="T", body="B", labels=("a",))
    assert transport.calls[-1] == ("POST", "repos/o/r/issues/4/labels", {"labels": ["a"]})


def _finding(severity: FindingSeverity, path: str | None) -> Finding:
    actor = Actor(actor_type="TOOL", actor_id="validator.x", version="1")
    return Finding(
        finding_id="finding_1",
        execution_id="run_1",
        validator_id="x",
        rule_id="x.rule",
        category="validation",
        severity=severity,
        message="Problem at line 3",
        location=FindingLocation(path=path, start_line=3, end_line=3) if path else None,
        provenance=Provenance(actor=actor, core_version="1"),
    )


def test_code_quality_severity_and_location() -> None:
    issues = code_quality_issues(
        [_finding(FindingSeverity.HIGH, "src/a.py"), _finding(FindingSeverity.LOW, None)]
    )
    assert issues[0]["severity"] == "critical"
    assert issues[0]["location"] == {"path": "src/a.py", "lines": {"begin": 3}}
    assert issues[1]["location"]["path"] == "."
    assert len(issues[0]["fingerprint"]) == 64


def test_tokens_come_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_FORGE_TEST", raising=False)
    with pytest.raises(ConfigurationError, match="HARNESS_FORGE_TEST"):
        build_transport("gitlab", "api", "https://gitlab.example/api/v4", "HARNESS_FORGE_TEST")
    monkeypatch.setenv("HARNESS_FORGE_TEST", "value-for-test")
    transport = build_transport(
        "gitlab", "api", "https://gitlab.example/api/v4", "HARNESS_FORGE_TEST"
    )
    assert "value-for-test" not in repr(transport)
    assert auth_headers("gitlab", "t") == {"PRIVATE-TOKEN": "t"}
    assert auth_headers("azure-devops", "t")["Authorization"].startswith("Basic ")
    with pytest.raises(ConfigurationError, match="no CLI transport"):
        build_transport("bitbucket", "cli", "https://api.bitbucket.org/2.0")


def test_template_and_status_mapping(tmp_path: Path) -> None:
    (tmp_path / ".gitlab" / "merge_request_templates").mkdir(parents=True)
    (tmp_path / ".gitlab" / "merge_request_templates" / "Default.md").write_text("## Checklist\n")
    text, source = read_template(tmp_path, "gitlab", None)
    assert source == ".gitlab/merge_request_templates/Default.md"
    assert pull_request_body("brief", text).startswith("## Checklist")
    assert read_template(tmp_path, "github", None) == (None, "none")
    with pytest.raises(ConfigurationError, match="template not found"):
        read_template(tmp_path, "github", "missing.md")
    assert status_for("PASSED", "CLOSURE", "PASSED") == "success"
    assert status_for("BLOCKED", "DECISION", "PASSED") == "pending"
    assert status_for("FAILED", "VERIFICATION", None) == "failure"


def test_forge_configuration_validation() -> None:
    with pytest.raises(ValueError, match="https"):
        ForgeConfig.model_validate({"apiUrl": "http://gitlab.example"})
    with pytest.raises(ValueError, match="relative"):
        ForgeConfig.model_validate({"template": "/etc/passwd"})
    assert ForgeConfig.model_validate({"kind": "gitlab"}).model_dump(by_alias=True) == {
        "kind": "gitlab"
    }
