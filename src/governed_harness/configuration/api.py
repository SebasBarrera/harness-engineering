"""Authentication and roles of the local API and dashboard (since 1.1, issue #18).

``harness api serve`` of 1.0.0 had no authentication: any process that reached the port could
read every run and record a decision under any actor id. The ``api`` section turns on a local
bearer-token check for every route and three roles:

* ``viewer``: every ``GET`` (the dashboard, runs, briefs, traces, evidence);
* ``reviewer``: a viewer that may also decide (``POST /api/runs/ID/decision``);
* ``admin``: a reviewer that may also read the effective configuration (``GET /api/config``).

Tokens never appear in ``project.yaml``: the section names the environment variable that holds
each one. The token of the person who starts the server comes from ``tokenEnv`` or, when that
variable is not set, is generated at start and shown once on the terminal.

Every key is optional. A project without the section keeps the 1.0.0 behaviour (no
authentication) and its configuration digest, because the section is left out of the
serialized configuration. With the section present and ``auth`` absent, authentication is on.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from governed_harness.configuration.agent_results import off_from_yaml
from governed_harness.domain.actors import DEFAULT_API_ACTOR, is_non_human_actor_id

ApiRole = Literal["viewer", "reviewer", "admin"]
API_ROLES: tuple[ApiRole, ...] = ("viewer", "reviewer", "admin")
"""The roles from the least to the most privileged; each one includes the ones before it."""

DEFAULT_API_TOKEN_ENV = "HARNESS_API_TOKEN"  # nosec B105 - an environment variable name, not a token
DEFAULT_API_TOKEN_ROLE: ApiRole = "admin"
MIN_API_TOKEN_LENGTH = 16
"""Shorter tokens taken from the environment are refused at start (fail closed)."""

_ENV_NAME = re.compile(r"^[A-Za-z_]\w{0,127}$", re.ASCII)
_USER_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


def role_allows(role: ApiRole, required: ApiRole) -> bool:
    """Whether ``role`` includes the permissions of ``required``."""
    return API_ROLES.index(role) >= API_ROLES.index(required)


def _env_name(value: str) -> str:
    if not _ENV_NAME.match(value):
        raise ValueError(f"not an environment variable name: {value!r}")
    return value


def _user_id(value: str) -> str:
    if not _USER_ID.match(value):
        raise ValueError(
            f"user id {value!r} must be an actor id: lower case, starting with a letter, "
            "then letters, digits, '_', '.' or '-' (2 to 128 characters)"
        )
    if is_non_human_actor_id(value):
        raise ValueError(
            f"user id {value!r} belongs to the agent, validator or harness namespace; "
            "API users are people"
        )
    return value


class _Section(BaseModel):
    """A section whose unset (``None``) keys are left out of the serialized configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name, field in type(self).model_fields.items():
            if getattr(self, name) is None:
                data.pop(name, None)
                data.pop(field.alias or name, None)
        return data


class ApiUser(_Section):
    """A person who may use the API: the actor id recorded on their decisions, their role and
    the NAME of the environment variable that holds their token (never the token itself)."""

    user_id: str = Field(alias="id")
    role: ApiRole
    token_env: str = Field(alias="tokenEnv")

    @field_validator("user_id")
    @classmethod
    def _valid_user(cls, value: str) -> str:
        return _user_id(value)

    @field_validator("token_env")
    @classmethod
    def _valid_env(cls, value: str) -> str:
        return _env_name(value)


class ApiConfig(_Section):
    """``harness api serve`` authentication (#18). ``auth: token`` (the default when the
    section is present) requires ``Authorization: Bearer TOKEN`` on every route; ``off`` keeps
    the 1.0.0 behaviour. ``tokenEnv`` names the variable holding the token of the person who
    starts the server (generated and shown once when it is not set), ``tokenUser`` the actor
    id recorded on that person's decisions and ``tokenRole`` their role. ``users`` declares
    further people, each with an id, a role and the variable of their token."""

    auth: Literal["token", "off"] | None = None
    token_env: str | None = Field(default=None, alias="tokenEnv")
    token_user: str | None = Field(default=None, alias="tokenUser")
    token_role: ApiRole | None = Field(default=None, alias="tokenRole")
    users: tuple[ApiUser, ...] | None = None

    @field_validator("auth", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        return off_from_yaml(value)

    @field_validator("token_env")
    @classmethod
    def _valid_env(cls, value: str | None) -> str | None:
        return None if value is None else _env_name(value)

    @field_validator("token_user")
    @classmethod
    def _valid_user(cls, value: str | None) -> str | None:
        return None if value is None else _user_id(value)

    @model_validator(mode="after")
    def _distinct_users(self) -> ApiConfig:
        ids = [self.start_user, *(user.user_id for user in self.users or ())]
        envs = [self.start_token_env, *(user.token_env for user in self.users or ())]
        for label, values in (("user id", ids), ("tokenEnv", envs)):
            repeated = sorted({value for value in values if values.count(value) > 1})
            if repeated:
                raise ValueError(
                    f"api: each {label} must be distinct (the start token counts as "
                    f"{self.start_user} with {self.start_token_env}): {', '.join(repeated)}"
                )
        return self

    @property
    def enabled(self) -> bool:
        """Whether the API requires a token (the section is present and ``auth`` is not
        ``off``)."""
        return self.auth != "off"

    @property
    def start_token_env(self) -> str:
        return self.token_env or DEFAULT_API_TOKEN_ENV

    @property
    def start_user(self) -> str:
        return self.token_user or DEFAULT_API_ACTOR

    @property
    def start_role(self) -> ApiRole:
        return self.token_role or DEFAULT_API_TOKEN_ROLE

    @property
    def token_env_names(self) -> tuple[str, ...]:
        """Every variable that may hold an API token (their values are redacted from the
        artifacts like any other secret of the environment)."""
        return (self.start_token_env, *(user.token_env for user in self.users or ()))


__all__ = [
    "API_ROLES",
    "DEFAULT_API_TOKEN_ENV",
    "DEFAULT_API_TOKEN_ROLE",
    "MIN_API_TOKEN_LENGTH",
    "ApiConfig",
    "ApiRole",
    "ApiUser",
    "role_allows",
]
