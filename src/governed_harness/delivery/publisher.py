"""``harness pr publish``: the decision brief and the SARIF report on a pull request.

A publisher posts (or updates) one comment per run on the pull request, marked with
``<!-- governed-harness:<run id> -->`` so that publishing again edits the same comment, and
uploads the run's findings as SARIF to code scanning. GitHub is the first publisher; it talks
to the REST API through a transport:

* ``gh``: the GitHub CLI (``gh api``), with the authentication the CLI already has;
* ``api``: HTTPS with a token read from an environment variable (``GITHUB_TOKEN`` by default),
  never from a file or the configuration.

The comment carries the brief (what was asked, what changed, gate, risks, what was and was not
verified, decisions) and no output of a validator or the agent. Nothing here decides: a comment
or a SARIF upload is information for the people who review the pull request."""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import subprocess  # nosec B404 - the forge CLI with a fixed argv, no shell
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from governed_harness.configuration.models import PublisherConfig
from governed_harness.domain.errors import ConfigurationError, HarnessError

MAX_COMMENT_CHARS = 60_000
"""GitHub accepts 65,536 characters in a comment; the brief is cut before that."""
_TIMEOUT_SECONDS = 30


class PublishError(HarnessError):
    """The publisher could not reach the platform or the platform refused a request."""


class Transport(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any: ...


@dataclass
class GhTransport:
    """``gh api``: the GitHub CLI's own authentication."""

    executable: str = "gh"

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if shutil.which(self.executable) is None:
            raise PublishError(f"{self.executable} is not on PATH; use the api transport")
        args = [self.executable, "api", "--method", method, path]
        if body is not None:
            args += ["--input", "-"]
        try:
            result = subprocess.run(  # nosec B603 - fixed argv of the forge CLI checked on PATH, no shell
                args,
                input=json.dumps(body).encode("utf-8") if body is not None else None,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PublishError(f"gh api could not run: {error}") from error
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()[:300]
            raise PublishError(f"gh api {method} {path} failed: {message}")
        text = result.stdout.decode("utf-8", "replace").strip()
        return json.loads(text) if text else None


@dataclass
class RestTransport:
    """HTTPS with a bearer token from the environment."""

    api_url: str
    token: str

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_url}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - the URL is https (validated in config)
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "governed-harness",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise PublishError(f"{method} {path} returned HTTP {error.code}") from error
        except (urllib.error.URLError, OSError) as error:
            raise PublishError(f"{method} {path} failed: {error}") from error
        return json.loads(text) if text.strip() else None


def transport_for(config: PublisherConfig) -> Transport:
    if config.transport == "gh":
        return GhTransport()
    token = os.environ.get(config.token_env)
    if not token:
        raise ConfigurationError(
            f"the api transport reads the token from {config.token_env}, which is not set"
        )
    return RestTransport(config.api_url, token)


def marker(run_id: str) -> str:
    return f"<!-- governed-harness:{run_id} -->"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def render_brief_markdown(brief: dict[str, Any]) -> str:
    """The decision brief (``harness review``) as GitHub Markdown."""
    run = brief["run"]
    asked = brief["asked"]
    changed = brief["changed"]
    gate = brief.get("gate")
    lines = [
        marker(run["executionId"]),
        f"## Governed Agent Harness: {_cell(asked['title'])}",
        "",
        f"Run `{run['executionId']}` (task `{run['taskId']}`): **{run['status']}** in "
        f"`{run['currentPhase']}`"
        + (" (waiting for a human decision)" if run.get("awaitingDecision") else ""),
        "",
        f"ChangeSet digest: `{run.get('changeSetDigest')}`",
        "",
    ]
    if gate:
        reasons = ", ".join(f"`{item['code']}`" for item in gate.get("reasons", [])) or "none"
        lines += [f"Gate: **{gate['status']}** ({reasons})", ""]
    if asked.get("acceptanceCriteria"):
        lines += ["### Acceptance criteria", ""]
        lines += [
            f"- {_cell(item['criterionId'])} [{item.get('priority', 'MUST')}]: {_cell(item['text'])}"
            for item in asked["acceptanceCriteria"]
        ]
        lines.append("")
    if changed.get("files"):
        lines += ["### Changed files", "", "| File | Status | + | - |", "|---|---|---:|---:|"]
        lines += [
            f"| `{_cell(item['path'])}` | {item['status']} | {item['additions']} | "
            f"{item['deletions']} |"
            for item in changed["files"]
        ]
        lines.append("")
    risks = brief.get("risks") or []
    if risks:
        lines += [
            "### Findings",
            "",
            "| Severity | Rule | Location | Message |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {item['severity']}{' (blocking)' if item.get('blocking') else ''} | "
            f"`{_cell(item['ruleId'])}` | {_cell(item.get('location') or '')} | "
            f"{_cell(item['message'])[:300]} |"
            for item in risks[:50]
        ]
        if len(risks) > 50:
            lines.append(f"| | | | {len(risks) - 50} more finding(s) in the SARIF report |")
        lines.append("")
    verified = (brief.get("verified") or {}).get("validations") or []
    if verified:
        lines += ["### Verified", ""]
        lines += [
            f"- `{item['validatorId']}`{'' if item['mandatory'] else ' (optional)'}: "
            f"{item['status']}"
            for item in verified
        ]
        lines.append("")
    if brief.get("notVerified"):
        lines += ["### Not verified", ""]
        lines += [f"- {_cell(item)}" for item in brief["notVerified"]]
        lines.append("")
    if brief.get("provenance"):
        provenance = brief["provenance"]
        lines += [
            "### Provenance",
            "",
            f"{provenance['agentFiles']} file(s) written by an agent invocation, "
            f"{provenance['outOfBandFiles']} changed out of band: "
            + (", ".join(f"`{path}`" for path in provenance["outOfBandPaths"]) or "none"),
            "",
        ]
    decisions = brief.get("decisions") or []
    if decisions:
        lines += ["### Decisions", ""]
        lines += [
            f"- {item['decision']} by `{_cell(item['actorId'])}` at {item['decidedAt']} on "
            f"`{item['changeSetDigest']}`: {_cell(item['rationale'])[:500]}"
            for item in decisions
        ]
        lines.append("")
    lines.append(
        "Verify that the merged tree is the approved ChangeSet: "
        "`harness verify-approval --base <base> --head <head> --bundle <bundle>`."
    )
    text = "\n".join(lines) + "\n"
    if len(text) > MAX_COMMENT_CHARS:
        text = text[: MAX_COMMENT_CHARS - 80] + "\n\n(cut: the brief is longer than a comment)\n"
    return text


@dataclass
class GitHubPublisher:
    transport: Transport
    repository: str

    def _comment(self, pull_request: int, run_id: str, body: str) -> dict[str, Any]:
        repo = self.repository
        existing = None
        page = 1
        while existing is None and page <= 10:
            comments = self.transport.request(
                "GET", f"repos/{repo}/issues/{pull_request}/comments?per_page=100&page={page}"
            )
            if not isinstance(comments, list) or not comments:
                break
            existing = next(
                (
                    item
                    for item in comments
                    if isinstance(item, dict) and marker(run_id) in str(item.get("body", ""))
                ),
                None,
            )
            page += 1
        if existing is not None:
            result = self.transport.request(
                "PATCH", f"repos/{repo}/issues/comments/{existing['id']}", {"body": body}
            )
            action = "updated"
        else:
            result = self.transport.request(
                "POST", f"repos/{repo}/issues/{pull_request}/comments", {"body": body}
            )
            action = "created"
        result = result if isinstance(result, dict) else {}
        return {"action": action, "id": result.get("id"), "url": result.get("html_url")}

    def _sarif(self, pull_request: int, sarif: bytes, commit_sha: str | None) -> dict[str, Any]:
        repo = self.repository
        if commit_sha is None:
            pull = self.transport.request("GET", f"repos/{repo}/pulls/{pull_request}")
            commit_sha = (
                ((pull or {}).get("head") or {}).get("sha") if isinstance(pull, dict) else None
            )
        if not commit_sha:
            return {"status": "SKIPPED", "reason": "the head commit of the pull request is unknown"}
        try:
            result = self.transport.request(
                "POST",
                f"repos/{repo}/code-scanning/sarifs",
                {
                    "commit_sha": commit_sha,
                    "ref": f"refs/pull/{pull_request}/head",
                    "sarif": base64.b64encode(gzip.compress(sarif, mtime=0)).decode("ascii"),
                    "tool_name": "governed-harness",
                },
            )
        except PublishError as error:
            # Code scanning may be unavailable for the repository (plan, permissions); the
            # comment stays published and the report says why the upload failed.
            return {"status": "FAILED", "reason": str(error), "commitSha": commit_sha}
        result = result if isinstance(result, dict) else {}
        return {"status": "UPLOADED", "id": result.get("id"), "commitSha": commit_sha}

    def publish(
        self,
        *,
        pull_request: int,
        run_id: str,
        brief_markdown: str,
        sarif: bytes | None,
        commit_sha: str | None = None,
    ) -> dict[str, Any]:
        comment = self._comment(pull_request, run_id, brief_markdown)
        upload = (
            self._sarif(pull_request, sarif, commit_sha)
            if sarif is not None
            else {"status": "SKIPPED", "reason": "SARIF upload disabled"}
        )
        return {
            "publisher": "github",
            "repository": self.repository,
            "pullRequest": pull_request,
            "runId": run_id,
            "comment": comment,
            "sarif": upload,
        }
