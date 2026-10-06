"""The verification ladder, certification and delivery hygiene of a run (#55), wired into the
engine's phases.

``RunEngine`` delegates every step of these settings to this class, as it does for the
agent-results settings: a project without any of them runs exactly the earlier code path
(``active`` is false and the engine never calls in). What each phase does:

* INTENT: localisation of M/L tasks, the operational contract and the interruption budget
  (``ladder_intake``);
* DISCOVERY: the environment preflight (``ladder_environment``);
* PLANNING: the verification plan (the rung each criterion requires, the rungs the profiles can
  reach here and why) and the preflight on the baseline: probes and frozen acceptance tests run
  before the change and the run is READY, PARTIAL or UNAVAILABLE; UNAVAILABLE stops the run
  until a person decides to continue uncertified (``harness verification decide``) or fixes the
  environment;
* VERIFICATION: the probes after the change, discriminating evidence and light mutation
  (``ladder_mutation``) and the certification of the ChangeSet;
* DECISION: the manual checklist, ticked by the person who decides; the gate's reason codes
  carry the certification;
* CLOSURE: deferred verifications are bound to the closure commit, and the change is staged,
  pushed, proposed as a pull request and commented as the contract authorises
  (``ladder_delivery``)."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from governed_harness.domain.enums import (
    DecisionKind,
    EvidenceKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
    VerificationLevel,
)
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    AcceptanceCriterion,
    CertificationRecord,
    ChangeSet,
    DeferredVerification,
    Execution,
    Finding,
    HumanAttachment,
    HumanDecision,
    PhaseExecution,
    ProbeDefinition,
    Task,
    ValidationResult,
    utc_now,
)
from governed_harness.ladder.capabilities import (
    CapabilityStatus,
    capability_statuses,
    load_catalog,
    profile_verification,
)
from governed_harness.ladder.certification import (
    CertificationInputs,
    NamedTest,
    ProbeOutcome,
    certify,
)
from governed_harness.ladder.probes import ProbeEvaluation
from governed_harness.orchestration.ladder_delivery import LadderDelivery
from governed_harness.orchestration.ladder_environment import EnvironmentPreflight
from governed_harness.orchestration.ladder_intake import IntentResult, LadderIntake
from governed_harness.orchestration.ladder_mutation import Mutation
from governed_harness.orchestration.ladder_probes import ProbeRun, run_probe
from governed_harness.orchestration.workspace_ops import Contents, materialized
from governed_harness.runtime import SafeProcessRunner
from governed_harness.validators import ValidatorOutput
from governed_harness.validators.traceability import (
    TestCorpus,
    load_test_corpus,
    tests_naming,
    word_pattern,
)

if TYPE_CHECKING:
    from governed_harness.configuration.ladder import LadderConfig, ProfileVerification
    from governed_harness.configuration.models import ProjectConfiguration
    from governed_harness.orchestration.engine import EngineServices, PhaseOutcome, RunEngine

CERTIFICATION_ID = "harness.certification"
CONTRACT_ID = "harness.contract"
SCOPE_RULE = "contract.scope-contradiction"
PREFLIGHT_DECISION_FLAG = "preflightdecision"
LEVEL_NOT_REACHED_RULE = "certification.level-not-reached"
PROBE_FAILED_RULE = "probe.assertion-failed"
PROBE_UNAVAILABLE_RULE = "probe.unavailable"
_GENERIC_TEST_SUFFIXES = (
    ".py",
    ".js",
    ".ts",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".cs",
    ".mjs",
    ".cjs",
)
_MAX_GENERIC_TEST_BYTES = 2_000_000
_SKIPPED_TEST_DIRECTORIES = frozenset({"node_modules", "build", "target", "vendor", "dist"})


@dataclass(frozen=True)
class PreflightResult:
    status: str  # READY, PARTIAL, UNAVAILABLE
    reasons: tuple[str, ...]
    ref: str


class VerificationLadder:
    def __init__(self, engine: RunEngine) -> None:
        self.engine = engine
        self.intake = LadderIntake(self)
        self.environment = EnvironmentPreflight(self)
        self.mutation = Mutation(self)
        self.delivery = LadderDelivery(self)
        self._capabilities: dict[str, list[CapabilityStatus]] = {}
        self._catalog: dict[str, ProfileVerification] | None = None

    # ----- configuration ------------------------------------------------------------------------
    @property
    def s(self) -> EngineServices:
        return self.engine.s

    @property
    def project(self) -> ProjectConfiguration:
        return self.s.resolved.project

    @property
    def config(self) -> LadderConfig | None:
        verification = self.project.verification
        ladder = verification.ladder if verification else None
        return ladder if ladder is not None and ladder.enabled else None

    @property
    def manual_checklist(self) -> bool:
        return bool(self.project.review and self.project.review.manual_checklist)

    @property
    def active(self) -> bool:
        """Whether any setting of #55 that acts on a run is configured; without one the engine
        runs the earlier path and never calls into this class."""
        project = self.project
        intake = project.intake
        verification = project.verification
        delivery = project.delivery
        context = project.context
        return any(
            (
                self.config is not None,
                bool(verification and verification.probes),
                self.mutation.enabled,
                self.manual_checklist,
                bool(intake and (intake.operational_contract or intake.interruptions)),
                project.environment is not None,
                bool(context and context.locate and context.locate.enabled),
                bool(
                    delivery
                    and (
                        delivery.stage is not None
                        or delivery.push is not None
                        or delivery.pull_request is not None
                        or delivery.comment is not None
                    )
                ),
            )
        )

    def profile_verifications(self) -> list[tuple[str, ProfileVerification]]:
        if self._catalog is None:
            self._catalog = load_catalog()
        return profile_verification(self.s.resolved.profiles, self._catalog)

    def capabilities(self, execution: Execution) -> list[CapabilityStatus]:
        """The profiles' capabilities and their availability here, detected once per run."""
        key = execution.execution_id
        if key not in self._capabilities:
            config = self.project.verification.ladder if self.project.verification else None
            self._capabilities[key] = capability_statuses(
                self.profile_verifications(),
                self.s.paths.workspace,
                {item.validator_id for item in self.s.resolved.effective_validators},
                run_detections=bool(config and config.capability_detection),
                timeout=config.detection_timeout if config else 10,
            )
        return self._capabilities[key]

    def level_validators(self, execution: Execution) -> dict[VerificationLevel, set[str]]:
        """Validators that establish L1 and L2, from the available capabilities; without a
        declared capability, every mandatory validator of the profiles establishes L1."""
        found: dict[VerificationLevel, set[str]] = {}
        for item in self.capabilities(execution):
            if item.available and item.validators:
                level = VerificationLevel(item.level)
                found.setdefault(level, set()).update(item.validators)
        if VerificationLevel.L1 not in found:
            found[VerificationLevel.L1] = {
                item.validator_id for item in self.s.resolved.effective_validators if item.mandatory
            }
        return found

    def probes(self, task: Task) -> list[ProbeDefinition]:
        """The project's probes and the task's (a task probe replaces a project probe with the
        same id)."""
        verification = self.project.verification
        merged: dict[str, ProbeDefinition] = {
            item.probe_id: item for item in (verification.probes if verification else None) or ()
        }
        merged.update({item.probe_id: item for item in task.probes})
        return list(merged.values())

    def manual_items(self, task: Task) -> list[dict[str, str]]:
        """What only a person can verify: the ``manual`` declaration of each criterion and the
        task's checklist."""
        if not self.manual_checklist:
            return []
        items = [
            {"itemId": criterion.criterion_id, "text": criterion.verification.manual}
            for criterion in task.acceptance_criteria
            if criterion.verification and criterion.verification.manual
        ]
        items.extend({"itemId": item.item_id, "text": item.text} for item in task.checklist)
        return items

    # ----- INTENT and DISCOVERY -----------------------------------------------------------------
    def intent(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        questions: tuple[Any, ...],
    ) -> IntentResult:
        return self.intake.intent(execution, phase, task, questions)

    def discovery(
        self, execution: Execution, phase: PhaseExecution, baseline_digest: str
    ) -> PhaseOutcome | None:
        return self.environment.run(execution, phase, baseline_digest)

    # ----- PLANNING: the verification plan and the preflight ------------------------------------
    def planning(
        self, execution: Execution, phase: PhaseExecution, task: Task
    ) -> PhaseOutcome | None:
        from governed_harness.orchestration.engine import PhaseOutcome

        if self.config is None:
            return None
        preflight = self.preflight(execution, phase, task)
        if preflight.status != "UNAVAILABLE":
            return None
        decision = self.preflight_decision(execution)
        if decision is not None:
            return None
        if self.config.mode != "enforce":
            # warn: the person sees it in the brief; nothing waits.
            self.engine.results.record_finding(
                execution,
                validator_id=CERTIFICATION_ID,
                rule_id="verification.preflight-unavailable",
                category="verification-ladder",
                severity=FindingSeverity.LOW,
                message="Verification preflight UNAVAILABLE: " + "; ".join(preflight.reasons[:5]),
                evidence_refs=(preflight.ref,),
            )
            return None
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            "Verification preflight UNAVAILABLE: "
            + "; ".join(preflight.reasons[:5])
            + f". Fix the environment and harness run continue --run {execution.execution_id}, "
            f"or decide: harness verification decide --run {execution.execution_id} "
            "--continue-uncertified --rationale '...'",
            (preflight.ref,),
        )

    def preflight_decision(self, execution: Execution) -> dict[str, Any] | None:
        value = self.engine.results.flag_json(f"{PREFLIGHT_DECISION_FLAG}:{execution.execution_id}")
        return value if isinstance(value, dict) else None

    def waived(self, execution: Execution) -> tuple[set[str], set[str]]:
        """Criteria and probes a person decided to continue without (preflight)."""
        decision = self.preflight_decision(execution)
        if not decision:
            return set(), set()
        return set(decision.get("criteria") or []), set(decision.get("probes") or [])

    def preflight(self, execution: Execution, phase: PhaseExecution, task: Task) -> PreflightResult:
        """The verification plan of every criterion and, under ``preflight``, the probes and the
        frozen acceptance tests on the baseline, classified READY, PARTIAL or UNAVAILABLE."""
        config = self.config
        assert config is not None
        results = self.engine.results
        capabilities = self.capabilities(execution)
        probes = self.probes(task)
        probe_runs: dict[str, ProbeRun] = {}
        friction = self.engine.friction
        if (
            config.preflight
            and probes
            and not (friction.active and friction.skips(execution, "preflight", PhaseId.PLANNING))
        ):
            # The fast lane of #58 leaves the probes on the baseline out (no long preflight).
            probe_runs = self._baseline_probes(execution, probes)
        reasons: list[str] = []
        partial: list[str] = []
        for probe_id, run in sorted(probe_runs.items()):
            if run.evaluation.readiness == "UNAVAILABLE":
                reasons.append(
                    f"probe {probe_id} cannot run on the baseline: "
                    + "; ".join(run.evaluation.problems[:2])
                )
            elif run.evaluation.passed:
                partial.append(
                    f"probe {probe_id} already passes on the baseline (it does not show the change)"
                )
        acceptance = results.acceptance.state(execution) or {}
        before = (acceptance.get("failBefore") or {}).get("status")
        if before in {"ERROR", "TIMED_OUT", "BLOCKED"}:
            reasons.append(f"the frozen acceptance tests cannot run on the baseline ({before})")
        plan = [
            self._criterion_plan(criterion, capabilities, probes, probe_runs, config)
            for criterion in task.acceptance_criteria
        ]
        for item in plan:
            if item["route"] == "unreachable" and item["declared"]:
                reasons.append(
                    f"criterion {item['criterionId']} requires {item['required']}: {item['why']}"
                )
            elif item["route"] in {"deferred", "manual"} and item["declared"]:
                partial.append(
                    f"criterion {item['criterionId']} reaches {item['required']} only "
                    f"{'after the run' if item['route'] == 'deferred' else 'with a person'}"
                )
        status = "UNAVAILABLE" if reasons else "PARTIAL" if partial else "READY"
        record = {
            "status": status,
            "reasons": reasons,
            "partial": partial,
            "criteria": plan,
            "capabilities": [item.as_dict() for item in capabilities],
            "probes": {key: value.as_dict() for key, value in sorted(probe_runs.items())},
            "acceptanceFailBefore": before,
            "preflight": bool(config.preflight),
        }
        ref = results.record_json(
            execution,
            PhaseId.PLANNING,
            record,
            kind="verification-plan",
            summary=f"Verification plan and preflight: {status}",
            evidence_kind=EvidenceKind.PLAN,
            supports=tuple(item.criterion_id for item in task.acceptance_criteria),
        )
        results.set_flag_json(
            f"preflight:{execution.execution_id}",
            {"status": status, "reasons": reasons, "ref": ref},
        )
        self.s.events.append(
            execution.execution_id,
            "verification.preflight.completed",
            {"status": status, "reasons": reasons, "partial": partial, "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        return PreflightResult(status, tuple(reasons), ref)

    def _baseline_probes(
        self, execution: Execution, probes: list[ProbeDefinition]
    ) -> dict[str, ProbeRun]:
        results = self.engine.results
        contents = results.baseline_contents(execution)
        diff = results.baseline_changes(execution)
        paths = [item.path for item in diff.changes] if diff is not None else []
        recorded = contents or Contents(lambda _path: False, lambda _path: None)
        runs: dict[str, ProbeRun] = {}
        with materialized(
            self.s.paths.workspace, self.s.paths.harness_dir / "tmp", recorded, paths
        ) as copy:
            for probe in probes:
                if copy is None:
                    continue
                runs[probe.probe_id] = run_probe(
                    self.engine, execution, probe, copy, SafeProcessRunner(copy)
                )
        return runs

    def _criterion_plan(
        self,
        criterion: AcceptanceCriterion,
        capabilities: list[CapabilityStatus],
        probes: list[ProbeDefinition],
        probe_runs: dict[str, ProbeRun],
        config: LadderConfig,
    ) -> dict[str, Any]:
        """Which rungs a criterion can reach here, the highest one, and why."""
        declaration = criterion.verification
        required = declaration.level if declaration else config.required_default
        linked = [
            probe
            for probe in probes
            if (declaration and declaration.probe == probe.probe_id)
            or criterion.criterion_id in probe.criteria
        ]
        reachable: dict[VerificationLevel, str] = {VerificationLevel.L0: "the mandatory validators"}
        for item in capabilities:
            level = VerificationLevel(item.level)
            if item.available and not item.probes:
                reachable.setdefault(level, f"{item.provides} ({item.profile_id})")
        for probe in linked:
            run = probe_runs.get(probe.probe_id)
            if run is None or run.evaluation.readiness == "READY":
                reachable.setdefault(probe.level, f"probe {probe.probe_id}")
        highest = max(reachable, key=lambda level: level.rank)
        missing_probe = (
            required.rank >= VerificationLevel.L3.rank
            and required is not VerificationLevel.L5
            and not linked
        )
        if any(level.rank >= required.rank for level in reachable):
            route, why = (
                "local",
                reachable[
                    min(
                        (level for level in reachable if level.rank >= required.rank),
                        key=lambda level: level.rank,
                    )
                ],
            )
        elif declaration and declaration.manual and required is VerificationLevel.L5:
            route = "manual" if self.manual_checklist else "unreachable"
            why = (
                f"a person checks: {declaration.manual}"
                if self.manual_checklist
                else "manual checks need review.manualChecklist"
            )
        elif declaration and declaration.deferred:
            route, why = "deferred", f"verified after the run: {declaration.deferred}"
        else:
            route = "unreachable"
            broken = [
                f"probe {probe.probe_id} cannot run here ("
                + "; ".join(probe_runs[probe.probe_id].evaluation.problems[:2])
                + ")"
                for probe in linked
                if probe.probe_id in probe_runs
                and probe_runs[probe.probe_id].evaluation.readiness == "UNAVAILABLE"
            ]
            unavailable = [
                f"{item.level} {item.provides} needs "
                + ", ".join(d.description for d in item.detections if not d.available)
                for item in capabilities
                if not item.available
                and not item.probes
                and VerificationLevel(item.level).rank >= required.rank
            ]
            reason = (
                "; ".join(broken[:2])
                if broken
                else "no probe is linked to it"
                if missing_probe
                else "; ".join(unavailable[:2]) or f"no capability reaches {required.value} here"
            )
            why = f"{reason}; highest reachable here is {highest.value} ({reachable[highest]})"
        return {
            "criterionId": criterion.criterion_id,
            "declared": declaration is not None,
            "required": required.value,
            "highestReachable": highest.value,
            "target": required.value if route != "unreachable" else highest.value,
            "route": route,
            "why": why,
            "probes": [probe.probe_id for probe in linked],
        }

    # ----- VERIFICATION -------------------------------------------------------------------------
    def verification(
        self,
        execution: Execution,
        phase: PhaseExecution,
        change_set: ChangeSet,
        outputs: list[Any],
    ) -> list[ValidatorOutput]:
        task = self.engine.run_task(execution)
        added: list[ValidatorOutput] = []
        scope = self._scope_check(execution, change_set, task)
        if scope is not None:
            added.append(scope)
        probe_outputs, evaluations = self._verify_probes(execution, change_set, task)
        added.extend(probe_outputs)
        friction = self.engine.friction
        mutation = (
            None
            if friction.active
            and (
                friction.skips(execution, "mutation", PhaseId.VERIFICATION)
                or friction.tests_exempt(execution, change_set, "light mutation") is not None
            )
            else self.mutation.run(execution, phase, change_set, [*outputs, *added])
        )
        if mutation is not None:
            added.append(mutation)
        if self.config is not None:
            added.extend(
                self._certification_validation(
                    execution, change_set, task, [*outputs, *added], evaluations
                )
            )
        return added

    def _scope_check(
        self, execution: Execution, change_set: ChangeSet, task: Task
    ) -> ValidatorOutput | None:
        """A ChangeSet path outside the contract's ``scopePaths`` contradicts the confirmed
        scope. Under the ``scope-contradiction`` stop condition (``intake.interruptions``) the
        validation is ``BLOCKED``: the run stops for a person instead of a correction."""
        contract = task.contract
        if contract is None or not contract.scope_paths:
            return None
        outside = sorted(
            item.path
            for item in change_set.files
            if not any(fnmatch.fnmatchcase(item.path, pattern) for pattern in contract.scope_paths)
        )
        results = self.engine.results
        intake = self.project.intake
        stops = intake.interruptions.conditions if intake and intake.interruptions else ()
        findings: list[Finding] = []
        if outside:
            findings.append(
                results.record_finding(
                    execution,
                    validator_id=CONTRACT_ID,
                    rule_id=SCOPE_RULE,
                    category="operational-contract",
                    severity=FindingSeverity.HIGH,
                    message=(
                        f"{len(outside)} changed path(s) outside the contract's scope "
                        f"({', '.join(contract.scope_paths)}): {', '.join(outside[:10])}"
                    ),
                    path=outside[0],
                    recommendation="Revert the out-of-scope change or revise the contract.",
                )
            )
            if "scope-contradiction" in stops:
                self.s.events.append(
                    execution.execution_id,
                    "stop.condition",
                    {
                        "condition": "scope-contradiction",
                        "detail": f"changed outside the contract's scope: {', '.join(outside[:10])}",
                        "changeSetDigest": change_set.digest,
                    },
                )
        status = (
            ResultStatus.PASSED
            if not outside
            else ResultStatus.BLOCKED
            if "scope-contradiction" in stops
            else ResultStatus.FAILED
        )
        ref = results.record_json(
            execution,
            PhaseId.VERIFICATION,
            {"scopePaths": list(contract.scope_paths), "outside": outside},
            kind="contract-scope",
            summary=f"Contract scope: {len(outside)} path(s) outside",
        )
        result = results.record_validation(
            execution,
            validator_id=CONTRACT_ID,
            digest=change_set.digest,
            status=status,
            kind=ValidationKind.SUCCESS if not outside else ValidationKind.POLICY_VIOLATION,
            mandatory=True,
            summary=f"{len(outside)} changed path(s) outside the contract's scope",
            findings=tuple(findings),
            evidence_refs=(ref,),
        )
        return ValidatorOutput(result, tuple(findings))

    def _verify_probes(
        self, execution: Execution, change_set: ChangeSet, task: Task
    ) -> tuple[list[ValidatorOutput], dict[str, ProbeEvaluation]]:
        results = self.engine.results
        outputs: list[ValidatorOutput] = []
        evaluations: dict[str, ProbeEvaluation] = {}
        _, waived_probes = self.waived(execution)
        enforce = self.config is not None and self.config.mode == "enforce"
        severity = FindingSeverity.HIGH if enforce else FindingSeverity.LOW
        for probe in self.probes(task):
            validator_id = f"probe.{probe.probe_id}"
            if probe.probe_id in waived_probes:
                ref = results.record_json(
                    execution,
                    PhaseId.VERIFICATION,
                    {"probeId": probe.probe_id, "waived": True},
                    kind="probe-report",
                    summary=f"Probe {probe.probe_id} waived in preflight",
                )
                result = results.record_validation(
                    execution,
                    validator_id=validator_id,
                    digest=change_set.digest,
                    status=ResultStatus.NOT_APPLICABLE,
                    kind=ValidationKind.SUCCESS,
                    mandatory=False,
                    summary="waived: a person decided in preflight to continue without it",
                    evidence_refs=(ref,),
                )
                outputs.append(ValidatorOutput(result, ()))
                continue
            run = run_probe(
                self.engine,
                execution,
                probe,
                self.s.paths.workspace,
                self.engine._runner(execution),
            )
            evaluation = run.evaluation
            evaluations[probe.probe_id] = evaluation
            ref = results.record_json(
                execution,
                PhaseId.VERIFICATION,
                run.as_dict(),
                kind="probe-report",
                summary=f"Probe {probe.probe_id}: {evaluation.readiness}, "
                + ("passed" if evaluation.passed else "did not pass"),
                evidence_kind=EvidenceKind.TEST_REPORT,
                supports=probe.criteria,
            )
            findings: list[Finding] = []
            if evaluation.readiness == "UNAVAILABLE":
                status = ResultStatus.BLOCKED
                findings.append(
                    results.record_finding(
                        execution,
                        validator_id=validator_id,
                        rule_id=PROBE_UNAVAILABLE_RULE,
                        category="verification-ladder",
                        severity=FindingSeverity.HIGH,
                        message=(
                            f"Probe {probe.probe_id} could not run: "
                            + "; ".join(evaluation.problems[:3])
                        ),
                        evidence_refs=(ref,),
                        recommendation="Make the probe's command available or waive it in "
                        "preflight (harness verification decide --continue-uncertified).",
                    )
                )
            elif not evaluation.passed:
                status = ResultStatus.FAILED
                for item in evaluation.results:
                    if item.passed:
                        continue
                    findings.append(
                        results.record_finding(
                            execution,
                            validator_id=validator_id,
                            rule_id=PROBE_FAILED_RULE,
                            category="verification-ladder",
                            severity=severity,
                            message=(
                                f"Probe {probe.probe_id}"
                                + (f" variant {item.variant}" if item.variant else "")
                                + f", assertion {item.index} ({item.kind}): {item.detail}"
                            ),
                            evidence_refs=(ref,),
                            introduced=True,
                        )
                    )
            else:
                status = ResultStatus.PASSED
            result = results.record_validation(
                execution,
                validator_id=validator_id,
                digest=change_set.digest,
                status=status,
                kind=ValidationKind.SUCCESS
                if status is ResultStatus.PASSED
                else ValidationKind.VALIDATION_FAILURE
                if status is ResultStatus.FAILED
                else ValidationKind.CONFIGURATION_ERROR,
                mandatory=enforce or status is ResultStatus.BLOCKED,
                summary=(
                    f"{len(evaluation.variants)} variant(s): "
                    + (
                        "every assertion held"
                        if evaluation.passed
                        else f"{len(findings)} problem(s)"
                    )
                ),
                findings=tuple(findings),
                evidence_refs=(ref,),
            )
            outputs.append(ValidatorOutput(result, tuple(findings)))
        return outputs, evaluations

    # ----- certification --------------------------------------------------------------------------
    def corpus(self) -> TestCorpus:
        technologies = [profile.technology for profile in self.s.resolved.profiles]
        return load_test_corpus(self.s.paths.workspace, technologies)

    def _generic_tests(self, identifiers: list[str]) -> dict[str, list[NamedTest]]:
        """For technologies the traceability corpus does not parse (Go, Rust, JVM, Swift),
        files under a test directory or named after tests that mention the identifier."""
        found: dict[str, list[NamedTest]] = {}
        root = self.s.paths.workspace
        patterns = {identifier: word_pattern(identifier) for identifier in identifiers}
        for current, directories, files in os.walk(root):
            directories[:] = sorted(
                item
                for item in directories
                if not item.startswith(".") and item not in _SKIPPED_TEST_DIRECTORIES
            )
            for name in sorted(files):
                path = Path(current) / name
                relative = PurePosixPath(path.relative_to(root).as_posix())
                if (
                    path.suffix not in _GENERIC_TEST_SUFFIXES
                    or path.is_symlink()
                    or "test" not in relative.as_posix().lower()
                    or path.stat().st_size > _MAX_GENERIC_TEST_BYTES
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for identifier, pattern in patterns.items():
                    if pattern.search(text):
                        found.setdefault(identifier, []).append(
                            NamedTest(relative.as_posix(), relative.as_posix())
                        )
        return found

    def named_tests(self, task: Task) -> dict[str, list[NamedTest]]:
        corpus = self.corpus()
        technologies = {profile.technology for profile in self.s.resolved.profiles}
        names: dict[str, list[str]] = {}
        for criterion in task.acceptance_criteria:
            declared = list(criterion.verification.tests) if criterion.verification else []
            names[criterion.criterion_id] = [criterion.criterion_id, *declared]
        found: dict[str, list[NamedTest]] = {}
        generic = (
            self._generic_tests(sorted({name for values in names.values() for name in values}))
            if technologies - {"python", "node"}
            else {}
        )
        for criterion_id, identifiers in names.items():
            tests: list[NamedTest] = []
            for identifier in identifiers:
                tests.extend(
                    NamedTest(item.node_id, item.path) for item in tests_naming(identifier, corpus)
                )
                tests.extend(generic.get(identifier, []))
            unique = {item.node_id: item for item in tests}
            if unique:
                found[criterion_id] = list(unique.values())
        return found

    def _acceptance_named(
        self, execution: Execution, task: Task
    ) -> tuple[bool, dict[str, list[NamedTest]]]:
        results = self.engine.results
        state = results.acceptance.state(execution) or {}
        frozen = sorted((state.get("frozen") or {}).keys())
        if state.get("status") != "APPROVED" or not frozen:
            return False, {}
        named: dict[str, list[NamedTest]] = {}
        for criterion in task.acceptance_criteria:
            pattern = word_pattern(criterion.criterion_id)
            for path in frozen:
                target = self.s.paths.workspace / path
                if target.is_file() and pattern.search(
                    target.read_text(encoding="utf-8", errors="replace")
                ):
                    named.setdefault(criterion.criterion_id, []).append(NamedTest(path, path))
        return True, named

    def certification_inputs(
        self,
        execution: Execution,
        task: Task,
        digest: str,
        validations: list[ValidationResult],
        evaluations: dict[str, ProbeEvaluation] | None = None,
        checked: set[str] | None = None,
        decided: bool = False,
    ) -> CertificationInputs:
        config = self.config
        assert config is not None
        own = {CERTIFICATION_ID}
        mandatory = [
            item for item in validations if item.mandatory and item.validator_id not in own
        ]
        passed = {item.validator_id for item in validations if item.status is ResultStatus.PASSED}
        probes: list[ProbeOutcome] = []
        definitions = {probe.probe_id: probe for probe in self.probes(task)}
        if evaluations is None:
            evaluations = {}
            for item in validations:
                if item.validator_id.startswith("probe.") and item.validator_id[6:] in definitions:
                    evaluations[item.validator_id[6:]] = ProbeEvaluation(
                        item.validator_id[6:],
                        "UNAVAILABLE" if item.status is ResultStatus.BLOCKED else "READY",
                        item.status is ResultStatus.PASSED,
                    )
        refs = {
            item.validator_id: item.evidence_refs[0] for item in validations if item.evidence_refs
        }
        for probe_id, evaluation in evaluations.items():
            definition = definitions.get(probe_id)
            if definition is None:
                continue
            probes.append(
                ProbeOutcome(
                    probe_id,
                    definition.level,
                    definition.criteria,
                    evaluation.passed,
                    evaluation.readiness,
                    refs.get(f"probe.{probe_id}"),
                )
            )
        acceptance_ran = any(
            item.validator_id == "harness.acceptance-tests" and item.status is ResultStatus.PASSED
            for item in validations
        )
        approved, accepted = self._acceptance_named(execution, task)
        waived_criteria, _ = self.waived(execution)
        return CertificationInputs(
            default_level=config.required_default,
            verification_passed=bool(mandatory)
            and all(item.status is ResultStatus.PASSED for item in mandatory),
            digest=digest,
            passed_validators=passed,
            level_validators=self.level_validators(execution),
            tests=self.named_tests(task),
            acceptance_passed=approved and acceptance_ran,
            acceptance_tests=accepted,
            probes=probes,
            deferred=self.deferred_items(execution, digest),
            checked=checked or set(),
            decided=decided,
            waived=waived_criteria,
            validation_refs=refs,
        )

    def record_certification(
        self,
        execution: Execution,
        task: Task,
        inputs: CertificationInputs,
        trigger: str,
    ) -> CertificationRecord:
        status, criteria = certify(task.acceptance_criteria, inputs)
        record = CertificationRecord(
            certification_id=new_id("certification"),
            execution_id=execution.execution_id,
            change_set_digest=inputs.digest,
            status=status,
            trigger=trigger,  # type: ignore[arg-type]
            criteria=criteria,
        )
        self.s.state.put(
            "certification",
            record.certification_id,
            record,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
        )
        ref = self.engine.results.record_json(
            execution,
            PhaseId.DECISION if trigger != "VERIFICATION" else PhaseId.VERIFICATION,
            record.model_dump(mode="json", by_alias=True),
            kind="certification",
            summary=f"Certification {status} ({trigger.lower()})",
            evidence_kind=EvidenceKind.GATE,
            supports=tuple(item.criterion_id for item in criteria),
        )
        self.s.events.append(
            execution.execution_id,
            "certification.recorded",
            {
                "certificationId": record.certification_id,
                "status": status,
                "trigger": trigger,
                "changeSetDigest": inputs.digest,
                "criteria": {item.criterion_id: item.status for item in criteria},
                "evidenceRef": ref,
            },
        )
        return record

    def latest_certification(
        self, execution_id: str, digest: str | None
    ) -> CertificationRecord | None:
        records = [
            item
            for item in self.s.state.list(
                "certification", CertificationRecord, execution_id=execution_id
            )
            if digest is None or item.change_set_digest == digest
        ]
        return max(records, key=lambda item: item.created_at) if records else None

    def _certification_validation(
        self,
        execution: Execution,
        change_set: ChangeSet,
        task: Task,
        outputs: list[Any],
        evaluations: dict[str, ProbeEvaluation],
    ) -> list[ValidatorOutput]:
        config = self.config
        assert config is not None
        self.ensure_deferred(execution, task, change_set.digest)
        validations = [item.result for item in outputs]
        inputs = self.certification_inputs(
            execution, task, change_set.digest, validations, evaluations
        )
        record = self.record_certification(execution, task, inputs, "VERIFICATION")
        others_failed = any(
            item.mandatory and item.status is not ResultStatus.PASSED for item in validations
        )
        results = self.engine.results
        severity = FindingSeverity.HIGH if config.mode == "enforce" else FindingSeverity.LOW
        findings: list[Finding] = []
        for item in record.criteria:
            if not item.declared or item.status != "NOT_CERTIFIED":
                continue
            findings.append(
                results.record_finding(
                    execution,
                    validator_id=CERTIFICATION_ID,
                    rule_id=LEVEL_NOT_REACHED_RULE,
                    category="verification-ladder",
                    severity=severity,
                    message=f"Criterion {item.criterion_id} {item.reason}",
                    recommendation=(
                        "Add the evidence the rung needs (a test that names the criterion for "
                        "L1, a probe for L3, deferred evidence for L4, a manual check for L5), or "
                        "decide with APPROVE_EXCEPTION."
                    ),
                )
            )
        if others_failed:
            # The verification already fails; the certification is recorded, the gate input
            # is the failing validators.
            return []
        blocking = config.mode == "enforce" and bool(findings)
        ref = self.latest_ref(execution)
        result = results.record_validation(
            execution,
            validator_id=CERTIFICATION_ID,
            digest=change_set.digest,
            status=ResultStatus.FAILED if blocking else ResultStatus.PASSED,
            kind=ValidationKind.VALIDATION_FAILURE if blocking else ValidationKind.SUCCESS,
            mandatory=config.mode == "enforce",
            summary=f"certification {record.status}: "
            + ", ".join(f"{item.criterion_id} {item.status}" for item in record.criteria[:10]),
            findings=tuple(findings),
            evidence_refs=(ref,) if ref else (change_set.diff_ref,),
        )
        return [ValidatorOutput(result, tuple(findings))]

    def latest_ref(self, execution: Execution) -> str | None:
        events = [
            event
            for event in self.s.events.list(execution.execution_id)
            if event.event_type == "certification.recorded"
        ]
        return str(events[-1].payload.get("evidenceRef")) if events else None

    def gate_reasons(self, execution: Execution, digest: str) -> tuple[str, ...]:
        if self.config is None:
            return ()
        record = self.latest_certification(execution.execution_id, digest)
        return (f"CERTIFICATION_{record.status}",) if record else ()

    # ----- deferred verification ----------------------------------------------------------------
    def deferred_items(
        self, execution: Execution, digest: str | None
    ) -> list[DeferredVerification]:
        now = utc_now()
        items: list[DeferredVerification] = []
        for item in self.s.state.list(
            "deferred_verification", DeferredVerification, execution_id=execution.execution_id
        ):
            if digest is not None and item.change_set_digest != digest:
                continue
            if item.status == "PENDING" and item.expires_at <= now:
                item = item.model_copy(update={"status": "EXPIRED"})
            items.append(item)
        return items

    def ensure_deferred(self, execution: Execution, task: Task, digest: str) -> None:
        """One pending item per criterion that declares ``deferred``, bound to the digest."""
        config = self.config
        assert config is not None
        existing = {
            item.criterion_id
            for item in self.s.state.list(
                "deferred_verification", DeferredVerification, execution_id=execution.execution_id
            )
            if item.change_set_digest == digest
        }
        for criterion in task.acceptance_criteria:
            declaration = criterion.verification
            if (
                declaration is None
                or not declaration.deferred
                or criterion.criterion_id in existing
            ):
                continue
            now = utc_now()
            item = DeferredVerification(
                deferred_id=new_id("deferred"),
                item_id=f"D-{criterion.criterion_id}",
                project_id=execution.project_id,
                execution_id=execution.execution_id,
                task_id=task.task_id,
                criterion_id=criterion.criterion_id,
                level=declaration.level
                if declaration.level.rank >= VerificationLevel.L4.rank
                else VerificationLevel.L4,
                where=declaration.deferred,
                change_set_digest=digest,
                created_at=now,
                expires_at=now + timedelta(days=config.expiry_days),
            )
            self.s.state.put(
                "deferred_verification",
                item.deferred_id,
                item,
                execution_id=execution.execution_id,
                project_id=execution.project_id,
            )
            self.s.events.append(
                execution.execution_id,
                "verification.deferred.created",
                item.model_dump(mode="json", by_alias=True),
            )

    def bind_commit(self, execution: Execution, digest: str, commit: str) -> None:
        for item in self.deferred_items(execution, digest):
            if item.status != "PENDING" or item.commit == commit:
                continue
            bound = item.model_copy(update={"commit": commit})
            self.s.state.put(
                "deferred_verification",
                bound.deferred_id,
                bound,
                execution_id=execution.execution_id,
                project_id=execution.project_id,
            )
            self.s.events.append(
                execution.execution_id,
                "verification.deferred.bound",
                {"deferredId": item.deferred_id, "itemId": item.item_id, "commit": commit},
            )

    # ----- DECISION: the manual checklist -------------------------------------------------------
    def check_decision(
        self,
        execution: Execution,
        decision: DecisionKind,
        checked: tuple[str, ...],
    ) -> None:
        task = self.engine.run_task(execution)
        items = {item["itemId"] for item in self.manual_items(task)}
        if checked and not self.manual_checklist:
            raise ConfigurationError("--check needs review.manualChecklist: true in project.yaml")
        unknown = sorted(set(checked) - items)
        if unknown:
            raise ConfigurationError(
                f"unknown checklist item(s): {', '.join(unknown)}; the run's items are "
                + (", ".join(sorted(items)) or "none")
            )
        if decision is DecisionKind.APPROVE and items - set(checked):
            raise PolicyViolationError(
                "these checklist items need a person's check before APPROVE: "
                + ", ".join(sorted(items - set(checked)))
                + " (use --check for each, or APPROVE_EXCEPTION with a rationale)"
            )

    def after_decision(self, execution: Execution, record: HumanDecision) -> None:
        """Recompute the certification with the items the person ticked."""
        if self.config is None:
            return
        task = self.engine.run_task(execution)
        validations = self.engine._latest_validations(
            execution.execution_id, record.change_set_digest
        )
        inputs = self.certification_inputs(
            execution,
            task,
            record.change_set_digest,
            validations,
            checked=set(record.checked_items),
            decided=True,
        )
        self.record_certification(execution, task, inputs, "DECISION")

    def after_evidence(self, execution: Execution) -> CertificationRecord | None:
        """Recompute the certification once deferred evidence was attached."""
        if self.config is None or not execution.change_set_digest:
            return None
        task = self.engine.run_task(execution)
        digest = execution.change_set_digest
        decisions = [
            item
            for item in self.s.state.list(
                "decision", HumanDecision, execution_id=execution.execution_id
            )
            if item.change_set_digest == digest
        ]
        latest = max(decisions, key=lambda item: item.decided_at) if decisions else None
        validations = self.engine._latest_validations(execution.execution_id, digest)
        inputs = self.certification_inputs(
            execution,
            task,
            digest,
            validations,
            checked=set(latest.checked_items) if latest else set(),
            decided=latest is not None,
        )
        return self.record_certification(execution, task, inputs, "EVIDENCE")

    # ----- IMPLEMENTATION: what the agent receives ----------------------------------------------
    def implement_extras(self, execution: Execution, task: Task) -> dict[str, Any]:
        """``locations`` (the locate call) and ``attachments`` (a person's intake context): only
        when there is something to send, so other requests keep their form and size."""
        extra: dict[str, Any] = {}
        located = self.intake.located(execution)
        if located:
            extra["locations"] = located["locations"]
        attachments = self.intake_attachments(task)
        if attachments:
            extra["attachments"] = attachments
        return extra

    def located_paths(self, execution: Execution) -> list[str]:
        located = self.intake.located(execution)
        return [item["path"] for item in located["locations"]] if located else []

    def intake_attachments(self, task: Task) -> list[dict[str, Any]]:
        from governed_harness.intake import task_digest

        digest = task_digest(task)
        attachments = [
            item
            for item in self.s.state.list(
                "human_attachment", HumanAttachment, project_id=task.project_id
            )
            if item.task_id == task.task_id
            and item.execution_id is None
            and item.task_digest == digest
        ]
        return [
            {
                "attachmentId": item.attachment_id,
                "fileName": item.file_name,
                "mediaType": item.media_type,
                "digest": item.digest,
                "note": item.note,
                "path": str(self.s.artifacts.path_for(item.artifact_ref)),
            }
            for item in attachments
        ]


__all__ = [
    "CERTIFICATION_ID",
    "LEVEL_NOT_REACHED_RULE",
    "PROBE_FAILED_RULE",
    "PROBE_UNAVAILABLE_RULE",
    "PreflightResult",
    "VerificationLadder",
]
