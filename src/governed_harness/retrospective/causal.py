"""Recommendations derived from causes (``retrospective.causal``); see ``causes``."""

from __future__ import annotations

from typing import Literal

from governed_harness.domain.enums import ActorType
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    Provenance,
    Recommendation,
    Retrospective,
    RetrospectiveCause,
    RetrospectiveObservation,
    RetrospectiveTrigger,
)
from governed_harness.retrospective.causes import CauseAnalysis
from governed_harness.telemetry.metrics import MetricValue

Risk = Literal["LOW", "MEDIUM", "HIGH"]


def causal_retrospective(
    *,
    execution_id: str,
    metrics: dict[str, MetricValue],
    evidence_refs: tuple[str, ...],
    provenance: Provenance,
    analysis: CauseAnalysis,
    trigger: RetrospectiveTrigger,
) -> Retrospective:
    observations: list[RetrospectiveObservation] = []
    recommendations: list[Recommendation] = []

    def observe(statement: str) -> None:
        observations.append(
            RetrospectiveObservation(
                observation_id=new_id("observation"),
                statement=statement,
                evidence_refs=evidence_refs,
            )
        )

    def recommend(
        cause: RetrospectiveCause | None,
        category: str,
        statement: str,
        rationale: str,
        confidence: float,
        risk: Risk,
    ) -> None:
        recommendations.append(
            Recommendation(
                recommendation_id=new_id("recommendation"),
                category=category,
                statement=statement,
                rationale=rationale,
                evidence_refs=(*evidence_refs, *(cause.evidence_refs if cause else ())),
                confidence=round(min(0.95, confidence), 2),
                risk=risk,
            )
        )

    attempts = int(metrics["implementation.attempts"].value or 0)
    files = int(metrics["changeset.files"].value or 0)
    reviews = int(metrics["review.cycles"].value or 0)
    observe(f"Retrospective {trigger} after {attempts} implementation attempt(s).")
    for cause in analysis.causes:
        where = (
            " (attempt " + ", ".join(str(item) for item in cause.attempts) + ")"
            if cause.attempts
            else ""
        )
        observe(
            f"{cause.reason_code} {cause.subject}: {cause.effect}, "
            f"{cause.occurrences} time(s){where}."
        )
    for note in analysis.notes:
        observe(note)
    excepted = {
        cause.subject for cause in analysis.causes if cause.reason_code == "EXCEPTION_GRANTED"
    }
    for cause in analysis.causes:
        code = cause.reason_code
        if code.startswith("MANDATORY_VALIDATOR_"):
            recommend(
                cause,
                f"validation:{cause.subject}",
                f"Run {cause.subject} before handing the change over: it stopped VERIFICATION "
                f"in {cause.occurrences} attempt(s).",
                f"Reason code {code} in attempt(s) "
                + ", ".join(str(item) for item in cause.attempts)
                + "; optional validators without effect on the gate are not counted.",
                0.6 + 0.1 * cause.occurrences,
                "LOW",
            )
        elif code == "BLOCKING_FINDING":
            precision = cause.subject in excepted
            recommend(
                cause,
                f"rule:{cause.subject}",
                (
                    f"Check whether rule {cause.subject} is precise: it failed the gate "
                    f"{cause.occurrences} time(s) and an exception was granted over it."
                )
                if precision
                else (
                    f"Address rule {cause.subject} before verification (task constraints or "
                    f"agent instructions): it failed the gate {cause.occurrences} time(s)."
                ),
                f"Reason code BLOCKING_FINDING for {cause.subject}.",
                0.65 + 0.1 * cause.occurrences,
                "MEDIUM" if precision else "LOW",
            )
        elif code == "CHANGES_REQUESTED":
            recommend(
                cause,
                "specification",
                "Review whether acceptance criteria can be made more discriminating before "
                "implementation.",
                f"A person requested changes {cause.occurrences} time(s) after the evidence had "
                "been produced.",
                0.70,
                "LOW",
            )
        elif code == "REJECTED":
            recommend(
                cause,
                "specification",
                "Compare the rejected change with the task's acceptance criteria before the next "
                "run: they did not exclude what was rejected.",
                "The run was rejected by a human decision.",
                0.70,
                "MEDIUM",
            )
        elif code == "PROVIDER_TRANSIENT_FAILURE":
            recommend(
                cause,
                "provider",
                f"Check the limits and availability of provider {cause.subject}: its call was "
                f"repeated {cause.occurrences} time(s) after transient failures.",
                "Reason code PROVIDER_TRANSIENT_FAILURE.",
                0.60,
                "LOW",
            )
        elif code.startswith("POST_RUN_"):
            kind = code.removeprefix("POST_RUN_").lower()
            recommend(
                cause,
                "outcome",
                f"Review which controls let this change pass: a {kind} ({cause.subject}) was "
                "linked to it after the run.",
                "An outcome recorded after the run points at the change this run approved.",
                0.75,
                "MEDIUM",
            )
    if files >= 12:
        recommend(
            None,
            "scope",
            "Consider splitting future tasks with a similar ChangeSet size into independently "
            "verifiable units.",
            f"The ChangeSet touched {files} files, increasing review and traceability burden.",
            0.65,
            "MEDIUM",
        )
    if reviews > 1:
        recommend(
            None,
            "review",
            "Inspect recurring review findings before changing any rule or prompt.",
            "The independent review phase was repeated.",
            0.60,
            "MEDIUM",
        )
    if not recommendations:
        recommend(
            None,
            "observation",
            "Preserve the current controls and collect additional executions before proposing a "
            "rule change.",
            "No cause stopped or redirected this run.",
            0.80,
            "LOW",
        )
    actor = Actor(actor_type=ActorType.HARNESS, actor_id="harness.retrospective", version="2")
    return Retrospective(
        retrospective_id=new_id("retro"),
        execution_id=execution_id,
        observations=tuple(observations),
        recommendations=tuple(recommendations),
        provenance=provenance.model_copy(update={"actor": actor}),
        trigger=trigger,
        causes=tuple(analysis.causes),
    )
