"""INTENT under the ladder settings (#55, items 7, 8 and 11).

* **Operational contract** (``intake.operationalContract``): the contract summary of the task
  revision (``ladder.contract``) is recorded, bound to the task digest. Under ``batch`` it rides
  in the one clarification request INTENT sends anyway (a ``contract`` section next to the
  questions, answered in the same file); under ``enforce`` INTENT also stops until every item is
  settled and a person confirmed the summary (``harness task confirm`` or ``confirmContract`` in
  the answers file).
* **Interruption budget** (``intake.interruptions``): the human interactions of a run are counted
  from its event chain and reported against the target; the declared stop conditions are
  recorded when they occur (here: questions asked again about a task that was already
  clarified, ``unresolvable-ambiguity``).
* **Localisation** (``context.locate``): for a task the router classifies M or L, a read-only
  ``locate`` call returns where to intervene; it runs once per task revision (cached by its
  digest, reused by later runs) on the cheapest rung of the router; ambiguous places come back
  as questions (rule ``A3``) and the locations feed the context manifest of IMPLEMENTATION."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from governed_harness.agents.routing import classify_size
from governed_harness.domain.enums import FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import (
    ClarificationQuestion,
    ClarificationRecord,
    Execution,
    PhaseExecution,
    Task,
)
from governed_harness.intake import task_digest
from governed_harness.ladder.contract import ContractSummary, derive_contract
from governed_harness.telemetry.metrics import HUMAN_INTERACTION_EVENTS, human_interactions

if TYPE_CHECKING:
    from governed_harness.orchestration.ladder_host import LadderHost

LOCATE_MALFORMED_RULE = "locate.malformed"
MAX_LOCATE_QUESTIONS = 10
HUMAN_EVENTS = HUMAN_INTERACTION_EVENTS
"""Events a person causes, by the kind of interruption they count as."""


@dataclass(frozen=True)
class IntentResult:
    questions: tuple[ClarificationQuestion, ...] = ()
    contract: dict[str, Any] | None = None
    blocked: str | None = None
    evidence_refs: tuple[str, ...] = ()


class LadderIntake:
    def __init__(self, ladder: LadderHost) -> None:
        self.ladder = ladder

    # ----- configuration ----------------------------------------------------------------------
    @property
    def contract_mode(self) -> str:
        intake = self.ladder.project.intake
        return (intake.operational_contract if intake else None) or "off"

    @property
    def locate_config(self) -> Any:
        context = self.ladder.project.context
        return context.locate if context else None

    def defaults(
        self, execution: Execution | None = None, task: Task | None = None
    ) -> dict[str, Any]:
        """What the project configuration says about the items a task's contract may leave
        out (a branch template is filled with the run and the task)."""
        project = self.ladder.project
        delivery = project.delivery_settings
        verification = project.verification
        ladder = verification.ladder if verification else None
        isolation = project.workspace.isolation
        coverage = None
        if verification and verification.test_quality and verification.test_quality.diff_coverage:
            coverage = verification.test_quality.diff_coverage
        branch_template: str | None
        if isolation and isolation.effective_mode == "worktree":
            branch_template = isolation.branch_template
        else:
            branch_template = delivery.branch_template if delivery.mode == "branch" else None
        values: dict[str, Any] = {
            "verificationLevel": ladder.required_default.value
            if ladder and ladder.enabled
            else None,
            "branch": branch_template,
            "push": delivery.push,
            "createPullRequest": delivery.pull_request.create if delivery.pull_request else None,
            "comment": delivery.comment,
            "coverageThreshold": coverage,
        }
        branch = values["branch"]
        if isinstance(branch, str) and execution is not None and task is not None:
            values["branch"] = branch.replace("{runId}", execution.execution_id).replace(
                "{taskId}", task.task_id
            )
        return values

    # ----- INTENT -----------------------------------------------------------------------------
    def intent(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        questions: tuple[ClarificationQuestion, ...],
    ) -> IntentResult:
        refs: list[str] = []
        located = self.locate(execution, phase, task, len(questions) + 1)
        if located.blocked is not None:
            return located
        refs.extend(located.evidence_refs)
        asked = (*questions, *located.questions)
        if asked:
            self._ambiguity_stop(execution, task)
        mode = self.contract_mode
        if mode == "off":
            return IntentResult(located.questions, None, None, tuple(refs))
        summary, ref = self.summarize(execution, phase, task)
        refs.append(ref)
        confirmed = self.confirmed(execution, summary)
        section = {**summary.as_dict(), "mode": mode, "confirmed": confirmed}
        blocked: str | None = None
        if mode == "enforce" and (summary.missing or not confirmed):
            blocked = (
                f"Operational contract: {len(summary.missing)} item(s) not settled "
                f"({', '.join(summary.missing)})"
                if summary.missing
                else "Operational contract not confirmed: harness task confirm --task "
                f"{task.task_id} --digest {summary.digest}"
            )
        include = bool(asked) or blocked is not None
        return IntentResult(located.questions, section if include else None, blocked, tuple(refs))

    def summarize(
        self, execution: Execution, phase: PhaseExecution, task: Task
    ) -> tuple[ContractSummary, str]:
        """The contract summary of the task revision, recorded once per task digest."""
        results = self.ladder.engine.results
        digest = task_digest(task)
        summary = derive_contract(task, digest, self.defaults(execution, task))
        key = f"contract:{execution.execution_id}:{summary.digest}"
        known = results.flag_json(key)
        if isinstance(known, dict) and isinstance(known.get("ref"), str):
            return summary, str(known["ref"])
        ref = results.record_json(
            execution,
            PhaseId.INTENT,
            summary.as_dict(),
            kind="operational-contract",
            summary=(
                f"Operational contract {summary.digest[:19]}: "
                f"{len(summary.missing)} item(s) not settled"
            ),
            supports=(task.task_id,),
        )
        results.set_flag_json(key, {"ref": ref})
        results.set_flag_json(
            f"contractcurrent:{execution.execution_id}",
            {
                "digest": summary.digest,
                "taskDigest": digest,
                "ref": ref,
            },
        )
        self.ladder.s.events.append(
            execution.execution_id,
            "contract.summarized",
            {
                "digest": summary.digest,
                "taskDigest": digest,
                "missing": list(summary.missing),
                "evidenceRef": ref,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return summary, ref

    def confirm_revision(self, execution: Execution, task: Task, actor: Any) -> str:
        """A person confirmed, with their answers, the contract of the revision the answers
        produce (``confirmContract`` in the answers file)."""
        from governed_harness.domain.models import utc_now

        summary = derive_contract(task, task_digest(task), self.defaults(execution, task))
        self.ladder.engine.results.set_flag_json(
            f"contractconfirmed:{execution.execution_id}",
            {
                "digest": summary.digest,
                "actorId": actor.actor_id,
                "confirmedAt": utc_now().isoformat(),
            },
        )
        self.ladder.s.events.append(
            execution.execution_id,
            "contract.confirmed",
            # Confirmed in the answers to the clarification: the same interaction.
            {"digest": summary.digest, "taskDigest": summary.task_digest, "withAnswers": True},
            actor=actor,
        )
        return summary.digest

    def confirmed(self, execution: Execution, summary: ContractSummary) -> bool:
        value = self.ladder.engine.results.flag_json(f"contractconfirmed:{execution.execution_id}")
        return isinstance(value, dict) and value.get("digest") == summary.digest

    # ----- interruptions ------------------------------------------------------------------------
    def _ambiguity_stop(self, execution: Execution, task: Task) -> None:
        """A task that a person already clarified in this run and still raises questions is
        the ``unresolvable-ambiguity`` stop condition."""
        config = self.ladder.project.intake.interruptions if self.ladder.project.intake else None
        if config is None or "unresolvable-ambiguity" not in config.conditions:
            return
        rounds = [
            item
            for item in self.ladder.s.state.list(
                "clarification", ClarificationRecord, execution_id=execution.execution_id
            )
            if item.task_id == task.task_id
        ]
        if not rounds:
            return
        self.ladder.s.events.append(
            execution.execution_id,
            "stop.condition",
            {
                "condition": "unresolvable-ambiguity",
                "rounds": len(rounds),
                "taskDigest": task_digest(task),
                "detail": "INTENT asks again after the task was clarified",
            },
        )

    def interruptions(self, execution: Execution) -> dict[str, Any] | None:
        """The human interactions of a run against the budget (``intake.interruptions``)."""
        intake = self.ladder.project.intake
        config = intake.interruptions if intake else None
        if config is None:
            return None
        events = self.ladder.s.events.list(execution.execution_id)
        by_kind = human_interactions(events)
        count = sum(by_kind.values())
        stops = [
            {
                "condition": event.payload.get("condition"),
                "detail": event.payload.get("detail"),
                "recordedAt": event.occurred_at,
            }
            for event in events
            if event.event_type == "stop.condition"
        ]
        return {
            "count": count,
            "target": config.effective_target,
            "overBudget": count > config.effective_target,
            "byKind": by_kind,
            "stopConditions": list(config.conditions),
            "stops": stops,
        }

    # ----- localisation ---------------------------------------------------------------------------
    def locate(
        self, execution: Execution, phase: PhaseExecution, task: Task, start: int
    ) -> IntentResult:
        config = self.locate_config
        if config is None or not config.enabled:
            return IntentResult()
        hub = self.ladder
        results = hub.engine.results
        policy = hub.project.agent_routing
        size, rule = classify_size(
            results.task_signals(execution, task), policy.thresholds if policy else None
        )
        digest = task_digest(task)
        if size == "S":
            hub.s.events.append(
                execution.execution_id,
                "locate.skipped",
                {"taskDigest": digest, "size": size, "rule": rule},
                phase_execution_id=phase.phase_execution_id,
            )
            return IntentResult()
        key = f"locate:{execution.project_id}:{task.task_id}:{digest}"
        cached = results.flag_json(key)
        if isinstance(cached, dict) and isinstance(cached.get("locations"), list):
            hub.s.events.append(
                execution.execution_id,
                "locate.reused",
                {
                    "taskDigest": digest,
                    "locations": len(cached["locations"]),
                    "evidenceRef": cached["ref"],
                },
                phase_execution_id=phase.phase_execution_id,
            )
            results.set_flag_json(f"located:{execution.execution_id}", cached)
            return IntentResult(
                self._questions(start, cached.get("questions") or []), None, None, (cached["ref"],)
            )
        payload = {"maxLocations": config.limit, "size": size}
        outcome = results.call_agent(
            execution,
            phase,
            "locate",
            payload,
            task=task,
            instruction_values={"maxLocations": config.limit},
        )
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return IntentResult(
                (),
                None,
                f"The locate call did not answer ({outcome.status}): {outcome.summary}",
                outcome.evidence_refs,
            )
        try:
            locations, questions = self._validated(outcome.result, config.limit)
        except ValueError as error:
            results.record_finding(
                execution,
                validator_id="harness.locate",
                rule_id=LOCATE_MALFORMED_RULE,
                category="localisation",
                severity=FindingSeverity.HIGH,
                message=f"The locate call returned a malformed result: {error}",
                evidence_refs=outcome.evidence_refs,
            )
            return IntentResult(
                (), None, f"The locate call was malformed: {error}", outcome.evidence_refs
            )
        record = {
            "taskDigest": digest,
            "size": size,
            "locations": locations,
            "questions": questions,
            "invocationId": outcome.invocation_id,
        }
        ref = results.record_json(
            execution,
            PhaseId.INTENT,
            record,
            kind="locate-result",
            summary=f"Locate: {len(locations)} location(s), {len(questions)} question(s)",
            supports=(task.task_id,),
        )
        stored = {**record, "ref": ref}
        results.set_flag_json(key, stored)
        results.set_flag_json(f"located:{execution.execution_id}", stored)
        hub.s.events.append(
            execution.execution_id,
            "locate.completed",
            {
                "taskDigest": digest,
                "locations": len(locations),
                "questions": len(questions),
                "evidenceRef": ref,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return IntentResult(
            self._questions(start, questions), None, None, (*outcome.evidence_refs, ref)
        )

    @staticmethod
    def _validated(result: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], list[str]]:
        raw = result.get("locations")
        if not isinstance(raw, list):
            raise ValueError("the locate result needs a 'locations' list")
        locations: list[dict[str, Any]] = []
        for index, item in enumerate(raw[:limit], start=1):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError(f"location {index} needs a path")
            path = item["path"].strip().removeprefix("./")
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ValueError(f"location {index} must be relative to the workspace: {path!r}")
            line = item.get("line")
            locations.append(
                {
                    "path": path,
                    "line": line if isinstance(line, int) and line >= 1 else None,
                    "evidence": str(item.get("evidence") or "")[:1000],
                    "reason": str(item.get("reason") or "")[:500],
                }
            )
        questions = [
            str(item.get("text") if isinstance(item, dict) else item).strip()[:1000]
            for item in (result.get("questions") or [])[:MAX_LOCATE_QUESTIONS]
        ]
        return locations, [item for item in questions if item]

    @staticmethod
    def _questions(start: int, texts: list[str]) -> tuple[ClarificationQuestion, ...]:
        return tuple(
            ClarificationQuestion.model_validate(
                {
                    "questionId": f"Q-{number}",
                    "ruleId": "A3",
                    "target": "task",
                    "text": text,
                    "category": "ambiguity",
                }
            )
            for number, text in enumerate(texts, start=start)
        )

    def located(self, execution: Execution) -> dict[str, Any] | None:
        value = self.ladder.engine.results.flag_json(f"located:{execution.execution_id}")
        return value if isinstance(value, dict) and value.get("locations") else None


__all__ = ["HUMAN_EVENTS", "IntentResult", "LadderIntake"]
