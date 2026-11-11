"""Which forge a repository lives on, from its ``origin`` remote (#56).

The detection is a pure function of the remote URL, so it is reproducible and needs no
network: the host names the forge (``github.com``, ``gitlab.com``, ``bitbucket.org``,
``dev.azure.com`` and ``*.visualstudio.com``) or, for a self-hosted forge, contains its name
(``gitlab.example.com``, ``gitea.example.com``, ``forgejo...``, ``codeberg.org``). Anything
else is unknown and needs ``delivery.forge.kind``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

KNOWN_FORGES: tuple[str, ...] = ("github", "gitlab", "bitbucket", "azure-devops", "gitea")


@dataclass(frozen=True)
class ForgeLocation:
    """A repository on a forge: its kind, host, path and the default API base URL."""

    kind: str
    host: str
    repository: str
    """``owner/name``, a GitLab project path, ``workspace/repo`` or
    ``organization/project/repository`` (Azure DevOps)."""
    api_url: str

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "host": self.host,
            "repository": self.repository,
            "apiUrl": self.api_url,
        }


_SCP = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)$")


def _host_and_path(url: str) -> tuple[str, str] | None:
    url = url.strip()
    if "://" in url:
        parts = urlsplit(url)
        if not parts.hostname:
            return None
        return parts.hostname.lower(), parts.path.strip("/")
    match = _SCP.match(url)
    if match:
        return match.group("host").lower(), match.group("path").strip("/")
    return None


_AZURE_SSH_HOST = "ssh.dev.azure.com"
_VISUALSTUDIO = ".visualstudio.com"
"""The suffix of the hosts of the former Azure DevOps service (``org.visualstudio.com``)."""


def kind_of_host(host: str) -> str | None:
    """The forge a host name points to, or ``None``."""
    if host == "github.com" or host.endswith(".github.com") or host.startswith("github."):
        return "github"
    if host == "bitbucket.org" or host.endswith(".bitbucket.org"):
        return "bitbucket"
    if (
        host in {"dev.azure.com", _AZURE_SSH_HOST}
        or host.endswith(_VISUALSTUDIO)
        or host.endswith(".dev.azure.com")
    ):
        return "azure-devops"
    if "gitlab" in host:
        return "gitlab"
    if "gitea" in host or "forgejo" in host or host == "codeberg.org":
        return "gitea"
    return None


def default_api_url(kind: str, host: str, repository: str = "") -> str:
    """The REST base URL of a forge on a host."""
    if kind == "github":
        return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    if kind == "gitlab":
        return f"https://{host}/api/v4"
    if kind == "bitbucket":
        return "https://api.bitbucket.org/2.0"
    if kind == "azure-devops":
        organization = repository.split("/", 1)[0] if repository else ""
        if host.endswith(_VISUALSTUDIO):
            return f"https://{host}"
        return f"https://dev.azure.com/{organization}" if organization else "https://dev.azure.com"
    return f"https://{host}/api/v1"


def _azure_repository(host: str, path: str) -> str | None:
    """``organization/project/repository`` from the forms of an Azure DevOps remote."""
    segments = [item for item in path.split("/") if item]
    if host == _AZURE_SSH_HOST and len(segments) >= 4 and segments[0] == "v3":
        return "/".join(segments[1:4])
    if "_git" in segments:
        index = segments.index("_git")
        if index + 1 >= len(segments):
            return None
        repository = segments[index + 1]
        before = segments[:index]
        if host.endswith(_VISUALSTUDIO):
            organization = host.split(".", 1)[0]
            project = before[-1] if before else repository
            return f"{organization}/{project}/{repository}"
        if len(before) >= 2:
            return f"{before[0]}/{before[1]}/{repository}"
        if len(before) == 1:
            return f"{before[0]}/{repository}/{repository}"
    return None


def parse_remote(url: str, kind: str | None = None) -> ForgeLocation | None:
    """The forge location of a remote URL (HTTPS, SSH or scp-like); ``kind`` overrides the
    detection by host (a self-hosted forge whose host does not name it)."""
    parsed = _host_and_path(url)
    if parsed is None:
        return None
    host, path = parsed
    path = path.removesuffix(".git")
    detected = kind if kind not in {None, "auto"} else kind_of_host(host)
    if detected is None:
        return None
    if detected == "azure-devops":
        repository = _azure_repository(host, path)
        if repository is None:
            return None
        api_host = "dev.azure.com" if host == _AZURE_SSH_HOST else host
        return ForgeLocation(
            detected, api_host, repository, default_api_url(detected, api_host, repository)
        )
    if path.startswith("scm/"):  # Bitbucket Server style; kept as the path after it
        path = path.removeprefix("scm/")
    segments = [item for item in path.split("/") if item]
    if len(segments) < 2:
        return None
    repository = "/".join(segments) if detected == "gitlab" else "/".join(segments[:2])
    return ForgeLocation(detected, host, repository, default_api_url(detected, host, repository))


__all__ = ["KNOWN_FORGES", "ForgeLocation", "default_api_url", "kind_of_host", "parse_remote"]
