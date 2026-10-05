"""Local bearer-token authentication, roles and the decision audit log of the API (#18).

Without an ``api`` section in ``project.yaml`` (or with ``api.auth: off``) none of this runs
and the API behaves as in 1.0.0. With it, every request (the dashboard page included) must
carry ``Authorization: Bearer TOKEN``; the token identifies a person (an actor id) with a role.

Tokens are compared in constant time, are never logged, never written to events, artifacts,
the audit log or error messages, and are only accepted in the ``Authorization`` header (never
in a query string, which servers log).
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from governed_harness.configuration.api import (
    MIN_API_TOKEN_LENGTH,
    ApiConfig,
    ApiRole,
    role_allows,
)
from governed_harness.configuration.loader import find_project_config, load_yaml
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import utc_now

PRINCIPAL_SCOPE_KEY = "governed_harness.principal"
"""Where the middleware leaves the authenticated :class:`Principal` in the ASGI scope."""

AUDIT_LOG = Path("audit") / "api-decisions.jsonl"
"""The append-only audit log of API decisions, relative to ``.harness/``."""

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


@dataclass(frozen=True)
class Principal:
    """The person a request was authenticated as."""

    user_id: str
    role: ApiRole

    def allows(self, required: ApiRole) -> bool:
        return role_allows(self.role, required)


@dataclass(frozen=True)
class StartToken:
    """The token of the person who starts the server; ``generated`` when it did not come
    from the environment (and must be shown to that person once)."""

    value: str
    generated: bool
    source: str
    """The environment variable it came from, or ``generated``."""


def api_settings(workspace: Path) -> ApiConfig:
    """The ``api`` section of the project, authentication off (1.0.0) without it.

    Fail closed: a ``project.yaml`` that cannot be read, or whose ``api`` section is invalid,
    raises instead of serving without authentication. A workspace without ``project.yaml``
    has nothing to configure and keeps the 1.0.0 behaviour."""
    try:
        config_path = find_project_config(workspace)
    except (ConfigurationError, OSError):
        return ApiConfig(auth="off")
    raw = load_yaml(config_path)
    section = raw.get("api")
    if section is None:
        return ApiConfig(auth="off")
    try:
        return ApiConfig.model_validate(section)
    except ValidationError as error:
        # Locations and messages only: a value (a token written in the file by mistake) is
        # never echoed.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'api'}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise ConfigurationError(f"invalid api section in {config_path}: {problems}") from None


def start_token(settings: ApiConfig, environ: Mapping[str, str] | None = None) -> StartToken:
    """The start token: the value of ``tokenEnv`` when it is set, else a new random one."""
    source = os.environ if environ is None else environ
    name = settings.start_token_env
    value = source.get(name, "")
    if value:
        _check_length(value, f"{name} (the token of {settings.start_user})")
        return StartToken(value=value, generated=False, source=name)
    return StartToken(value=secrets.token_urlsafe(32), generated=True, source="generated")


def _check_length(value: str, label: str) -> None:
    if len(value) < MIN_API_TOKEN_LENGTH or value != value.strip():
        # The message names the variable, never its value.
        raise ConfigurationError(
            f"the API token in {label} must have at least {MIN_API_TOKEN_LENGTH} characters "
            "and no surrounding spaces"
        )


class TokenAuthenticator:
    """Maps bearer tokens to people. Every comparison runs (no early exit) in constant time."""

    def __init__(self, credentials: tuple[tuple[bytes, Principal], ...]) -> None:
        values = [secret for secret, _ in credentials]
        if len(set(values)) != len(values):
            raise ConfigurationError(
                "two API users share the same token; give each person their own token"
            )
        self._credentials = credentials

    @property
    def users(self) -> tuple[Principal, ...]:
        return tuple(principal for _, principal in self._credentials)

    @classmethod
    def from_settings(
        cls,
        settings: ApiConfig,
        start: StartToken | str | None,
        environ: Mapping[str, str] | None = None,
        notices: list[str] | None = None,
    ) -> TokenAuthenticator:
        """The start token (if any) plus each declared user whose variable is set. A user
        whose variable is not set cannot sign in (a notice says so); a token that is too short
        stops the server."""
        source = os.environ if environ is None else environ
        credentials: list[tuple[bytes, Principal]] = []
        start_value = start.value if isinstance(start, StartToken) else start
        if start_value:
            _check_length(start_value, f"the token of {settings.start_user}")
            credentials.append(
                (start_value.encode("utf-8"), Principal(settings.start_user, settings.start_role))
            )
        for user in settings.users or ():
            value = source.get(user.token_env, "")
            if not value:
                if notices is not None:
                    notices.append(
                        f"API user {user.user_id} cannot sign in: {user.token_env} is not set"
                    )
                continue
            _check_length(value, f"{user.token_env} (the token of {user.user_id})")
            credentials.append((value.encode("utf-8"), Principal(user.user_id, user.role)))
        return cls(tuple(credentials))

    def authenticate(self, authorization: str | None) -> Principal | None:
        presented = bearer_token(authorization)
        if presented is None:
            return None
        candidate = presented.encode("utf-8")
        match: Principal | None = None
        for secret, principal in self._credentials:
            if hmac.compare_digest(secret, candidate):
                match = principal
        return match


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


_UNAUTHORIZED_DETAIL = "authentication required: send Authorization: Bearer TOKEN"


class AuthenticationMiddleware:
    """Requires a valid bearer token on every HTTP request and WebSocket connection; leaves
    the :class:`Principal` in the scope. ``GET /`` without a token answers 401 with the sign-in
    page, which asks for the token and keeps it in the tab's ``sessionStorage``."""

    def __init__(self, app: ASGIApp, authenticator: TokenAuthenticator, sign_in_page: str):
        self.app = app
        self.authenticator = authenticator
        self.sign_in_page = sign_in_page.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        principal = self.authenticator.authenticate(_header(scope, b"authorization"))
        if principal is not None:
            scope[PRINCIPAL_SCOPE_KEY] = principal
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope.get("method") == "GET" and scope.get("path") == "/":
            body, media = self.sign_in_page, b"text/html; charset=utf-8"
        else:
            body = json.dumps({"detail": _UNAUTHORIZED_DETAIL}).encode("utf-8")
            media = b"application/json"
        if scope.get("method") == "HEAD":
            body = b""
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", media),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer realm="governed-harness"'),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key.lower() == name:
            return bytes(value).decode("latin-1")
    return None


class DecisionAuditLog:
    """``.harness/audit/api-decisions.jsonl``: one JSON line per API decision attempt and one
    per outcome, appended (never rewritten) with mode 0600. It records who (user id and
    role), what (route, run, decision, ChangeSet digest), from where (client host) and the
    result, never a token."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    @classmethod
    def for_workspace(cls, workspace: Path) -> DecisionAuditLog:
        return cls(find_project_config(workspace).parent / AUDIT_LOG)

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps({"time": utc_now().isoformat(), **record}, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


__all__ = [
    "AUDIT_LOG",
    "PRINCIPAL_SCOPE_KEY",
    "AuthenticationMiddleware",
    "DecisionAuditLog",
    "Principal",
    "StartToken",
    "TokenAuthenticator",
    "api_settings",
    "bearer_token",
    "start_token",
]
