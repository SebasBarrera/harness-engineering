"""Differential verification against the baseline (#7, #52) and the Ruff/Mypy ratchet.

Before 1.1 a mandatory validator that failed blocked the gate whether or not the change caused
the failure: Mypy failed on every governed brownfield run because of an error the repository
already had, and a step that inherited a red test from an earlier step was blamed for it.
Under ``verification.differential`` the harness runs each failing mandatory command validator
on the baseline (a temporary copy of the workspace with every change since DISCOVERY reverted)
and classifies the failure:

* the baseline passes: the failure is introduced (``INTRODUCED_ERROR``) and blocks;
* the baseline fails with the same problems: the failure is pre-existing
  (``PREEXISTING_ERROR``): the validator's latest result for the ChangeSet passes, with a LOW
  finding that says what already failed;
* the baseline fails with fewer problems: only the new problems are findings and block;
* the outputs cannot be compared (no problem could be parsed, the baseline could not be
  built or the validator did not run on it): the failure stays as it was (fail closed).

Problems are compared by tool rule, path and message (line numbers and other digits removed),
as the output parsers report them. A baseline run is cached by baseline digest and command.

Under ``verification.ratchet`` an optional validator (Ruff, Mypy) that fails is compared the
same way: more problems than on the baseline is a ``ratchet.regressed`` finding; never worse
than the baseline, without blocking on the debt the repository already had."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.domain.enums import (
    ActorType,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.models import (
    Actor,
    ChangeSet,
    Execution,
    Finding,
    PhaseExecution,
    ValidationResult,
)
from governed_harness.evidence import sha256_json
from governed_harness.orchestration.workspace_ops import changes_since, materialized
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.validators import CommandValidator, ValidationContext, ValidatorOutput
from governed_harness.validators.parsers import parse_output

if TYPE_CHECKING:
    from pathlib import Path

    from governed_harness.orchestration.agent_results import AgentResults

RATCHET_ID = "harness.ratchet"
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class RunSummary:
    status: ResultStatus
    keys: tuple[str, ...]
    evidence_refs: tuple[str, ...]


def issue_keys(stdout: str, stderr: str, workspace: Path) -> tuple[str, ...]:
    """Comparable identities of the problems a validator reported."""
    return tuple(
        sorted(
            f"{item.rule}|{item.path or ''}|{_DIGITS.sub('#', item.message)}"
            for item in parse_output(stdout, stderr, workspace)
        )
    )


class Differential:
    def __init__(self, results: AgentResults) -> None:
        self.results = results

    @property
    def differential(self) -> bool:
        config = self.results.project.verification
        return bool(config and config.differential)

    @property
    def ratchet(self) -> str:
        config = self.results.project.verification
        return (config.ratchet if config else None) or "off"

    def reclassify(
        self,
        execution: Execution,
        phase: PhaseExecution,
        change_set: ChangeSet,
        outputs: list[Any],
    ) -> list[Any]:
        if not self.differential and self.ratchet == "off":
            return outputs
        definitions = {
            item.validator_id: item for item in self.results.s.resolved.effective_validators
        }
        failing_mandatory = [
            index
            for index, output in enumerate(outputs)
            if self.differential
            and output.result.mandatory
            and output.result.status is ResultStatus.FAILED
            and output.result.validator_id in definitions
            and output.result.tool_invocation_ids
        ]
        failing_optional = [
            index
            for index, output in enumerate(outputs)
            if self.ratchet != "off"
            and not output.result.mandatory
            and output.result.status is ResultStatus.FAILED
            and output.result.validator_id in definitions
            and output.result.tool_invocation_ids
        ]
        if not failing_mandatory and not failing_optional:
            return outputs
        baseline = self.results.baseline_snapshot(execution)
        if baseline is None:
            return outputs
        validator_ids = [
            outputs[index].result.validator_id for index in failing_mandatory + failing_optional
        ]
        runs = self._baseline_runs(execution, change_set, baseline, validator_ids)
        updated = list(outputs)
        workspace = self.results.s.paths.workspace
        for index in failing_mandatory:
            output = outputs[index]
            run = runs.get(output.result.validator_id)
            if run is None:
                continue
            candidate = self._candidate_keys(output.result, workspace)
            replacement = self._classify(execution, change_set, output, candidate, run)
            if replacement is not None:
                updated[index] = replacement
        regressions: list[Finding] = []
        refs: list[str] = []
        for index in failing_optional:
            output = outputs[index]
            run = runs.get(output.result.validator_id)
            if run is None:
                continue
            candidate = self._candidate_keys(output.result, workspace)
            finding = self._ratchet(execution, output, candidate, run)
            refs.extend((*output.result.evidence_refs[:1], *run.evidence_refs[:1]))
            if finding is not None:
                regressions.append(finding)
        if failing_optional:
            enforce = self.ratchet == "enforce"
            blocking = enforce and bool(regressions)
            ratchet = self.results.record_validation(
                execution,
                validator_id=RATCHET_ID,
                digest=change_set.digest,
                status=ResultStatus.FAILED if blocking else ResultStatus.PASSED,
                kind=ValidationKind.INTRODUCED_ERROR if regressions else ValidationKind.SUCCESS,
                mandatory=enforce,
                summary=(
                    f"{len(regressions)} optional validator(s) report more problems than on "
                    "the baseline"
                    if regressions
                    else "No optional validator got worse than on the baseline"
                ),
                findings=tuple(regressions),
                evidence_refs=tuple(dict.fromkeys(refs)) or (change_set.diff_ref,),
            )
            updated.append(ValidatorOutput(ratchet, tuple(regressions)))
        return updated

    # ----- baseline runs ---------------------------------------------------------------------
    def _baseline_runs(
        self,
        execution: Execution,
        change_set: ChangeSet,
        baseline: Any,
        validator_ids: list[str],
    ) -> dict[str, RunSummary]:
        results = self.results
        engine = results.engine
        definitions = {item.validator_id: item for item in results.s.resolved.effective_validators}
        runs: dict[str, RunSummary] = {}
        pending: list[str] = []
        for validator_id in validator_ids:
            cached = results.flag_json(self._cache_key(baseline.digest, definitions[validator_id]))
            if isinstance(cached, dict):
                runs[validator_id] = RunSummary(
                    ResultStatus(cached["status"]),
                    tuple(cached["keys"]),
                    tuple(cached["evidenceRefs"]),
                )
            else:
                pending.append(validator_id)
        if not pending:
            return runs
        workspace = results.s.paths.workspace
        diff = changes_since(workspace, baseline)
        paths = [item.path for item in diff.changes]
        scratch = results.s.paths.harness_dir / "tmp"
        with materialized(workspace, scratch, baseline, paths) as copy:
            if copy is None:
                results.s.events.append(
                    execution.execution_id,
                    "verification.baseline.unavailable",
                    {"reason": "a changed path has no recorded text in the baseline"},
                )
                return runs
            for validator_id in pending:
                definition = engine._bounded_definition(definitions[validator_id])
                actor = Actor(
                    actor_type=ActorType.TOOL,
                    actor_id=f"validator.{validator_id}",
                    version="1",
                )
                output = CommandValidator(validator_id).execute(
                    ValidationContext(
                        execution_id=execution.execution_id,
                        workspace=copy,
                        task=engine.run_task(execution),
                        change_set=change_set,
                        definition=definition,
                        grants=grants_from_rules(
                            execution.execution_id, actor, results.s.resolved.effective_capabilities
                        ),
                        artifact_store=results.s.artifacts,
                        process_runner=SafeProcessRunner(copy),
                        provenance=engine._provenance(execution).model_copy(
                            update={"actor": actor}
                        ),
                        cancellation=CancellationToken(
                            lambda: engine.is_cancelled(execution.execution_id)
                        ),
                        max_output_bytes=results.project.runtime.max_output_bytes,
                    )
                )
                status = output.result.status
                keys = self._candidate_keys(output.result, copy)
                summary = RunSummary(status, keys, output.result.evidence_refs)
                runs[validator_id] = summary
                record = {
                    "validatorId": validator_id,
                    "baselineDigest": baseline.digest,
                    "status": status.value,
                    "keys": list(keys),
                    "evidenceRefs": list(output.result.evidence_refs),
                }
                ref = results.record_json(
                    execution,
                    PhaseId.VERIFICATION,
                    record,
                    kind="baseline-validation",
                    summary=f"{validator_id} on the baseline: {status.value}",
                )
                record["ref"] = ref
                if status in {ResultStatus.PASSED, ResultStatus.FAILED}:
                    results.set_flag_json(
                        self._cache_key(baseline.digest, definitions[validator_id]), record
                    )
                results.s.events.append(
                    execution.execution_id,
                    "verification.baseline.completed",
                    {
                        "validatorId": validator_id,
                        "status": status.value,
                        "problems": len(keys),
                        "evidenceRef": ref,
                    },
                )
        return runs

    @staticmethod
    def _cache_key(digest: str, definition: Any) -> str:
        command = sha256_json(list(definition.command or ()))
        return f"baselinerun:{digest}:{definition.validator_id}:{command}"

    def _candidate_keys(self, result: ValidationResult, workspace: Path) -> tuple[str, ...]:
        artifacts = self.results.s.artifacts
        try:
            report = json.loads(artifacts.get(result.evidence_refs[0]))
            stdout = artifacts.get(report["stdoutRef"]).decode("utf-8", "replace")
            stderr = artifacts.get(report["stderrRef"]).decode("utf-8", "replace")
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return ()
        return issue_keys(stdout, stderr, workspace)

    # ----- classification --------------------------------------------------------------------
    def _classify(
        self,
        execution: Execution,
        change_set: ChangeSet,
        output: ValidatorOutput,
        candidate: tuple[str, ...],
        baseline: RunSummary,
    ) -> ValidatorOutput | None:
        results = self.results
        validator_id = output.result.validator_id
        refs = tuple(dict.fromkeys((*output.result.evidence_refs, *baseline.evidence_refs)))
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{validator_id}", version="1")
        started = datetime.now(UTC)
        if baseline.status is ResultStatus.PASSED:
            finding = results.record_finding(
                execution,
                validator_id=validator_id,
                rule_id="differential.introduced",
                category="validation",
                severity=FindingSeverity.HIGH,
                message=f"{validator_id} fails on the ChangeSet and passes on the baseline",
                evidence_refs=refs,
                recommendation="The change introduced this failure; fix it before approval.",
                introduced=True,
                actor=actor,
            )
            result = results.record_validation(
                execution,
                validator_id=validator_id,
                digest=change_set.digest,
                status=ResultStatus.FAILED,
                kind=ValidationKind.INTRODUCED_ERROR,
                mandatory=True,
                summary=f"{validator_id} failed; the baseline passes (introduced)",
                findings=(*_original(results, output), finding),
                evidence_refs=refs,
                started_at=started,
            )
            return ValidatorOutput(result, ())
        if baseline.status is not ResultStatus.FAILED or not candidate:
            # Not comparable: keep the failure (fail closed) and say why.
            results.s.events.append(
                execution.execution_id,
                "verification.differential.inconclusive",
                {
                    "validatorId": validator_id,
                    "baselineStatus": baseline.status.value,
                    "parsedProblems": len(candidate),
                },
            )
            return None
        known = set(baseline.keys)
        introduced = [key for key in candidate if key not in known]
        preexisting = [key for key in candidate if key in known]
        note = results.record_finding(
            execution,
            validator_id=validator_id,
            rule_id="differential.preexisting",
            category="validation",
            severity=FindingSeverity.LOW,
            message=(
                f"{len(preexisting)} problem(s) reported by {validator_id} also fail on the "
                "baseline (pre-existing): " + "; ".join(_shown(preexisting))
            ),
            evidence_refs=refs,
            recommendation="Pre-existing problems do not block this ChangeSet; track them apart.",
            introduced=False,
            actor=actor,
        )
        if not introduced:
            result = results.record_validation(
                execution,
                validator_id=validator_id,
                digest=change_set.digest,
                status=ResultStatus.PASSED,
                kind=ValidationKind.PREEXISTING_ERROR,
                mandatory=True,
                summary=(
                    f"{validator_id} failed only with problems the baseline already had "
                    f"({len(preexisting)} pre-existing)"
                ),
                findings=(note,),
                evidence_refs=refs,
                started_at=started,
            )
            return ValidatorOutput(result, ())
        new = [
            results.record_finding(
                execution,
                validator_id=validator_id,
                rule_id="differential.introduced",
                category="validation",
                severity=FindingSeverity.HIGH,
                message=f"Introduced by the change: {_describe(key)}",
                path=key.split("|")[1] or None,
                evidence_refs=refs,
                recommendation="Fix this problem; it does not occur on the baseline.",
                introduced=True,
                actor=actor,
            )
            for key in introduced[:50]
        ]
        result = results.record_validation(
            execution,
            validator_id=validator_id,
            digest=change_set.digest,
            status=ResultStatus.FAILED,
            kind=ValidationKind.INTRODUCED_ERROR,
            mandatory=True,
            summary=(
                f"{validator_id}: {len(introduced)} introduced and {len(preexisting)} "
                "pre-existing problem(s)"
            ),
            findings=(*new, note),
            evidence_refs=refs,
            started_at=started,
        )
        return ValidatorOutput(result, ())

    def _ratchet(
        self,
        execution: Execution,
        output: ValidatorOutput,
        candidate: tuple[str, ...],
        baseline: RunSummary,
    ) -> Finding | None:
        validator_id = output.result.validator_id
        known = set(baseline.keys)
        worse = (
            [key for key in candidate if key not in known]
            if baseline.status is ResultStatus.FAILED
            else list(candidate) or ["(failed; the baseline passes)"]
        )
        if baseline.status is ResultStatus.FAILED and not candidate:
            return None
        if not worse:
            return None
        severity = FindingSeverity.HIGH if self.ratchet == "enforce" else FindingSeverity.LOW
        return self.results.record_finding(
            execution,
            validator_id=RATCHET_ID,
            rule_id="ratchet.regressed",
            category="validation",
            severity=severity,
            message=(
                f"{validator_id} reports {len(worse)} problem(s) the baseline does not have: "
                + "; ".join(_shown(worse))
            ),
            evidence_refs=tuple(
                dict.fromkeys((*output.result.evidence_refs, *baseline.evidence_refs))
            ),
            recommendation=(
                f"Fix the new {validator_id} problems; the project must not get worse than "
                "its baseline."
            ),
            introduced=True,
        )


def _original(results: AgentResults, output: ValidatorOutput) -> tuple[Finding, ...]:
    return tuple(
        results.s.state.get("finding", item, Finding) for item in output.result.finding_ids
    )


def _describe(key: str) -> str:
    rule, path, message = (key.split("|", 2) + ["", ""])[:3]
    location = f" at {path}" if path else ""
    return f"[{rule}]{location}: {message}"


def _shown(keys: list[str], limit: int = 5) -> list[str]:
    shown = [_describe(key) for key in keys[:limit]]
    if len(keys) > limit:
        shown.append(f"and {len(keys) - limit} more")
    return shown
