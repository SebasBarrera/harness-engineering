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
_Spec = tuple[RetrospectiveCause | None, str, str, str, float, Risk]
"""A recommendation before it has an id: (cause, category, statement, rationale, confidence,
risk)."""


def _observation(statement: str, evidence_refs: tuple[str, ...]) -> RetrospectiveObservation:
    return RetrospectiveObservation(
        observation_id=new_id("observation"),
        statement=statement,
        evidence_refs=evidence_refs,
    )


def _recommendation(evidence_refs: tuple[str, ...], spec: _Spec) -> Recommendation:
    cause, category, statement, rationale, confidence, risk = spec
    return Recommendation(
        recommendation_id=new_id("recommendation"),
        category=category,
        statement=statement,
        rationale=rationale,
        evidence_refs=(*evidence_refs, *(cause.evidence_refs if cause else ())),
        confidence=round(min(0.95, confidence), 2),
        risk=risk,
    )


def _cause_statement(cause: RetrospectiveCause) -> str:
    where = (
        " (attempt " + ", ".join(str(item) for item in cause.attempts) + ")"
        if cause.attempts
        else ""
    )
    return (
        f"{cause.reason_code} {cause.subject}: {cause.effect}, {cause.occurrences} time(s){where}."
    )


def _blocking_finding_spec(cause: RetrospectiveCause, excepted: set[str]) -> _Spec:
    precision = cause.subject in excepted
    return (
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


def _cause_spec(cause: RetrospectiveCause, excepted: set[str]) -> _Spec | None:
    """The recommendation a cause leads to, if any."""
    code = cause.reason_code
    if code.startswith("MANDATORY_VALIDATOR_"):
        return (
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
    if code == "BLOCKING_FINDING":
        return _blocking_finding_spec(cause, excepted)
    if code == "CHANGES_REQUESTED":
        return (
            cause,
            "specification",
            "Review whether acceptance criteria can be made more discriminating before "
            "implementation.",
            f"A person requested changes {cause.occurrences} time(s) after the evidence had "
            "been produced.",
            0.70,
            "LOW",
        )
    if code == "REJECTED":
        return (
            cause,
            "specification",
            "Compare the rejected change with the task's acceptance criteria before the next "
            "run: they did not exclude what was rejected.",
            "The run was rejected by a human decision.",
            0.70,
            "MEDIUM",
        )
    if code == "PROVIDER_TRANSIENT_FAILURE":
        return (
            cause,
            "provider",
            f"Check the limits and availability of provider {cause.subject}: its call was "
            f"repeated {cause.occurrences} time(s) after transient failures.",
            "Reason code PROVIDER_TRANSIENT_FAILURE.",
            0.60,
            "LOW",
        )
    if code.startswith("POST_RUN_"):
        kind = code.removeprefix("POST_RUN_").lower()
        return (
            cause,
            "outcome",
            f"Review which controls let this change pass: a {kind} ({cause.subject}) was "
            "linked to it after the run.",
            "An outcome recorded after the run points at the change this run approved.",
            0.75,
            "MEDIUM",
        )
    return None


def _run_specs(files: int, reviews: int) -> list[_Spec]:
    """Recommendations from the size of the ChangeSet and the review cycles."""
    specs: list[_Spec] = []
    if files >= 12:
        specs.append(
            (
                None,
                "scope",
                "Consider splitting future tasks with a similar ChangeSet size into "
                "independently verifiable units.",
                f"The ChangeSet touched {files} files, increasing review and traceability burden.",
                0.65,
                "MEDIUM",
            )
        )
    if reviews > 1:
        specs.append(
            (
                None,
                "review",
                "Inspect recurring review findings before changing any rule or prompt.",
                "The independent review phase was repeated.",
                0.60,
                "MEDIUM",
            )
        )
    return specs


_NO_CAUSE: _Spec = (
    None,
    "observation",
    "Preserve the current controls and collect additional executions before proposing a "
    "rule change.",
    "No cause stopped or redirected this run.",
    0.80,
    "LOW",
)


def causal_retrospective(
    *,
    execution_id: str,
    metrics: dict[str, MetricValue],
    evidence_refs: tuple[str, ...],
    provenance: Provenance,
    analysis: CauseAnalysis,
    trigger: RetrospectiveTrigger,
) -> Retrospective:
    attempts = int(metrics["implementation.attempts"].value or 0)
    files = int(metrics["changeset.files"].value or 0)
    reviews = int(metrics["review.cycles"].value or 0)
    statements = [
        f"Retrospective {trigger} after {attempts} implementation attempt(s).",
        *(_cause_statement(cause) for cause in analysis.causes),
        *analysis.notes,
    ]
    observations = [_observation(statement, evidence_refs) for statement in statements]
    excepted = {
        cause.subject for cause in analysis.causes if cause.reason_code == "EXCEPTION_GRANTED"
    }
    specs = [spec for spec in (_cause_spec(cause, excepted) for cause in analysis.causes) if spec]
    specs += _run_specs(files, reviews)
    recommendations = [_recommendation(evidence_refs, spec) for spec in specs or [_NO_CAUSE]]
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
