"""The workflow graph, its exit conditions and the rules of governance.enforceWorkflow (#3)."""

from __future__ import annotations

import pytest

from governed_harness.configuration.declared import DECLARATIVE_SETTINGS
from governed_harness.configuration.loader import load_builtin_workflow
from governed_harness.configuration.models import (
    GovernanceConfig,
    ValidatorDefinition,
    WorkflowDefinition,
    WorkflowPhaseDefinition,
)
from governed_harness.configuration.workflow_rules import (
    CONDITION_ALIASES,
    EXIT_GATE_CONDITIONS,
    canonical_condition,
    enforced_workflow_errors,
    validate_enforced_workflow,
)
from governed_harness.domain.enums import PhaseId
from governed_harness.domain.errors import ConfigurationError
from governed_harness.orchestration import PHASE_ORDER, WorkflowGraph
from governed_harness.orchestration.exit_gates import ExitGateEvaluator
from governed_harness.orchestration.workflow import validator_batches


def default() -> WorkflowDefinition:
    return load_builtin_workflow("default_development")


def with_phase(
    workflow: WorkflowDefinition, phase_id: PhaseId, **update: object
) -> WorkflowDefinition:
    phases = tuple(
        item.model_copy(update=update) if item.phase_id is phase_id else item
        for item in workflow.phases
    )
    return workflow.model_copy(update={"phases": phases})


def phase(workflow: WorkflowDefinition, phase_id: PhaseId) -> WorkflowPhaseDefinition:
    return next(item for item in workflow.phases if item.phase_id is phase_id)


def test_default_graph_keeps_the_normative_order() -> None:
    graph = WorkflowGraph(default())
    assert graph.order() == PHASE_ORDER
    assert graph.next_phase(PhaseId.INTENT) is PhaseId.DISCOVERY
    assert graph.next_phase(PhaseId.DECISION) is PhaseId.CLOSURE
    assert graph.next_phase(PhaseId.CLOSURE) is None
    assert graph.ancestors(PhaseId.VERIFICATION) == frozenset(PHASE_ORDER[:5])


def test_unmet_dependencies() -> None:
    graph = WorkflowGraph(default())
    assert graph.unmet_dependencies(PhaseId.INTENT, ()) == ()
    assert graph.unmet_dependencies(PhaseId.PLANNING, {PhaseId.INTENT}) == (PhaseId.SPECIFICATION,)
    assert graph.unmet_dependencies(PhaseId.PLANNING, {PhaseId.SPECIFICATION}) == ()


def test_dependencies_decide_the_order_and_a_cycle_is_refused() -> None:
    # The graph orders by dependsOn (the resolver refuses a dependency on a later phase, so
    # under governance.enforceWorkflow the order stays the normative one).
    moved = with_phase(default(), PhaseId.DISCOVERY, depends_on=(PhaseId.SPECIFICATION,))
    moved = with_phase(moved, PhaseId.SPECIFICATION, depends_on=(PhaseId.INTENT,))
    order = WorkflowGraph(moved).order()
    assert order.index(PhaseId.SPECIFICATION) < order.index(PhaseId.DISCOVERY)
    cycle = WorkflowGraph(with_phase(default(), PhaseId.INTENT, depends_on=(PhaseId.DISCOVERY,)))
    with pytest.raises(ValueError, match="cycle"):
        cycle.order()


def test_exit_conditions_reconcile_the_decision_gate() -> None:
    graph = WorkflowGraph(default())
    # DECISION names decision_recorded as its gate and decision_approved on its transition.
    assert phase(default(), PhaseId.DECISION).exit_gate == "decision_recorded"
    assert graph.exit_conditions(PhaseId.DECISION) == ("decision_approved",)
    assert graph.exit_conditions(PhaseId.VERIFICATION) == ("verification_passed",)
    assert graph.exit_conditions(PhaseId.CLOSURE) == ("run_closed",)
    assert canonical_condition("decision_recorded") == "decision_approved"
    assert canonical_condition("plan_authorized") == "plan_authorized"


def test_every_condition_has_a_check() -> None:
    names = {canonical_condition(item.exit_gate) for item in default().phases}
    assert names <= set(EXIT_GATE_CONDITIONS)
    assert set(CONDITION_ALIASES.values()) <= set(EXIT_GATE_CONDITIONS)
    evaluator = ExitGateEvaluator(None)  # type: ignore[arg-type]
    assert set(evaluator._checks) == set(EXIT_GATE_CONDITIONS)


def test_parallel_groups_need_independent_read_only_phases() -> None:
    graph = WorkflowGraph(default())
    # The built-in phases form a chain: nothing may overlap.
    assert all(len(group) == 1 for group in graph.parallel_groups())
    assert graph.runs_parallel_work(PhaseId.VERIFICATION)
    assert not graph.runs_parallel_work(PhaseId.IMPLEMENTATION)
    independent = with_phase(
        default(), PhaseId.INDEPENDENT_REVIEW, depends_on=(PhaseId.IMPLEMENTATION,)
    )
    graph = WorkflowGraph(independent)
    assert graph.may_overlap(PhaseId.VERIFICATION, PhaseId.INDEPENDENT_REVIEW)
    assert (PhaseId.VERIFICATION, PhaseId.INDEPENDENT_REVIEW) in graph.parallel_groups()
    # A phase that may write the workspace never overlaps.
    writing = with_phase(
        independent,
        PhaseId.INDEPENDENT_REVIEW,
        allowed_capabilities=("filesystem.read", "filesystem.write"),
    )
    assert not WorkflowGraph(writing).may_overlap(PhaseId.VERIFICATION, PhaseId.INDEPENDENT_REVIEW)


def test_validator_batches_group_consecutive_parallel_safe_validators() -> None:
    def item(validator_id: str, safe: bool | None) -> ValidatorDefinition:
        return ValidatorDefinition.model_validate(
            {"id": validator_id, "command": ["true"], "parallelSafe": safe}
        )

    validators = [
        item("a", True),
        item("b", True),
        item("c", None),
        item("d", True),
        item("e", True),
        item("f", False),
    ]
    batches = validator_batches(validators, parallel=True)
    assert [[v.validator_id for v in batch] for batch in batches] == [
        ["a", "b"],
        ["c"],
        ["d", "e"],
        ["f"],
    ]
    sequential = validator_batches(validators, parallel=False)
    assert all(len(batch) == 1 for batch in sequential)


def test_the_default_workflow_can_be_enforced() -> None:
    assert enforced_workflow_errors(default()) == []
    validate_enforced_workflow(default())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"exit_gate": "looks_fine"}, "unknown exitGate 'looks_fine'"),
        ({"depends_on": (PhaseId.CLOSURE,)}, "dependsOn CLOSURE is not an earlier phase"),
        (
            {"parallelizable": True, "allowed_capabilities": ("filesystem.write",)},
            "parallelizable but allowed filesystem.write",
        ),
    ],
)
def test_a_workflow_that_cannot_be_enforced(change: dict[str, object], message: str) -> None:
    workflow = with_phase(default(), PhaseId.PLANNING, **change)
    assert any(message in item for item in enforced_workflow_errors(workflow))
    with pytest.raises(ConfigurationError, match="governance.enforceWorkflow"):
        validate_enforced_workflow(workflow)


def test_transitions_must_be_known_and_complete() -> None:
    workflow = default()
    transitions = tuple(
        item.model_copy(update={"condition": "whatever"})
        if item.source is PhaseId.SPECIFICATION
        else item
        for item in workflow.transitions
        if item.source is not PhaseId.INTENT
    )
    errors = enforced_workflow_errors(workflow.model_copy(update={"transitions": transitions}))
    assert any("unknown condition 'whatever'" in item for item in errors)
    assert "no transition from INTENT to DISCOVERY" in errors


def test_the_key_is_left_out_while_absent() -> None:
    assert "enforceWorkflow" not in GovernanceConfig().model_dump(by_alias=True)
    assert GovernanceConfig.model_validate({"enforceWorkflow": True}).model_dump(by_alias=True) == {
        "enforceWorkflow": True
    }
    definition = ValidatorDefinition.model_validate({"id": "x", "command": ["true"]})
    assert "parallelSafe" not in definition.model_dump(by_alias=True)


def test_enforced_settings_are_not_declarative() -> None:
    assert "workflow.phases[].dependsOn" not in DECLARATIVE_SETTINGS
    assert "workflow.phases[].parallelizable" not in DECLARATIVE_SETTINGS
    assert "runtime.maxParallel" not in DECLARATIVE_SETTINGS
    # Applied under governance.phaseCapabilities (#4).
    assert "workflow.phases[].allowedCapabilities" not in DECLARATIVE_SETTINGS
