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
        }
