"""Capabilities per phase (#4, governance.phaseCapabilities): profile ∩ project, the phase's
allowed capabilities, read-only agent calls outside IMPLEMENTATION."""

from __future__ import annotations

from governed_harness.capabilities import grants_from_rules
from governed_harness.capabilities.phase import (
    PhasePolicy,
    current_policy,
    intersect_capabilities,
    intersect_scopes,
    phase_scope,
)
from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor

AGENT = Actor(actor_type=ActorType.AGENT, actor_id="agent.coder", version="1")
TOOL = Actor(actor_type=ActorType.TOOL, actor_id="validator.x", version="1")
RULES = (
    CapabilityRule(capability="filesystem.read", scope=("**",)),
    CapabilityRule(capability="filesystem.write", scope=("src/**", "tests/**")),
    CapabilityRule(capability="process.execute", scope=("python",)),
    CapabilityRule(capability="git.read", scope=("**",)),
)


def test_scopes_intersect_conservatively() -> None:
    assert intersect_scopes(["src/**", "tests/**"], ["src/app/**"]) == ("src/app/**",)
    assert intersect_scopes(["**"], ["docs/**"]) == ("docs/**",)
    assert intersect_scopes(["python"], ["python -m pytest"]) == ("python -m pytest",)
    assert intersect_scopes(["src/**"], ["lib/**"]) == ()
    # Two patterns that only overlap partially are not kept: the check fails closed.
    assert intersect_scopes(["src/*.py"], ["src/a*"]) == ()


def test_a_project_narrows_but_never_widens_the_profiles() -> None:
    project = (
        CapabilityRule(capability="filesystem.write", scope=("src/**",)),
        CapabilityRule(capability="process.execute", scope=("make",)),
    )
    result = {rule.capability: rule.scope for rule in intersect_capabilities(RULES, project)}
    assert result["filesystem.write"] == ("src/**",)  # narrowed
    assert "process.execute" not in result  # "make" is not within any profile scope
    assert result["git.read"] == ("**",)  # not mentioned by the project: unchanged


def test_the_phase_policy_filters_every_grant() -> None:
    policy = PhasePolicy(
        "VERIFICATION",
        frozenset({"filesystem.read", "process.execute"}),
        {"agent.coder": ("claude",)},
    )
    assert current_policy() is None
    with phase_scope(policy):
        tool = {grant.capability for grant in grants_from_rules("run", TOOL, RULES)}
        agent = grants_from_rules("run", AGENT, RULES)
    assert tool == {"filesystem.read", "process.execute"}
    # Outside IMPLEMENTATION an agent is read-only and may start only its own command.
    assert [(grant.capability, grant.scope) for grant in agent] == [
        ("filesystem.read", ("**",)),
        ("process.execute", ("claude",)),
    ]
    assert current_policy() is None
    # Without a policy nothing changes (1.0.0).
    assert len(grants_from_rules("run", AGENT, RULES)) == 4


def test_implementation_keeps_the_agent_write_grant() -> None:
    policy = PhasePolicy(
        "IMPLEMENTATION",
        frozenset({"filesystem.read", "filesystem.write", "process.execute", "git.read"}),
    )
    with phase_scope(policy):
        capabilities = {grant.capability for grant in grants_from_rules("run", AGENT, RULES)}
    assert capabilities == {"filesystem.read", "filesystem.write", "process.execute", "git.read"}
    description = policy.describe(RULES, "agent.coder")
    assert description["phase"] == "IMPLEMENTATION"
    assert {item["capability"] for item in description["agent"]["grants"]} == capabilities
