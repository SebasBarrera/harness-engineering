"""First-run help: the .gitignore entry, an example task and the environment checks of doctor."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from governed_harness.configuration.models import ResolvedConfiguration, ValidatorDefinition
from governed_harness.runtime.sandbox import SandboxHost
from governed_harness.validators.command import _MODULE_MISSING_EXIT_CODE, _MODULE_PROBE

GITIGNORE_ENTRY = ".harness/"

EXAMPLE_TASK_NAME = "task.example.yaml"

_EXAMPLE_TASK = """\
# Example task written by `harness init`. Copy it, edit it and create it with:
#   harness task create --file .harness/task.example.yaml
# Reference: docs/reference/task-file.md
taskId: task_example
title: Describe the change in one line
intent: >-
  Say what should change and why, in terms a reviewer can check against the result.
requirements:
  # Identified requirements; with verification.requirementTraceability a test must name
  # each id (for example test_req_example_behaviour).
  - requirementId: req_example
    text: State one observable behaviour the change must have.
    source: example
acceptanceCriteria:
  # Observable criteria: an input, an expected result and a condition. INTENT asks
  # clarification questions about criteria it cannot check.
  - criterionId: ac_example
    text: Given input X, the result is Y.
constraints:
  - Do not change the public interface of {surface}.
metadata:
  # Paths the agent may change; the ChangeSet only contains these paths.
  ownedPaths:
{owned}
"""

_OWNED = {
    "python": ["src/", "tests/"],
    "node": ["src/", "test/"],
}


def ensure_gitignore(workspace: Path) -> str:
    """Add ``.harness/`` to the workspace .gitignore; returns added, present or created."""
    path = workspace / ".gitignore"
    if not path.exists():
        path.write_text(f"{GITIGNORE_ENTRY}\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    entries = {line.strip() for line in text.splitlines()}
    if entries & {".harness", ".harness/", "/.harness", "/.harness/", ".harness/*"}:
        return "present"
    separator = "" if not text or text.endswith("\n") else "\n"
    path.write_text(f"{text}{separator}{GITIGNORE_ENTRY}\n", encoding="utf-8")
    return "added"


def write_example_task(harness_dir: Path, technologies: list[str], *, force: bool) -> Path | None:
    """Write the example task unless one exists (or ``force``). Returns the path written."""
    target = harness_dir / EXAMPLE_TASK_NAME
    if target.exists() and not force:
        return None
    owned = next((_OWNED[item] for item in technologies if item in _OWNED), ["src/"])
    surface = "the package" if "python" in technologies else "the module"
    content = _EXAMPLE_TASK.format(
        surface=surface, owned="\n".join(f"    - {item}" for item in owned)
    )
    target.write_text(content, encoding="utf-8")
    return target


def _run(
    argv: list[str], cwd: Path | None, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def git_identity(cwd: Path | None) -> dict[str, Any]:
    if not shutil.which("git"):
        return {"status": "NOT_APPLICABLE", "message": "git is not installed"}
    name = _run(["git", "config", "user.name"], cwd).stdout.strip()
    email = _run(["git", "config", "user.email"], cwd).stdout.strip()
    if name and email:
        return {"status": "PASSED", "name": name, "email": email}
    return {
        "status": "WARNING",
        "message": "Git has no user.name or user.email here",
        "hint": 'Set them with `git config user.name "Your Name"` and '
        "`git config user.email you@example.com`; the harness records the person who decides.",
    }


def repository_checks(workspace: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    if not shutil.which("git"):
        return checks
    inside = _run(["git", "rev-parse", "--is-inside-work-tree"], workspace)
    if inside.returncode != 0:
        checks["baseline"] = {
            "status": "WARNING",
            "message": "the project is not a Git repository",
            "hint": "Run `git init` and commit the project: the baseline revision of a run is "
            "the commit it starts from.",
        }
        return checks
    head = _run(["git", "rev-parse", "--verify", "HEAD"], workspace)
    checks["baseline"] = (
        {"status": "PASSED", "head": head.stdout.strip()}
        if head.returncode == 0
        else {
            "status": "WARNING",
            "message": "the repository has no commit",
            "hint": "Commit the current state (`git add -A && git commit -m baseline`) so a run "
            "can record its baseline revision.",
        }
    )
    ignored = _run(["git", "check-ignore", "-q", ".harness/state.db"], workspace)
    checks["harnessIgnored"] = (
        {"status": "PASSED"}
        if ignored.returncode == 0
        else {
            "status": "WARNING",
            "message": ".harness/ is not ignored by Git",
            "hint": "Add `.harness/` to .gitignore (`harness init` does it): it holds the state "
            "database and artifacts with copies of your code.",
        }
    )
    return checks


def _executable(argv0: str, workspace: Path) -> str | None:
    if os.sep in argv0:
        candidate = Path(argv0) if Path(argv0).is_absolute() else workspace / argv0
        return str(candidate) if os.access(candidate, os.X_OK) else None
    return shutil.which(argv0)


def provider_checks(resolved: ResolvedConfiguration) -> dict[str, Any]:
    project = resolved.project
    default = project.agent_provider
    checks: dict[str, Any] = {}
    if default == "simulated":
        checks["agentProvider"] = {
            "status": "PASSED",
            "provider": "simulated",
            "message": "built-in deterministic provider; it calls no agent CLI",
        }
    others: list[dict[str, Any]] = []
    for provider_id, provider in project.agent_providers.items():
        found = _executable(provider.command[0], resolved.workspace_root)
        entry: dict[str, Any] = {
            "provider": provider_id,
            "command": provider.command[0],
            "status": "PASSED" if found else "FAILED" if provider_id == default else "WARNING",
        }
        if found:
            entry["path"] = found
        else:
            entry["hint"] = (
                f"Install the agent CLI or adapter {provider.command[0]!r} or put it on PATH; "
                f"runs with provider {provider_id!r} fail in IMPLEMENTATION without it."
            )
        if provider_id == default:
            checks["agentProvider"] = entry
        else:
            others.append(entry)
    if "agentProvider" not in checks:
        checks["agentProvider"] = {
            "status": "FAILED",
            "provider": default,
            "hint": f"Declare agentProviders.{default} in project.yaml or set agentProvider.",
        }
    if others:
        checks["otherAgentProviders"] = {
            "status": "WARNING" if any(item["status"] != "PASSED" for item in others) else "PASSED",
            "providers": others,
        }
    return checks


def sandbox_check(
    resolved: ResolvedConfiguration, host: SandboxHost | None = None
) -> dict[str, Any]:
    mode = resolved.project.runtime.effective_agent_sandbox
    if mode == "off":
        return {
            "status": "WARNING",
            "mode": "off",
            "message": "agent writes are not confined",
            "hint": "Set runtime.agentSandbox: enforce to confine a command provider's writes.",
        }
    host = host or SandboxHost.detect()
    mechanism = host.sandbox_exec or host.bwrap
    if mechanism:
        return {"status": "PASSED", "mode": mode, "mechanism": mechanism}
    command_provider = resolved.project.agent_provider != "simulated"
    if host.system == "Linux":
        fix = "Install bubblewrap (for example `apt-get install bubblewrap`)"
    elif host.system == "Darwin":
        fix = "/usr/bin/sandbox-exec should exist on macOS; check the system installation"
    else:
        fix = f"No sandbox mechanism is supported on {host.system or 'this platform'}"
    return {
        "status": "FAILED" if command_provider else "WARNING",
        "mode": mode,
        "message": "no sandbox mechanism is available on this host",
        "hint": f"{fix}, or set runtime.agentSandbox: off to run a command provider unconfined. "
        "Until then a command provider's run is BLOCKED; the simulated provider is not affected.",
    }


def _validator_status(definition: ValidatorDefinition, workspace: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": definition.validator_id, "mandatory": definition.mandatory}
    missing = "FAILED" if definition.mandatory else "NOT_APPLICABLE"
    if definition.command is None:
        return {**entry, "status": missing, "message": "no command is configured"}
    if definition.script:
        try:
            package = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if definition.script not in scripts:
            return {
                **entry,
                "status": missing,
                "message": f"package script {definition.script!r} is not defined",
                "hint": f"Add a {definition.script!r} script to package.json.",
            }
    argv0 = definition.command[0]
    if shutil.which(argv0) is None:
        return {
            **entry,
            "status": missing,
            "message": f"executable {argv0!r} is not available",
            "hint": f"Install {argv0!r} or put it on PATH where the harness runs.",
        }
    if len(definition.command) >= 3 and definition.command[1] == "-m":
        module = definition.command[2]
        try:
            probe = _run([argv0, "-c", _MODULE_PROBE, module], workspace, timeout=60)
            available = probe.returncode != _MODULE_MISSING_EXIT_CODE
        except (OSError, subprocess.TimeoutExpired):
            available = False
        if not available:
            return {
                **entry,
                "status": missing,
                "message": f"Python module {module!r} is not installed for {argv0!r}",
                "hint": f"Install it where the harness runs: `{argv0} -m pip install {module}`.",
            }
    return {**entry, "status": "PASSED"}


def validator_checks(resolved: ResolvedConfiguration) -> dict[str, Any]:
    entries = [
        _validator_status(item, resolved.workspace_root) for item in resolved.effective_validators
    ]
    status = "FAILED" if any(item["status"] == "FAILED" for item in entries) else "PASSED"
    return {"status": status, "validators": entries}
