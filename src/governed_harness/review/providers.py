"""The providers of the reviewers and their isolation (#57, item 8).

A reviewer runs on a configured provider (``agentProviders``) or the deterministic ``simulated``
one. Under ``runtime.agentSandbox: enforce`` its process runs in the agent sandbox with the whole
workspace read-only (it is protected after the write paths are allowed), so a reviewer cannot
change what it reviews; an unavailable sandbox is an answer that never came (``UNKNOWN``), not an
unconfined call. Only the environment the provider declares reaches it.

The MCP servers a reviewer may use are read from the repository's ``.mcp.json`` and kept only
when ``review.panel.mcpServers`` allows them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governed_harness.agents import CommandAgentProvider, SimulatedAgentProvider
from governed_harness.agents.command import CommandAgentConfiguration
from governed_harness.agents.environment import provider_environment
from governed_harness.agents.native import native_provider
from governed_harness.configuration.models import ResolvedConfiguration
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor
from governed_harness.evidence.hashing import sha256_json
from governed_harness.review.invoke import BuiltProvider
from governed_harness.runtime.sandbox import (
    SandboxHost,
    SandboxPlan,
    SandboxUnavailable,
    build_sandbox,
)

MCP_FILE = ".mcp.json"


@dataclass(frozen=True)
class ReviewerProvider:
    built: BuiltProvider
    sandbox: SandboxPlan | None


def mcp_servers(workspace: Path, allowlist: tuple[str, ...] | None) -> dict[str, Any]:
    """The definitions of the allowed MCP servers (``.mcp.json`` of the repository)."""
    if not allowlist:
        return {}
    path = workspace / MCP_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {name: servers[name] for name in sorted(servers) if name in set(allowlist)}


def mcp_digest(servers: dict[str, Any]) -> str:
    return sha256_json(servers)


def build_reviewer_provider(
    resolved: ResolvedConfiguration,
    provider_id: str,
    *,
    workspace: Path,
    protected: tuple[Path, ...] = (),
    host: SandboxHost | None = None,
    allow_network: bool = True,
) -> ReviewerProvider | str:
    """The provider of a reviewer, or why it cannot run."""
    if provider_id == "simulated":
        actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.simulated", version="1")
        return ReviewerProvider(BuiltProvider(SimulatedAgentProvider(), actor), None)
    project = resolved.project
    config = project.agent_providers.get(provider_id)
    if config is None:
        return f"provider {provider_id!r} is not configured in agentProviders"
    sandbox: SandboxPlan | None = None
    if project.runtime.effective_agent_sandbox == "enforce":
        try:
            sandbox = build_sandbox(
                workspace,
                project.runtime.sandbox_write_paths or (),
                host or SandboxHost.detect(),
                protected=(workspace, *protected),
                allow_network=allow_network,
            )
        except SandboxUnavailable as error:
            return f"the agent sandbox is enforced but unavailable: {error}"
    environment = None
    if config.pass_env is not None or config.env is not None:
        environment = provider_environment(config)
        if environment.missing:
            return (
                f"provider {provider_id!r} needs environment variable(s) "
                f"{', '.join(environment.missing)}"
            )
    configuration = CommandAgentConfiguration(
        provider_id=provider_id,
        argv_prefix=config.effective_command,
        model=config.model,
        sandbox_prefix=sandbox.prefix if sandbox else (),
        environment=environment,
        extra_args=config.args or (),
    )
    provider = (
        native_provider(config.kind, configuration)
        if config.native
        else CommandAgentProvider(configuration)
    )
    actor = Actor(actor_type=ActorType.AGENT, actor_id=f"agent.{provider_id}", version="1")
    return ReviewerProvider(BuiltProvider(provider, actor), sandbox)


__all__ = [
    "MCP_FILE",
    "ReviewerProvider",
    "build_reviewer_provider",
    "mcp_digest",
    "mcp_servers",
]
