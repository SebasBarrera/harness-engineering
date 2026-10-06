"""Forges: where a governed change is proposed and reported (#56).

One interface (``BaseForge``) over GitHub, GitLab, Bitbucket, Azure DevOps and Gitea:
publish the decision brief as a comment, attach the quality report, create the pull or merge
request and set a commit status. The forge is detected from the ``origin`` remote unless
``delivery.forge.kind`` names it; the token comes only from the environment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governed_harness.configuration.engineering import ForgeConfig
from governed_harness.delivery.vcs import Git
from governed_harness.domain.errors import ConfigurationError
from governed_harness.forges.codequality import (
    CODE_QUALITY_FILE,
    code_quality_issues,
    render_code_quality,
)
from governed_harness.forges.detect import (
    KNOWN_FORGES,
    ForgeLocation,
    default_api_url,
    kind_of_host,
    parse_remote,
)
from governed_harness.forges.providers import (
    FORGES,
    STATUS_CONTEXT,
    AzureDevOpsForge,
    BaseForge,
    BitbucketForge,
    GiteaForge,
    GitHubForge,
    GitLabForge,
    forge_for,
)
from governed_harness.forges.transport import (
    DEFAULT_TOKEN_ENV,
    CliTransport,
    HttpTransport,
    Transport,
    auth_headers,
    build_transport,
)

_PR_TEMPLATE = "pull_request_template.md"

TEMPLATE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "github": (
        f".github/{_PR_TEMPLATE}",
        ".github/PULL_REQUEST_TEMPLATE.md",
        f"docs/{_PR_TEMPLATE}",
        _PR_TEMPLATE,
    ),
    "gitlab": (
        ".gitlab/merge_request_templates/Default.md",
        ".gitlab/merge_request_templates/default.md",
    ),
    "bitbucket": ("PULL_REQUEST_TEMPLATE.md", f".bitbucket/{_PR_TEMPLATE}"),
    "azure-devops": (
        f".azuredevops/{_PR_TEMPLATE}",
        f"docs/{_PR_TEMPLATE}",
        _PR_TEMPLATE,
    ),
    "gitea": (
        f".gitea/{_PR_TEMPLATE}",
        f".github/{_PR_TEMPLATE}",
        _PR_TEMPLATE,
    ),
}
"""Where each forge looks for its default pull request template, in order."""

_MAX_TEMPLATE_BYTES = 20_000


@dataclass(frozen=True)
class ResolvedForge:
    location: ForgeLocation
    transport: str
    token_env: str
    source: str
    """``configuration`` when ``delivery.forge.kind`` named it, ``origin`` when detected."""

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.location.as_dict(),
            "transport": self.transport,
            "tokenEnv": self.token_env,
            "source": self.source,
        }


def origin_url(workspace: Path, remote: str = "origin") -> str | None:
    git = Git(workspace)
    if not git.is_repository():
        return None
    url = git.run("remote", "get-url", remote, check=False).stdout.decode().strip()
    return url or None


_DEFAULT_HOSTS = {
    "github": "github.com",
    "gitlab": "gitlab.com",
    "bitbucket": "bitbucket.org",
    "azure-devops": "dev.azure.com",
    "gitea": "codeberg.org",
}


def _configured_location(wanted: str | None, name: str | None, *, explicit: bool) -> ForgeLocation:
    """The forge the configuration names when ``origin`` points to none: its kind on the
    default host, with the configured repository."""
    if not explicit:
        raise ConfigurationError(
            "no forge detected: the origin remote is missing or on an unknown host; set "
            "delivery.forge.kind (github, gitlab, bitbucket, azure-devops, gitea) and "
            "delivery.forge.repository, or pass --forge and --repository"
        )
    if name is None:
        raise ConfigurationError(
            "no repository: pass --repository or set delivery.forge.repository"
        )
    host = _DEFAULT_HOSTS[str(wanted)]
    return ForgeLocation(str(wanted), host, name, default_api_url(str(wanted), host, name))


def resolve_forge(
    workspace: Path,
    settings: ForgeConfig | None,
    *,
    kind: str | None = None,
    repository: str | None = None,
    transport: str | None = None,
) -> ResolvedForge:
    """The forge of the workspace: CLI options, then ``delivery.forge``, then ``origin``."""
    configured = settings or ForgeConfig()
    wanted = kind or configured.kind
    explicit = wanted not in {None, "auto"}
    url = origin_url(workspace)
    location = parse_remote(url, wanted) if url else None
    name = repository or configured.repository
    if location is None:
        location = _configured_location(wanted, name, explicit=explicit)
    if name is not None and name != location.repository:
        location = ForgeLocation(
            location.kind,
            location.host,
            name,
            default_api_url(location.kind, location.host, name),
        )
    if configured.api_url:
        location = ForgeLocation(
            location.kind, location.host, location.repository, configured.api_url
        )
    mode = transport or configured.transport or ("cli" if location.kind == "github" else "api")
    if mode not in {"cli", "api"}:
        raise ConfigurationError(f"transport must be cli or api, got {mode!r}")
    return ResolvedForge(
        location,
        mode,
        configured.token_env or DEFAULT_TOKEN_ENV[location.kind],
        "configuration" if explicit else "origin",
    )


def open_forge(resolved: ResolvedForge, transport: Transport | None = None) -> BaseForge:
    """The provider of a resolved forge; ``transport`` replaces the network (tests)."""
    location = resolved.location
    return forge_for(
        location.kind,
        transport
        or build_transport(location.kind, resolved.transport, location.api_url, resolved.token_env),
        location.repository,
    )


def read_template(workspace: Path, kind: str, configured: str | None) -> tuple[str | None, str]:
    """The pull request template text and where it came from (``none`` when there is none)."""
    candidates = (configured,) if configured else TEMPLATE_CANDIDATES.get(kind, ())
    root = workspace.resolve()
    for candidate in candidates:
        if not candidate:
            continue
        target = (root / candidate).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file():
            data = target.read_bytes()[:_MAX_TEMPLATE_BYTES]
            return data.decode("utf-8", "replace"), candidate
    if configured:
        raise ConfigurationError(f"pull request template not found: {configured}")
    return None, "none"


def pull_request_body(brief_markdown: str, template: str | None) -> str:
    """The template (when there is one) followed by the decision brief."""
    if not template:
        return brief_markdown
    return template.rstrip() + "\n\n---\n\n" + brief_markdown


def status_for(execution_status: str, current_phase: str, gate_status: str | None) -> str:
    """The commit status of a run: ``success`` once closed, ``pending`` while a person decides,
    ``failure`` when blocked, failed or rejected."""
    if execution_status == "PASSED" and current_phase == "CLOSURE":
        return "success"
    if current_phase == "DECISION" and gate_status == "PASSED":
        return "pending"
    if execution_status in {"PENDING", "RUNNING"}:
        return "pending"
    return "failure"


__all__ = [
    "CODE_QUALITY_FILE",
    "DEFAULT_TOKEN_ENV",
    "FORGES",
    "KNOWN_FORGES",
    "STATUS_CONTEXT",
    "TEMPLATE_CANDIDATES",
    "AzureDevOpsForge",
    "BaseForge",
    "BitbucketForge",
    "CliTransport",
    "ForgeLocation",
    "GitHubForge",
    "GitLabForge",
    "GiteaForge",
    "HttpTransport",
    "ResolvedForge",
    "Transport",
    "auth_headers",
    "build_transport",
    "code_quality_issues",
    "default_api_url",
    "forge_for",
    "kind_of_host",
    "open_forge",
    "origin_url",
    "parse_remote",
    "pull_request_body",
    "read_template",
    "render_code_quality",
    "resolve_forge",
    "status_for",
]
