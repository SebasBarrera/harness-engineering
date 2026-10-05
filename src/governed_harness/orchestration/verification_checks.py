"""Deterministic validators of the agent-results settings in VERIFICATION (#40, #52).

Each enabled check becomes one validation result for the current ChangeSet, with its issues as
findings. Under ``enforce`` a check is mandatory and fails when it finds something the gate
blocks on, so the correction loop (``runtime.verificationCorrections``) sends the agent the
finding with its location; under ``warn`` its findings are ``LOW`` and the check never fails.
The checks read the unredacted diff in memory only (as the independent review does); what is
stored is the redacted record of each finding."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.checks import DiffFile, Issue, is_test_path, parse_unified_diff
from governed_harness.checks.architecture import ArchitectureLimits, check_architecture
from governed_harness.checks.constraints import check_constraints, constraints_from_task
from governed_harness.checks.interface import check_interface, declared_names
from governed_harness.checks.risk import detect_risk_factors
from governed_harness.checks.sarif import read_sarif
from governed_harness.checks.secrets import scan_secrets
from governed_harness.checks.security_patterns import check_security_patterns
from governed_harness.checks.suite_quality import (
    assertion_free_tests,
    changed_line_coverage,
    pytest_node_ids,
    untested_interface_methods,
)
from governed_harness.checks.weakened import check_weakened_controls
from governed_harness.configuration.agent_results import DEFAULT_RISK_ACTIONS, RISK_FACTORS
from governed_harness.configuration.models import ValidatorDefinition
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
    Task,
)
from governed_harness.runtime import CancellationToken, WorkspaceSnapshot
from governed_harness.runtime.process_runner import CommandSpec
from governed_harness.validators import CommandValidator, ValidationContext, ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.orchestration.agent_results import AgentResults

INTERFACE_ID = "harness.interface"
ARCHITECTURE_ID = "harness.architecture"
SECURITY_ID = "harness.security-patterns"
CONSTRAINTS_ID = "harness.constraints"
WEAKENED_ID = "harness.weakened-controls"
TEST_QUALITY_ID = "harness.test-quality"
SECRETS_ID = "harness.secrets"
SARIF_ID = "harness.sarif"
RISK_ID = "harness.risk-factors"
CHANGE_REQUESTS_ID = "harness.change-requests"
RISK_ACK_FLAG = "riskack"

_SARIF_SEVERITY = {
    "error": FindingSeverity.HIGH,
    "warning": FindingSeverity.MEDIUM,
    "note": FindingSeverity.LOW,
}
_RISK_SEVERITY = {
    "block": FindingSeverity.HIGH,
    "acknowledge": FindingSeverity.MEDIUM,
    "inform": FindingSeverity.LOW,
}
_SEVERITY_RANK = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}


def declared_interfaces(task: Task) -> list[tuple[str, str]]:
    """``(stub, module)`` pairs a task declares in ``metadata.interface``: an object with
    ``stub`` and ``module`` paths, or a list of them."""
    raw = task.metadata.get("interface")
    entries = raw if isinstance(raw, list) else [raw] if raw else []
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("stub") and entry.get("module"):
            pairs.append((str(entry["stub"]), str(entry["module"])))
    return pairs


class VerificationChecks:
    def __init__(self, results: AgentResults) -> None:
        self.results = results

    @property
    def config(self) -> Any:
        return self.results.project.verification

    def blocking(self) -> set[FindingSeverity]:
        names = self.results.s.resolved.effective_policies.get(
            "findingBlockSeverities", ["HIGH", "CRITICAL"]
        )
        return {FindingSeverity(str(name)) for name in names}

    # ----- entry point ----------------------------------------------------------------------
    def run(
        self, execution: Execution, phase: PhaseExecution, change_set: ChangeSet
    ) -> list[ValidatorOutput]:
        engine = self.results.engine
        raw = engine._compute_owned_diff(execution).unified_diff.decode("utf-8", "replace")
        diff = parse_unified_diff(raw)
        outputs: list[ValidatorOutput] = []
        owned = self.results.stop_line.owned_paths_output(execution, change_set)
        if owned is not None:
            outputs.append(owned)
        reproduced = self.results.corrections.validation(execution, change_set)
        if reproduced is not None:
            outputs.append(reproduced)
        requests = self.results.change_requests(execution)
        if requests:
            outputs.append(self._change_requests(execution, change_set, requests, diff))
        config = self.config
        if config is None:
            return outputs
        task = engine.run_task(execution)
        files = current_files(self.results.s.paths.workspace, diff)
        for item in pure_checks(
            config,
            task,
            diff,
            files,
            self.results.s.paths.workspace,
            self.results.baseline_snapshot(execution),
        ):
            outputs.append(
                self._output(
                    execution,
                    change_set,
                    item.validator_id,
                    item.policy,
                    item.issues,
                    item.label,
                    started=item.started,
                )
            )
        if config.test_quality is not None:
            outputs.append(self._test_quality(execution, change_set, task, diff, files))
        if config.invariants:
            outputs.extend(self._invariants(execution, change_set, task))
        if config.sarif:
            outputs.append(self._sarif(execution, change_set))
        if config.risk_factors is not None:
            outputs.append(self._risk(execution, change_set, task, diff))
        return outputs

    # ----- helpers ---------------------------------------------------------------------------
    def _output(
        self,
        execution: Execution,
        change_set: ChangeSet,
        validator_id: str,
        policy: str,
        issues: list[Issue],
        label: str,
        *,
        started: datetime,
        extra_evidence: tuple[str, ...] = (),
        mandatory: bool | None = None,
        report: dict[str, Any] | None = None,
    ) -> ValidatorOutput:
        results = self.results
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{validator_id}", version="1")
        warn = policy == "warn"
        findings: list[Finding] = []
        report_ref = results.s.artifacts.put_json(
            {
                "validatorId": validator_id,
                "changeSetDigest": change_set.digest,
                "policy": policy,
                "issues": [
                    {
                        "ruleId": item.rule_id,
                        "severity": item.severity,
                        "path": item.path,
                        "line": item.line,
                        "message": item.message,
                    }
                    for item in issues
                ],
                **(report or {}),
            },
            metadata={"kind": "verification-check", "validatorId": validator_id},
        )
        refs = (report_ref.uri, *extra_evidence)
        for issue in issues:
            severity = FindingSeverity.LOW if warn and _rank(issue.severity) > 1 else issue.severity
            findings.append(
                results.record_finding(
                    execution,
                    validator_id=validator_id,
                    rule_id=issue.rule_id,
                    category=issue.category,
                    severity=severity,
                    message=issue.message,
                    path=issue.path,
                    line=issue.line,
                    evidence_refs=(report_ref.uri,),
                    recommendation=issue.recommendation,
                    introduced=True,
                    actor=actor,
                )
            )
        is_mandatory = (policy == "enforce") if mandatory is None else mandatory
        blocking = [item for item in findings if item.severity in self.blocking()]
        failed = is_mandatory and bool(blocking)
        result = results.record_validation(
            execution,
            validator_id=validator_id,
            digest=change_set.digest,
            status=ResultStatus.FAILED if failed else ResultStatus.PASSED,
            kind=ValidationKind.VALIDATION_FAILURE if failed else ValidationKind.SUCCESS,
            mandatory=is_mandatory,
            summary=(
                f"{label}: {len(findings)} finding(s), {len(blocking)} blocking"
                if findings
                else f"{label}: no finding"
            ),
            findings=tuple(findings),
            evidence_refs=refs,
            started_at=started,
        )
        return ValidatorOutput(result, ())

    # ----- test quality (#52) ----------------------------------------------------------------
    def _test_quality(
        self,
        execution: Execution,
        change_set: ChangeSet,
        task: Task,
        diff: Sequence[DiffFile],
        files: Mapping[str, str],
    ) -> ValidatorOutput:
        config = self.config.test_quality
        severity = config.severity or FindingSeverity.MEDIUM
        started = datetime.now(UTC)
        issues: list[Issue] = []
        report: dict[str, Any] = {}
        evidence: list[str] = []
        tests = {path: text for path, text in files.items() if is_test_path(path)}
        if config.assertions:
            issues.extend(assertion_free_tests(tests, severity=severity))
        if config.interface_tests and declared_interfaces(task):
            names: list[str] = []
            for stub, _module in declared_interfaces(task):
                text = _read(self.results.s.paths.workspace, stub)
                if text is not None:
                    names.extend(declared_names(text))
            issues.extend(untested_interface_methods(names, self._all_tests(), severity=severity))
        if config.diff_coverage is not None and self._python():
            coverage, refs = self._coverage(execution, change_set)
            evidence.extend(refs)
            if coverage is None:
                report["diffCoverage"] = None
                issues.append(
                    Issue(
                        rule_id="tests.coverage-unavailable",
                        severity=FindingSeverity.INFO,
                        message="Coverage of the changed lines could not be measured (the "
                        "coverage package or the tests did not run)",
                        category="tests",
                    )
                )
            else:
                percent, found = changed_line_coverage(
                    diff, coverage, minimum_percent=config.diff_coverage, severity=severity
                )
                report["diffCoverage"] = percent
                issues.extend(found)
        if config.flaky_reruns and self._python():
            flaky, refs = self._flaky(execution, change_set, config.flaky_reruns)
            evidence.extend(refs)
            issues.extend(flaky)
        policy = "enforce" if severity in self.blocking() else "warn-keep"
        return self._output(
            execution,
            change_set,
            TEST_QUALITY_ID,
            policy,
            issues,
            "test quality",
            started=started,
            extra_evidence=tuple(evidence),
            mandatory=policy == "enforce",
            report=report,
        )

    def _python(self) -> bool:
        return any(item.technology == "python" for item in self.results.s.resolved.profiles)

    def _all_tests(self) -> dict[str, str]:
        workspace = self.results.s.paths.workspace
        found: dict[str, str] = {}
        for path in sorted(workspace.rglob("*.py")):
            relative = path.relative_to(workspace).as_posix()
            if any(
                part.startswith(".") or part in {"venv", "node_modules"}
                for part in Path(relative).parts
            ):
                continue
            if is_test_path(relative):
                try:
                    found[relative] = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
        return found

    def _context(
        self,
        execution: Execution,
        change_set: ChangeSet,
        validator_id: str,
        command: tuple[str, ...],
    ) -> ValidationContext:
        engine = self.results.engine
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{validator_id}", version="1")
        return ValidationContext(
            execution_id=execution.execution_id,
            workspace=self.results.s.paths.workspace,
            task=engine.run_task(execution),
            change_set=change_set,
            definition=engine._bounded_definition(
                ValidatorDefinition(id=validator_id, command=command, mandatory=True)
            ),
            grants=grants_from_rules(
                execution.execution_id, actor, self.results.s.resolved.effective_capabilities
            ),
            artifact_store=self.results.s.artifacts,
            process_runner=engine._runner(execution),
            provenance=engine._provenance(execution).model_copy(update={"actor": actor}),
            cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
            max_output_bytes=self.results.project.runtime.max_output_bytes,
        )

    def _coverage(
        self, execution: Execution, change_set: ChangeSet
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Run the tests under coverage.py and read its JSON report (Python projects)."""
        data_dir = self.results.s.paths.harness_dir / "coverage"
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = data_dir / f"{execution.execution_id}.diffcov"
        report = data_dir / f"{execution.execution_id}.diffcov.json"
        report.unlink(missing_ok=True)
        refs: list[str] = []
        steps = (
            ("python", "-m", "coverage", "run", f"--data-file={data}", "-m", "pytest", "-q"),
            ("python", "-m", "coverage", "json", f"--data-file={data}", "-o", str(report)),
        )
        for step in steps:
            context = self._context(execution, change_set, TEST_QUALITY_ID, step)
            output = CommandValidator(TEST_QUALITY_ID).execute(context)
            for tool in output.tool_invocations:
                self.results.engine._save_tool(execution, tool)
            refs.extend(output.result.evidence_refs[:1])
            if output.result.status is not ResultStatus.PASSED and step[3] == "json":
                return None, refs
        try:
            value = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, refs
        return (value if isinstance(value, dict) else None), refs

    def _flaky(
        self, execution: Execution, change_set: ChangeSet, reruns: int
    ) -> tuple[list[Issue], list[str]]:
        """Run the test suite again with another hash seed and the test files in reverse order;
        a failure the first run did not have marks the suite as flaky."""
        engine = self.results.engine
        latest = {
            item.validator_id: item
            for item in engine._latest_validations(execution.execution_id, change_set.digest)
        }
        first = latest.get("python.pytest")
        if first is None or first.status is not ResultStatus.PASSED:
            return [], []
        tests = sorted(self._all_tests(), reverse=True)
        refs: list[str] = []
        issues: list[Issue] = []
        actor = Actor(
            actor_type=ActorType.TOOL, actor_id=f"validator.{TEST_QUALITY_ID}", version="1"
        )
        grants = grants_from_rules(
            execution.execution_id, actor, self.results.s.resolved.effective_capabilities
        )
        for attempt in range(1, reruns + 1):
            seed = str(1000 + attempt)
            spec = CommandSpec(
                argv=("python", "-m", "pytest", "-q", "-p", "no:randomly", *tests),
                cwd=self.results.s.paths.workspace,
                timeout_seconds=float(engine._bounded_timeout(900)),
                max_output_bytes=self.results.project.runtime.max_output_bytes,
                allowed_environment=("PYTHONHASHSEED",),
            )
            process = engine._runner(execution).run(
                spec,
                actor=actor,
                grants=grants,
                extra_env={"PYTHONHASHSEED": seed},
                cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
            )
            ref = self.results.s.artifacts.put_json(
                {
                    "attempt": attempt,
                    "pythonHashSeed": seed,
                    "order": "reversed",
                    "exitCode": process.exit_code,
                    "status": process.status,
                    "stdoutRef": self.results.s.artifacts.put(
                        process.stdout, media_type="text/plain"
                    ).uri,
                },
                metadata={"kind": "flaky-rerun", "executionId": execution.execution_id},
            )
            refs.append(ref.uri)
            if process.status is not ResultStatus.PASSED:
                failing = pytest_node_ids(process.stdout.decode("utf-8", "replace"))
                shown = ", ".join(failing[:10]) or "see the stored output"
                issues.append(
                    Issue(
                        rule_id="tests.flaky",
                        severity=FindingSeverity.HIGH,
                        message=(
                            f"The tests passed once and failed when run again with "
                            f"PYTHONHASHSEED={seed} in reverse file order: {shown}"
                        ),
                        category="tests",
                        recommendation=(
                            "Remove the dependence on order, hash seed, time or randomness."
                        ),
                    )
                )
                break
        return issues, refs

    # ----- structured change requests (#52) --------------------------------------------------
    def _change_requests(
        self,
        execution: Execution,
        change_set: ChangeSet,
        items: list[dict[str, Any]],
        diff: Sequence[DiffFile],
    ) -> ValidatorOutput:
        """Every blocking item of a REQUEST_CHANGES is a temporary criterion: ``test:`` items
        must pass, ``absent:`` patterns must not be added, ``text`` items are reminders."""
        started = datetime.now(UTC)
        issues: list[Issue] = []
        evidence: list[str] = []
        added = [(item.path, line.number, line.text) for item in diff for line in item.added]
        for item in items:
            condition = str(item.get("condition", "text"))
            kind, _, value = condition.partition(":")
            label = f"{item.get('itemId')} ({item.get('description')})"
            if kind == "test":
                context = self._context(
                    execution,
                    change_set,
                    CHANGE_REQUESTS_ID,
                    ("python", "-m", "pytest", "-q", value.strip()),
                )
                output = CommandValidator(CHANGE_REQUESTS_ID).execute(context)
                for tool in output.tool_invocations:
                    self.results.engine._save_tool(execution, tool)
                evidence.extend(output.result.evidence_refs[:1])
                if output.result.status is not ResultStatus.PASSED:
                    issues.append(
                        Issue(
                            rule_id="change-request.unmet",
                            severity=FindingSeverity.HIGH,
                            message=f"{label}: the test {value.strip()} does not pass",
                            category="change-request",
                            recommendation="Make the requested test pass.",
                        )
                    )
            elif kind == "absent":
                try:
                    pattern = re.compile(value)
                except re.error:
                    pattern = re.compile(re.escape(value))
                hit = next(
                    ((path, number) for path, number, text in added if pattern.search(text)),
                    None,
                )
                if hit is not None:
                    issues.append(
                        Issue(
                            rule_id="change-request.unmet",
                            severity=FindingSeverity.HIGH,
                            message=f"{label}: {value!r} is still added by the ChangeSet",
                            path=hit[0],
                            line=hit[1],
                            category="change-request",
                            recommendation="Remove what the reviewer asked to remove.",
                        )
                    )
            else:
                issues.append(
                    Issue(
                        rule_id="change-request.unverified",
                        severity=FindingSeverity.INFO,
                        message=f"{label}: not checked by a tool; the reviewer verifies it",
                        category="change-request",
                    )
                )
        return self._output(
            execution,
            change_set,
            CHANGE_REQUESTS_ID,
            "enforce",
            issues,
            "change requests",
            started=started,
            extra_evidence=tuple(evidence),
            report={"items": items},
        )

    # ----- invariants (#52) ------------------------------------------------------------------
    def _invariants(
        self, execution: Execution, change_set: ChangeSet, task: Task
    ) -> list[ValidatorOutput]:
        outputs: list[ValidatorOutput] = []
        for invariant in self.config.invariants or ():
            validator_id = f"invariant.{invariant.invariant_id}"
            context = self._context(execution, change_set, validator_id, invariant.command)
            output = CommandValidator(validator_id).execute(context)
            self.results.engine._save_validator_output(execution, output)
            outputs.append(output)
        return outputs

    # ----- SARIF (#52) -----------------------------------------------------------------------
    def _sarif(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput:
        workspace = self.results.s.paths.workspace
        started = datetime.now(UTC)
        issues: list[Issue] = []
        tools: list[dict[str, Any]] = []
        missing: list[str] = []
        for source in self.config.sarif or ():
            path = workspace / source.path
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                if source.required:
                    missing.append(source.path)
                continue
            results = read_sarif(text, workspace)
            versions = sorted(
                {
                    f"{item.tool}@{item.tool_version or '?'}"
                    + (f" rule {item.rule_id}@{item.rule_version}" if item.rule_version else "")
                    for item in results
                }
            )
            tools.append({"path": source.path, "results": len(results), "versions": versions})
            for item in results:
                issues.append(
                    Issue(
                        rule_id=f"sarif.{source.tool or item.tool}.{item.rule_id}",
                        severity=_SARIF_SEVERITY.get(item.level, FindingSeverity.MEDIUM),
                        message=(
                            f"{item.message} ({item.tool} {item.tool_version or ''}"
                            f"{', rule ' + item.rule_version if item.rule_version else ''})"
                        ).replace(" )", ")"),
                        path=item.path,
                        line=item.line,
                        category="external-scanner",
                        recommendation=f"Reported by {item.tool}; fix it at the location shown.",
                    )
                )
        for path in missing:
            issues.append(
                Issue(
                    rule_id="sarif.missing-report",
                    severity=FindingSeverity.HIGH,
                    message=f"The required SARIF report {path} was not found in the workspace",
                    path=path,
                    category="external-scanner",
                    recommendation="Run the scanner before verification or mark it optional.",
                )
            )
        return self._output(
            execution,
            change_set,
            SARIF_ID,
            "enforce",
            issues,
            "external scanners (SARIF)",
            started=started,
            report={"reports": tools, "missing": missing},
        )

    # ----- risk factors (#52) ----------------------------------------------------------------
    def risk_actions(self) -> dict[str, str]:
        configured = self.config.risk_factors if self.config else None
        actions: dict[str, str] = dict(DEFAULT_RISK_ACTIONS)
        if configured:
            actions.update(configured)
        return actions

    def _risk(
        self, execution: Execution, change_set: ChangeSet, task: Task, diff: Sequence[DiffFile]
    ) -> ValidatorOutput:
        started = datetime.now(UTC)
        actions = self.risk_actions()
        interfaces = [module for _stub, module in declared_interfaces(task)] + [
            stub for stub, _module in declared_interfaces(task)
        ]
        signals = detect_risk_factors(diff, interface_paths=interfaces)
        issues: list[Issue] = []
        acknowledge: list[str] = []
        for signal in signals:
            action = actions.get(signal.factor, "inform")
            if action == "off":
                continue
            if action == "acknowledge" and signal.factor not in acknowledge:
                acknowledge.append(signal.factor)
            issues.append(
                Issue(
                    rule_id=f"risk.{signal.factor}",
                    severity=_RISK_SEVERITY[action],
                    message=f"{signal.message} (risk factor {signal.factor}: {action})",
                    path=signal.path,
                    line=signal.line,
                    category="risk",
                    recommendation={
                        "block": "A person must approve an exception for this change.",
                        "acknowledge": (
                            "A person must acknowledge this risk factor in the decision "
                            f"(--acknowledge-risk {signal.factor})."
                        ),
                        "inform": "Shown in the decision brief.",
                    }[action],
                )
            )
        self.results.set_flag_json(
            f"{RISK_ACK_FLAG}:{execution.execution_id}:{change_set.digest}",
            [item for item in RISK_FACTORS if item in acknowledge],
        )
        return self._output(
            execution,
            change_set,
            RISK_ID,
            "enforce",
            issues,
            "risk factors",
            started=started,
            mandatory=False,
            report={"actions": actions, "acknowledgementRequired": acknowledge},
        )


def _rank(severity: FindingSeverity) -> int:
    return _SEVERITY_RANK[severity]


def _subject(message: str) -> str:
    """What a limit message is about: the module or function (and, for a forbidden import,
    the module it imports), without the measured value."""
    words = message.split()
    return " ".join(words[:3]) if " imports " in message else (words[0] if words else "")


def _read(workspace: Path, relative: str) -> str | None:
    target = (workspace / relative).resolve()
    try:
        target.relative_to(workspace.resolve())
        return target.read_text(encoding="utf-8")
    except (ValueError, OSError, UnicodeDecodeError):
        return None


def _task_text(task: Task) -> str:
    return "\n".join(
        [
            task.title,
            task.intent,
            *task.constraints,
            *(item.text for item in task.requirements),
            *(item.text for item in task.acceptance_criteria),
            *(item.verification_hint or "" for item in task.acceptance_criteria),
        ]
    )


@dataclass(frozen=True)
class CheckResult:
    validator_id: str
    policy: str
    issues: list[Issue]
    label: str
    started: datetime


def current_files(workspace: Path, diff: Sequence[DiffFile]) -> dict[str, str]:
    """The current text of every file the diff adds or changes."""
    files: dict[str, str] = {}
    for item in diff:
        if item.is_deleted:
            continue
        try:
            files[item.path] = (workspace / item.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return files


def pure_checks(
    config: Any,
    task: Task,
    diff: Sequence[DiffFile],
    files: Mapping[str, str],
    workspace: Path,
    baseline: WorkspaceSnapshot | None,
) -> list[CheckResult]:
    """The checks that read only the diff and the files (VERIFICATION and ``harness check``):
    interface, architecture, security patterns, constraints, weakened controls and secrets."""
    planned: list[tuple[str, str, Callable[[], tuple[list[Issue], str]]]] = []
    if config.interface not in {None, "off"} and declared_interfaces(task):
        planned.append(
            (
                INTERFACE_ID,
                config.interface,
                lambda: interface_issues(workspace, task, config.interface),
            )
        )
    if config.architecture is not None:
        planned.append(
            (
                ARCHITECTURE_ID,
                "enforce",
                lambda: architecture_issues(config.architecture, files, baseline),
            )
        )
    if config.security_patterns:
        planned.append(
            (
                SECURITY_ID,
                "enforce",
                lambda: (check_security_patterns(diff, files), "security patterns"),
            )
        )
    if config.constraints not in {None, "off"}:
        planned.append(
            (
                CONSTRAINTS_ID,
                config.constraints,
                lambda: constraint_issues(task, files, config.constraints),
            )
        )
    if config.weakened_controls not in {None, "off"}:
        planned.append(
            (
                WEAKENED_ID,
                config.weakened_controls,
                lambda: (
                    check_weakened_controls(diff, config.weakened_controls),
                    "weakened controls",
                ),
            )
        )
    if config.secrets == "context":
        planned.append(
            (
                SECRETS_ID,
                "enforce",
                lambda: (scan_secrets(diff, task_text=_task_text(task)), "secrets in context"),
            )
        )
    results: list[CheckResult] = []
    for validator_id, policy, check in planned:
        started = datetime.now(UTC)
        issues, label = check()
        results.append(CheckResult(validator_id, policy, issues, label, started))
    return results


def interface_issues(workspace: Path, task: Task, policy: str) -> tuple[list[Issue], str]:
    severity = FindingSeverity.HIGH if policy == "enforce" else FindingSeverity.LOW
    issues: list[Issue] = []
    for stub, module in declared_interfaces(task):
        declared = _read(workspace, stub)
        implementation = _read(workspace, module)
        if declared is None or implementation is None:
            missing = stub if declared is None else module
            issues.append(
                Issue(
                    rule_id="interface.missing",
                    severity=severity,
                    message=f"The declared interface file {missing} is not in the workspace",
                    path=missing,
                    category="interface",
                    recommendation="Keep the declared interface and its module in place.",
                )
            )
            continue
        issues.extend(
            check_interface(
                declared,
                implementation,
                declared_path=stub,
                implementation_path=module,
                severity=severity,
            )
        )
    return issues, "declared interface conformance"


def architecture_issues(
    config: Any, files: Mapping[str, str], baseline: WorkspaceSnapshot | None
) -> tuple[list[Issue], str]:
    severity = config.severity or FindingSeverity.MEDIUM
    limits = ArchitectureLimits(
        max_module_lines=config.max_module_lines,
        max_function_lines=config.max_function_lines,
        max_complexity=config.max_complexity,
        forbidden_imports=tuple(
            (item.source, item.target) for item in config.forbidden_imports or ()
        ),
    )
    kept: list[Issue] = []
    for issue in check_architecture(files, limits, severity=severity):
        # A limit the file already exceeded on the baseline (same rule, same subject) is
        # pre-existing: reported as LOW so the agent is not blamed for it.
        state = baseline.files.get(issue.path) if baseline and issue.path else None
        before = state.text if state else None
        previous = (
            check_architecture({issue.path or "": before}, limits, severity=severity)
            if before is not None
            else []
        )
        if any(
            item.rule_id == issue.rule_id and _subject(item.message) == _subject(issue.message)
            for item in previous
        ):
            kept.append(
                replace(
                    issue, severity=FindingSeverity.LOW, message="Pre-existing: " + issue.message
                )
            )
        else:
            kept.append(issue)
    return kept, "architecture limits"


def constraint_issues(task: Task, files: Mapping[str, str], policy: str) -> tuple[list[Issue], str]:
    declared = task.metadata.get("checks") or []
    checks = constraints_from_task(
        task.constraints, [str(item) for item in declared] if isinstance(declared, list) else []
    )
    if not checks:
        return [], "task constraints (none verifiable)"
    severity = FindingSeverity.HIGH if policy == "enforce" else FindingSeverity.LOW
    return check_constraints(checks, files, severity=severity), (
        f"task constraints ({', '.join(checks)})"
    )
