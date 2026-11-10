"""Disputed frozen acceptance tests (``verification.acceptanceTests.disputes: person``, #94).

The frozen acceptance tests are written by an agent call and approved by a person from their
digest; they can be wrong. In the wave 9 pilot a correct change failed VERIFICATION three times
against a test that expected ``b"Hellon"`` for ``b"Hello"`` and against inputs the clarified task
says must be rejected, and the only outcome the run had was to fail.

Under ``disputes: person`` the implement request tells the agent not to change a frozen test it
believes is wrong but to list it, with the reason, in its self-report
(``disputedAcceptanceTests``). When VERIFICATION then fails **only** on the frozen acceptance
tests (no frozen file was changed, and any other mandatory validator that failed, such as the
project's own test command, locates all its failures in the frozen files) and the self-report
of the implementation that produced the ChangeSet carries a dispute, the run waits for a person
(``BLOCKED``, an ``acceptance-dispute`` entry in the inbox) instead of failing:

* ``UPHOLD``: the tests stand. VERIFICATION runs again, fails as before and the run goes on to
  the correction cycles or stops, as it would have without the dispute; the next implement
  request says the person upheld the tests.
* ``REVISE``: the frozen files are removed and the run returns to SPECIFICATION, where the
  acceptance call writes the tests again knowing what was disputed, a person approves them from
  their digest and the harness freezes them (the earlier ones stay recorded with their digests).
  The plan and the implementation stand: the run goes from SPECIFICATION straight back to
  VERIFICATION of the same change.

The decision is a person's, bound to the digest of the dispute (the ChangeSet, the approved tests
and what the agent disputed). A self-report is the agent's word (``REPORTED``): it opens the
wait, it never changes a test."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from governed_harness.domain.enums import ActorType, PhaseId, ResultStatus
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.domain.models import (
    Actor,
    AgentSelfReport,
    ChangeSet,
    Execution,
    Finding,
    ValidationResult,
    utc_now,
)
from governed_harness.evidence import sha256_json
from governed_harness.orchestration.acceptance import ACCEPTANCE_ID, AcceptanceTests
from governed_harness.orchestration.engine_types import PhaseOutcome

if TYPE_CHECKING:
    from governed_harness.orchestration.agent_results import AgentResults

DISPUTE_FLAG = "acceptdispute"
RESUME_FLAG = "acceptresume"
DISPUTE_KIND = "acceptance-dispute"
DISPUTE_NOTE = (
    "If you believe one of these tests is wrong (it contradicts the task or asserts a wrong "
    "value), do not change it and do not write code to satisfy it: list it in your self-report "
    'under disputedAcceptanceTests as [{"test": "<file or test id>", "reason": "<why>"}]. A '
    "person decides whether the test stands or is written again."
)
UPHELD_NOTE = (
    "A person reviewed your dispute of these tests and upheld them: they stand, make them pass."
)


class DisputeDecision(StrEnum):
    UPHOLD = "UPHOLD"
    REVISE = "REVISE"


def decide_command(execution_id: str, digest: str) -> str:
    return (
        f"harness acceptance dispute --run {execution_id} --decision <UPHOLD|REVISE> "
        f"--digest {digest}"
    )


class AcceptanceDisputes:
    def __init__(self, results: AgentResults, acceptance: AcceptanceTests) -> None:
        self.results = results
        self.acceptance = acceptance

    @property
    def enabled(self) -> bool:
        config = self.acceptance.config
        return bool(config and config.enabled and config.disputes_enabled)

    def _key(self, execution_id: str) -> str:
        return f"{DISPUTE_FLAG}:{execution_id}"

    def state(self, execution: Execution) -> dict[str, Any] | None:
        value = self.results.flag_json(self._key(execution.execution_id))
        return value if isinstance(value, dict) else None

    # ----- the implement request -------------------------------------------------------------
    def request_extra(self, execution: Execution) -> dict[str, Any]:
        """What the ``acceptanceTests`` block of the implement request adds: how to dispute a
        test and, after a person upheld a dispute, that the tests stand."""
        if not self.enabled:
            return {}
        extra: dict[str, Any] = {"dispute": DISPUTE_NOTE}
        state = self.state(execution)
        if state is not None and state.get("status") == "UPHELD":
            extra["upheld"] = {
                "tests": state["disputed"],
                "rationale": state.get("rationale"),
                "note": UPHELD_NOTE,
            }
        return extra

    # ----- VERIFICATION ----------------------------------------------------------------------
    def waiting(self, execution: Execution, change_set: ChangeSet) -> PhaseOutcome | None:
        """The wait of an open dispute of this ChangeSet, without running the validators
        again."""
        state = self.state(execution) if self.enabled else None
        if state is None or state.get("status") != "OPEN":
            return None
        if state.get("changeSetDigest") != change_set.digest:
            return None
        return self._blocked(execution, state)

    def _blocked(self, execution: Execution, state: dict[str, Any]) -> PhaseOutcome:
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"The agent disputes {len(state['disputed'])} frozen acceptance test(s) and only "
            "they fail; a person decides: "
            + decide_command(execution.execution_id, state["digest"]),
            (state["ref"],),
        )

    def open(
        self,
        execution: Execution,
        change_set: ChangeSet,
        failing: list[ValidationResult],
    ) -> PhaseOutcome | None:
        """Open a dispute when the frozen acceptance tests are the only mandatory validation
        that did not pass and the agent disputed them; ``None`` otherwise (the run fails as it
        did before)."""
        if not self.enabled or not self._only_acceptance(execution, failing):
            return None
        previous = self.state(execution)
        if previous is not None and previous.get("changeSetDigest") == change_set.digest:
            return None  # a person already decided the dispute of this ChangeSet
        report = self._report(execution, change_set)
        if report is None:
            return None
        acceptance = self.acceptance.state(execution) or {}
        disputed = [
            {"test": item.test, "reason": item.reason} for item in report.disputed_acceptance_tests
        ]
        record: dict[str, Any] = {
            "status": "OPEN",
            "changeSetDigest": change_set.digest,
            "acceptanceDigest": acceptance.get("digest"),
            "frozen": acceptance.get("frozen") or {},
            "disputed": disputed,
            "reportId": report.report_id,
            "invocationId": report.invocation_id,
            "validation": failing[0].summary,
        }
        record["digest"] = sha256_json(
            {key: record[key] for key in ("changeSetDigest", "acceptanceDigest", "disputed")}
        )
        ref = self.results.record_json(
            execution,
            PhaseId.VERIFICATION,
            record,
            kind=DISPUTE_KIND,
            summary=f"Acceptance tests disputed by the agent: {len(disputed)}",
        )
        state = {**record, "ref": ref}
        self.results.set_flag_json(self._key(execution.execution_id), state)
        self.results.s.events.append(
            execution.execution_id,
            "acceptance.dispute.opened",
            {
                "digest": record["digest"],
                "changeSetDigest": change_set.digest,
                "disputed": [item["test"] for item in disputed],
                "evidenceRef": ref,
            },
        )
        return self._blocked(execution, state)

    def _only_acceptance(self, execution: Execution, failing: list[ValidationResult]) -> bool:
        """Whether the failed frozen tests are all that failed, with every frozen file as it
        was approved (a changed frozen file is the agent's fault, not a dispute). Another
        mandatory validator that failed counts as the same failure only when every failure it
        locates is in a frozen file: the project's own test command also collects them."""
        frozen = (self.acceptance.state(execution) or {}).get("frozen") or {}
        if not frozen or self.acceptance.untouched(execution) != set(frozen):
            return False
        if any(item.status is not ResultStatus.FAILED for item in failing):
            return False
        others = [item for item in failing if item.validator_id != ACCEPTANCE_ID]
        if len(others) == len(failing):
            return False
        return all(self._explained(item, frozen) for item in others)

    def _explained(self, validation: ValidationResult, frozen: dict[str, str]) -> bool:
        """Whether a failed validation locates its failures, all of them in frozen files
        (needs parsed output, ``verification.outputParsers``)."""
        located: list[str] = []
        for finding_id in validation.finding_ids:
            try:
                finding = self.results.s.state.get("finding", finding_id, Finding)
            except NotFoundError:
                return False
            if finding.location is not None and finding.location.path:
                located.append(finding.location.path)
        return bool(located) and all(path in frozen for path in located)

    def _report(self, execution: Execution, change_set: ChangeSet) -> AgentSelfReport | None:
        """The self-report of the implementation that produced this ChangeSet, when it
        disputes a test."""
        reports = [
            item
            for item in self.results.s.state.list(
                "agent_self_report", AgentSelfReport, execution_id=execution.execution_id
            )
            if item.contrast is not None and item.contrast.change_set_digest == change_set.digest
        ]
        if not reports:
            return None
        latest = max(reports, key=lambda item: item.created_at)
        return latest if latest.disputed_acceptance_tests else None

    # ----- the decision (a person) -----------------------------------------------------------
    def decide(
        self,
        execution: Execution,
        *,
        decision: DisputeDecision,
        digest: str,
        actor: Actor,
        rationale: str,
    ) -> dict[str, Any]:
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a person decides a dispute of acceptance tests")
        state = self.state(execution)
        if state is None or state.get("status") != "OPEN":
            raise NotFoundError(
                f"run {execution.execution_id} has no open dispute of acceptance tests"
            )
        if state["digest"] != digest:
            raise PolicyViolationError("the digest does not match the dispute")
        if not rationale.strip():
            raise PolicyViolationError("a rationale is required")
        revise = decision is DisputeDecision.REVISE
        decided = {
            **state,
            "status": "REVISED" if revise else "UPHELD",
            "decidedBy": actor.actor_id,
            "rationale": rationale,
            "decidedAt": utc_now().isoformat(),
        }
        self.results.set_flag_json(self._key(execution.execution_id), decided)
        payload: dict[str, Any] = {"decision": decision.value, "digest": digest}
        if revise:
            payload["invalidatedPhases"] = self._revise(execution, decided)
        self.results.s.events.append(
            execution.execution_id, "acceptance.dispute.decided", payload, actor=actor
        )
        return decided

    def _revise(self, execution: Execution, decided: dict[str, Any]) -> list[str]:
        """Drop the frozen tests and send the run back to SPECIFICATION."""
        self.acceptance.revise(
            execution,
            {
                "digest": decided["digest"],
                "disputed": decided["disputed"],
                "rationale": decided["rationale"],
                "decidedBy": decided["decidedBy"],
            },
        )
        engine = self.results.engine
        transition = engine.state_machine.authorize_acceptance_revision(PhaseId.VERIFICATION)
        latest = engine.get_execution(execution.execution_id)
        engine._save_execution(
            latest.model_copy(
                update={
                    "status": ResultStatus.PENDING,
                    "current_phase": transition.target,
                    "gate_evaluation_id": None,
                    "human_decision_id": None,
                    "terminal_reason": None,
                    "updated_at": utc_now(),
                }
            )
        )
        self.results.s.state.set_flag(f"{RESUME_FLAG}:{execution.execution_id}", "1")
        return [phase.value for phase in transition.invalidated]

    # ----- after the revision ----------------------------------------------------------------
    def resume_target(self, execution_id: str) -> PhaseId | None:
        """VERIFICATION, once, for the SPECIFICATION that passed after a revision: the plan
        and the implementation stand, so the run does not plan or implement again."""
        key = f"{RESUME_FLAG}:{execution_id}"
        if not self.results.s.state.get_flag(key):
            return None
        self.results.s.state.set_flag(key, "")
        self.results.s.events.append(
            execution_id,
            "acceptance.revision.resumed",
            {"from": PhaseId.SPECIFICATION.value, "to": PhaseId.VERIFICATION.value},
        )
        return PhaseId.VERIFICATION


__all__ = [
    "DISPUTE_FLAG",
    "DISPUTE_KIND",
    "AcceptanceDisputes",
    "DisputeDecision",
    "decide_command",
]
