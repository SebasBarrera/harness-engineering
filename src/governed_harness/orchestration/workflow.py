"""The phase graph of a workflow (#3).

Under ``governance.enforceWorkflow`` the engine asks this graph which phase follows a passed
one, whether a phase may start (every ``dependsOn`` phase passed), which conditions a passed
attempt must meet (the ``exitGate`` and the condition of the transition it takes) and which
work may overlap (``parallelizable``). The nine normative phases and their order stay fixed
(``WorkflowDefinition.validate_graph``); the graph only orders, gates and groups them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from governed_harness.configuration.models import (
    ValidatorDefinition,
    WorkflowDefinition,
    WorkflowPhaseDefinition,
    WorkflowTransitionDefinition,
)
from governed_harness.configuration.workflow_rules import canonical_condition
from governed_harness.domain.enums import PhaseId

WRITE_CAPABILITY = "filesystem.write"


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

    # ----- ordering (dependsOn) ----------------------------------------------------------
    def order(self) -> tuple[PhaseId, ...]:
        """The phases in an order that respects ``dependsOn``: each phase comes after its
        dependencies and, among the phases that are ready, the declared order decides (so the
        result is deterministic). Raises ``ValueError`` on a cycle or an unknown dependency."""
        declared = [phase.phase_id for phase in self.definition.phases]
        remaining = {phase_id: set(self._phases[phase_id].depends_on) for phase_id in declared}
        for phase_id, needs in remaining.items():
            unknown = needs - set(declared)
            if unknown:
                names = ", ".join(sorted(item.value for item in unknown))
                raise ValueError(f"{phase_id.value} depends on unknown phase(s) {names}")
        ordered: list[PhaseId] = []
        while remaining:
            ready = [phase_id for phase_id in declared if remaining.get(phase_id) == set()]
            if not ready:
                names = ", ".join(sorted(item.value for item in remaining))
                raise ValueError(f"dependsOn has a cycle among {names}")
            chosen = ready[0]
            ordered.append(chosen)
            del remaining[chosen]
            for needs in remaining.values():
                needs.discard(chosen)
        return tuple(ordered)

    def next_phase(self, phase_id: PhaseId) -> PhaseId | None:
        """The phase that may start after ``phase_id`` passed (``None`` after the last)."""
        order = self.order()
        index = order.index(phase_id)
        return order[index + 1] if index + 1 < len(order) else None

    def unmet_dependencies(
        self, phase_id: PhaseId, passed: Collection[PhaseId]
    ) -> tuple[PhaseId, ...]:
        """The ``dependsOn`` phases of ``phase_id`` that are not in ``passed``."""
        return tuple(item for item in self.dependencies(phase_id) if item not in passed)

    def ancestors(self, phase_id: PhaseId) -> frozenset[PhaseId]:
        """Every phase ``phase_id`` depends on, directly or through another phase."""
        seen: set[PhaseId] = set()
        pending = list(self.dependencies(phase_id))
        while pending:
            item = pending.pop()
            if item in seen or item not in self._phases:
                continue
            seen.add(item)
            pending.extend(self.dependencies(item))
        return frozenset(seen)

    def transition(self, source: PhaseId, target: PhaseId) -> WorkflowTransitionDefinition | None:
        return self._transitions.get((source, target))

    # ----- exit gates ----------------------------------------------------------------------
    def exit_conditions(self, phase_id: PhaseId) -> tuple[str, ...]:
        """The conditions a passed attempt of ``phase_id`` must meet, without repeats: its
        ``exitGate`` and the condition of the transition to the next phase (the built-in
        DECISION names ``decision_recorded`` and ``decision_approved``, one check)."""
        names = [self._phases[phase_id].exit_gate]
        target = self.next_phase(phase_id)
        if target is not None:
            transition = self.transition(phase_id, target)
            if transition is not None:
                names.append(transition.condition)
        return tuple(dict.fromkeys(canonical_condition(name) for name in names))

    # ----- parallelism ---------------------------------------------------------------------
    def independent(self, first: PhaseId, second: PhaseId) -> bool:
        """Neither phase depends on the other, directly or through another phase."""
        return (
            first != second
            and first not in self.ancestors(second)
            and second not in self.ancestors(first)
        )

    def may_overlap(self, first: PhaseId, second: PhaseId) -> bool:
        """Two phases may run at once only when both are parallelizable, independent and
        read-only: a run has one workspace, so a phase that may write it never overlaps."""
        one, other = self._phases[first], self._phases[second]
        return (
            one.parallelizable
            and other.parallelizable
            and WRITE_CAPABILITY not in one.allowed_capabilities
            and WRITE_CAPABILITY not in other.allowed_capabilities
            and self.independent(first, second)
        )

    def parallel_groups(self) -> tuple[tuple[PhaseId, ...], ...]:
        """The phases in ``order()``, consecutive ones grouped when every pair may overlap."""
        groups: list[list[PhaseId]] = []
        for phase_id in self.order():
            if groups and all(self.may_overlap(item, phase_id) for item in groups[-1]):
                groups[-1].append(phase_id)
            else:
                groups.append([phase_id])
        return tuple(tuple(group) for group in groups)

    def runs_parallel_work(self, phase_id: PhaseId) -> bool:
        """Whether the read-only work inside the phase (its parallel-safe validators) may run
        at once: the phase is parallelizable and does not write the workspace."""
        phase = self._phases[phase_id]
        return phase.parallelizable and WRITE_CAPABILITY not in phase.allowed_capabilities


def validator_batches(
    definitions: Iterable[ValidatorDefinition], *, parallel: bool
) -> tuple[tuple[ValidatorDefinition, ...], ...]:
    """The validators of a VERIFICATION in their declared order, consecutive validators
    declared ``parallelSafe`` grouped into one batch when ``parallel`` holds; every other
    validator is a batch of its own. The grouping depends only on the declarations."""
    batches: list[list[ValidatorDefinition]] = []
    previous_safe = False
    for definition in definitions:
        safe = parallel and bool(definition.parallel_safe)
        if safe and previous_safe:
            batches[-1].append(definition)
        else:
            batches.append([definition])
        previous_safe = safe
    return tuple(tuple(batch) for batch in batches)
