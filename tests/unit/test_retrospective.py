from __future__ import annotations

from governed_harness.domain.enums import ActorType, MetricQuality
from governed_harness.domain.models import Actor, Provenance
from governed_harness.retrospective import RetrospectiveEngine
from governed_harness.telemetry import MetricValue


def metric(name: str, value: int) -> MetricValue:
    return MetricValue(name, value, "count", MetricQuality.OBSERVED, name, "test")


def test_retrospective_never_auto_applies() -> None:
    metrics = {
        "validation.non_passed": metric("validation.non_passed", 3),
        "correction.cycles": metric("correction.cycles", 1),
        "implementation.attempts": metric("implementation.attempts", 2),
        "review.cycles": metric("review.cycles", 2),
        "changeset.files": metric("changeset.files", 15),
    }
    retrospective = RetrospectiveEngine().generate(
        execution_id="run_1",
        metrics=metrics,
        evidence_refs=("artifact://sha256/abc",),
        provenance=Provenance(
            actor=Actor(actor_type=ActorType.HARNESS, actor_id="harness.core"), core_version="test"
        ),
    )
    assert retrospective.applied_automatically is False
    assert len(retrospective.recommendations) >= 3
    assert all(item.requires_human_review for item in retrospective.recommendations)
