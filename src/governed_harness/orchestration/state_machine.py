from __future__ import annotations

from dataclasses import dataclass

from governed_harness.domain.enums import PhaseId, ResultStatus


class InvalidTransition(ValueError):
    pass


PHASE_ORDER: tuple[PhaseId, ...] = (
    PhaseId.INTENT,
    PhaseId.DISCOVERY,
    PhaseId.SPECIFICATION,
    PhaseId.PLANNING,
    PhaseId.IMPLEMENTATION,
    PhaseId.VERIFICATION,
    PhaseId.INDEPENDENT_REVIEW,
    PhaseId.DECISION,
    PhaseId.CLOSURE,
)


@dataclass(frozen=True)
class TransitionDecision:
    source: PhaseId
    target: PhaseId
    invalidated: tuple[PhaseId, ...] = ()


class NormativeStateMachine:
    def advance(
        self, phase: PhaseId, result: ResultStatus, *, required: bool = True
    ) -> TransitionDecision:
        accepted = result is ResultStatus.PASSED or (
            not required and result is ResultStatus.NOT_APPLICABLE
        )
        if not accepted:
            raise InvalidTransition(f"phase {phase} cannot advance with {result}")
        try:
            index = PHASE_ORDER.index(phase)
        except ValueError as error:
            raise InvalidTransition(f"phase is not in the primary workflow: {phase}") from error
        if index == len(PHASE_ORDER) - 1:
            raise InvalidTransition("CLOSURE has no next primary phase")
        return TransitionDecision(source=phase, target=PHASE_ORDER[index + 1])

    def authorize_correction(self, phase: PhaseId) -> TransitionDecision:
        if phase is not PhaseId.DECISION:
            raise InvalidTransition("correction may only be authorized from DECISION")
        return TransitionDecision(
            source=PhaseId.DECISION,
            target=PhaseId.IMPLEMENTATION,
            invalidated=(
                PhaseId.VERIFICATION,
                PhaseId.INDEPENDENT_REVIEW,
                PhaseId.DECISION,
                PhaseId.CLOSURE,
            ),
        )

    def authorize_verification_correction(self, phase: PhaseId) -> TransitionDecision:
        """The automatic correction of ``runtime.verificationCorrections``: a failed
        VERIFICATION returns to IMPLEMENTATION and its result stops counting, as after a
        REQUEST_CHANGES decision."""
        if phase is not PhaseId.VERIFICATION:
            raise InvalidTransition("an automatic correction may only start from VERIFICATION")
        return TransitionDecision(
            source=PhaseId.VERIFICATION,
            target=PhaseId.IMPLEMENTATION,
            invalidated=(
                PhaseId.VERIFICATION,
                PhaseId.INDEPENDENT_REVIEW,
                PhaseId.DECISION,
                PhaseId.CLOSURE,
            ),
        )

    def authorize_review_correction(self, phase: PhaseId) -> TransitionDecision:
        """Since 1.1 (``review.agentReview``, #38): blocking findings of the second reviewer
        return INDEPENDENT_REVIEW to IMPLEMENTATION within the correction budget."""
        if phase is not PhaseId.INDEPENDENT_REVIEW:
            raise InvalidTransition("a review correction may only start from INDEPENDENT_REVIEW")
        return TransitionDecision(
            source=PhaseId.INDEPENDENT_REVIEW,
            target=PhaseId.IMPLEMENTATION,
            invalidated=(
                PhaseId.VERIFICATION,
                PhaseId.INDEPENDENT_REVIEW,
                PhaseId.DECISION,
                PhaseId.CLOSURE,
            ),
        )

    def authorize_next_subtask(self, phase: PhaseId) -> TransitionDecision:
        """Since 1.1 (``planning.decomposition``, #39): a sub-task whose gate passed hands the
        workspace to the next sub-task's IMPLEMENTATION."""
        if phase is not PhaseId.VERIFICATION:
            raise InvalidTransition("the next sub-task may only start after VERIFICATION")
        return TransitionDecision(
            source=PhaseId.VERIFICATION,
            target=PhaseId.IMPLEMENTATION,
            invalidated=(PhaseId.INDEPENDENT_REVIEW, PhaseId.DECISION, PhaseId.CLOSURE),
        )

    def authorize_replanning(self, phase: PhaseId) -> TransitionDecision:
        """Since 1.1 (``planning.granularity: adaptive``, #39): a coarse attempt that failed
        its corrections returns to PLANNING to be decomposed."""
        if phase is not PhaseId.VERIFICATION:
            raise InvalidTransition("replanning may only start after VERIFICATION")
        return TransitionDecision(
            source=PhaseId.VERIFICATION,
            target=PhaseId.PLANNING,
            invalidated=(
                PhaseId.IMPLEMENTATION,
                PhaseId.VERIFICATION,
                PhaseId.INDEPENDENT_REVIEW,
                PhaseId.DECISION,
                PhaseId.CLOSURE,
            ),
        )

    @staticmethod
    def approval_is_current(approved_digest: str, current_digest: str) -> bool:
        return approved_digest == current_digest

    @staticmethod
    def can_resume(status: ResultStatus) -> bool:
        return status in {
            ResultStatus.PENDING,
            ResultStatus.RUNNING,
            ResultStatus.BLOCKED,
            ResultStatus.FAILED,
            ResultStatus.INCONCLUSIVE,
            ResultStatus.TIMED_OUT,
            ResultStatus.ERROR,
            ResultStatus.INTERRUPTED,
        }
