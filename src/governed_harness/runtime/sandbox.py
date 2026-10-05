"""Write confinement of agent-provider processes (``runtime.agentSandbox: enforce``).

The harness's own file handling is contained to the workspace by ``contained_path``; an agent
CLI launched as a command provider is a separate process with the user's permissions and is not.
Under ``enforce`` the provider command is wrapped so that the operating system denies every file
write outside the workspace, the resolved ``$TMPDIR`` and the declared write paths. Reads,
network access and process execution stay allowed: an agent needs to read the system, reach its
model API and run tools.

* macOS: ``/usr/bin/sandbox-exec -p <profile>`` with a Seatbelt profile that allows everything
  and then denies ``file-write*`` except on the allowed paths.
* Linux: ``bwrap`` (bubblewrap) with the root file system bound read-only and each allowed path
  bound writable.
* Anything else, or a host without the tool: no confinement exists, and the caller must block
  instead of running the agent unconfined (:class:`SandboxUnavailable`).
"""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from governed_harness.evidence.hashing import sha256_bytes

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

SandboxMechanism = Literal["sandbox-exec", "bwrap"]
PathMatch = Literal["subpath", "prefix"]

# Device files a confined process may still write: the null and zero devices, its own standard
# streams and descriptors, terminals (an interactive agent or a pty-backed tool) and the DTrace
# helper that Node.js opens for writing at start-up.
_DEVICE_LITERALS = (
    "/dev/null",
    "/dev/zero",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/ptmx",
    "/dev/dtracehelper",
)
_DEVICE_PATTERNS = ("^/dev/fd/", "^/dev/tty")

_SBPL_REGEX_SPECIALS = re.compile(r"([.^$*+?()\[\]{}|\\])")


class SandboxUnavailable(RuntimeError):
    """``enforce`` was requested and this host offers no confinement mechanism."""


@dataclass(frozen=True)
class SandboxHost:
    """What the sandbox needs to know about the host; injectable so tests choose the platform."""

    system: str
    home: Path
    temp_dir: Path
    sandbox_exec: str | None = None
    bwrap: str | None = None

    @classmethod
    def detect(cls) -> SandboxHost:
        system = platform.system()
        return cls(
            system=system,
            home=Path.home(),
            temp_dir=Path(tempfile.gettempdir()),
            sandbox_exec=SANDBOX_EXEC
            if system == "Darwin" and os.access(SANDBOX_EXEC, os.X_OK)
            else None,
            bwrap=shutil.which("bwrap") if system == "Linux" else None,
        )


@dataclass(frozen=True)
class SandboxPath:
    path: str
    match: PathMatch
    source: str


@dataclass(frozen=True)
class SandboxPlan:
    """The confinement applied to one provider invocation, recorded as evidence."""

    mechanism: SandboxMechanism
    system: str
    prefix: tuple[str, ...]
    """argv placed before the provider command."""
    profile: str
    """The Seatbelt profile, or the bwrap arguments as a JSON array."""
    profile_digest: str
    allowed_paths: tuple[SandboxPath, ...]
    skipped_paths: tuple[SandboxPath, ...] = field(default=())
    protected_paths: tuple[str, ...] = field(default=())
    """Paths inside the workspace the agent may read but not write
    (``governance.protectExcludedPaths``: ``.harness`` and ``.git``)."""

    def evidence(self) -> dict[str, object]:
        record: dict[str, object] = {
            "mode": "enforce",
            "mechanism": self.mechanism,
            "platform": self.system,
            "executable": self.prefix[0],
            "profileDigest": self.profile_digest,
            "profile": self.profile,
            "allowedPaths": [_path_record(item) for item in self.allowed_paths],
            "skippedPaths": [_path_record(item) for item in self.skipped_paths],
        }
        if self.protected_paths:
            record["protectedPaths"] = list(self.protected_paths)
        return record


def _path_record(item: SandboxPath) -> dict[str, str]:
    return {"path": item.path, "match": item.match, "source": item.source}


def resolve_write_paths(
    workspace: Path, configured: Sequence[str], host: SandboxHost
) -> tuple[SandboxPath, ...]:
    """The workspace, the resolved temporary directory and each configured path, with ``~``
    expanded and symbolic links resolved (on macOS ``/tmp`` is ``/private/tmp``, ``/var`` is
    ``/private/var``): the kernel checks the resolved path."""
    resolved: list[SandboxPath] = [
        SandboxPath(_real(workspace), "subpath", "workspace"),
        SandboxPath(_real(host.temp_dir), "subpath", "tempdir"),
    ]
    for entry in configured:
        expanded = str(host.home) + entry[1:] if entry.startswith("~") else entry
        if expanded.endswith("*"):
            stem = Path(expanded[:-1])
            resolved.append(SandboxPath(str(Path(_real(stem.parent)) / stem.name), "prefix", entry))
        else:
            resolved.append(SandboxPath(_real(Path(expanded)), "subpath", entry))
    unique: dict[tuple[str, str], SandboxPath] = {}
    for item in resolved:
        unique.setdefault((item.path, item.match), item)
    return tuple(unique.values())


def _real(path: Path) -> str:
    return os.path.realpath(path)


def build_sandbox(
    workspace: Path,
    configured: Sequence[str],
    host: SandboxHost,
    protected: Sequence[Path] = (),
) -> SandboxPlan:
    """The confinement for one provider invocation. ``protected`` paths (inside the
    workspace) stay readable but are not writable, whatever the allowed paths say."""
    paths = resolve_write_paths(workspace, configured, host)
    denied = tuple(dict.fromkeys(_real(path) for path in protected))
    if host.system == "Darwin" and host.sandbox_exec:
        profile = seatbelt_profile(paths, denied)
        return SandboxPlan(
            mechanism="sandbox-exec",
            system=host.system,
            prefix=(host.sandbox_exec, "-p", profile),
            profile=profile,
            profile_digest=sha256_bytes(profile.encode("utf-8")),
            allowed_paths=paths,
            protected_paths=denied,
        )
    if host.system == "Linux" and host.bwrap:
        arguments, allowed, skipped = bwrap_arguments(paths, denied)
        profile = json.dumps(arguments)
        return SandboxPlan(
            mechanism="bwrap",
            system=host.system,
            prefix=(host.bwrap, *arguments, "--"),
            profile=profile,
            profile_digest=sha256_bytes(profile.encode("utf-8")),
            allowed_paths=allowed,
            skipped_paths=skipped,
            protected_paths=tuple(path for path in denied if Path(path).exists()),
        )
    if host.system == "Darwin":
        reason = f"{SANDBOX_EXEC} is not available"
    elif host.system == "Linux":
        reason = "bwrap (bubblewrap) is not installed"
    else:
        reason = f"no agent sandbox mechanism is supported on {host.system or 'this platform'}"
    raise SandboxUnavailable(reason)


def _sbpl_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(paths: Sequence[SandboxPath], protected: Sequence[str] = ()) -> str:
    """Allow everything, deny every file write, then allow writes below the allowed paths and,
    last, deny them again below the protected paths.

    In a Seatbelt profile the last matching rule wins, so the ``allow`` re-opens only the listed
    paths and the final ``deny`` closes the protected ones inside them."""
    rules = []
    for item in paths:
        if item.match == "subpath":
            rules.append(f"(subpath {_sbpl_string(item.path)})")
        else:
            escaped = _SBPL_REGEX_SPECIALS.sub(r"\\\1", item.path)
            rules.append(f'(regex #"^{escaped}")')
    rules.extend(f"(literal {_sbpl_string(device)})" for device in _DEVICE_LITERALS)
    rules.extend(f'(regex #"{pattern}")' for pattern in _DEVICE_PATTERNS)
    body = "\n  ".join(rules)
    profile = f"(version 1)\n(allow default)\n(deny file-write*)\n(allow file-write*\n  {body})\n"
    if protected:
        denied = "\n  ".join(f"(subpath {_sbpl_string(path)})" for path in protected)
        profile += f"(deny file-write*\n  {denied})\n"
    return profile


def bwrap_arguments(
    paths: Sequence[SandboxPath],
    protected: Sequence[str] = (),
) -> tuple[list[str], tuple[SandboxPath, ...], tuple[SandboxPath, ...]]:
    """Bind the root read-only, keep /dev, bind each existing allowed path writable and then
    bind each existing protected path read-only again (a later mount covers an earlier one).

    A bind mount needs an existing source, so a path that does not exist is skipped (and
    recorded); a prefix pattern binds each existing file that matches it."""
    arguments = ["--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--die-with-parent"]
    allowed: list[SandboxPath] = []
    skipped: list[SandboxPath] = []
    for item in paths:
        if item.match == "prefix":
            matches = sorted(glob.glob(glob.escape(item.path) + "*"))
            if not matches:
                skipped.append(item)
            for match in matches:
                arguments.extend(("--bind", match, match))
                allowed.append(SandboxPath(match, "subpath", item.source))
        elif Path(item.path).exists():
            arguments.extend(("--bind", item.path, item.path))
            allowed.append(item)
        else:
            skipped.append(item)
    for path in protected:
        if Path(path).exists():
            arguments.extend(("--ro-bind", path, path))
    return arguments, tuple(allowed), tuple(skipped)


_DENIAL_MARKERS = re.compile(
    r"operation not permitted|read-only file system|sandbox|deny\(\d+\) file-write", re.IGNORECASE
)
_QUOTED_PATH = re.compile(r"""['"`](/[^'"`\n]+)['"`]""")
_PATH_BEFORE_DENIAL = re.compile(
    r"(/[^\s:'\"`]+):\s*(?:operation not permitted|read-only file system)", re.IGNORECASE
)
_PATH_AFTER_DENY = re.compile(r"deny\(\d+\) file-write[\w-]* (/\S+)")


def denied_writes(stderr: bytes) -> tuple[bool, tuple[str, ...]]:
    """Whether the provider's standard error reports a denied write, and the paths it names.

    Python (``[Errno 1] Operation not permitted: '/path'``), Node.js (``EPERM: operation not
    permitted, open '/path'``) and GNU tools under bwrap (``cannot touch '/path': Read-only file
    system``) quote the path; shells write ``sh: /path: Operation not permitted`` and the
    Seatbelt log ``deny(1) file-write-create /path``."""
    text = stderr.decode("utf-8", errors="replace")
    found = False
    paths: dict[str, None] = {}
    for line in text.splitlines():
        if not _DENIAL_MARKERS.search(line):
            continue
        found = True
        candidates = (
            _QUOTED_PATH.findall(line)
            or _PATH_BEFORE_DENIAL.findall(line)
            or _PATH_AFTER_DENY.findall(line)
        )
        for candidate in candidates:
            paths.setdefault(candidate, None)
    return found, tuple(paths)
