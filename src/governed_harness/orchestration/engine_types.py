"""Types and constants the engine shares with its helpers (#69): its paths and services, the
outcome of a phase step, of a read-only agent call and of the independent review, and the
effective testing strategy.

They live apart from ``engine``, ``agent_results``, ``agent_review`` and ``engineering`` so that
the helpers can build a ``PhaseOutcome`` and type what they share without importing their owner
or a sibling: an import back into the owner closed a module cycle. The modules that defined them
re-export them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governed_harness.agents.environment import secret_values
from governed_harness.configuration.models import ResolvedConfiguration
from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import Finding
from governed_harness.events import SQLiteEventStore
from governed_harness.evidence import LocalArtifactStore, SecretRedactor
from governed_harness.runtime.state_location import resolve_state_location
from governed_harness.storage import SQLiteStateStore


@dataclass(frozen=True)
class EnginePaths:
    workspace: Path
    harness_dir: Path
    database: Path
    artifact_dir: Path
    state_root: Path | None = None
    """The run registry outside the workspace (``runtime.stateDir`` or an isolated run's
    origin, #55); ``None`` when the state lives in ``.harness/`` as in 1.0.0."""

    @classmethod
    def from_workspace(cls, workspace: Path) -> EnginePaths:
        root = workspace.resolve(strict=True)
        harness_dir = root / ".harness"
        harness_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return cls(root, harness_dir, harness_dir / "state.db", harness_dir / "artifacts")

    @classmethod
    def for_project(cls, resolved: ResolvedConfiguration) -> EnginePaths:
        """The paths of a resolved project: ``.harness/`` for the workspace's own files and the
        state location ``runtime.stateDir`` (or an isolation marker) names for the registry."""
        paths = cls.from_workspace(resolved.workspace_root)
        location = resolve_state_location(
            paths.workspace, resolved.project.project_id, resolved.project.runtime.state_dir
        )
        if not location.external:
            return paths
        return cls(
            paths.workspace,
            paths.harness_dir,
            location.database,
            location.artifacts,
            location.root,
        )


@dataclass
class EngineServices:
    resolved: ResolvedConfiguration
    paths: EnginePaths
    state: SQLiteStateStore
    events: SQLiteEventStore
    artifacts: LocalArtifactStore

    @classmethod
    def open(cls, resolved: ResolvedConfiguration) -> EngineServices:
        paths = EnginePaths.for_project(resolved)
        return cls(
            resolved=resolved,
            paths=paths,
            state=SQLiteStateStore(paths.database),
            events=SQLiteEventStore(paths.database),
            artifacts=LocalArtifactStore(
                paths.artifact_dir,
                SecretRedactor(
                    literals=secret_values(resolved.project),
                    extended=bool(resolved.project.runtime.extended_redaction),
                ),
            ),
        )

    def close(self) -> None:
        self.state.close()
        self.events.close()


@dataclass(frozen=True)
class PhaseOutcome:
    status: ResultStatus
    summary: str
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


READ_ONLY_RULE = "agent.read-only-violation"
"""Rule id of the finding recorded when a read-only agent call changed the workspace."""


@dataclass(frozen=True)
class AgentCallOutcome:
    """What a read-only agent call returned: its status, the structured result when it passed
    and the evidence it left."""

    status: ResultStatus
    summary: str
    result: dict[str, Any] | None
    invocation_id: str | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ReviewOutcome:
    """Whether the independent review ran and the findings of it that block."""

    ran: bool
    blocking: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class Strategy:
    """The effective testing strategy and where it came from."""

    name: str
    """``tdd``, ``bdd``, ``conventional`` or ``unknown`` (the ``testing.strategy`` value)."""
    source: str
    """``configuration``, ``project-setup`` (a person's answer), ``detected`` or ``unknown``."""
    frameworks: tuple[str, ...] = ()

    @property
    def strategy(self) -> str:
        """The strategy's name, as the callers and the ``strategy`` key of ``as_dict`` read it."""
        return self.name

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.name,
            "source": self.source,
            "frameworks": list(self.frameworks),
        }


__all__ = [
    "READ_ONLY_RULE",
    "AgentCallOutcome",
    "EnginePaths",
    "EngineServices",
    "PhaseOutcome",
    "ReviewOutcome",
    "Strategy",
]
