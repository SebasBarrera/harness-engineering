from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class DetectionResult:
    profile_id: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedResult:
    status: str
    kind: str
    summary: str
    evidence_refs: tuple[str, ...]
    finding_refs: tuple[str, ...] = ()


class ProjectDetector(Protocol):
    def detect(self, workspace: Path) -> DetectionResult:
        """Detect the project in ``workspace`` (or whether the validator applies to ``context``)."""


class TechnologyProfile(Protocol):
    def capabilities(self) -> tuple[str, ...]:
        """The capabilities the extension declares."""

    def default_validators(self) -> tuple[str, ...]:
        """The identifiers of the validators the profile enables by default."""

    def default_commands(self) -> dict[str, tuple[str, ...]]:
        """The commands the profile uses by default, by name."""

    def default_policies(self) -> dict[str, Any]:
        """The policies the profile sets by default."""


class Validator(Protocol):
    def detect(self, context: dict[str, Any]) -> bool:
        """Detect the project in ``workspace`` (or whether the validator applies to ``context``)."""

    def execute(self, context: dict[str, Any]) -> Any:
        """Run the validator on ``context`` and return its raw result."""

    def parse(self, raw_result: Any) -> Any:
        """Parse the raw result of the validator."""

    def normalize(self, parsed_result: Any) -> NormalizedResult:
        """Normalize the parsed result into a ``NormalizedResult``."""


class AgentProvider(Protocol):
    def capabilities(self) -> tuple[str, ...]:
        """The capabilities the extension declares."""

    def start(self, request: dict[str, Any]) -> str:
        """Start a session for ``request`` and return its identifier."""

    def resume(self, session: str) -> None:
        """Resume the session."""

    def cancel(self, session: str) -> None:
        """Cancel the session."""

    def collect_events(self, session: str) -> list[dict[str, Any]]:
        """Return the events the session produced."""

    def collect_usage(self, session: str) -> dict[str, Any]:
        """Return the resource usage of the session."""


class Reporter(Protocol):
    def render(self, execution: dict[str, Any], format: str) -> bytes:
        """Render ``execution`` in ``format`` as bytes."""
