"""How a forge provider reaches its REST API (#56).

* ``HttpTransport``: HTTPS with the forge's authentication header; the token is read from an
  environment variable when the transport is built and is never logged, stored or put in an
  error message.
* ``CliTransport``: the forge's own CLI (``gh api`` for GitHub, ``glab api`` for GitLab), with
  the authentication the CLI already has.

Tests inject a fake transport; nothing in the harness's test suite reaches the network."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from governed_harness.delivery.publisher import PublishError
from governed_harness.domain.errors import ConfigurationError

_TIMEOUT_SECONDS = 30

DEFAULT_TOKEN_ENV: dict[str, str] = {
    "github": "GITHUB_TOKEN",
    "gitlab": "GITLAB_TOKEN",
    "bitbucket": "BITBUCKET_TOKEN",
    "azure-devops": "AZURE_DEVOPS_TOKEN",
    "gitea": "GITEA_TOKEN",
}
"""The environment variable each forge reads its token from unless ``tokenEnv`` names another."""

CLI_EXECUTABLES: dict[str, str] = {"github": "gh", "gitlab": "glab"}
"""Forges with a CLI that can call the REST API with its own authentication."""


class Transport(Protocol):
    def request(self, method: str, path: str, body: Any = None) -> Any: ...


@dataclass
class HttpTransport:
    """HTTPS with headers built once; the token only lives in the headers."""

    api_url: str
    headers: dict[str, str] = field(repr=False)

    def request(self, method: str, path: str, body: Any = None) -> Any:
        url = f"{self.api_url}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - the URL is https (validated in config)
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "governed-harness",
                **self.headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise PublishError(
                f"{method} {path.split('?')[0]} returned HTTP {error.code}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise PublishError(f"{method} {path.split('?')[0]} failed: {error}") from error
        return json.loads(text) if text.strip() else None


@dataclass
class CliTransport:
    """``<cli> api --method M PATH --input -``: the CLI's own authentication."""

    executable: str

    def request(self, method: str, path: str, body: Any = None) -> Any:
        if shutil.which(self.executable) is None:
            raise PublishError(f"{self.executable} is not on PATH; use the api transport")
        args = [self.executable, "api", "--method", method, path]
        if body is not None:
            args += ["--input", "-"]
        try:
            result = subprocess.run(  # nosec B603 - fixed argv, no shell
                args,
                input=json.dumps(body).encode("utf-8") if body is not None else None,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PublishError(f"{self.executable} api could not run: {error}") from error
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()[:300]
            raise PublishError(f"{self.executable} api {method} {path} failed: {message}")
        text = result.stdout.decode("utf-8", "replace").strip()
        return json.loads(text) if text else None


def auth_headers(kind: str, token: str) -> dict[str, str]:
    """The authentication header of each forge's REST API."""
    if kind == "github":
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    if kind == "gitlab":
        return {"PRIVATE-TOKEN": token}
    if kind == "bitbucket":
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if kind == "azure-devops":
        basic = base64.b64encode(f":{token}".encode()).decode("ascii")
        return {"Authorization": f"Basic {basic}", "Accept": "application/json"}
    return {"Authorization": f"token {token}", "Accept": "application/json"}


def build_transport(
    kind: str, transport: str, api_url: str, token_env: str | None = None
) -> Transport:
    """The transport of a forge: its CLI, or HTTPS with the token from the environment."""
    if transport == "cli":
        executable = CLI_EXECUTABLES.get(kind)
        if executable is None:
            raise ConfigurationError(f"the {kind} forge has no CLI transport; use transport: api")
        return CliTransport(executable)
    name = token_env or DEFAULT_TOKEN_ENV[kind]
    token = os.environ.get(name)
    if not token:
        raise ConfigurationError(
            f"the api transport reads the {kind} token from {name}, which is not set"
        )
    return HttpTransport(api_url, auth_headers(kind, token))


__all__ = [
    "CLI_EXECUTABLES",
    "DEFAULT_TOKEN_ENV",
    "CliTransport",
    "HttpTransport",
    "Transport",
    "auth_headers",
    "build_transport",
]
