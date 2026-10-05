"""Generic environment preflight (#55, item 10; the ``environment`` section).

Before anything changes, DISCOVERY checks what the run will rely on, so that a missing tool or a
broken baseline stops the run before an agent is paid to work on it:

* ``tools``: each declared tool prints its version and the version matches the pattern;
* ``variables``: each declared environment variable is set (its value is never read into the
  record);
* ``gitHooks.required``: each hook is present and executable in the hooks directory Git uses
  (``core.hooksPath`` included). The harness never bypasses hooks; ``install`` is the command the
  project declares to install them, named in the reason and run only by ``harness doctor
  --install-hooks``;
* ``dirtyTree``: uncommitted changes outside ``.harness/`` (``block`` stops the run, ``warn``
  records a LOW finding, ``allow`` records them);
* ``baseline``: the mandatory validators run on a scratch copy of the baseline; ``require``
  stops the run when one does not pass, ``report`` records it (and the comparison with the
  baseline of ``verification.differential`` reuses the result).

The tools the profiles' capabilities need (their detection commands) are reported, not
enforced: the validators report a missing tool in VERIFICATION as before."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.ladder import EnvironmentConfig
from governed_harness.domain.enums import ActorType, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import Actor, ChangeSet, Execution, PhaseExecution
from governed_harness.ladder.capabilities import run_detection
from governed_harness.orchestration.workspace_ops import Contents, materialized
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.validators import ValidationContext

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import PhaseOutcome
    from governed_harness.orchestration.ladder import VerificationLadder

ENVIRONMENT_ID = "harness.environment"
_TIMEOUT = 15


def _git(workspace: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            check=False,
            timeout=60,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, str(error)
    return result.returncode, result.stdout.decode("utf-8", "replace")


def hooks_directory(workspace: Path) -> Path | None:
    code, output = _git(workspace, "rev-parse", "--git-path", "hooks")
    if code != 0 or not output.strip():
        return None
    path = Path(output.strip())
    return path if path.is_absolute() else (workspace / path)


def dirty_paths(workspace: Path) -> list[str] | None:
    """Uncommitted changes outside ``.harness/`` (``None`` outside a Git repository)."""
    code, output = _git(workspace, "status", "--porcelain", "--untracked-files=normal")
    if code != 0:
        return None
    paths = []
    for line in output.splitlines():
        path = line[3:].strip().strip('"')
        if path and not path.startswith(".harness"):
            paths.append(path)
    return sorted(paths)


def environment_checks(
    config: EnvironmentConfig, workspace: Path, *, timeout: int = _TIMEOUT
) -> dict[str, Any]:
    """Tools, variables, hooks and the dirty tree (no validator runs): the checks DISCOVERY
    and ``harness doctor`` share. ``problems`` are the ones that stop a run."""
    problems: list[str] = []
    tools: list[dict[str, Any]] = []
    for item in config.tools or ():
        code, output = run_detection(tuple(item.command), workspace, timeout)
        ok = code == 0 and (item.pattern is None or re.search(item.pattern, output) is not None)
        version = output.strip().splitlines()[0][:200] if output.strip() else ""
        tools.append({"name": item.name, "available": ok, "version": version})
        if not ok:
            want = f" matching {item.pattern!r}" if item.pattern else ""
            problems.append(f"tool {item.name}{want} is not available ({version or output[:200]})")
    variables = [
        {"name": name, "set": bool(os.environ.get(name))} for name in config.variables or ()
    ]
    problems.extend(
        f"environment variable {item['name']} is not set" for item in variables if not item["set"]
    )
    hooks: list[dict[str, Any]] = []
    git_hooks = config.git_hooks
    if git_hooks and git_hooks.required:
        directory = hooks_directory(workspace)
        for name in git_hooks.required:
            path = directory / name if directory else None
            present = bool(path and path.is_file())
            executable = bool(path and present and (os.name == "nt" or os.access(path, os.X_OK)))
            hooks.append({"name": name, "present": present, "executable": executable})
            if not executable:
                install = (
                    f"; install it with: {' '.join(git_hooks.install)}" if git_hooks.install else ""
                )
                problems.append(
                    f"Git hook {name} is {'not executable' if present else 'missing'}{install}"
                )
    dirty = dirty_paths(workspace)
    policy = config.dirty_tree or "allow"
    if dirty and policy == "block":
        problems.append(
            f"the working tree has {len(dirty)} uncommitted change(s): {', '.join(dirty[:10])}"
        )
    return {
        "tools": tools,
        "variables": variables,
        "gitHooks": hooks,
        "dirtyTree": {"policy": policy, "paths": dirty},
        "problems": problems,
    }


class EnvironmentPreflight:
    def __init__(self, ladder: VerificationLadder) -> None:
        self.ladder = ladder

    @property
    def config(self) -> EnvironmentConfig | None:
        return self.ladder.project.environment

    def run(
        self, execution: Execution, phase: PhaseExecution, baseline_digest: str
    ) -> PhaseOutcome | None:
        from governed_harness.orchestration.engine import PhaseOutcome

        config = self.config
        if config is None:
            return None
        hub = self.ladder
        results = hub.engine.results
        workspace = hub.s.paths.workspace
        report = environment_checks(config, workspace)
        report["profileTools"] = [
            item.as_dict()
            for item in hub.capabilities(execution)
            if item.level in {"L0", "L1"} and item.detections
        ]
        problems: list[str] = list(report["problems"])
        dirty = report["dirtyTree"]["paths"] or []
        if dirty and config.dirty_tree == "warn":
            results.record_finding(
                execution,
                validator_id=ENVIRONMENT_ID,
                rule_id="environment.dirty-tree",
                category="environment",
                severity=FindingSeverity.LOW,
                message=(
                    f"The run starts on {len(dirty)} uncommitted change(s): "
                    + ", ".join(dirty[:10])
                ),
                recommendation="Commit or stash unrelated changes before a governed run.",
            )
        if config.baseline in {"require", "report"}:
            baseline = self._baseline(execution, baseline_digest)
            report["baseline"] = baseline
            failing = [item for item in baseline["validators"] if item["status"] != "PASSED"]
            if failing and config.baseline == "require":
                problems.append(
                    "the baseline does not pass: "
                    + ", ".join(f"{item['validatorId']} {item['status']}" for item in failing)
                )
            elif failing:
                results.record_finding(
                    execution,
                    validator_id=ENVIRONMENT_ID,
                    rule_id="environment.baseline-failing",
                    category="environment",
                    severity=FindingSeverity.LOW,
                    message=(
                        "The baseline already fails before the change: "
                        + ", ".join(f"{item['validatorId']} {item['status']}" for item in failing)
                    ),
                    recommendation=(
                        "Failures the baseline already has are not the change's; "
                        "verification.differential tells them apart."
                    ),
                )
        report["problems"] = problems
        ref = results.record_json(
            execution,
            PhaseId.DISCOVERY,
            report,
            kind="environment-preflight",
            summary=(
                f"Environment preflight: {len(problems)} problem(s)"
                if problems
                else "Environment preflight passed"
            ),
        )
        hub.s.events.append(
            execution.execution_id,
            "environment.preflight.completed",
            {"problems": problems, "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        if problems:
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                "Environment preflight: " + "; ".join(problems[:5]),
                (ref,),
            )
        return None

    def _baseline(self, execution: Execution, baseline_digest: str) -> dict[str, Any]:
        """Run the mandatory validators on a scratch copy of the workspace as DISCOVERY found
        it, and keep each result where the baseline comparison looks for it."""
        from governed_harness.orchestration.differential import Differential

        hub = self.ladder
        engine = hub.engine
        results = engine.results
        workspace = hub.s.paths.workspace
        scratch = hub.s.paths.harness_dir / "tmp"
        placeholder = ChangeSet(
            change_set_id="baseline",
            execution_id=execution.execution_id,
            files=(),
            diff_ref="baseline",
            digest=baseline_digest,
        )
        entries: list[dict[str, Any]] = []
        unchanged = Contents(lambda _path: True, lambda _path: None)
        with materialized(workspace, scratch, unchanged, []) as copy:
            if copy is None:
                return {"validators": [], "problem": "the baseline could not be copied"}
            for definition in hub.s.resolved.effective_validators:
                if not definition.mandatory:
                    continue
                actor = Actor(
                    actor_type=ActorType.TOOL,
                    actor_id=f"validator.{definition.validator_id}",
                    version="1",
                )
                output = engine.validators.create(definition.validator_id).execute(
                    ValidationContext(
                        execution_id=execution.execution_id,
                        workspace=copy,
                        task=engine.run_task(execution),
                        change_set=placeholder,
                        definition=engine._bounded_definition(definition),
                        grants=grants_from_rules(
                            execution.execution_id, actor, hub.s.resolved.effective_capabilities
                        ),
                        artifact_store=hub.s.artifacts,
                        process_runner=SafeProcessRunner(copy),
                        provenance=engine._provenance(execution).model_copy(
                            update={"actor": actor}
                        ),
                        cancellation=CancellationToken(
                            lambda: engine.is_cancelled(execution.execution_id)
                        ),
                        max_output_bytes=hub.project.runtime.max_output_bytes,
                    )
                )
                status = output.result.status
                differential = Differential(results)
                keys = differential._candidate_keys(output.result, copy)
                record = {
                    "validatorId": definition.validator_id,
                    "baselineDigest": baseline_digest,
                    "status": status.value,
                    "keys": list(keys),
                    "evidenceRefs": list(output.result.evidence_refs),
                }
                if status in {ResultStatus.PASSED, ResultStatus.FAILED}:
                    results.set_flag_json(
                        differential._cache_key(baseline_digest, definition), record
                    )
                entries.append(
                    {
                        "validatorId": definition.validator_id,
                        "status": status.value,
                        "summary": output.result.summary,
                        "evidenceRefs": list(output.result.evidence_refs),
                    }
                )
        return {"validators": entries}


__all__ = [
    "ENVIRONMENT_ID",
    "EnvironmentPreflight",
    "dirty_paths",
    "environment_checks",
    "hooks_directory",
]
