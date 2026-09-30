from __future__ import annotations

from governed_harness.configuration.models import WorkflowDefinition, WorkflowPhaseDefinition
from governed_harness.domain.enums import PhaseId


class WorkflowGraph:
    def __init__(self, definition: WorkflowDefinition) -> None:
        self.definition = definition
        self._phases = {phase.phase_id: phase for phase in definition.phases}
        self._transitions = {(item.source, item.target): item for item in definition.transitions}

    def phase(self, phase_id: PhaseId) -> WorkflowPhaseDefinition:
        return self._phases[phase_id]

    def next_primary(self, phase_id: PhaseId) -> PhaseId | None:
        primary = [phase.phase_id for phase in self.definition.phases]
        index = primary.index(phase_id)
        return primary[index + 1] if index + 1 < len(primary) else None

    def dependencies(self, phase_id: PhaseId) -> tuple[PhaseId, ...]:
        return self._phases[phase_id].depends_on

    def transition_allowed(self, source: PhaseId, target: PhaseId) -> bool:
        return (source, target) in self._transitions
