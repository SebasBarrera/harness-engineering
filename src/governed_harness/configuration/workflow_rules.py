"""The workflow conditions the engine evaluates under ``governance.enforceWorkflow`` (#3).

The workflow names an ``exitGate`` per phase and a ``condition`` per transition. Under
``governance.enforceWorkflow`` the engine evaluates them from the run's records after a phase
attempt returns ``PASSED``, so a name it does not know is a configuration error when the
configuration is resolved, not a silent pass during a run. Without the key the names stay
labels, as in 1.0.0.
"""

from __future__ import annotations

from governed_harness.configuration.models import WorkflowDefinition
from governed_harness.domain.errors import ConfigurationError

EXIT_GATE_CONDITIONS: dict[str, str] = {
    "intent_complete": "the run's task has acceptance criteria and INTENT recorded it",
    "discovery_sufficient": "DISCOVERY recorded the baseline snapshot and revision",
    "specification_approved": "SPECIFICATION froze the acceptance contract and the run's task "
    "still produces it",
    "plan_authorized": "PLANNING stored the run's plan (a PASSED PLANNING is authorised: a plan "
    "that needs a person's approval keeps PLANNING BLOCKED)",
    "candidate_changeset": "the run's current ChangeSet is recorded and is not empty (unless "
    "policies.allowEmptyChangeSet)",
    "verification_passed": "every mandatory validation this VERIFICATION attempt recorded for the "
    "current ChangeSet passed",
    "review_complete": "the independent review recorded its result for the current ChangeSet",
    "decision_approved": "the current human decision is APPROVE or APPROVE_EXCEPTION, bound to "
    "the current ChangeSet digest and not expired",
    "run_closed": "CLOSURE recorded run.closed for the current ChangeSet",
}
"""Every condition the engine evaluates, with what it checks."""

CONDITION_ALIASES: dict[str, str] = {"decision_recorded": "decision_approved"}
"""Names accepted for a condition. The built-in DECISION phase names ``decision_recorded`` as
its exit gate while its transition to CLOSURE names ``decision_approved``: an attempt of
DECISION passes only on an approval (``REQUEST_CHANGES`` returns to IMPLEMENTATION and
``REJECT`` ends the run through ``harness gate decide``, never through a passed attempt), so
both names are the same check."""

DECISION_TRANSITION_CONDITIONS: frozenset[str] = frozenset({"authorized_correction"})
"""Conditions of transitions that ``harness gate decide`` takes, not a phase attempt."""


def canonical_condition(name: str) -> str:
    """The condition a gate or transition name stands for."""
    return CONDITION_ALIASES.get(name, name)


def known_condition(name: str) -> bool:
    return canonical_condition(name) in EXIT_GATE_CONDITIONS


def enforced_workflow_errors(workflow: WorkflowDefinition) -> list[str]:
    """What makes a workflow unusable under ``governance.enforceWorkflow``: unknown exit gates
    or transition conditions, a dependency on a phase that does not come earlier (the order of
    the nine phases is fixed, so it could never be met), a phase that is not followed by a
    declared transition to the next one, and a parallelizable phase that writes the workspace
    (there is one workspace per run)."""
    return [
        *_phase_errors(workflow),
        *_transition_errors(workflow),
        *_missing_transitions(workflow),
    ]


def _phase_errors(workflow: WorkflowDefinition) -> list[str]:
    errors: list[str] = []
    order = [phase.phase_id for phase in workflow.phases]
    for position, phase in enumerate(workflow.phases):
        if not known_condition(phase.exit_gate):
            errors.append(
                f"phase {phase.phase_id.value}: unknown exitGate {phase.exit_gate!r}; known: "
                f"{', '.join(sorted({*EXIT_GATE_CONDITIONS, *CONDITION_ALIASES}))}"
            )
        errors.extend(
            f"phase {phase.phase_id.value}: dependsOn {dependency.value} is not an "
            "earlier phase of the workflow"
            for dependency in phase.depends_on
            if dependency not in order[:position]
        )
        if phase.parallelizable and "filesystem.write" in phase.allowed_capabilities:
            errors.append(
                f"phase {phase.phase_id.value}: parallelizable but allowed filesystem.write; "
                "a run has one workspace"
            )
    return errors


def _transition_errors(workflow: WorkflowDefinition) -> list[str]:
    return [
        f"transition {transition.source.value} -> {transition.target.value}: unknown "
        f"condition {transition.condition!r}"
        for transition in workflow.transitions
        if transition.condition not in DECISION_TRANSITION_CONDITIONS
        and not known_condition(transition.condition)
    ]


def _missing_transitions(workflow: WorkflowDefinition) -> list[str]:
    order = [phase.phase_id for phase in workflow.phases]
    declared = {(item.source, item.target) for item in workflow.transitions}
    return [
        f"no transition from {source.value} to {target.value}"
        for source, target in zip(order, order[1:], strict=False)
        if (source, target) not in declared
    ]


def validate_enforced_workflow(workflow: WorkflowDefinition) -> None:
    """Raise ``ConfigurationError`` when the workflow cannot be enforced."""
    errors = enforced_workflow_errors(workflow)
    if errors:
        raise ConfigurationError(
            f"workflow {workflow.workflow_id} cannot be enforced "
            f"(governance.enforceWorkflow): {'; '.join(errors)}"
        )
