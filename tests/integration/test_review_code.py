"""``harness review-code`` and the review panel commands outside governed runs (#57): base
resolution, modes, evidence refs verified without models, rule sync, a pull request comment
through a fake forge transport, the pre-push hook and variance. No network: the remote of the
hook mode is a local bare repository."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.application.review_code import HOOK_MARKER
from governed_harness.cli.main import app
from governed_harness.domain.errors import ConfigurationError
from governed_harness.review.reviewers import BEGIN_MARKER, END_MARKER
from tests.conftest import GIT_ENV, GIT_ISOLATION

BROKEN_REVIEWER = """\
import json, sys
request = json.load(sys.stdin)
print(json.dumps({"status": "PASSED", "summary": "no contract", "result": {"findings": []}}))
"""

FINDING_REVIEWER = """\
import json, sys
request = json.load(sys.stdin)
reviewer = request.get("reviewer", {}).get("id")
findings = []
if reviewer == "quality" and "project.no-magic-discount" in request["instructions"]:
    findings.append({
        "file": "src/sample/pricing.py", "side": "new", "line": 2,
        "rule": "project.no-magic-discount", "severity": "error",
        "issue": "the discount rate is a literal", "evidence": "0.9",
    })
result = {"verdict": "FAIL" if findings else "PASS", "findings": findings, "summary": "done"}
usage = {"inputTokens": len(json.dumps(request)) // 4, "outputTokens": 20}
print(json.dumps({"status": "PASSED", "summary": "reviewed", "result": result, "usage": usage}))
"""

PROJECT_RULE = """\
---
domain: quality
---

## project.no-magic-discount: No literal discount rates

- severity: blocking
- priority: 2

Discount rates come from the pricing configuration, never from a literal in the code.
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *GIT_ISOLATION, *args],
        cwd=root,
        check=True,
        env=GIT_ENV,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _configure(root: Path, **panel: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.setdefault("review", {})["panel"] = {"mode": "enforce", **panel}
    config["runtime"]["agentSandbox"] = "off"
    config["agentProviders"] = {
        name: {"kind": "command", "command": [sys.executable, f"{name}.py"]}
        for name in ("broken", "finder")
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (root / "broken.py").write_text(BROKEN_REVIEWER, encoding="utf-8")
    (root / "finder.py").write_text(FINDING_REVIEWER, encoding="utf-8")
    # The command providers run the interpreter by its absolute path.
    capabilities = config.setdefault("capabilities", {"default": "deny", "grants": []})
    capabilities.setdefault("grants", []).append(
        {"capability": "process.execute", "scope": [sys.executable]}
    )
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _feature(root: Path, *, content: str = "    return subtotal * 0.9\n") -> str:
    git(root, "checkout", "-q", "-b", "feat/discount")
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n" + content,
        encoding="utf-8",
    )
    git(root, "add", "src/sample/pricing.py")
    git(root, "commit", "-qm", "discount")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def workspace(python_workspace: Path) -> Path:
    (python_workspace / ".gitignore").write_text(".harness/\n*.py.log\n", encoding="utf-8")
    _configure(python_workspace)
    git(python_workspace, "add", ".gitignore", "broken.py", "finder.py")
    git(python_workspace, "commit", "-qm", "fixtures")
    return python_workspace


def test_manual_review_records_evidence_verified_without_models(workspace: Path) -> None:
    head = _feature(workspace)
    application = HarnessApplication()
    report = application.review_code(workspace)
    assert report["verdict"] == "PASS"
    assert report["baseResolution"]["source"] == "merge-base"
    assert report["head"] == head
    assert report["evidenceRef"] == f"refs/harness/review/pass/{head}"
    ran = {item["id"] for item in report["reviewers"] if item["status"] != "SKIPPED"}
    assert ran == {"architecture", "quality"}  # no test, script or concurrency change
    verified = application.review_verify(workspace, head)
    assert verified["valid"], verified["problems"]
    # A report edited after the fact no longer verifies.
    ref = report["evidenceRef"]
    edited = json.loads(git(workspace, "cat-file", "blob", ref))
    edited["findings"] = [
        {
            "file": "x",
            "side": "new",
            "line": 1,
            "rule": "a.b",
            "severity": "suggestion",
            "issue": "i",
        }
    ]
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=workspace,
        input=json.dumps(edited),
        capture_output=True,
        text=True,
        check=True,
        env=GIT_ENV,
    ).stdout.strip()
    git(workspace, "update-ref", ref, blob)
    tampered = application.review_verify(workspace, head)
    assert not tampered["valid"]
    assert "the report digest does not match its content" in tampered["problems"]


def test_cli_project_rule_reaches_the_reviewer_and_blocks(workspace: Path) -> None:
    (workspace / ".harness" / "review" / "rules").mkdir(parents=True)
    (workspace / ".harness" / "review" / "rules" / "quality.md").write_text(PROJECT_RULE)
    _feature(workspace)
    runner = CliRunner()
    shown = json.loads(
        runner.invoke(app, ["--json", "review", "rules", "show", "--path", str(workspace)]).stdout
    )
    assert shown["catalog"]["byLayer"]["C"] == 1
    assert any(rule["id"] == "python.no-mutable-defaults" for rule in shown["rules"])
    result = runner.invoke(
        app, ["--json", "review-code", "--path", str(workspace), "--provider", "finder"]
    )
    assert result.exit_code == 6, result.output
    report = json.loads(result.stdout)
    assert report["verdict"] == "FAIL"
    assert [(item["rule"], item["line"]) for item in report["findings"]] == [
        ("project.no-magic-discount", 2)
    ]
    assert report["evidenceRef"] is None
    assert report["tokens"]["total"] > 0


def test_unknown_reviewer_is_retried_then_blocks(workspace: Path) -> None:
    _feature(workspace)
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "review-code",
            "--path",
            str(workspace),
            "--provider",
            "broken",
            "--fallback-provider",
            "broken",
        ],
    )
    assert result.exit_code == 6, result.output
    report = json.loads(result.stdout)
    assert report["verdict"] == "UNKNOWN"
    unknown = [item for item in report["reviewers"] if item["status"] == "UNKNOWN"]
    assert unknown and all(item["attempts"] == 2 for item in unknown)


def test_cache_hit_skips_the_reviewers(workspace: Path) -> None:
    _feature(workspace)
    application = HarnessApplication()
    first = application.review_code(workspace, provider="finder")
    assert first["cache"]["global"] == "miss"
    second = application.review_code(workspace, provider="finder")
    assert second["cache"]["global"] == "hit"
    assert second["digest"] == first["digest"]
    assert second["tokens"]["total"] == 0
    hook_like = application.review_code(workspace, provider="finder", mode="manual", skip=())
    assert hook_like["cache"]["global"] == "hit"


def test_hook_mode_fetches_the_base_and_aborts_without_it(workspace: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=GIT_ENV)
    git(workspace, "remote", "add", "origin", str(remote))
    git(workspace, "push", "-q", "origin", "HEAD:refs/heads/main")
    _feature(workspace)
    report = HarnessApplication().review_code(workspace, mode="hook", base="main")
    assert report["baseResolution"] == {
        "ref": "origin/main",
        "sha": git(workspace, "rev-parse", "origin/main"),
        "source": "explicit",
        "fetched": True,
    }
    assert report["verdict"] == "PASS"
    git(workspace, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    result = CliRunner().invoke(
        app, ["--json", "review-code", "--path", str(workspace), "--mode", "hook", "--base", "main"]
    )
    assert result.exit_code == 6
    assert "could not be fetched" in result.output


def test_staged_mode_reviews_the_index(workspace: Path) -> None:
    (workspace / "tests" / "test_more.py").write_text(
        "def test_constant() -> None:\n    assert True\n", encoding="utf-8"
    )
    git(workspace, "add", "tests/test_more.py")
    report = HarnessApplication().review_code(workspace, mode="staged")
    assert report["head"].startswith("tree:")
    assert report["evidenceRef"] is None
    assert [item["rule"] for item in report["findings"]] == ["tests.tautological-assertion"]
    assert report["verdict"] == "FAIL"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        return [] if method == "GET" else {"id": 7}


def test_one_comment_per_passing_result(workspace: Path) -> None:
    git(workspace, "remote", "add", "origin", "https://github.com/acme/shop.git")
    _feature(workspace)
    application = HarnessApplication()
    transport = Recorder()
    report = application.review_code(
        workspace, comment=True, pull_request=12, transport_override=transport
    )
    assert report["comment"]["status"] == "PASSED"
    posts = [call for call in transport.calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "repos/acme/shop/issues/12/comments"
    assert "Review panel: PASS" in posts[0][2]["body"]
    again = application.review_code(
        workspace, comment=True, pull_request=12, transport_override=transport
    )
    assert again["comment"]["status"] == "SKIPPED"
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    failing = application.review_code(
        workspace,
        comment=True,
        pull_request=12,
        provider="broken",
        fallback="broken",
        transport_override=transport,
    )
    assert failing["comment"] == {"status": "SKIPPED", "reason": "no comment on a UNKNOWN review"}


def test_rules_sync_and_check(workspace: Path) -> None:
    agents = workspace / ".harness" / "review" / "agents"
    agents.mkdir(parents=True)
    (agents / "pricing.md").write_text(
        f"---\ndomain: quality\nactivation: always\n---\nPricing.\n\n{BEGIN_MARKER}\n{END_MARKER}\n"
    )
    runner = CliRunner()
    check = runner.invoke(
        app, ["--json", "review", "rules", "sync", "--check", "--path", str(workspace)]
    )
    assert check.exit_code == 6
    assert json.loads(check.stdout)["drift"][0]["reviewer"] == "pricing"
    sync = runner.invoke(app, ["--json", "review", "rules", "sync", "--path", str(workspace)])
    assert sync.exit_code == 0 and json.loads(sync.stdout)["reviewers"][0]["status"] == "updated"
    again = runner.invoke(
        app, ["--json", "review", "rules", "sync", "--check", "--path", str(workspace)]
    )
    assert again.exit_code == 0


def test_hook_install(workspace: Path) -> None:
    application = HarnessApplication()
    installed = application.review_hook_install(workspace)
    hook = Path(installed["hook"])
    text = hook.read_text()
    assert HOOK_MARKER in text and "review-code --mode hook" in text
    application.review_hook_install(workspace)  # its own hook is replaced
    hook.write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(ConfigurationError):
        application.review_hook_install(workspace)
    application.review_hook_install(workspace, force=True)
    assert HOOK_MARKER in hook.read_text()


def test_variance_of_identical_reviews(workspace: Path) -> None:
    _feature(workspace)
    variance = HarnessApplication().review_variance(workspace, runs=2, provider="finder")
    assert variance["runs"] == 2
    assert variance["verdicts"] == {"PASS": 2}
    assert variance["verdictStability"] == 1.0 and variance["meanJaccard"] == 1.0


def test_review_brief_still_works_as_a_command(python_workspace: Path) -> None:
    result = CliRunner().invoke(app, ["--json", "review", "--path", str(python_workspace)])
    # No run yet: the brief reports it (not found), not a usage error.
    assert result.exit_code == 3
