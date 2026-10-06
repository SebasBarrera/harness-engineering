"""Worktree isolation per run (#55, item 9; ``workspace.isolation``).

``harness run start --isolate worktree`` (or ``workspace.isolation.mode: worktree``) runs the
phases in a Git worktree of their own, on a new branch made from the updated base branch, so
that several runs proceed in parallel without touching the person's working tree:

1. the base is ``isolation.base`` (default: the current branch); with ``fetch`` (default) and a
   remote, the harness fetches it first and branches from the remote's copy;
2. the branch is ``isolation.branch`` (``harness/{taskId}-{runId}`` by default, or the branch the
   task's operational contract names) and the worktree goes under ``isolation.directory``
   (``.harness/worktrees/<run>``);
3. the project configuration is copied into the worktree's ``.harness/`` with a marker
   (``isolation.json``) that names the run registry of the workspace it came from, so the run is
   recorded where the person looks for it and ``harness status``, ``review`` or ``gate decide``
   work from either directory.

A branch or a directory that already exists is a collision: the run is ``BLOCKED`` with the
reason and a ``stop.condition`` (``destructive-collision``) event; the harness never resets,
deletes or reuses what it did not create, and ``run continue`` tries again once a person
resolved it. ``harness run cleanup`` removes a closed run's worktree (never its branch), and only
a worktree the harness created and that has no uncommitted change."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.ladder import IsolationConfig
from governed_harness.delivery.vcs import Git, VcsError
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Execution, Task, utc_now
from governed_harness.runtime.state_location import ISOLATION_MARKER

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices, RunEngine

ISOLATION_FLAG = "isolation"
_HARNESS_DIR = ".harness"
GENERATED_DIRECTORIES = frozenset(
    {_HARNESS_DIR, "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage"}
)
"""What a run leaves untracked in its worktree (the harness's directory, tool caches)."""


def _generated(path: str) -> bool:
    return any(part in GENERATED_DIRECTORIES for part in Path(path.rstrip("/")).parts)


def isolation_record(services: EngineServices, execution_id: str) -> dict[str, Any] | None:
    raw = services.state.get_flag(f"{ISOLATION_FLAG}:{execution_id}")
    value = json.loads(raw) if raw else None
    return value if isinstance(value, dict) else None


def _branch(settings: IsolationConfig, task: Task, run_id: str) -> str:
    if task.contract is not None and task.contract.branch:
        return task.contract.branch
    return settings.branch_template.replace("{taskId}", task.task_id).replace("{runId}", run_id)


def plan_worktree(
    services: EngineServices, task: Task, run_id: str, settings: IsolationConfig
) -> dict[str, Any]:
    """Where the worktree goes, on which branch and from which base, and any collision."""
    workspace = services.paths.workspace
    git = Git(workspace)
    if not git.is_repository() or git.resolve("HEAD") is None:
        raise ConfigurationError(
            "worktree isolation needs a Git repository with at least one commit"
        )
    branch = _branch(settings, task, run_id)
    directory = workspace / settings.worktree_directory / run_id
    current = git.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    base = settings.base or current.stdout.decode("utf-8", "replace").strip() or "HEAD"
    remote = settings.remote or "origin"
    plan: dict[str, Any] = {
        "branch": branch,
        "directory": str(directory),
        "base": base,
        "remote": None,
        "fetched": False,
        "collision": None,
    }
    if git.resolve(f"refs/heads/{branch}") is not None:
        plan["collision"] = f"branch {branch} already exists"
    elif directory.exists():
        plan["collision"] = f"directory {directory} already exists"
    has_remote = git.run("remote", "get-url", remote, check=False).returncode == 0
    fetch = settings.fetch is not False
    if plan["collision"] is None and fetch and has_remote and base != "HEAD":
        try:
            git.run("fetch", "--quiet", remote, base)
        except VcsError as error:
            plan["collision"] = (
                f"the base branch {base} could not be fetched from {remote}: {error}"
            )
        else:
            plan["remote"] = remote
            plan["fetched"] = True
    start = f"{remote}/{base}" if plan["fetched"] else base
    commit = git.resolve(start)
    if plan["collision"] is None and commit is None:
        plan["collision"] = f"the base {start} does not resolve to a commit"
    plan["start"] = start
    plan["baseCommit"] = commit
    return plan


def create_worktree(services: EngineServices, run_id: str, plan: dict[str, Any]) -> Path:
    """Create the worktree and its ``.harness/`` (configuration and marker)."""
    workspace = services.paths.workspace
    directory = Path(plan["directory"])
    directory.parent.mkdir(parents=True, exist_ok=True)
    Git(workspace).run("worktree", "add", "-b", plan["branch"], str(directory), plan["start"])
    harness = directory / _HARNESS_DIR
    harness.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(services.paths.harness_dir / "project.yaml", harness / "project.yaml")
    state_root = services.paths.state_root or services.paths.harness_dir
    marker = {
        "schemaVersion": "1.0",
        "createdBy": "governed-harness",
        "executionId": run_id,
        "origin": str(workspace),
        "stateRoot": str(state_root),
        "branch": plan["branch"],
        "base": plan["base"],
        "baseCommit": plan["baseCommit"],
        "createdAt": utc_now().isoformat(),
    }
    (harness / ISOLATION_MARKER).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return directory


def start_isolated(
    open_services: Any,
    services: EngineServices,
    task: Task,
    provider: str | None,
    settings: IsolationConfig,
) -> tuple[Execution, EngineServices | None]:
    """Create the run in its own worktree. Returns the run and the services of the worktree
    (``None`` on a collision: the run is recorded BLOCKED in the workspace's registry)."""
    from governed_harness.orchestration.engine import RunEngine

    run_id = new_id("run")
    plan = plan_worktree(services, task, run_id, settings)
    if plan["collision"] is not None:
        engine = RunEngine(services)
        execution = engine.create_execution(task, provider=provider, execution_id=run_id)
        return block_collision(services, engine, execution, plan), None
    directory = create_worktree(services, run_id, plan)
    isolated = open_services(directory)
    engine = RunEngine(isolated)
    execution = engine.create_execution(task, provider=provider, execution_id=run_id)
    record = {**plan, "status": "CREATED", "origin": str(services.paths.workspace)}
    isolated.state.set_flag(f"{ISOLATION_FLAG}:{run_id}", json.dumps(record, sort_keys=True))
    isolated.events.append(run_id, "isolation.created", record)
    return execution, isolated


def block_collision(
    services: EngineServices, engine: RunEngine, execution: Execution, plan: dict[str, Any]
) -> Execution:
    record = {**plan, "status": "COLLISION", "origin": str(services.paths.workspace)}
    services.state.set_flag(
        f"{ISOLATION_FLAG}:{execution.execution_id}", json.dumps(record, sort_keys=True)
    )
    services.events.append(execution.execution_id, "isolation.collision", record)
    services.events.append(
        execution.execution_id,
        "stop.condition",
        {"condition": "destructive-collision", "detail": plan["collision"]},
    )
    blocked = execution.model_copy(
        update={
            "status": ResultStatus.BLOCKED,
            "terminal_reason": (
                f"Isolation collision: {plan['collision']}. The harness never resets or "
                "deletes it; resolve it and harness run continue"
            ),
            "updated_at": utc_now(),
        }
    )
    engine._save_execution(blocked)
    return blocked


def retry_isolation(
    open_services: Any, services: EngineServices, execution: Execution
) -> tuple[Execution, EngineServices | None]:
    """``run continue`` on a run blocked by a collision: plan again, create the worktree when
    the collision is gone."""
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    task = engine.run_task(execution)
    settings = services.resolved.project.workspace.isolation or IsolationConfig(mode="worktree")
    plan = plan_worktree(services, task, execution.execution_id, settings)
    if plan["collision"] is not None:
        return block_collision(services, engine, execution, plan), None
    directory = create_worktree(services, execution.execution_id, plan)
    isolated = open_services(directory)
    record = {**plan, "status": "CREATED", "origin": str(services.paths.workspace)}
    isolated.state.set_flag(
        f"{ISOLATION_FLAG}:{execution.execution_id}", json.dumps(record, sort_keys=True)
    )
    isolated.events.append(execution.execution_id, "isolation.created", record)
    moved = execution.model_copy(
        update={
            "workspace": str(directory.resolve()),
            "status": ResultStatus.PENDING,
            "terminal_reason": None,
            "updated_at": utc_now(),
        }
    )
    RunEngine(isolated)._save_execution(moved)
    return moved, isolated


def cleanup_worktree(services: EngineServices, execution: Execution) -> dict[str, Any]:
    """Remove the worktree of a finished isolated run: only one the harness created, without
    uncommitted changes, never its branch."""
    from governed_harness.orchestration.engine import run_is_open

    record = isolation_record(services, execution.execution_id)
    if record is None or record.get("status") != "CREATED":
        raise PolicyViolationError(
            f"run {execution.execution_id} has no worktree the harness created"
        )
    if run_is_open(execution) and execution.current_phase is not PhaseId.CLOSURE:
        raise PolicyViolationError(f"run {execution.execution_id} is still open")
    directory = Path(record["directory"])
    marker = directory / _HARNESS_DIR / ISOLATION_MARKER
    if not marker.is_file():
        raise PolicyViolationError(f"{directory} is not a worktree the harness created")
    origin = Path(record.get("origin") or services.paths.workspace)
    git = Git(origin)
    status = Git(directory).run("status", "--porcelain", "--untracked-files=normal", check=False)
    changes = [
        line
        for line in status.stdout.decode("utf-8", "replace").splitlines()
        if line[3:].strip() and not _generated(line[3:].strip().strip('"'))
    ]
    if changes:
        raise PolicyViolationError(
            f"the worktree {directory} has {len(changes)} uncommitted change(s); the harness "
            "does not delete them"
        )
    for line in status.stdout.decode("utf-8", "replace").splitlines():
        # What the harness and the validators wrote there: its own directory and caches.
        target = directory / line[3:].strip().strip('"').rstrip("/")
        if line.startswith("??") and _generated(line[3:].strip()) and target.is_dir():
            shutil.rmtree(target)
    if (directory / _HARNESS_DIR).is_dir():
        shutil.rmtree(directory / _HARNESS_DIR)
    git.run("worktree", "remove", str(directory))
    updated = {**record, "status": "REMOVED", "removedAt": utc_now().isoformat()}
    services.state.set_flag(
        f"{ISOLATION_FLAG}:{execution.execution_id}", json.dumps(updated, sort_keys=True)
    )
    services.events.append(execution.execution_id, "isolation.removed", updated)
    return updated


__all__ = [
    "ISOLATION_FLAG",
    "block_collision",
    "cleanup_worktree",
    "create_worktree",
    "isolation_record",
    "plan_worktree",
    "retry_isolation",
    "start_isolated",
]
