"""The ``api`` section (#18): defaults, validation, digest stability and the role checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from governed_harness.agents.environment import secret_values
from governed_harness.api.auth import (
    Principal,
    StartToken,
    TokenAuthenticator,
    bearer_token,
    start_token,
)
from governed_harness.application import HarnessApplication
from governed_harness.configuration import ProjectConfiguration
from governed_harness.configuration.api import (
    API_ROLES,
    DEFAULT_API_TOKEN_ENV,
    ApiConfig,
    role_allows,
)
from governed_harness.domain.errors import ConfigurationError
from governed_harness.evidence.hashing import sha256_json

BASE = {"configVersion": "1.0", "projectId": "project_x", "workspace": {"root": ".."}}


def _token(name: str) -> str:
    return f"tok-{name}-" + "y" * 20


def test_a_project_without_the_section_keeps_its_serialization_and_digest() -> None:
    config = ProjectConfiguration.model_validate(BASE)
    dumped = config.model_dump(mode="json", by_alias=True)
    assert "api" not in dumped
    assert config.api_settings.enabled is False
    with_section = ProjectConfiguration.model_validate({**BASE, "api": {"auth": "token"}})
    assert with_section.model_dump(mode="json", by_alias=True)["api"] == {"auth": "token"}
    assert sha256_json(dumped) != sha256_json(with_section.model_dump(mode="json", by_alias=True))
    assert sha256_json(dumped) == sha256_json(
        ProjectConfiguration.model_validate(BASE).model_dump(mode="json", by_alias=True)
    )


def test_the_section_turns_authentication_on_unless_auth_is_off() -> None:
    assert ApiConfig().enabled is True
    assert ApiConfig.model_validate(yaml.safe_load("auth: off")).enabled is False
    defaults = ApiConfig()
    assert defaults.start_token_env == DEFAULT_API_TOKEN_ENV
    assert defaults.start_user == "human.web"
    assert defaults.start_role == "admin"
    assert defaults.token_env_names == (DEFAULT_API_TOKEN_ENV,)


@pytest.mark.parametrize(
    "section",
    [
        {"token": "inline-value"},
        {"users": [{"id": "agent.reviewer", "role": "reviewer", "tokenEnv": "A"}]},
        {"users": [{"id": "Alice Smith", "role": "reviewer", "tokenEnv": "A"}]},
        {"users": [{"id": "alice", "role": "owner", "tokenEnv": "A"}]},
        {"users": [{"id": "alice", "role": "viewer", "tokenEnv": "NOT-A-NAME"}]},
        {"users": [{"id": "alice", "role": "viewer", "tokenEnv": "A", "token": "x"}]},
        {
            "users": [
                {"id": "alice", "role": "viewer", "tokenEnv": "A"},
                {"id": "alice", "role": "admin", "tokenEnv": "B"},
            ]
        },
        {
            "users": [
                {"id": "alice", "role": "viewer", "tokenEnv": "A"},
                {"id": "bob", "role": "admin", "tokenEnv": "A"},
            ]
        },
        {"users": [{"id": "human.web", "role": "viewer", "tokenEnv": "A"}]},
        {"users": [{"id": "alice", "role": "viewer", "tokenEnv": DEFAULT_API_TOKEN_ENV}]},
        {"tokenUser": "harness.core"},
    ],
)
def test_invalid_sections_are_refused(section: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ApiConfig.model_validate(section)


def test_roles_include_the_ones_below_them() -> None:
    assert API_ROLES == ("viewer", "reviewer", "admin")
    allowed = {(role, required) for role in API_ROLES for required in API_ROLES}
    expected = {
        ("viewer", "viewer"),
        ("reviewer", "viewer"),
        ("reviewer", "reviewer"),
        ("admin", "viewer"),
        ("admin", "reviewer"),
        ("admin", "admin"),
    }
    assert {pair for pair in allowed if role_allows(*pair)} == expected
    assert Principal("alice", "reviewer").allows("reviewer")
    assert not Principal("bob", "viewer").allows("reviewer")


def test_bearer_tokens_are_parsed_from_the_header_only() -> None:
    assert bearer_token(None) is None
    assert bearer_token("") is None
    assert bearer_token("Bearer") is None
    assert bearer_token("Basic abc") is None
    assert bearer_token("bearer  value ") == "value"


def test_the_authenticator_maps_tokens_to_people() -> None:
    settings = ApiConfig.model_validate(
        {"users": [{"id": "alice", "role": "reviewer", "tokenEnv": "ALICE_TOKEN"}]}
    )
    notices: list[str] = []
    owner, alice = _token("owner"), _token("alice")
    authenticator = TokenAuthenticator.from_settings(
        settings, owner, {"ALICE_TOKEN": alice}, notices
    )
    assert notices == []
    assert authenticator.authenticate(f"Bearer {owner}") == Principal("human.web", "admin")
    assert authenticator.authenticate(f"Bearer {alice}") == Principal("alice", "reviewer")
    assert authenticator.authenticate(f"Bearer {alice}x") is None
    assert authenticator.authenticate(f"Bearer {alice[:-1]}") is None
    assert authenticator.authenticate(None) is None
    # Without a start token and without the user's variable nobody can sign in.
    empty = TokenAuthenticator.from_settings(settings, None, {}, notices)
    assert empty.users == ()
    assert notices == ["API user alice cannot sign in: ALICE_TOKEN is not set"]


def test_short_and_shared_tokens_are_refused_without_echoing_them() -> None:
    settings = ApiConfig.model_validate(
        {"users": [{"id": "alice", "role": "reviewer", "tokenEnv": "ALICE_TOKEN"}]}
    )
    with pytest.raises(ConfigurationError) as short:
        TokenAuthenticator.from_settings(settings, "tiny-value", {})
    assert "tiny-value" not in str(short.value)
    shared = _token("shared")
    with pytest.raises(ConfigurationError, match="same token") as same:
        TokenAuthenticator.from_settings(settings, shared, {"ALICE_TOKEN": shared})
    assert shared not in str(same.value)


def test_the_start_token_comes_from_its_variable_or_is_generated() -> None:
    settings = ApiConfig(token_env="MY_API_TOKEN")
    chosen = _token("chosen")
    taken = start_token(settings, {"MY_API_TOKEN": chosen})
    assert taken == StartToken(value=chosen, generated=False, source="MY_API_TOKEN")
    generated = start_token(settings, {})
    assert generated.generated
    assert generated.source == "generated"
    assert len(generated.value) >= 32
    assert start_token(settings, {}).value != generated.value
    with pytest.raises(ConfigurationError, match="MY_API_TOKEN"):
        start_token(settings, {"MY_API_TOKEN": "tiny"})


def test_api_tokens_are_redacted_from_artifacts() -> None:
    value = _token("redacted")
    without = ProjectConfiguration.model_validate(BASE)
    assert secret_values(without, {DEFAULT_API_TOKEN_ENV: value}) == ()
    with_section = ProjectConfiguration.model_validate(
        {
            **BASE,
            "api": {"users": [{"id": "alice", "role": "viewer", "tokenEnv": "ALICE_TOKEN"}]},
        }
    )
    assert secret_values(with_section, {DEFAULT_API_TOKEN_ENV: value, "ALICE_TOKEN": value}) == (
        value.encode(),
        value.encode(),
    )


def test_init_writes_the_section_and_config_validate_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DEFAULT_API_TOKEN_ENV, raising=False)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
    HarnessApplication().init(tmp_path)
    written = yaml.safe_load((tmp_path / ".harness" / "project.yaml").read_text(encoding="utf-8"))
    assert written["api"] == {
        "auth": "token",
        "tokenEnv": DEFAULT_API_TOKEN_ENV,
        "tokenUser": "human.web",
        "tokenRole": "admin",
        "users": [],
    }
    report = HarnessApplication().validate_config(tmp_path)
    assert report["api"] == {
        "auth": "token",
        "tokenEnv": DEFAULT_API_TOKEN_ENV,
        "tokenSet": False,
        "tokenUser": "human.web",
        "tokenRole": "admin",
        "users": [],
    }
