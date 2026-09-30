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
    with pytest.raises(CapabilityDenied):
        CapabilityAuthorizer().authorize(
            actor=other,
            capability="filesystem.read",
            resource="README.md",
            grants=[grant(actor, "filesystem.read", ("**",))],
        )


def test_approval_required_grant_is_not_implicit() -> None:
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.test")
    with pytest.raises(CapabilityDenied):
        CapabilityAuthorizer().authorize(
            actor=actor,
            capability="git.push",
            resource="origin/main",
            grants=[grant(actor, "git.push", ("**",), approval=True)],
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
    with pytest.raises(CapabilityDenied):
        CapabilityAuthorizer().authorize_command(
            actor=actor,
            argv=("bash", "-c", "echo x"),
            grants=[grant(actor, "process.execute", ("python",))],
        )


def test_contained_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(CapabilityDenied):
        contained_path(root, Path("../outside"))
