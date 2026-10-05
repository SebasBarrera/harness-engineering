"""The gate contract in the provider request and ``harness check`` (#52, N3).

The evaluation's agents did not know what the gate would run: about one turn in ten was a
denied attempt to run Ruff or Mypy, and correct work was rejected for a rule the agent never
saw. Under ``runtime.gateContract`` the implement request carries the contract: the validators
(command, mandatory or optional), the enabled checks and their policies, the review rules, the
blocking severities, the absolute workspace path and a command, ``harness check``, that runs the
same validators and checks on the workspace without recording anything.

``harness check`` reads the project configuration and a check state file the harness writes
before each implement call (``.harness/check/<run>.json``: the task and the baseline), so it
needs no write access to ``.harness`` and can run inside the agent sandbox. Its artifacts go to
a temporary directory that is removed afterwards."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness import __version__
from governed_harness.capabilities import grants_from_rules
from governed_harness.checks import parse_unified_diff
from governed_harness.configuration import ConfigurationResolver
from governed_harness.configuration.models import ResolvedConfiguration, ValidatorDefinition
from governed_harness.domain.enums import ActorType, FindingSeverity, ResultStatus
from governed_harness.domain.errors import NotFoundError
from governed_harness.domain.models import (
    Actor,
    CapabilityGrant,
    ChangeSet,
    Execution,
    Provenance,
    Task,
)
from governed_harness.evidence import LocalArtifactStore
from governed_harness.runtime import CancellationToken, SafeProcessRunner, WorkspaceSnapshotter
from governed_harness.validators import CommandValidator, ValidationContext
from governed_harness.validators.review import RULES

if TYPE_CHECKING:
    from governed_harness.orchestration.agent_results import AgentResults

CHECK_DIRECTORY = "check"


def check_state_path(harness_dir: Path, execution_id: str) -> Path:
    return harness_dir / CHECK_DIRECTORY / f"{execution_id}.json"


class GateContract:
    def __init__(self, results: AgentResults) -> None:
        self.results = results

    @property
    def enabled(self) -> bool:
        return bool(self.results.project.runtime.gate_contract)

    def validators(self) -> list[ValidatorDefinition]:
        resolved = self.results.s.resolved
        definitions = list(resolved.effective_validators)
        verification = resolved.project.verification
        for invariant in (verification.invariants if verification else None) or ():
            definitions.append(
                ValidatorDefinition(
                    id=f"invariant.{invariant.invariant_id}", command=invariant.command
                )
            )
        return definitions

    def contract(self, execution: Execution) -> dict[str, Any]:
        resolved = self.results.s.resolved
        project = resolved.project
        workspace = str(self.results.s.paths.workspace)
        verification = project.verification
        checks: dict[str, Any] = {}
        if verification is not None:
            for name, alias in (
                ("interface", "interface"),
                ("constraints", "constraints"),
                ("weakened_controls", "weakenedControls"),
                ("ratchet", "ratchet"),
                ("secrets", "secrets"),
                ("security_patterns", "securityPatterns"),
                ("differential", "differential"),
            ):
                value = getattr(verification, name)
                if value not in {None, "off", False}:
                    checks[alias] = value
            if verification.architecture is not None:
                checks["architecture"] = verification.architecture.model_dump(
                    mode="json", by_alias=True
                )
            if verification.test_quality is not None:
                checks["testQuality"] = verification.test_quality.model_dump(
                    mode="json", by_alias=True
                )
            if verification.risk_factors is not None:
                checks["riskFactors"] = self.results.verification.risk_actions()
        skipped = {"review.possible-secret"} if self.results.secrets_in_context else set()
        return {
            "workspace": workspace,
            "validators": [
                {
                    "id": item.validator_id,
                    "command": list(item.command or ()),
                    "mandatory": item.mandatory,
                }
                for item in self.validators()
            ],
            "checks": checks,
            "requirementTraceability": project.requirement_traceability,
            "reviewRules": [
                {"ruleId": rule.rule_id, "severity": rule.severity, "message": rule.message}
                for rule in RULES
                if rule.rule_id not in skipped
            ],
            "blockingSeverities": list(
                resolved.effective_policies.get("findingBlockSeverities", ["HIGH", "CRITICAL"])
            ),
            "checkCommand": [
                "harness",
                "check",
                "--path",
                workspace,
                "--run",
                execution.execution_id,
            ],
        }

    def write_check_state(self, execution: Execution, task: Task) -> Path:
        """The task and the baseline ``harness check`` compares the workspace with."""
        baseline = self.results.baseline_snapshot(execution)
        path = check_state_path(self.results.s.paths.harness_dir, execution.execution_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = {
            "executionId": execution.execution_id,
            "task": task.model_dump(mode="json", by_alias=True),
            "baseline": self.results.engine._snapshot_to_dict(baseline) if baseline else None,
        }
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        return path


def permissions(
    kind: str, grants: list[CapabilityGrant], read_only: bool, network: bool
) -> dict[str, Any]:
    """What a call kind may do, derived from the capability grants of the agent actor
    (``governance.phasePermissions``): read-only kinds get no write scope."""
    scopes: dict[str, list[str]] = {}
    for grant in grants:
        if grant.approval_required:
            continue
        scopes.setdefault(grant.capability, []).extend(grant.scope)
    write = [] if read_only else sorted(set(scopes.get("filesystem.write", [])))
    return {
        "callKind": kind,
        "readOnly": read_only,
        "filesystem": {
            "read": sorted(set(scopes.get("filesystem.read", []))),
            "write": write,
        },
        "process": sorted(set(scopes.get("process.execute", []))),
        "network": network,
        "other": sorted(
            name
            for name in scopes
            if name not in {"filesystem.read", "filesystem.write", "process.execute"}
        ),
    }


# ----- harness check ------------------------------------------------------------------------
def run_check(path: Path, execution_id: str | None = None) -> dict[str, Any]:
    """Run the gate's validators and the diff checks on the workspace; nothing is recorded."""
    from governed_harness.orchestration.engine import RunEngine
    from governed_harness.orchestration.verification_checks import (
        current_files,
        pure_checks,
    )

    resolved = ConfigurationResolver().resolve(path)
    workspace = resolved.workspace_root.resolve()
    state = _check_state(workspace / ".harness", execution_id)
    task = Task.model_validate(state["task"]) if state else None
    baseline = (
        RunEngine._snapshot_from_dict(state["baseline"]) if state and state["baseline"] else None
    )
    run_id = str(state["executionId"]) if state else "check"
    validators: list[dict[str, Any]] = []
    blocking_names = resolved.effective_policies.get("findingBlockSeverities", ["HIGH", "CRITICAL"])
    blocking = {FindingSeverity(str(name)) for name in blocking_names}
    snapshotter = WorkspaceSnapshotter(workspace)
    diff_text = ""
    digest = "sha256:check"
    if baseline is not None:
        diff = snapshotter.diff(baseline, snapshotter.snapshot())
        diff_text = diff.unified_diff.decode("utf-8", "replace")
        digest = diff.digest
    with tempfile.TemporaryDirectory(prefix="harness-check-") as scratch:
        store = LocalArtifactStore(Path(scratch) / "artifacts")
        change_set = ChangeSet(
            change_set_id="check", execution_id=run_id, files=(), diff_ref="check", digest=digest
        )
        for definition in _definitions(resolved):
            actor = Actor(
                actor_type=ActorType.TOOL,
                actor_id=f"validator.{definition.validator_id}",
                version="1",
            )
            output = CommandValidator(definition.validator_id).execute(
                ValidationContext(
                    execution_id=run_id,
                    workspace=workspace,
                    task=task or _placeholder_task(resolved),
                    change_set=change_set,
                    definition=definition,
                    grants=grants_from_rules(run_id, actor, resolved.effective_capabilities),
                    artifact_store=store,
                    process_runner=SafeProcessRunner(workspace),
                    provenance=Provenance(actor=actor, core_version=__version__),
                    cancellation=CancellationToken(lambda: False),
                    max_output_bytes=resolved.project.runtime.max_output_bytes,
                    parse_output=True,
                )
            )
            validators.append(
                {
                    "id": definition.validator_id,
                    "mandatory": definition.mandatory,
                    "status": output.result.status.value,
                    "summary": output.result.summary,
                    "problems": [
                        {
                            "rule": item.rule_id,
                            "path": item.location.path if item.location else None,
                            "line": item.location.start_line if item.location else None,
                            "message": item.message,
                        }
                        for item in output.findings[1:51]
                    ],
                    "output": _tail(store, output.result.evidence_refs),
                }
            )
    checks: list[dict[str, Any]] = []
    verification = resolved.project.verification
    if verification is not None and task is not None and baseline is not None:
        parsed = parse_unified_diff(diff_text)
        for item in pure_checks(
            verification,
            task,
            parsed,
            current_files(workspace, parsed),
            workspace,
            baseline,
        ):
            checks.append(
                {
                    "id": item.validator_id,
                    "policy": item.policy,
                    "issues": [
                        {
                            "rule": issue.rule_id,
                            "severity": issue.severity.value,
                            "path": issue.path,
                            "line": issue.line,
                            "message": issue.message,
                        }
                        for issue in item.issues
                    ],
                }
            )
    failed_validators = [
        item for item in validators if item["mandatory"] and item["status"] != "PASSED"
    ]
    blocking_issues = [
        issue
        for check in checks
        if check["policy"] == "enforce"
        for issue in check["issues"]
        if FindingSeverity(issue["severity"]) in blocking
    ]
    status = ResultStatus.FAILED if failed_validators or blocking_issues else ResultStatus.PASSED
    return {
        "status": status.value,
        "executionId": state["executionId"] if state else None,
        "workspace": str(workspace),
        "validators": validators,
        "checks": checks,
        "blockingIssues": len(blocking_issues),
        "note": (
            "Nothing was recorded. The gate also runs the independent review and, when "
            "configured, requirement traceability, coverage, SARIF and the baseline comparison."
        ),
    }


def _definitions(resolved: ResolvedConfiguration) -> list[ValidatorDefinition]:
    definitions = list(resolved.effective_validators)
    verification = resolved.project.verification
    for invariant in (verification.invariants if verification else None) or ():
        definitions.append(
            ValidatorDefinition(id=f"invariant.{invariant.invariant_id}", command=invariant.command)
        )
    return definitions


def _check_state(harness_dir: Path, execution_id: str | None) -> dict[str, Any] | None:
    directory = harness_dir / CHECK_DIRECTORY
    if execution_id:
        candidate = directory / f"{execution_id}.json"
        if not candidate.is_file():
            raise NotFoundError(f"no check state for run {execution_id}")
    else:
        files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        if not files:
            return None
        candidate = files[-1]
    value = json.loads(candidate.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _placeholder_task(resolved: ResolvedConfiguration) -> Task:
    return Task.model_validate(
        {
            "taskId": "check",
            "projectId": resolved.project.project_id,
            "title": "harness check",
            "intent": "Run the gate's validators on the workspace.",
            "acceptanceCriteria": [{"criterionId": "check", "text": "The validators pass."}],
        }
    )


def _tail(store: LocalArtifactStore, refs: tuple[str, ...], limit: int = 2000) -> str:
    try:
        report = json.loads(store.get(refs[0]))
        stdout = store.get(report["stdoutRef"]).decode("utf-8", "replace")
        stderr = store.get(report["stderrRef"]).decode("utf-8", "replace")
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ""
    text = (stdout + ("\n" + stderr if stderr.strip() else "")).strip()
    return text[-limit:]


__all__ = [
    "GateContract",
    "check_state_path",
    "permissions",
    "run_check",
]
