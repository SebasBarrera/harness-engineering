"""Second-agent review in INDEPENDENT_REVIEW (#38).

Validators check what tools can check; blind reviews of the evaluated implementations found
defects no validator saw (unrestricted pickle of stored state, wrong fare formulas, broken public
signatures, card data stored in full). Under ``review.agentReview`` the harness asks a provider
(call kind ``review``, read-only, optionally another provider or model through
``review.reviewer``) to compare the ChangeSet with the task's requirements, criteria and
constraints and return findings with severity and location.

Cost rule: the review runs only after every blocking deterministic validator passed (no reviewer
call while tests, lint or types fail) and again only when the ChangeSet digest changed since the
last review. Under ``enforce`` its HIGH and CRITICAL findings block the gate and, while the
correction budget (``runtime.verificationCorrections``) lasts, return the run to IMPLEMENTATION
with the findings as feedback; under ``warn`` they are recorded as MEDIUM at most. The request,
the reviewer's identity and its output are evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from governed_harness.agents.requests import REVIEW_SEVERITIES
from governed_harness.domain.enums import (
    ActorType,
    FindingSeverity,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.models import (
    Actor,
    ChangeSet,
    Execution,
    Finding,
    PhaseExecution,
)
from governed_harness.orchestration.engine_types import ReviewOutcome as ReviewOutcome

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

AGENT_REVIEW_ID = "review.agent"
MAX_DIFF_CHARS = 200_000
MAX_FINDINGS = 50
_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class AgentReview:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def policy(self) -> str:
        review = self.results.project.review
        if review is not None and review.panel is not None and review.panel.mode is not None:
            # #57: the review panel replaces the single reviewer under its own policy.
            return review.panel.mode
        return (review.agent_review if review else None) or "off"

    def blocking_severities(self) -> set[FindingSeverity]:
        names = self.results.s.resolved.effective_policies.get(
            "findingBlockSeverities", ["HIGH", "CRITICAL"]
        )
        return {FindingSeverity(str(name)) for name in names}

    def run(
        self, execution: Execution, phase: PhaseExecution, change_set: ChangeSet
    ) -> ReviewOutcome:
        if self.policy == "off":
            return ReviewOutcome(False)
        results = self.results
        engine = results.engine
        key = f"agentreview:{execution.execution_id}"
        last = results.flag_json(key)
        if isinstance(last, dict) and last.get("digest") == change_set.digest:
            results.s.events.append(
                execution.execution_id,
                "review.agent.skipped",
                {
                    "reason": "ChangeSet digest unchanged since the last review",
                    "digest": change_set.digest,
                },
            )
            return ReviewOutcome(False)
        validations = engine._latest_validations(execution.execution_id, change_set.digest)
        failing = [
            item.validator_id
            for item in validations
            if item.mandatory and item.status is not ResultStatus.PASSED
        ]
        blocking_ids = {finding_id for item in validations for finding_id in item.finding_ids}
        blocking_findings = [
            item
            for item in results.s.state.list(
                "finding", Finding, execution_id=execution.execution_id
            )
            if item.finding_id in blocking_ids and item.severity in self.blocking_severities()
        ]
        if failing or blocking_findings:
            # Cost rule: no reviewer call while a blocking deterministic check fails.
            results.s.events.append(
                execution.execution_id,
                "review.agent.skipped",
                {
                    "reason": "blocking deterministic validators or findings",
                    "failingValidators": failing,
                    "blockingFindings": len(blocking_findings),
                },
            )
            return ReviewOutcome(False)
        if self.results.panel.configured:
            return self.results.panel.run(execution, phase, change_set)
        task = engine.run_task(execution)
        diff = engine._compute_owned_diff(execution).unified_diff.decode("utf-8", "replace")
        payload: dict[str, Any] = {
            "changeSet": {
                "digest": change_set.digest,
                "files": [item.model_dump(mode="json", by_alias=True) for item in change_set.files],
                "diff": diff[:MAX_DIFF_CHARS],
                "diffTruncated": len(diff) > MAX_DIFF_CHARS,
            },
            "severities": list(REVIEW_SEVERITIES),
        }
        suffix = ""
        if results.engineering.configured:
            # The standards cards no tool verifies and the principles checklist ride on this
            # call (#56): no extra agent call.
            checklist, suffix = results.engineering.review_extra(execution, change_set)
            payload.update(checklist)
        outcome = results.call_agent(
            execution, phase, "review", payload, task=task, instructions_suffix=suffix
        )
        provider = results.provider_for(execution, "review")
        actor = Actor(actor_type=ActorType.AGENT, actor_id=f"agent.{provider}", version="1")
        enforce = self.policy == "enforce"
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            self._record(
                execution,
                change_set,
                (),
                ResultStatus.BLOCKED if enforce else ResultStatus.PASSED,
                f"The agent review did not answer ({outcome.status}): {outcome.summary}",
                outcome.evidence_refs,
                enforce,
            )
            return ReviewOutcome(True)
        try:
            items = _findings_from(outcome.result)
        except ValueError as error:
            note = results.record_finding(
                execution,
                validator_id=AGENT_REVIEW_ID,
                rule_id="review.agent-malformed",
                category="agent-protocol",
                severity=FindingSeverity.HIGH,
                message=f"The agent review returned a malformed result: {error}",
                evidence_refs=outcome.evidence_refs,
            )
            self._record(
                execution,
                change_set,
                (note,),
                ResultStatus.BLOCKED if enforce else ResultStatus.PASSED,
                f"The agent review was malformed: {error}",
                outcome.evidence_refs,
                enforce,
            )
            return ReviewOutcome(True)
        findings: list[Finding] = []
        for item in items[:MAX_FINDINGS]:
            severity = FindingSeverity(item["severity"])
            if not enforce and _RANK[severity.value] > _RANK["MEDIUM"]:
                severity = FindingSeverity.MEDIUM
            findings.append(
                results.record_finding(
                    execution,
                    validator_id=AGENT_REVIEW_ID,
                    rule_id=f"review.agent.{item['rule']}",
                    category="agent-review",
                    severity=severity,
                    message=item["message"],
                    path=item["path"],
                    line=item["line"],
                    evidence_refs=outcome.evidence_refs,
                    recommendation=(
                        f"Evidence given by the reviewer: {item['evidence']}"
                        if item["evidence"]
                        else None
                    ),
                    introduced=True,
                    actor=actor,
                )
            )
        blocking = tuple(item for item in findings if item.severity in self.blocking_severities())
        self._record(
            execution,
            change_set,
            tuple(findings),
            ResultStatus.PASSED,
            f"Agent review by {provider}: {len(findings)} finding(s), {len(blocking)} blocking",
            outcome.evidence_refs,
            enforce,
        )
        results.set_flag_json(key, {"digest": change_set.digest})
        return ReviewOutcome(True, blocking if enforce else ())

    def _record(
        self,
        execution: Execution,
        change_set: ChangeSet,
        findings: tuple[Finding, ...],
        status: ResultStatus,
        summary: str,
        refs: tuple[str, ...],
        mandatory: bool,
    ) -> None:
        self.results.record_validation(
            execution,
            validator_id=AGENT_REVIEW_ID,
            digest=change_set.digest,
            status=status,
            kind=ValidationKind.SUCCESS
            if status is ResultStatus.PASSED
            else ValidationKind.PROTOCOL_ERROR,
            mandatory=mandatory,
            summary=summary,
            findings=findings,
            evidence_refs=refs or (change_set.diff_ref,),
        )


def _findings_from(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("findings")
    if not isinstance(raw, list):
        raise ValueError("the review result needs a 'findings' list")
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"finding {index} is not an object")
        severity = str(entry.get("severity", "")).upper()
        if severity not in REVIEW_SEVERITIES:
            raise ValueError(f"finding {index} has an unknown severity {entry.get('severity')!r}")
        message = entry.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"finding {index} has no message")
        rule = (
            "".join(
                char if char.isalnum() or char in "-_." else "-"
                for char in str(entry.get("rule") or "finding").strip().lower()
            )[:60]
            or "finding"
        )
        line = entry.get("line")
        items.append(
            {
                "severity": severity,
                "rule": rule,
                "path": str(entry["path"]) if entry.get("path") else None,
                "line": line if isinstance(line, int) and line >= 1 else None,
                "message": " ".join(message.split())[:1000],
                "evidence": " ".join(str(entry.get("evidence") or "").split())[:1000],
            }
        )
    return items
