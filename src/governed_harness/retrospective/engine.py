from __future__ import annotations

from governed_harness.domain.enums import ActorType
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    Provenance,
    Recommendation,
    Retrospective,
    RetrospectiveObservation,
)
from governed_harness.telemetry.metrics import MetricValue


class RetrospectiveEngine:
    """Deterministic recommendation engine. It never mutates policies or configuration."""

    def generate(
        self,
        *,
        execution_id: str,
        metrics: dict[str, MetricValue],
        evidence_refs: tuple[str, ...],
        provenance: Provenance,
    ) -> Retrospective:
        observations: list[RetrospectiveObservation] = []
        recommendations: list[Recommendation] = []

        def observe(statement: str) -> str:
            observation_id = new_id("observation")
            observations.append(
                RetrospectiveObservation(
                    observation_id=observation_id,
                    statement=statement,
                    evidence_refs=evidence_refs,
                )
            )
            return observation_id

        non_passed = int(metrics["validation.non_passed"].value or 0)
        corrections = int(metrics["correction.cycles"].value or 0)
        attempts = int(metrics["implementation.attempts"].value or 0)
        reviews = int(metrics["review.cycles"].value or 0)
        files = int(metrics["changeset.files"].value or 0)
        observe(f"The execution recorded {attempts} implementation attempt(s).")
        observe(f"The execution recorded {non_passed} non-passed validation result(s).")
        observe(f"The execution recorded {corrections} human-authorized correction cycle(s).")
        if non_passed >= 2:
            recommendations.append(
                Recommendation(
                    recommendation_id=new_id("recommendation"),
                    category="validation",
                    statement="Run the fastest deterministic validators earlier in implementation.",
                    rationale="Multiple validation failures increase delayed feedback and retrabajo.",
                    evidence_refs=evidence_refs,
                    confidence=min(0.95, 0.55 + 0.1 * non_passed),
                    risk="LOW",
                )
            )
        if corrections >= 1:
            recommendations.append(
                Recommendation(
                    recommendation_id=new_id("recommendation"),
                    category="specification",
                    statement="Review whether acceptance criteria can be made more discriminating before implementation.",
                    rationale="A correction cycle occurred after review and decision evidence had been produced.",
                    evidence_refs=evidence_refs,
                    confidence=0.70,
                    risk="LOW",
                )
            )
        if files >= 12:
            recommendations.append(
                Recommendation(
                    recommendation_id=new_id("recommendation"),
                    category="scope",
                    statement="Consider splitting future tasks with a similar ChangeSet size into independently verifiable units.",
                    rationale=f"The ChangeSet touched {files} files, increasing review and traceability burden.",
                    evidence_refs=evidence_refs,
                    confidence=0.65,
                    risk="MEDIUM",
                )
            )
        if reviews > 1:
            recommendations.append(
                Recommendation(
                    recommendation_id=new_id("recommendation"),
                    category="review",
                    statement="Inspect recurring review findings before changing any rule or prompt.",
                    rationale="The independent review phase was repeated, which may indicate recurrent issues or expected correction.",
                    evidence_refs=evidence_refs,
                    confidence=0.60,
                    risk="MEDIUM",
                )
            )
        if not recommendations:
            recommendations.append(
                Recommendation(
                    recommendation_id=new_id("recommendation"),
                    category="observation",
                    statement="Preserve the current controls and collect additional executions before proposing a rule change.",
                    rationale="This single execution does not provide enough recurring evidence for a structural change.",
                    evidence_refs=evidence_refs,
                    confidence=0.80,
                    risk="LOW",
                )
            )
        actor = Actor(actor_type=ActorType.HARNESS, actor_id="harness.retrospective", version="1")
        return Retrospective(
            retrospective_id=new_id("retro"),
            execution_id=execution_id,
            observations=tuple(observations),
            recommendations=tuple(recommendations),
            provenance=provenance.model_copy(update={"actor": actor}),
        )
