"""What the ladder helpers need from the :class:`VerificationLadder` that owns them (#55).

The helpers (intake, environment preflight, mutation-lite, delivery) type their owner against
this protocol instead of importing ``ladder``, so ``ladder`` imports them without a module cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from governed_harness.configuration.ladder import ProfileVerification
    from governed_harness.configuration.models import ProjectConfiguration
    from governed_harness.domain.models import CertificationRecord, Execution
    from governed_harness.ladder.capabilities import CapabilityStatus
    from governed_harness.orchestration.engine_types import EngineServices
    from governed_harness.orchestration.hosts import EngineHost


class LadderHost(Protocol):
    @property
    def engine(self) -> EngineHost: ...

    @property
    def s(self) -> EngineServices: ...

    @property
    def project(self) -> ProjectConfiguration: ...

    def capabilities(self, execution: Execution) -> list[CapabilityStatus]: ...

    def profile_verifications(self) -> list[tuple[str, ProfileVerification]]: ...

    def latest_certification(
        self, execution_id: str, digest: str | None
    ) -> CertificationRecord | None: ...


__all__ = ["LadderHost"]
