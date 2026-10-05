"""Low friction for small changes (#58) and the plan-approval checkpoint (#8), wired into the
engine's phases.

``RunEngine`` delegates every step of the ``friction`` settings to this class, as it does for
the agent-results and ladder settings: a project without the section runs exactly the earlier
code path (``active`` is false and the engine never calls in). What each phase does:

* INTENT: the lane. ``fastLane.mode: auto`` classifies the task with the router's size (#44) and
  its risk flags: size ``S`` without a risk flag is the fast lane, anything else the full flow.
  The lane, the size, the rule that decided and what the fast lane leaves out are recorded
  (``lane.classified`` and a ``lane-decision`` evidence). A fast-lane run skips the agent's
  ambiguity review.
* PLANNING: no decomposition and no preflight on the baseline in the fast lane; the
  plan-approval checkpoint (``planApproval``) for ``L`` tasks and tasks with a risk flag.
* VERIFICATION: the ChangeSet is profiled (risk factors, files, change type). A risk factor or
  a ChangeSet larger than ``S`` takes the run out of the fast lane (``lane.escalated``). The
  fast lane runs the affected tests first, the validators side by side and reuses a validator
  result for the same validator, ChangeSet, baseline and configuration; no light mutation. A
  documentation-only or configuration-only ChangeSet needs no new tests and no requirement
  traceability (``changeTypes``).
* INDEPENDENT_REVIEW: in the fast lane the agent review runs only on a signal (a risk factor, a
  finding of severity MEDIUM or above, a ChangeSet larger than ``S``).
* DECISION: a pre-authorised approval is applied on the person's behalf only when its condition
  holds (gate passed, no risk factor, size ``S``), its contract digest is unchanged and it has
  not expired; otherwise the person is asked as usual.

Nothing here skips a mandatory validator before the decision or decides for a person: the
human decision is either a person's, or a person's recorded in advance under a condition the
harness checked."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from governed_harness.agents.routing import classify_size
from governed_harness.checks import parse_unified_diff
from governed_harness.checks.risk import detect_risk_factors
from governed_harness.configuration.agent_results import DEFAULT_SIZE_THRESHOLDS
from governed_harness.configuration.models import ValidatorDefinition
from governed_harness.domain.actors import require_human_actor
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    EvidenceKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
)
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    HARNESS_ACTOR,
    Actor,
    ChangeSet,
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Plan,
    PreAuthorization,
    Task,
    ValidationResult,
    utc_now,
)
from governed_harness.evidence.hashing import sha256_json
from governed_harness.friction import (
    affected_python_tests,
    change_type,
    exempt_from_tests,
    plan_digest,
)
from governed_harness.intake import task_digest
from governed_harness.ladder.contract import derive_contract
from governed_harness.validators import ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.configuration.friction import (
        FastLaneConfig,
        FastVerificationConfig,
        FrictionConfig,
        PreAuthorizationConfig,
    )
    from governed_harness.configuration.models import ProjectConfiguration
    from governed_harness.orchestration.engine import EngineServices, PhaseOutcome, RunEngine

LANE_FLAG = "lane"
PROFILE_FLAG = "changeprofile"
PREAUTH_FLAG = "preauth"
PREAUTH_USED_FLAG = "preauthused"
PLAN_APPROVAL_FLAG = "planapproval"
CACHE_FLAG = "validatorcache"
AFFECTED_TESTS_ID = "harness.affected-tests"
_REVIEW_SEVERITIES = frozenset(
    {FindingSeverity.MEDIUM, FindingSeverity.HIGH, FindingSeverity.CRITICAL}
)
_BLOCKING_SEVERITIES = frozenset({FindingSeverity.HIGH, FindingSeverity.CRITICAL})
_CACHEABLE = frozenset({ResultStatus.PASSED})


class Friction:
    def __init__(self, engine: RunEngine) -> None:
        self.engine = engine

    # ----- configuration ------------------------------------------------------------------------
    @property
    def s(self) -> EngineServices:
        return self.engine.s

    @property
    def project(self) -> ProjectConfiguration:
        return self.s.resolved.project

    @property
    def config(self) -> FrictionConfig | None:
        return self.project.friction

    @property
    def fast_lane(self) -> FastLaneConfig | None:
        config = self.config
        lane = config.fast_lane if config else None
        return lane if lane is not None and lane.enabled else None

    @property
    def pre_authorization(self) -> PreAuthorizationConfig | None:
        config = self.config
        settings = config.pre_authorization if config else None
        return settings if settings is not None and settings.enabled else None

    @property
    def fast_verification(self) -> FastVerificationConfig | None:
        lane = self.fast_lane
        return lane.verification if lane else None

    @property
    def change_types(self) -> bool:
        config = self.config
        return bool(config and config.change_types)

    @property
    def plan_approval(self) -> str:
        config = self.config
        return (config.plan_approval if config else None) or "off"

    @property
    def active(self) -> bool:
        """Whether a ``friction`` setting that acts on a run is configured; without one the
        engine runs the earlier path and never calls into this class."""
        return any(
            (
                self.fast_lane is not None,
                self.pre_authorization is not None,
                self.change_types,
                self.plan_approval != "off",
            )
        )

    def _flag(self, key: str) -> Any:
        raw = self.s.state.get_flag(key)
        return json.loads(raw) if raw else None

    def _set_flag(self, key: str, value: Any) -> None:
        self.s.state.set_flag(key, json.dumps(value, sort_keys=True))

    def _files_bound(self) -> int:
        routing = self.project.agent_routing
        thresholds = routing.thresholds if routing else None
        configured = thresholds.files if thresholds else None
        return (configured or DEFAULT_SIZE_THRESHOLDS["files"])[0]

    # ----- size and risk of a task ----------------------------------------------------------------
    def task_size(self, execution: Execution, task: Task) -> dict[str, Any]:
        """The router's size of the task (#44) and its risk flags, before any change."""
        signals = self.engine.results.task_signals(execution, task)
        routing = self.project.agent_routing
        size, rule = classify_size(signals, routing.thresholds if routing else None)
        return {
            "size": size,
            "rule": rule,
            "riskFlags": list(signals.risk_flags),
            "requirements": signals.requirements,
            "files": signals.files,
            "loc": signals.loc,
        }

    # ----- INTENT: the lane ---------------------------------------------------------------------
    def classify(self, execution: Execution, phase: PhaseExecution, task: Task) -> dict[str, Any]:
        """The lane of the run for the current task revision, recorded once per revision."""
        config = self.fast_lane
        assert config is not None
        digest = task_digest(task)
        known = self.lane(execution)
        if known is not None and known.get("taskDigest") == digest:
            return known
        sized = self.task_size(execution, task)
        reasons: list[str] = []
        if sized["size"] != "S":
            reasons.append(f"size {sized['size']} ({sized['rule']})")
        declared = [
            item.criterion_id
            for item in task.acceptance_criteria
            if item.verification is not None
            and (
                item.verification.probe
                or item.verification.deferred
                or item.verification.manual
                or item.verification.level.value > "L1"
            )
        ]
        if task.probes or task.checklist or declared:
            # A person who asked for stronger evidence asked for the full flow.
            reasons.append("the task declares verification beyond L1 (probes, deferred or manual)")
        lane = "full" if reasons else "fast"
        record: dict[str, Any] = {
            "lane": lane,
            "size": sized["size"],
            "rule": sized["rule"],
            "riskFlags": sized["riskFlags"],
            "signals": {key: sized[key] for key in ("requirements", "files", "loc")},
            "reasons": reasons or ["size S and no risk flag"],
            "skip": list(config.skipped) if lane == "fast" else [],
            "taskDigest": digest,
            "escalated": False,
        }
        ref = self.engine.results.record_json(
            execution,
            PhaseId.INTENT,
            record,
            kind="lane-decision",
            summary=f"Lane {lane}: {'; '.join(record['reasons'])}",
            supports=(task.task_id,),
        )
        record["ref"] = ref
        self._set_flag(f"{LANE_FLAG}:{execution.execution_id}", record)
        self.s.events.append(
            execution.execution_id,
            "lane.classified",
            {key: record[key] for key in ("lane", "size", "rule", "reasons", "skip", "taskDigest")}
            | {"evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        return record

    def lane(self, execution: Execution) -> dict[str, Any] | None:
        value = self._flag(f"{LANE_FLAG}:{execution.execution_id}")
        return value if isinstance(value, dict) else None

    def fast(self, execution: Execution) -> bool:
        lane = self.lane(execution)
        return bool(lane and lane.get("lane") == "fast" and not lane.get("escalated"))

    def skips(self, execution: Execution, step: str, phase_id: PhaseId | None = None) -> bool:
        """Whether the fast lane leaves ``step`` out of this run; recorded when it does."""
        if self.fast_lane is None or not self.fast(execution):
            return False
        lane = self.lane(execution) or {}
        if step not in (lane.get("skip") or []):
            return False
        self.s.events.append(
            execution.execution_id,
            "lane.step.skipped",
            {"step": step, "phaseId": phase_id.value if phase_id else None, "lane": "fast"},
        )
        return True

    def escalate(self, execution: Execution, reasons: Sequence[str]) -> None:
        lane = self.lane(execution)
        if lane is None or lane.get("lane") != "fast" or lane.get("escalated"):
            return
        lane = {**lane, "escalated": True, "escalationReasons": list(reasons)}
        self._set_flag(f"{LANE_FLAG}:{execution.execution_id}", lane)
        self.s.events.append(
            execution.execution_id,
            "lane.escalated",
            {"from": "fast", "to": "full", "reasons": list(reasons)},
        )

    # ----- the ChangeSet: risk factors, size and change type --------------------------------------
    def profile(self, execution: Execution, change_set: ChangeSet) -> dict[str, Any]:
        """Risk factors, files, lines and change type of the ChangeSet, once per digest. A fast
        run whose ChangeSet shows a risk factor or outgrows ``S`` leaves the fast lane."""
        key = f"{PROFILE_FLAG}:{execution.execution_id}:{change_set.digest}"
        known = self._flag(key)
        if isinstance(known, dict):
            return known
        from governed_harness.orchestration.verification_checks import declared_interfaces

        task = self.engine.run_task(execution)
        text = self.engine._compute_owned_diff(execution).unified_diff.decode("utf-8", "replace")
        interfaces = [path for pair in declared_interfaces(task) for path in pair]
        signals = detect_risk_factors(parse_unified_diff(text), interface_paths=interfaces)
        factors = sorted({signal.factor for signal in signals}, key=str)
        paths = [item.path for item in change_set.files]
        profile: dict[str, Any] = {
            "digest": change_set.digest,
            "riskFactors": factors,
            "files": len(paths),
            "lines": sum(item.additions + item.deletions for item in change_set.files),
            "changeType": change_type(paths),
            "filesBound": self._files_bound(),
        }
        self._set_flag(key, profile)
        reasons = [f"risk factor {factor}" for factor in factors]
        if len(paths) > profile["filesBound"]:
            reasons.append(f"files={len(paths)}>{profile['filesBound']} (ChangeSet larger than S)")
        if reasons and self.fast(execution):
            self.escalate(execution, reasons)
        return profile

    def tests_exempt(self, execution: Execution, change_set: ChangeSet, check: str) -> str | None:
        """The reason ``check`` (a test or traceability requirement) does not apply to this
        ChangeSet, when ``changeTypes`` is on and the ChangeSet is not code."""
        if not self.change_types or not change_set.files:
            return None
        kind = self.profile(execution, change_set)["changeType"]
        if not exempt_from_tests(kind):
            return None
        self.s.events.append(
            execution.execution_id,
            "change-type.exempted",
            {"check": check, "changeType": kind, "digest": change_set.digest},
        )
        return f"{kind} change: {check} does not apply (friction.changeTypes)"

    def not_applicable(
        self, execution: Execution, change_set: ChangeSet, validator_id: str, reason: str
    ) -> ValidatorOutput:
        """A NOT_APPLICABLE result that records why a check did not run."""
        results = self.engine.results
        ref = results.record_json(
            execution,
            PhaseId.VERIFICATION,
            {"validatorId": validator_id, "status": "NOT_APPLICABLE", "reason": reason},
            kind="validator-not-applicable",
            summary=f"{validator_id}: {reason}",
            evidence_kind=EvidenceKind.TEST_REPORT,
        )
        from governed_harness.domain.enums import ValidationKind

        now = utc_now()
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{validator_id}", version="1")
        result = ValidationResult(
            validation_result_id=new_id("validation"),
            execution_id=execution.execution_id,
            validator_id=validator_id,
            change_set_digest=change_set.digest,
            status=ResultStatus.NOT_APPLICABLE,
            kind=ValidationKind.SUCCESS,
            mandatory=False,
            summary=reason,
            evidence_refs=(ref,),
            started_at=now,
            finished_at=now,
            provenance=self.engine._provenance(execution).model_copy(update={"actor": actor}),
        )
        return ValidatorOutput(result)

    # ----- INDEPENDENT_REVIEW: the agent review on signals -----------------------------------------
    def review_signals(self, execution: Execution, change_set: ChangeSet) -> list[str]:
        profile = self.profile(execution, change_set)
        signals = [f"risk factor {factor}" for factor in profile["riskFactors"]]
        if profile["files"] > profile["filesBound"]:
            signals.append(f"{profile['files']} files (larger than S)")
        validations = self.engine._latest_validations(execution.execution_id, change_set.digest)
        mandatory = {
            finding for item in validations if item.mandatory for finding in item.finding_ids
        }
        ids = {finding for item in validations for finding in item.finding_ids}
        findings = [
            item
            for item in self.s.state.list("finding", Finding, execution_id=execution.execution_id)
            if item.finding_id in ids
            and (
                item.severity in _BLOCKING_SEVERITIES
                or (
                    item.severity in _REVIEW_SEVERITIES
                    and (item.finding_id in mandatory or item.introduced is True)
                )
            )
        ]
        if findings:
            # A finding an optional validator also reports on the baseline (introduced is not
            # true) is no signal: it was there before the change.
            signals.append(f"{len(findings)} finding(s) of this change of severity MEDIUM or above")
        lane = self.lane(execution) or {}
        if lane.get("escalated"):
            signals.append("the run left the fast lane")
        return signals

    def agent_review_wanted(self, execution: Execution, change_set: ChangeSet) -> bool:
        """In the fast lane the agent review runs only on a signal; elsewhere always."""
        lane = self.lane(execution) or {}
        if (
            self.fast_lane is None
            or lane.get("lane") != "fast"
            or "agentReview" not in (lane.get("skip") or [])
        ):
            return True
        signals = self.review_signals(execution, change_set)
        if signals:
            self.s.events.append(
                execution.execution_id,
                "review.agent.signals",
                {"signals": signals, "digest": change_set.digest},
            )
            return True
        self.s.events.append(
            execution.execution_id,
            "review.agent.skipped",
            {"reason": "fast lane: no review signal", "digest": change_set.digest},
        )
        return False

    # ----- VERIFICATION: affected tests first, parallel validators, cache by digest -------------
    def _cache_key(self, execution: Execution, change_set: ChangeSet, definition: Any) -> str:
        return sha256_json(
            {
                "validator": definition.model_dump(mode="json", by_alias=True),
                "changeSet": change_set.digest,
                "baseline": self.s.state.get_flag(f"baseline:{execution.execution_id}"),
                "configuration": execution.configuration_digest,
            }
        )

    def _reused(
        self, execution: Execution, change_set: ChangeSet, definition: Any
    ) -> ValidatorOutput | None:
        key = self._cache_key(execution, change_set, definition)
        cached = self._flag(f"{CACHE_FLAG}:{execution.project_id}:{key}")
        if not isinstance(cached, dict):
            return None
        try:
            earlier = self.s.state.get("validation", str(cached["validationId"]), ValidationResult)
        except (NotFoundError, KeyError):
            return None
        now = utc_now()
        result = earlier.model_copy(
            update={
                "validation_result_id": new_id("validation"),
                "execution_id": execution.execution_id,
                "change_set_digest": change_set.digest,
                "summary": f"{earlier.summary} (reused from {earlier.validation_result_id}: "
                "same validator, ChangeSet, baseline and configuration)",
                "finding_ids": (),
                "tool_invocation_ids": (),
                "started_at": now,
                "finished_at": now,
            }
        )
        self.s.events.append(
            execution.execution_id,
            "validator.reused",
            {
                "validatorId": definition.validator_id,
                "fromValidationId": earlier.validation_result_id,
                "fromExecutionId": earlier.execution_id,
                "digest": change_set.digest,
            },
        )
        return ValidatorOutput(result)

    def _remember(
        self, execution: Execution, change_set: ChangeSet, definition: Any, output: Any
    ) -> None:
        if output.result.status not in _CACHEABLE or output.findings:
            return
        key = self._cache_key(execution, change_set, definition)
        self._set_flag(
            f"{CACHE_FLAG}:{execution.project_id}:{key}",
            {"validationId": output.result.validation_result_id},
        )

    def affected_tests(
        self, execution: Execution, change_set: ChangeSet, definitions: Sequence[Any]
    ) -> tuple[Any, list[str]] | None:
        """The pytest validator and the affected test files, when there are any."""
        pytest_definition = next(
            (
                item
                for item in definitions
                if item.command and "pytest" in " ".join(item.command) and item.mandatory
            ),
            None,
        )
        if pytest_definition is None:
            return None
        tests = affected_python_tests(
            self.s.paths.workspace, [item.path for item in change_set.files if item.after_digest]
        )
        if not tests:
            return None
        return pytest_definition, tests

    def run_validators(
        self,
        execution: Execution,
        change_set: ChangeSet,
        definitions: Sequence[ValidatorDefinition],
        prepare: Callable[[ValidatorDefinition], Callable[[], ValidatorOutput]],
        save: Callable[[ValidatorOutput], None],
    ) -> list[ValidatorOutput]:
        """The profile validators of VERIFICATION. Outside the fast lane (or without
        ``fastLane.verification``) one after the other, as before. In the fast lane: the
        affected tests first (a failure stops the attempt; the full suite runs before the
        gate), then the validators side by side, reusing a PASSED result of the same validator
        for the same ChangeSet, baseline and configuration."""
        config = self.fast_verification
        if config is None or not self.fast(execution):
            outputs = []
            for definition in definitions:
                output = prepare(definition)()
                save(output)
                outputs.append(output)
            return outputs
        outputs = []
        if config.affected_tests_first:
            selection = self.affected_tests(execution, change_set, definitions)
            if selection is not None:
                pytest_definition, tests = selection
                command = (*(pytest_definition.command or ()), *tests)
                affected = pytest_definition.model_copy(
                    update={"validator_id": AFFECTED_TESTS_ID, "command": command}
                )
                output = prepare(affected)()
                save(output)
                self.s.events.append(
                    execution.execution_id,
                    "verification.affected-tests",
                    {
                        "tests": tests,
                        "status": output.result.status.value,
                        "digest": change_set.digest,
                    },
                )
                outputs.append(output)
                if output.result.status is not ResultStatus.PASSED:
                    # The affected tests already fail: the full suite would not change the
                    # outcome of this attempt, and it still runs before the gate.
                    return outputs
        pending: list[tuple[int, ValidatorDefinition]] = []
        slots: list[ValidatorOutput | None] = [None] * len(definitions)
        for index, definition in enumerate(definitions):
            reused = self._reused(execution, change_set, definition) if config.cache else None
            if reused is not None:
                slots[index] = reused
            else:
                pending.append((index, definition))
        workers = max(1, min(len(pending), self.project.runtime.max_parallel))
        if config.parallel and workers > 1:
            # Contexts are prepared on this thread; only the validator commands run side by side.
            calls = [(index, prepare(definition)) for index, definition in pending]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [(index, pool.submit(call)) for index, call in calls]
                for index, future in futures:
                    slots[index] = future.result()
        else:
            for index, definition in pending:
                slots[index] = prepare(definition)()
        ran = {index for index, _ in pending}
        for index, definition in enumerate(definitions):
            slot = slots[index]
            assert slot is not None
            save(slot)
            if config.cache and index in ran:
                self._remember(execution, change_set, definition, slot)
            outputs.append(slot)
        if config.parallel and workers > 1:
            self.s.events.append(
                execution.execution_id,
                "verification.parallel",
                {"validators": [item.validator_id for _, item in pending], "workers": workers},
            )
        return outputs

    # ----- PLANNING: the plan-approval checkpoint (#8) -----------------------------------------
    def plan_checkpoint(
        self, execution: Execution, phase: PhaseExecution, task: Task, plan: Plan, plan_ref: str
    ) -> PhaseOutcome | None:
        from governed_harness.orchestration.engine import PhaseOutcome

        mode = self.plan_approval
        if mode == "off":
            return None
        digest = plan_digest(plan, task_digest(task))
        key = f"{PLAN_APPROVAL_FLAG}:{execution.execution_id}"
        state = self._flag(key)
        if isinstance(state, dict) and state.get("digest") == digest:
            status = state.get("status")
            if status in {"APPROVED", "NOT_REQUIRED"}:
                return None
            if status == "REJECTED":
                return PhaseOutcome(ResultStatus.FAILED, "The plan was rejected by a person")
        sized = self.task_size(execution, task)
        reasons: list[str] = []
        if mode == "always":
            reasons.append("planApproval: always")
        if sized["size"] == "L":
            reasons.append(f"size L ({sized['rule']})")
        if sized["riskFlags"]:
            reasons.append(f"risk flag(s) {', '.join(sized['riskFlags'])}")
        if not reasons:
            reason = f"size {sized['size']} and no risk flag"
            self._set_flag(key, {"digest": digest, "status": "NOT_REQUIRED", "reason": reason})
            self.s.events.append(
                execution.execution_id,
                "plan.approval.skipped",
                {"digest": digest, "reason": reason, "size": sized["size"]},
                phase_execution_id=phase.phase_execution_id,
            )
            return None
        covering = self._covering_pre_authorization(execution, task)
        if covering is not None:
            self._set_flag(
                key,
                {
                    "digest": digest,
                    "status": "APPROVED",
                    "reasons": reasons,
                    "preAuthorizationId": covering.pre_authorization_id,
                    "actorId": covering.actor.actor_id,
                },
            )
            self.s.events.append(
                execution.execution_id,
                "plan.approval.covered",
                {
                    "digest": digest,
                    "reasons": reasons,
                    "preAuthorizationId": covering.pre_authorization_id,
                    "actorId": covering.actor.actor_id,
                },
                phase_execution_id=phase.phase_execution_id,
            )
            return None
        pending = {"digest": digest, "status": "PENDING", "reasons": reasons, "planRef": plan_ref}
        if state != pending:
            self._set_flag(key, pending)
            self.s.events.append(
                execution.execution_id,
                "plan.approval.requested",
                {"digest": digest, "reasons": reasons, "planRef": plan_ref},
                phase_execution_id=phase.phase_execution_id,
            )
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"The plan needs a person's approval ({'; '.join(reasons)}): harness plan decide "
            f"--run {execution.execution_id} --decision APPROVE --digest {digest}",
            (plan_ref,),
        )

    def plan_approval_state(self, execution: Execution) -> dict[str, Any] | None:
        value = self._flag(f"{PLAN_APPROVAL_FLAG}:{execution.execution_id}")
        return value if isinstance(value, dict) else None

    def decide_plan(
        self,
        execution: Execution,
        *,
        decision: DecisionKind,
        digest: str,
        actor: Actor,
        rationale: str,
    ) -> dict[str, Any]:
        """A person approves or rejects the plan waiting at the checkpoint, bound to its
        digest."""
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a person can approve a plan")
        require_human_actor(actor.actor_id, "approve a plan")
        if decision not in {DecisionKind.APPROVE, DecisionKind.REJECT}:
            raise PolicyViolationError("a plan is approved or rejected")
        state = self.plan_approval_state(execution)
        if not state or state.get("status") != "PENDING":
            raise NotFoundError(f"run {execution.execution_id} has no plan waiting for approval")
        if state.get("digest") != digest:
            raise PolicyViolationError(
                f"the digest does not match the plan waiting for approval ({state.get('digest')})"
            )
        status = "APPROVED" if decision is DecisionKind.APPROVE else "REJECTED"
        record = {
            **state,
            "status": status,
            "actorId": actor.actor_id,
            "rationale": rationale.strip() or decision.value,
            "decidedAt": utc_now().isoformat(),
        }
        self._set_flag(f"{PLAN_APPROVAL_FLAG}:{execution.execution_id}", record)
        self.s.events.append(
            execution.execution_id,
            "plan.approval.decided",
            {"digest": digest, "decision": decision.value, "rationale": record["rationale"]},
            actor=actor,
        )
        if decision is DecisionKind.REJECT:
            latest = self.engine.get_execution(execution.execution_id)
            self.engine._save_execution(
                latest.model_copy(
                    update={
                        "status": ResultStatus.FAILED,
                        "terminal_reason": "The plan was rejected by a person",
                        "updated_at": utc_now(),
                    }
                )
            )
        return record

    # ----- pre-authorised approval ---------------------------------------------------------------
    def contract_digest(self, execution: Execution, task: Task) -> str:
        """The operational-contract digest of the run's task revision (#55), the same summary
        INTENT records."""
        defaults = self.engine.ladder.intake.defaults(execution, task)
        return derive_contract(task, task_digest(task), defaults).digest

    def preauthorize(
        self,
        execution: Execution,
        actor: Actor,
        *,
        hours: int | None = None,
        rationale: str = "",
        identity_source: str | None = None,
        with_contract: bool = False,
    ) -> PreAuthorization:
        """Record a person's approval in advance for this run, bound to the contract digest
        and the condition (gate passed, no risk factor, size S)."""
        settings = self.pre_authorization
        if settings is None:
            raise PolicyViolationError(
                "pre-authorised approval needs friction.preAuthorization.mode: allow"
            )
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a person can pre-authorise an approval")
        require_human_actor(actor.actor_id, "pre-authorise an approval")
        if execution.current_phase is not PhaseId.INTENT:
            raise PolicyViolationError(
                "an approval is pre-authorised with the operational contract, in INTENT; "
                f"run {execution.execution_id} is in {execution.current_phase.value}"
            )
        chosen = hours or settings.hours
        if chosen < 1 or chosen > settings.limit_hours:
            raise PolicyViolationError(
                f"a pre-authorisation lasts 1 to {settings.limit_hours} hours "
                "(friction.preAuthorization.maxHours)"
            )
        task = self.engine.run_task(execution)
        digest = self.contract_digest(execution, task)
        now = utc_now()
        record = PreAuthorization(
            pre_authorization_id=new_id("preauth"),
            execution_id=execution.execution_id,
            task_id=task.task_id,
            actor=actor,
            contract_digest=digest,
            task_digest=task_digest(task),
            rationale=rationale.strip()
            or "Approve in advance if the gate passes, with no risk factor, at size S",
            authorized_at=now,
            expires_at=now + timedelta(hours=chosen),
            identity_source=identity_source,  # type: ignore[arg-type]
        )
        self.s.state.put(
            "pre_authorization",
            record.pre_authorization_id,
            record,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        ref = self.s.artifacts.put_json(
            record.model_dump(mode="json", by_alias=True),
            metadata={"kind": "pre-authorization", "executionId": execution.execution_id},
        )
        self.engine._record_evidence(
            execution,
            PhaseId.INTENT,
            EvidenceKind.HUMAN_DECISION,
            ref,
            f"Pre-authorised approval until {record.expires_at.isoformat()} under gatePassed, "
            "noRiskFactors and sizeS",
            supports=(task.task_id,),
        )
        self.s.events.append(
            execution.execution_id,
            "decision.preauthorized",
            record.model_dump(mode="json", by_alias=True),
            actor=actor,
        )
        self.s.state.set_flag(
            f"{PREAUTH_FLAG}:{execution.execution_id}", record.pre_authorization_id
        )
        if with_contract:
            # The person confirms the contract the pre-authorisation is bound to in the same
            # act: one interaction (withPreAuthorization), not two.
            self.engine.results.set_flag_json(
                f"contractconfirmed:{execution.execution_id}",
                {"digest": digest, "actorId": actor.actor_id, "confirmedAt": now.isoformat()},
            )
            self.s.events.append(
                execution.execution_id,
                "contract.confirmed",
                {"digest": digest, "taskDigest": record.task_digest, "withPreAuthorization": True},
                actor=actor,
            )
        return record

    def pre_authorization_record(self, execution: Execution) -> PreAuthorization | None:
        identifier = self.s.state.get_flag(f"{PREAUTH_FLAG}:{execution.execution_id}")
        if not identifier:
            return None
        try:
            return self.s.state.get("pre_authorization", identifier, PreAuthorization)
        except NotFoundError:
            return None

    def _covering_pre_authorization(
        self, execution: Execution, task: Task
    ) -> PreAuthorization | None:
        """A pre-authorisation of the run that is in force for the current contract: it may
        cover the plan approval (the person approved the run's work in advance)."""
        record = self.pre_authorization_record(execution)
        if record is None or record.expires_at <= utc_now():
            return None
        if record.contract_digest != self.contract_digest(execution, task):
            return None
        return record

    def pre_authorization_failures(
        self,
        execution: Execution,
        record: PreAuthorization,
        gate: GateEvaluation,
        change_set: ChangeSet,
    ) -> list[str]:
        """Why the condition of ``record`` does not hold for this ChangeSet (empty: it holds)."""
        reasons: list[str] = []
        if self.s.state.list("decision", HumanDecision, execution_id=execution.execution_id):
            reasons.append("a person already decided in this run")
        if record.expires_at <= utc_now():
            reasons.append(f"it expired at {record.expires_at.isoformat()}")
        task = self.engine.run_task(execution)
        if self.contract_digest(execution, task) != record.contract_digest:
            reasons.append("the operational contract changed after it was given")
        if gate.status is not ResultStatus.PASSED:
            reasons.append(f"the automatic gate is {gate.status.value}, not PASSED")
        if gate.change_set_digest != change_set.digest:
            reasons.append("the gate was evaluated for another ChangeSet")
        profile = self.profile(execution, change_set)
        if profile["riskFactors"]:
            reasons.append(f"risk factor(s) {', '.join(profile['riskFactors'])}")
        sized = self.task_size(execution, task)
        if sized["size"] != "S":
            reasons.append(f"the task is size {sized['size']} ({sized['rule']})")
        if profile["files"] > profile["filesBound"]:
            reasons.append(f"the ChangeSet has {profile['files']} files (larger than S)")
        if self.engine.ladder.active and self.engine.ladder.manual_items(task):
            reasons.append("manual checklist items only a person can tick")
        if self.engine.results.active and self.engine.results.required_acknowledgements(
            execution, change_set.digest
        ):
            reasons.append("risk factors a person must acknowledge")
        return reasons

    def apply_pre_authorization(
        self, execution: Execution, gate: GateEvaluation, change_set: ChangeSet
    ) -> HumanDecision | None:
        """At DECISION: the approval the person gave in advance, recorded on their behalf when
        its condition holds; ``None`` (the person is asked as usual) otherwise."""
        record = self.pre_authorization_record(execution)
        if record is None or self.s.state.get_flag(f"{PREAUTH_USED_FLAG}:{execution.execution_id}"):
            return None
        reasons = self.pre_authorization_failures(execution, record, gate, change_set)
        if reasons:
            key = f"preauthrefused:{execution.execution_id}:{change_set.digest}"
            if not self.s.state.get_flag(key):
                self.s.state.set_flag(key, "1")
                self.s.events.append(
                    execution.execution_id,
                    "decision.preauthorization.not-applied",
                    {
                        "preAuthorizationId": record.pre_authorization_id,
                        "digest": change_set.digest,
                        "reasons": reasons,
                    },
                )
            return None
        try:
            contract = self.engine._current_contract_digest(execution)
        except PolicyViolationError:
            return None
        decided_at = utc_now()
        expiry = self.project.governance_settings.decision_expiry_hours
        decision = HumanDecision(
            decision_id=new_id("decision"),
            execution_id=execution.execution_id,
            gate_evaluation_id=gate.gate_evaluation_id,
            actor=record.actor,
            decision=DecisionKind.APPROVE,
            rationale=(
                f"Pre-authorised approval {record.pre_authorization_id} given at "
                f"{record.authorized_at.isoformat()}: the gate passed, no risk factor, size S. "
                f"{record.rationale}"
            ),
            change_set_digest=change_set.digest,
            configuration_digest=execution.configuration_digest,
            policy_digest=execution.policy_digest,
            acceptance_contract_digest=contract,
            identity_source=record.identity_source,
            decided_at=decided_at,
            expires_at=decided_at + timedelta(hours=expiry) if expiry else None,
            pre_authorization_id=record.pre_authorization_id,
        )
        self.s.state.put(
            "decision",
            decision.decision_id,
            decision,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        # The harness writes the record; the decision is the person's, given in advance (the
        # event of that act, decision.preauthorized, carries their actor).
        self.s.events.append(
            execution.execution_id,
            "human.decision.recorded",
            decision.model_dump(mode="json"),
            actor=HARNESS_ACTOR,
        )
        self.s.events.append(
            execution.execution_id,
            "decision.preauthorization.applied",
            {
                "preAuthorizationId": record.pre_authorization_id,
                "decisionId": decision.decision_id,
                "digest": change_set.digest,
                "actorId": record.actor.actor_id,
            },
        )
        self.s.state.set_flag(f"{PREAUTH_USED_FLAG}:{execution.execution_id}", decision.decision_id)
        if self.engine.ladder.active:
            self.engine.ladder.after_decision(execution, decision)
        latest = self.engine.get_execution(execution.execution_id)
        self.engine._save_execution(
            latest.model_copy(
                update={"human_decision_id": decision.decision_id, "updated_at": utc_now()}
            )
        )
        return decision

    # ----- reports ------------------------------------------------------------------------------
    def brief(self, execution: Execution) -> dict[str, Any] | None:
        """What the decision brief shows: the lane, the change type and the pre-authorisation."""
        if not self.active:
            return None
        lane = self.lane(execution)
        record = self.pre_authorization_record(execution)
        profile: dict[str, Any] | None = None
        if execution.change_set_digest:
            known = self._flag(
                f"{PROFILE_FLAG}:{execution.execution_id}:{execution.change_set_digest}"
            )
            profile = known if isinstance(known, dict) else None
        return {
            "lane": {
                key: lane.get(key)
                for key in ("lane", "size", "rule", "reasons", "skip", "escalated")
            }
            | {"escalationReasons": lane.get("escalationReasons") or []}
            if lane
            else None,
            "changeType": profile.get("changeType") if profile else None,
            "riskFactors": profile.get("riskFactors") if profile else None,
            "preAuthorization": {
                "preAuthorizationId": record.pre_authorization_id,
                "actorId": record.actor.actor_id,
                "expiresAt": record.expires_at.isoformat(),
                "contractDigest": record.contract_digest,
                "applied": bool(
                    self.s.state.get_flag(f"{PREAUTH_USED_FLAG}:{execution.execution_id}")
                ),
            }
            if record
            else None,
            "planApproval": self.plan_approval_state(execution),
        }


__all__ = ["AFFECTED_TESTS_ID", "Friction"]
