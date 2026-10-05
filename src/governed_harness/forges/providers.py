"""Forge providers behind one interface (#56): GitHub, GitLab, Bitbucket, Azure DevOps, Gitea.

Every provider does the same four things through its REST API (a ``Transport``):

* ``upsert_comment``: one comment per run on the pull or merge request, marked with
  ``<!-- governed-harness:<run id> -->`` so publishing again edits it;
* ``upload_reports``: the findings as a quality report where the forge has an API for it
  (GitHub code scanning SARIF, Bitbucket Code Insights); GitLab and Azure DevOps read their
  reports from CI artifacts, so the provider says so and the CI templates attach the file;
* ``create_pull_request``: a pull or merge request from a branch, with base, title, body,
  labels and draft;
* ``set_status``: a commit status (``success``, ``failure``, ``pending``) named
  ``governed-harness``.

Nothing here decides: the comment, the status and the reports are information for the people
who review the change. Tokens never reach a message, a record or a log."""

from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from governed_harness.delivery.publisher import MAX_COMMENT_CHARS, PublishError, marker
from governed_harness.forges.transport import Transport

STATUS_CONTEXT = "governed-harness"
StatusState = str  # "success" | "failure" | "pending"
_MAX_PAGES = 10


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _cut(body: str, limit: int = MAX_COMMENT_CHARS) -> str:
    if len(body) <= limit:
        return body
    return body[: limit - 80] + "\n\n(cut: the brief is longer than a comment)\n"


@dataclass
class BaseForge:
    transport: Transport
    repository: str
    kind: str = ""

    # ----- comments ---------------------------------------------------------------------------
    def _comments(self, number: int, page: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _comment_body(self, item: dict[str, Any]) -> str:
        return str(item.get("body", ""))

    def _create_comment(self, number: int, body: str) -> dict[str, Any]:
        raise NotImplementedError

    def _update_comment(self, number: int, item: dict[str, Any], body: str) -> dict[str, Any]:
        raise NotImplementedError

    def upsert_comment(self, number: int, run_id: str, body: str) -> dict[str, Any]:
        body = _cut(body)
        existing: dict[str, Any] | None = None
        for page in range(1, _MAX_PAGES + 1):
            items = self._comments(number, page)
            if not items:
                break
            existing = next(
                (item for item in items if marker(run_id) in self._comment_body(item)), None
            )
            if existing is not None:
                break
        if existing is not None:
            result = self._update_comment(number, existing, body)
            action = "updated"
        else:
            result = self._create_comment(number, body)
            action = "created"
        return {"action": action, **result}

    # ----- reports, pull requests, statuses ----------------------------------------------------
    def upload_reports(
        self,
        number: int,
        *,
        sarif: bytes | None,
        code_quality: bytes | None,
        commit_sha: str | None,
    ) -> dict[str, Any]:
        return {"status": "SKIPPED", "reason": f"{self.kind} has no report upload API"}

    def create_pull_request(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        draft: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def set_status(
        self,
        *,
        commit_sha: str,
        state: StatusState,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


# ----- GitHub ----------------------------------------------------------------------------------
@dataclass
class GitHubForge(BaseForge):
    kind: str = "github"

    def _comments(self, number: int, page: int) -> list[dict[str, Any]]:
        value = self.transport.request(
            "GET", f"repos/{self.repository}/issues/{number}/comments?per_page=100&page={page}"
        )
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _create_comment(self, number: int, body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "POST", f"repos/{self.repository}/issues/{number}/comments", {"body": body}
            )
        )
        return {"id": result.get("id"), "url": result.get("html_url")}

    def _update_comment(self, number: int, item: dict[str, Any], body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "PATCH", f"repos/{self.repository}/issues/comments/{item['id']}", {"body": body}
            )
        )
        return {"id": result.get("id"), "url": result.get("html_url")}

    def upload_reports(
        self,
        number: int,
        *,
        sarif: bytes | None,
        code_quality: bytes | None,
        commit_sha: str | None,
    ) -> dict[str, Any]:
        if sarif is None:
            return {"status": "SKIPPED", "reason": "SARIF upload disabled"}
        if commit_sha is None:
            pull = _dict(self.transport.request("GET", f"repos/{self.repository}/pulls/{number}"))
            commit_sha = _dict(pull.get("head")).get("sha")
        if not commit_sha:
            return {"status": "SKIPPED", "reason": "the head commit of the pull request is unknown"}
        try:
            result = _dict(
                self.transport.request(
                    "POST",
                    f"repos/{self.repository}/code-scanning/sarifs",
                    {
                        "commit_sha": commit_sha,
                        "ref": f"refs/pull/{number}/head",
                        "sarif": base64.b64encode(gzip.compress(sarif, mtime=0)).decode("ascii"),
                        "tool_name": "governed-harness",
                    },
                )
            )
        except PublishError as error:
            return {"status": "FAILED", "reason": str(error), "commitSha": commit_sha}
        return {"status": "UPLOADED", "id": result.get("id"), "commitSha": commit_sha}

    def create_pull_request(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        draft: bool = False,
    ) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "POST",
                f"repos/{self.repository}/pulls",
                {"title": title, "head": head, "base": base, "body": body, "draft": draft},
            )
        )
        number = result.get("number")
        if labels and number is not None:
            self.transport.request(
                "POST", f"repos/{self.repository}/issues/{number}/labels", {"labels": list(labels)}
            )
        return {"number": number, "url": result.get("html_url")}

    def set_status(
        self,
        *,
        commit_sha: str,
        state: StatusState,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "state": state,
            "context": STATUS_CONTEXT,
            "description": description[:140],
        }
        if target_url:
            body["target_url"] = target_url
        result = _dict(
            self.transport.request("POST", f"repos/{self.repository}/statuses/{commit_sha}", body)
        )
        return {"id": result.get("id"), "state": state}


# ----- GitLab ----------------------------------------------------------------------------------
_GITLAB_STATE = {"success": "success", "failure": "failed", "pending": "pending"}


@dataclass
class GitLabForge(BaseForge):
    kind: str = "gitlab"

    @property
    def _project(self) -> str:
        return f"projects/{quote(self.repository, safe='')}"

    def _comments(self, number: int, page: int) -> list[dict[str, Any]]:
        value = self.transport.request(
            "GET", f"{self._project}/merge_requests/{number}/notes?per_page=100&page={page}"
        )
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _create_comment(self, number: int, body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "POST", f"{self._project}/merge_requests/{number}/notes", {"body": body}
            )
        )
        return {"id": result.get("id")}

    def _update_comment(self, number: int, item: dict[str, Any], body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "PUT", f"{self._project}/merge_requests/{number}/notes/{item['id']}", {"body": body}
            )
        )
        return {"id": result.get("id")}

    def upload_reports(
        self,
        number: int,
        *,
        sarif: bytes | None,
        code_quality: bytes | None,
        commit_sha: str | None,
    ) -> dict[str, Any]:
        return {
            "status": "SKIPPED",
            "reason": "GitLab reads Code Quality and SAST reports from CI artifacts: attach "
            "gl-code-quality-report.json (harness trace --format codequality) as "
            "artifacts:reports:codequality",
        }

    def create_pull_request(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        draft: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_branch": head,
            "target_branch": base,
            "title": f"Draft: {title}" if draft else title,
            "description": body,
        }
        if labels:
            payload["labels"] = ",".join(labels)
        result = _dict(self.transport.request("POST", f"{self._project}/merge_requests", payload))
        return {"number": result.get("iid"), "url": result.get("web_url")}

    def set_status(
        self,
        *,
        commit_sha: str,
        state: StatusState,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "state": _GITLAB_STATE[state],
            "name": STATUS_CONTEXT,
            "description": description[:255],
        }
        if target_url:
            body["target_url"] = target_url
        result = _dict(
            self.transport.request("POST", f"{self._project}/statuses/{commit_sha}", body)
        )
        return {"id": result.get("id"), "state": body["state"]}


# ----- Bitbucket Cloud -------------------------------------------------------------------------
_BITBUCKET_STATE = {"success": "SUCCESSFUL", "failure": "FAILED", "pending": "INPROGRESS"}
_BITBUCKET_SEVERITY = {
    "info": "LOW",
    "minor": "LOW",
    "major": "MEDIUM",
    "critical": "HIGH",
    "blocker": "CRITICAL",
}
_REPORT_ID = "governed-harness"


@dataclass
class BitbucketForge(BaseForge):
    kind: str = "bitbucket"

    @property
    def _repo(self) -> str:
        return f"repositories/{self.repository}"

    def _comments(self, number: int, page: int) -> list[dict[str, Any]]:
        value = _dict(
            self.transport.request(
                "GET", f"{self._repo}/pullrequests/{number}/comments?pagelen=100&page={page}"
            )
        )
        items = value.get("values")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _comment_body(self, item: dict[str, Any]) -> str:
        return str(_dict(item.get("content")).get("raw", ""))

    def _create_comment(self, number: int, body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "POST", f"{self._repo}/pullrequests/{number}/comments", {"content": {"raw": body}}
            )
        )
        return {"id": result.get("id")}

    def _update_comment(self, number: int, item: dict[str, Any], body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "PUT",
                f"{self._repo}/pullrequests/{number}/comments/{item['id']}",
                {"content": {"raw": body}},
            )
        )
        return {"id": result.get("id")}

    def upload_reports(
        self,
        number: int,
        *,
        sarif: bytes | None,
        code_quality: bytes | None,
        commit_sha: str | None,
    ) -> dict[str, Any]:
        """A Code Insights report on the commit with one annotation per finding."""
        if code_quality is None:
            return {"status": "SKIPPED", "reason": "the quality report is disabled"}
        if commit_sha is None:
            pull = _dict(self.transport.request("GET", f"{self._repo}/pullrequests/{number}"))
            commit_sha = _dict(_dict(pull.get("source")).get("commit")).get("hash")
        if not commit_sha:
            return {"status": "SKIPPED", "reason": "the head commit of the pull request is unknown"}
        issues = json.loads(code_quality.decode("utf-8"))
        blocking = any(item.get("severity") in {"critical", "blocker"} for item in issues)
        base = f"{self._repo}/commit/{commit_sha}/reports/{_REPORT_ID}"
        try:
            self.transport.request(
                "PUT",
                base,
                {
                    "title": "Governed Agent Harness",
                    "details": f"{len(issues)} finding(s) of the governed run",
                    "report_type": "BUG",
                    "reporter": "governed-harness",
                    "result": "FAILED" if blocking else "PASSED",
                },
            )
            annotations = [
                {
                    "external_id": str(item.get("fingerprint", index))[:64],
                    "annotation_type": "BUG",
                    "summary": str(item.get("description", ""))[:450],
                    "severity": _BITBUCKET_SEVERITY.get(str(item.get("severity")), "LOW"),
                    "path": _dict(item.get("location")).get("path"),
                    "line": _dict(_dict(item.get("location")).get("lines")).get("begin"),
                }
                for index, item in enumerate(issues[:100])
            ]
            if annotations:
                self.transport.request("POST", f"{base}/annotations", annotations)
        except PublishError as error:
            return {"status": "FAILED", "reason": str(error), "commitSha": commit_sha}
        return {"status": "UPLOADED", "annotations": min(len(issues), 100), "commitSha": commit_sha}

    def create_pull_request(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        draft: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "description": body,
            "source": {"branch": {"name": head}},
            "destination": {"branch": {"name": base}},
        }
        if draft:
            payload["draft"] = True
        result = _dict(self.transport.request("POST", f"{self._repo}/pullrequests", payload))
        report: dict[str, Any] = {
            "number": result.get("id"),
            "url": _dict(_dict(result.get("links")).get("html")).get("href"),
        }
        if labels:
            report["labelsIgnored"] = list(labels)  # Bitbucket pull requests have no labels
        return report

    def set_status(
        self,
        *,
        commit_sha: str,
        state: StatusState,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "state": _BITBUCKET_STATE[state],
            "key": STATUS_CONTEXT,
            "name": "Governed Agent Harness",
            "description": description[:255],
            "url": target_url or "https://localhost.invalid/governed-harness",
        }
        result = _dict(
            self.transport.request("POST", f"{self._repo}/commit/{commit_sha}/statuses/build", body)
        )
        return {"key": result.get("key", STATUS_CONTEXT), "state": body["state"]}


# ----- Azure DevOps ----------------------------------------------------------------------------
_AZURE_STATE = {"success": "succeeded", "failure": "failed", "pending": "pending"}
_AZURE_VERSION = "api-version=7.1"


@dataclass
class AzureDevOpsForge(BaseForge):
    """``repository`` is ``organization/project/repository``; the transport's base URL is the
    organization (``https://dev.azure.com/<organization>``)."""

    kind: str = "azure-devops"

    @property
    def _repo(self) -> str:
        _organization, project, repository = self.repository.split("/", 2)
        return f"{quote(project)}/_apis/git/repositories/{quote(repository)}"

    def _comments(self, number: int, page: int) -> list[dict[str, Any]]:
        if page > 1:
            return []  # threads are returned in one response
        value = _dict(
            self.transport.request(
                "GET", f"{self._repo}/pullRequests/{number}/threads?{_AZURE_VERSION}"
            )
        )
        threads = value.get("value")
        items: list[dict[str, Any]] = []
        for thread in threads if isinstance(threads, list) else []:
            comments = _dict(thread).get("comments")
            first = comments[0] if isinstance(comments, list) and comments else None
            if isinstance(first, dict):
                items.append({"threadId": thread.get("id"), **first})
        return items

    def _comment_body(self, item: dict[str, Any]) -> str:
        return str(item.get("content", ""))

    def _create_comment(self, number: int, body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "POST",
                f"{self._repo}/pullRequests/{number}/threads?{_AZURE_VERSION}",
                {
                    "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
                    "status": 1,
                },
            )
        )
        return {"id": result.get("id")}

    def _update_comment(self, number: int, item: dict[str, Any], body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "PATCH",
                f"{self._repo}/pullRequests/{number}/threads/{item['threadId']}/comments/"
                f"{item['id']}?{_AZURE_VERSION}",
                {"content": body},
            )
        )
        return {"id": item.get("threadId"), "commentId": result.get("id")}

    def upload_reports(
        self,
        number: int,
        *,
        sarif: bytes | None,
        code_quality: bytes | None,
        commit_sha: str | None,
    ) -> dict[str, Any]:
        return {
            "status": "SKIPPED",
            "reason": "Azure DevOps reads SARIF from the pipeline: publish harness.sarif as the "
            "CodeAnalysisLogs build artifact",
        }

    def create_pull_request(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        draft: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sourceRefName": f"refs/heads/{head}",
            "targetRefName": f"refs/heads/{base}",
            "title": title,
            "description": body[:4000],
            "isDraft": draft,
        }
        if labels:
            payload["labels"] = [{"name": item} for item in labels]
        result = _dict(
            self.transport.request("POST", f"{self._repo}/pullrequests?{_AZURE_VERSION}", payload)
        )
        return {"number": result.get("pullRequestId"), "url": result.get("url")}

    def set_status(
        self,
        *,
        commit_sha: str,
        state: StatusState,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "state": _AZURE_STATE[state],
            "description": description[:255],
            "context": {"name": STATUS_CONTEXT, "genre": "governance"},
        }
        if target_url:
            body["targetUrl"] = target_url
        result = _dict(
            self.transport.request(
                "POST", f"{self._repo}/commits/{commit_sha}/statuses?{_AZURE_VERSION}", body
            )
        )
        return {"id": result.get("id"), "state": body["state"]}


# ----- Gitea and Forgejo -----------------------------------------------------------------------
_GITEA_STATE = {"success": "success", "failure": "failure", "pending": "pending"}


@dataclass
class GiteaForge(BaseForge):
    kind: str = "gitea"

    def _comments(self, number: int, page: int) -> list[dict[str, Any]]:
        value = self.transport.request(
            "GET", f"repos/{self.repository}/issues/{number}/comments?limit=50&page={page}"
        )
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _create_comment(self, number: int, body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "POST", f"repos/{self.repository}/issues/{number}/comments", {"body": body}
            )
        )
        return {"id": result.get("id"), "url": result.get("html_url")}

    def _update_comment(self, number: int, item: dict[str, Any], body: str) -> dict[str, Any]:
        result = _dict(
            self.transport.request(
                "PATCH", f"repos/{self.repository}/issues/comments/{item['id']}", {"body": body}
            )
        )
        return {"id": result.get("id"), "url": result.get("html_url")}

    def _label_ids(self, labels: tuple[str, ...]) -> tuple[list[int], list[str]]:
        value = self.transport.request("GET", f"repos/{self.repository}/labels?limit=50")
        known = {
            str(item.get("name")): int(item["id"])
            for item in (value if isinstance(value, list) else [])
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        return [known[name] for name in labels if name in known], [
            name for name in labels if name not in known
        ]

    def create_pull_request(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        draft: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "head": head,
            "base": base,
            "title": f"WIP: {title}" if draft else title,
            "body": body,
        }
        unknown: list[str] = []
        if labels:
            ids, unknown = self._label_ids(labels)
            if ids:
                payload["labels"] = ids
        result = _dict(self.transport.request("POST", f"repos/{self.repository}/pulls", payload))
        report: dict[str, Any] = {"number": result.get("number"), "url": result.get("html_url")}
        if unknown:
            report["labelsUnknown"] = unknown
        return report

    def set_status(
        self,
        *,
        commit_sha: str,
        state: StatusState,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "state": _GITEA_STATE[state],
            "context": STATUS_CONTEXT,
            "description": description[:255],
        }
        if target_url:
            body["target_url"] = target_url
        result = _dict(
            self.transport.request("POST", f"repos/{self.repository}/statuses/{commit_sha}", body)
        )
        return {"id": result.get("id"), "state": body["state"]}


FORGES: dict[str, type[BaseForge]] = {
    "github": GitHubForge,
    "gitlab": GitLabForge,
    "bitbucket": BitbucketForge,
    "azure-devops": AzureDevOpsForge,
    "gitea": GiteaForge,
}


def forge_for(kind: str, transport: Transport, repository: str) -> BaseForge:
    try:
        return FORGES[kind](transport=transport, repository=repository)
    except KeyError as error:
        raise PublishError(f"unknown forge: {kind}") from error


__all__ = [
    "FORGES",
    "STATUS_CONTEXT",
    "AzureDevOpsForge",
    "BaseForge",
    "BitbucketForge",
    "GitHubForge",
    "GitLabForge",
    "GiteaForge",
    "forge_for",
]
