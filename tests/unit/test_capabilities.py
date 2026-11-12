from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from governed_harness.capabilities import CapabilityAuthorizer, CapabilityDenied, contained_path
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor, CapabilityGrant


def grant(
    actor: Actor, capability: str, scope: tuple[str, ...], *, approval: bool = False
) -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        grant_id="grant_001",
        execution_id="run_001",
        actor=actor,
        capability=capability,
        scope=scope,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=1),
        approval_required=approval,
    )


def test_glob_scope_authorizes_nested_file() -> None:
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.test")
    result = CapabilityAuthorizer().authorize(
        actor=actor,
        capability="filesystem.write",
        resource="src/package/file.py",
        grants=[grant(actor, "filesystem.write", ("src/**",))],
    )
    assert result.grant_id == "grant_001"


def test_different_actor_is_denied() -> None:
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.one")
    other = Actor(actor_type=ActorType.AGENT, actor_id="agent.two")
    capability_authorizer = CapabilityAuthorizer()
    grants = [grant(actor, "filesystem.read", ("**",))]
    with pytest.raises(CapabilityDenied):
        capability_authorizer.authorize(
            actor=other, capability="filesystem.read", resource="README.md", grants=grants
        )


def test_approval_required_grant_is_not_implicit() -> None:
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.test")
    capability_authorizer = CapabilityAuthorizer()
    grants = [grant(actor, "git.push", ("**",), approval=True)]
    with pytest.raises(CapabilityDenied):
        capability_authorizer.authorize(
            actor=actor, capability="git.push", resource="origin/main", grants=grants
        )


def test_command_prefix_authorization() -> None:
    actor = Actor(actor_type=ActorType.TOOL, actor_id="tool.test")
    CapabilityAuthorizer().authorize_command(
        actor=actor,
        argv=("python", "-m", "pytest"),
        grants=[grant(actor, "process.execute", ("python",))],
    )


def test_unlisted_command_is_denied() -> None:
    actor = Actor(actor_type=ActorType.TOOL, actor_id="tool.test")
    capability_authorizer = CapabilityAuthorizer()
    grants = [grant(actor, "process.execute", ("python",))]
    with pytest.raises(CapabilityDenied):
        capability_authorizer.authorize_command(
            actor=actor, argv=("bash", "-c", "echo x"), grants=grants
        )


def test_contained_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(CapabilityDenied):
        contained_path(root, Path("../outside"))


@pytest.mark.parametrize(
    ("resource", "scope"),
    [
        (".env", "env"),
        ("../src/x.py", "src/**"),
        ("src/../../x.py", "src/**"),
        ("github/w.yml", ".github/**"),
        ("/etc/passwd", "**"),
        ("C:/Windows/x.py", "**"),
        (r"..\outside.py", "**"),
    ],
)
def test_write_scope_is_a_path_glob_not_a_character_strip(resource: str, scope: str) -> None:
    # F11: lstrip("./") removed characters, not a prefix, and `..` was not refused.
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.test")
    with pytest.raises(CapabilityDenied):
        CapabilityAuthorizer().authorize(
            actor=actor,
            capability="filesystem.write",
            resource=resource,
            grants=[grant(actor, "filesystem.write", (scope,))],
        )


@pytest.mark.parametrize(
    ("resource", "scope"),
    [
        ("./src/a.py", "src/**"),
        ("src", "src/**"),
        (r"src\pkg\a.py", "src/**"),
        ("setup.py", "*.py"),
        ("pkg/mod.py", "*.py"),  # * crosses / as in 0.8.0
        ("src/a/b/c.py", "src/*.py"),
        ("./setup.py", "./*.py"),
        ("cmd/app/main.go", "**/*.go"),
        ("lib/src/Main.kt", "**/src/**"),
        (".github/workflows/ci.yml", ".github/**"),
        ("src/a.py", "src/*.py"),
        ("pyproject.toml", "pyproject.toml"),
        ("anything/at/all", "**"),
    ],
)
def test_write_scope_globs_keep_their_meaning(resource: str, scope: str) -> None:
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.test")
    CapabilityAuthorizer().authorize(
        actor=actor,
        capability="filesystem.write",
        resource=resource,
        grants=[grant(actor, "filesystem.write", (scope,))],
    )


@pytest.mark.parametrize(
    ("argv", "scope"),
    [
        (("python", "-m pytest", "x"), "python -m pytest"),
        (("python evil.py",), "python"),
    ],
)
def test_command_scope_compares_whole_arguments(argv: tuple[str, ...], scope: str) -> None:
    # A scope prefix is a sequence of arguments, not a prefix of the joined text.
    actor = Actor(actor_type=ActorType.TOOL, actor_id="tool.test")
    with pytest.raises(CapabilityDenied):
        CapabilityAuthorizer().authorize_command(
            actor=actor, argv=argv, grants=[grant(actor, "process.execute", (scope,))]
        )


def test_command_scope_prefix_of_arguments() -> None:
    actor = Actor(actor_type=ActorType.TOOL, actor_id="tool.test")
    CapabilityAuthorizer().authorize_command(
        actor=actor,
        argv=("python", "-m", "pytest", "-q"),
        grants=[grant(actor, "process.execute", ("python -m pytest",))],
    )


def test_command_scope_with_spaces_in_the_executable() -> None:
    actor = Actor(actor_type=ActorType.TOOL, actor_id="tool.test")
    executable = r"C:\Users\John Doe\venv\python.exe"
    grants = [grant(actor, "process.execute", (f"{executable} agent.py",))]
    CapabilityAuthorizer().authorize_command(
        actor=actor, argv=(executable, "agent.py", "--fast"), grants=grants
    )
    CapabilityAuthorizer().authorize_command(
        actor=actor,
        argv=(executable, "-q"),
        grants=[grant(actor, "process.execute", (executable,))],
    )
