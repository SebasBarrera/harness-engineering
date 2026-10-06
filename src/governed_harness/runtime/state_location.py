"""Where a project's run registry lives (``runtime.stateDir``, since 1.1, #55).

In 1.0.0 the state database and the artifact store live in the workspace's ``.harness/``: an
agent with write access to the workspace could reach them (only the sandbox and the excluded
path guard stood in the way), a workspace cleanup deleted the record, and every repository had
its own dashboard. With ``runtime.stateDir`` the registry moves out of the workspace:

* ``auto``: ``$HARNESS_STATE_DIR``, else ``$XDG_DATA_HOME/governed-harness/state``, else the
  platform's application-data directory (``~/Library/Application Support`` on macOS,
  ``%LOCALAPPDATA%`` on Windows, ``~/.local/share`` elsewhere);
* an absolute path, or one that starts with ``~/``, used as given.

Each project gets one directory there, named after its id and a digest of the repository it
belongs to (the Git common directory, so every worktree of a repository shares it), holding
``state.db``, ``artifacts/`` and ``registry.json`` (the project id and the workspace path, read
by the dashboard to list the runs of several repositories). The workspace keeps ``.harness/``
for the configuration, the lease, caches and scratch copies.

A worktree the harness created for an isolated run (``workspace.isolation``) carries
``.harness/isolation.json``, which names the registry of the workspace it was created from, so
its runs are recorded where the person looks for them. Without ``stateDir`` and without that
marker nothing changes: ``.harness/state.db`` and ``.harness/artifacts/``."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess  # nosec B404 - git rev-parse with a fixed argv, no shell
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governed_harness.domain.errors import ConfigurationError
from governed_harness.evidence.hashing import sha256_bytes

STATE_DIR_ENV = "HARNESS_STATE_DIR"
ISOLATION_MARKER = "isolation.json"
REGISTRY_FILE = "registry.json"
_SLUG = re.compile(r"[^a-z0-9_.-]+")


def default_state_root() -> Path:
    """The directory ``runtime.stateDir: auto`` resolves to."""
    configured = os.environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "governed-harness" / "state"
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "governed-harness" / "state"
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home) / "governed-harness" / "state"
    return home / ".local" / "share" / "governed-harness" / "state"


def configured_root(state_dir: str) -> Path:
    """The base directory of a ``runtime.stateDir`` value (``auto`` or a path)."""
    if state_dir == "auto":
        return default_state_root()
    return Path(state_dir).expanduser()


def repository_identity(workspace: Path) -> Path:
    """The Git common directory of ``workspace`` (shared by its worktrees), else the workspace
    itself."""
    try:
        result = subprocess.run(  # nosec B603 B607 - git from PATH, fixed argv, no shell
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=workspace,
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return workspace.resolve()
    value = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0 or not value:
        return workspace.resolve()
    return Path(value).resolve()


def project_key(project_id: str, identity: Path) -> str:
    slug = _SLUG.sub("_", project_id.lower()).strip("_")[:60] or "project"
    digest = sha256_bytes(str(identity).encode("utf-8")).removeprefix("sha256:")[:12]
    return f"{slug}-{digest}"


@dataclass(frozen=True)
class StateLocation:
    """Where the state database and the artifact store of a workspace are."""

    root: Path
    database: Path
    artifacts: Path
    external: bool
    """The registry lives outside the workspace (``runtime.stateDir`` or an isolated run)."""


def isolation_marker(workspace: Path) -> dict[str, Any] | None:
    """The marker of a worktree the harness created, or ``None``."""
    path = workspace / ".harness" / ISOLATION_MARKER
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("stateRoot"), str) else None


def resolve_state_location(
    workspace: Path, project_id: str, state_dir: str | None, *, create: bool = True
) -> StateLocation:
    """The state location of ``workspace``: the registry its isolation marker names, else the
    ``runtime.stateDir`` registry, else ``.harness/`` (1.0.0)."""
    root_workspace = workspace.resolve()
    marker = isolation_marker(root_workspace)
    if marker is not None:
        root = Path(marker["stateRoot"])
        return StateLocation(root, root / "state.db", root / "artifacts", True)
    if state_dir is None:
        harness = root_workspace / ".harness"
        return StateLocation(harness, harness / "state.db", harness / "artifacts", False)
    identity = repository_identity(root_workspace)
    root = configured_root(state_dir) / project_key(project_id, identity)
    if create:
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as error:
            raise ConfigurationError(
                f"cannot create the run registry {root} (runtime.stateDir: {state_dir}): "
                f"{error.strerror or error}; set {STATE_DIR_ENV} or runtime.stateDir to a "
                "writable directory"
            ) from error
        _register(root, project_id, root_workspace, identity)
    return StateLocation(root, root / "state.db", root / "artifacts", True)


def _register(root: Path, project_id: str, workspace: Path, identity: Path) -> None:
    """Write ``registry.json`` (project id, workspace) once per workspace path, so the dashboard
    can list the runs of several repositories. A failure to write it never stops a command."""
    path = root / REGISTRY_FILE
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        current = {}
    main = identity.parent if identity.name == ".git" else workspace
    value = {
        "schemaVersion": "1.0",
        "projectId": project_id,
        "workspace": str(main),
        "stateRoot": str(root),
        "registeredAt": current.get("registeredAt") or datetime.now(UTC).isoformat(),
    }
    if current == value:
        return
    try:
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return


def registered_projects(state_root: Path | None = None) -> list[dict[str, Any]]:
    """Every project registered under a state root (default: ``auto``), sorted by id."""
    base = state_root or default_state_root()
    found: list[dict[str, Any]] = []
    if not base.is_dir():
        return found
    for path in sorted(base.glob(f"*/{REGISTRY_FILE}")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("projectId"), str):
            found.append({**value, "database": str(path.parent / "state.db")})
    return sorted(found, key=lambda item: (item["projectId"], item.get("workspace", "")))


__all__ = [
    "ISOLATION_MARKER",
    "STATE_DIR_ENV",
    "StateLocation",
    "configured_root",
    "default_state_root",
    "isolation_marker",
    "project_key",
    "registered_projects",
    "repository_identity",
    "resolve_state_location",
]
