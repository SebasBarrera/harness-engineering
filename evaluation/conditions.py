"""The project configuration of each governed condition of the 2.0.0 evaluation.

====================  =======================================================================
Condition             ``.harness/project.yaml``
====================  =======================================================================
``direct``            none: Claude Code runs on its own (``agentlib``).
``harness-core``      the file ``harness init`` wrote in 1.0.0, written here key by key, so it has
                      none of the keys added after 1.0.0 (every one of them is optional and its
                      absence keeps the 1.0.0 behaviour and configuration digest).
``harness``           what ``harness init`` of the version under test writes (every 2.0.0 setting
                      on), with ``agentRouting.mode: fixed``: every call uses the cell's model.
``harness-anchored``  what ``harness init`` writes, routing left as init writes it (``anchored``,
                      #85) with ``agentRouting.anchorModel`` set to the cell's model: the model the
                      person invoked is the ceiling, cheaper rungs of the routing tables below it
                      are kept. The factor is the invoking model (Haiku, Sonnet or Opus).
====================  =======================================================================

The evaluation changes only what it must in every governed condition: the agent provider (the
evaluation adapter ``claude_provider.py`` as a command provider, or the harness's deterministic
``simulated`` provider in a dry run), the grant of exactly that provider command (wave 9, #87:
a provider command must be allowed by a ``process.execute`` grant; it goes to
``capabilities.extend`` under ``governance.phaseCapabilities`` and to ``capabilities.grants``,
the 1.0.0 key, in ``harness-core``), ``runtime.commandTimeoutSeconds`` (1800 s, the agent limit
of 1500 s plus the harness's own work, as in the 0.9.0 evaluation) and, where the write sandbox
is on, one extra ``runtime.sandboxWritePaths`` entry: the run's ``agent-calls`` directory outside
the workspace, where the adapter writes its usage records. The exact file of every run is saved
next to its results (``project.yaml``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

GOVERNED = ("harness-core", "harness", "harness-anchored")
# Read in records of the first 2.0.0 pilot (routing left ``tiered``, before #85).
LEGACY_GOVERNED = ("harness-tiered",)
CONDITIONS = ("direct", *GOVERNED)
TIMEOUT_SECONDS = 1800
# The 1.0.0 ``harness init`` file (src/governed_harness/configuration/init_project.py at v1.0.0).
CORE_KEYS = {
    "configVersion",
    "projectId",
    "workspace",
    "profiles",
    "workflow",
    "capabilities",
    "validators",
    "policies",
    "agentProvider",
    "agentProviders",
    "runtime",
    "retention",
}
CORE_RUNTIME_KEYS = {"commandTimeoutSeconds", "maxOutputBytes", "maxParallel", "allowNetwork"}


def core_config(project_id: str) -> dict[str, Any]:
    """The configuration ``harness init`` wrote in 1.0.0, key by key."""
    return {
        "configVersion": "1.0",
        "projectId": project_id,
        "workspace": {"root": "..", "units": []},
        "profiles": ["auto"],
        "workflow": "default_development",
        "capabilities": {"default": "deny", "grants": []},
        "validators": [],
        "policies": {"requireHumanDecision": True, "findingBlockSeverities": ["HIGH", "CRITICAL"]},
        "agentProvider": "simulated",
        "runtime": {
            "commandTimeoutSeconds": 900,
            "maxOutputBytes": 1000000,
            "maxParallel": 2,
            "allowNetwork": False,
        },
        "retention": {"artifactDays": 30, "eventDays": 365},
    }


def provider_command(
    code: Path,
    model: str,
    *,
    honor_routing: bool,
    claude_bin: str | None,
    resumable: bool = False,
) -> list[str]:
    command = ["python", str(code / "claude_provider.py"), "--model", model]
    if honor_routing:
        command.append("--honor-routing")
    if claude_bin:
        command += ["--claude-bin", claude_bin]
    if resumable:
        command.append("--resumable")
    return command


def apply_provider(
    config: dict[str, Any],
    *,
    condition: str,
    model: str,
    code: Path,
    run_dir: Path,
    provider: str,
    claude_bin: str | None = None,
    resumable: bool = False,
) -> dict[str, Any]:
    """Point the configuration at the agent of the run and set the evaluation's time limit."""
    runtime = config.setdefault("runtime", {})
    runtime["commandTimeoutSeconds"] = 7 * 24 * 3600 if resumable else TIMEOUT_SECONDS
    if provider == "simulated":
        config["agentProvider"] = "simulated"
        config.pop("agentProviders", None)
    else:
        command = provider_command(
            code,
            model,
            honor_routing=condition in ("harness-anchored", "harness-tiered"),
            claude_bin=claude_bin,
            resumable=resumable,
        )
        config["agentProvider"] = "claude"
        config["agentProviders"] = {
            "claude": {"kind": "command", "command": command, "model": model}
        }
        grant_provider_command(config, command, core=condition == "harness-core")
    if runtime.get("agentSandbox") == "enforce":
        paths = list(runtime.get("sandboxWritePaths") or [])
        calls = str((run_dir / "agent-calls").resolve())
        if calls not in paths:
            paths.append(calls)
        runtime["sandboxWritePaths"] = paths
    if condition == "harness":
        config.setdefault("agentRouting", {})["mode"] = "fixed"
    if condition == "harness-anchored":
        config.setdefault("agentRouting", {})["anchorModel"] = model
    return config


def provider_grant(command: list[str]) -> dict[str, Any]:
    """The ``process.execute`` rule of exactly the provider's command line: its scope is the
    whole command (``command_in_scope`` matches it, or it followed by arguments the harness
    adds), never its executable alone (``python`` would allow every Python command)."""
    return {"capability": "process.execute", "scope": [" ".join(command)]}


def grant_provider_command(config: dict[str, Any], command: list[str], *, core: bool) -> None:
    """Grant the provider command (wave 9, #87). Under ``governance.phaseCapabilities``
    ``capabilities.grants`` only narrows the profiles, so the grant goes to
    ``capabilities.extend``; ``harness-core`` has no ``extend`` (a 1.0.0 key set), so there it
    goes to ``capabilities.grants``, which 1.0.0 adds to the profiles (``core``).

    The adapter is started as ``python claude_provider.py ...`` and the Python profile already
    allows ``python``; the explicit rule makes the grant of the provider independent of that
    profile and visible in the saved ``project.yaml``."""
    capabilities = config.setdefault("capabilities", {"default": "deny", "grants": []})
    key = "grants" if core else "extend"
    rules = [dict(r) for r in capabilities.get(key) or []]
    rule = provider_grant(command)
    if rule not in rules:
        rules.append(rule)
    capabilities[key] = rules


def write_config(
    workspace: Path,
    condition: str,
    model: str,
    *,
    run_dir: Path,
    code: Path,
    harness: Any,
    provider: str = "claude",
    claude_bin: str | None = None,
    resumable: bool = False,
) -> dict[str, Any]:
    """Write ``.harness/project.yaml`` of a governed condition and return it.

    ``harness`` runs a harness command in the workspace (``run_eval.harness``)."""
    path = workspace / ".harness" / "project.yaml"
    if condition == "harness-core":
        # ``harness init`` (since #86 it writes .harness/ to .git/info/exclude, not .gitignore);
        # the core file is then written over its project.yaml.
        harness(workspace, "init", "--path", ".")
        config = core_config(yaml.safe_load(path.read_text(encoding="utf-8"))["projectId"])
    elif condition in ("harness", "harness-anchored"):
        harness(workspace, "init", "--path", ".")
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        if (
            condition == "harness-anchored"
            and (config.get("agentRouting") or {}).get("mode") != "anchored"
        ):
            raise RuntimeError("harness init did not write agentRouting.mode: anchored")
    else:
        raise ValueError(f"not a governed condition: {condition}")
    apply_provider(
        config,
        condition=condition,
        model=model,
        code=code,
        run_dir=run_dir,
        provider=provider,
        claude_bin=claude_bin,
        resumable=resumable,
    )
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config


def core_keys_only(config: dict[str, Any]) -> list[str]:
    """Keys of a configuration that the 1.0.0 file did not have (empty for harness-core)."""
    extra = sorted(set(config) - CORE_KEYS)
    extra += sorted(f"runtime.{k}" for k in set(config.get("runtime") or {}) - CORE_RUNTIME_KEYS)
    return extra


_DIGEST = (
    "import json,sys\n"
    "from pathlib import Path\n"
    "from governed_harness.configuration.resolver import ConfigurationResolver\n"
    "from governed_harness.evidence.hashing import sha256_json\n"
    "resolved = ConfigurationResolver().resolve(Path(sys.argv[1]))\n"
    "print(sha256_json(resolved.model_dump(mode='json', by_alias=True)))\n"
)


def configuration_digest(
    workspace: Path, python: str = sys.executable, src: Path | None = None
) -> str:
    """The configuration digest a run records (sha256 of the resolved configuration), computed
    by the harness whose package is importable from ``python`` (or from ``src`` first)."""
    env = {**os.environ}
    if src is not None:
        env["PYTHONPATH"] = str(src)
    proc = subprocess.run(
        [python, "-c", _DIGEST, str(workspace)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:])
    return proc.stdout.strip()


def check_grant(workspace: Path, harness: Any) -> dict[str, Any]:
    """Evidence that the run's provider command is granted (wave 9, #87): the rules the
    evaluation wrote and the warnings of ``harness config validate`` about provider commands no
    ``process.execute`` grant allows (empty when the grant holds)."""
    config = yaml.safe_load((workspace / ".harness" / "project.yaml").read_text(encoding="utf-8"))
    capabilities = config.get("capabilities") or {}
    proc = harness(workspace, "config", "validate", "--path", ".", "--json")
    validate = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    return {
        "extend": capabilities.get("extend"),
        "grants": capabilities.get("grants"),
        "validateExit": proc.returncode,
        "providerWarnings": [
            w for w in validate.get("warnings") or [] if str(w).startswith("agentProviders.")
        ],
    }


def check_core(workspace: Path, harness: Any, reference_src: Path | None = None) -> dict[str, Any]:
    """Evidence that the harness-core configuration is the 1.0.0-compatible one.

    * its keys are the keys of the 1.0.0 file (``extraKeys`` empty);
    * ``harness config validate`` resolves it and every setting added after 1.0.0 is off (the
      ``agentResults``, ``agentSandbox``, ``ladder`` and ``friction`` sections are kept as printed);
    * with ``reference_src`` (the ``src`` directory of the 1.0.0 source), the configuration digest
      the 1.0.0 harness computes for the same file equals the one the harness under test computes.
    """
    config = yaml.safe_load((workspace / ".harness" / "project.yaml").read_text(encoding="utf-8"))
    proc = harness(workspace, "config", "validate", "--path", ".", "--json")
    validate = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    result: dict[str, Any] = {
        "extraKeys": core_keys_only(config),
        "validateExit": proc.returncode,
        "agentResults": validate.get("agentResults"),
        "agentSandbox": validate.get("agentSandbox"),
        "criteriaPolicy": (validate.get("intake") or {}).get("criteriaPolicy"),
        "ladder": validate.get("ladder"),
        "friction": validate.get("friction"),
        "governance": validate.get("governance"),
    }
    result["digestUnderTest"] = configuration_digest(workspace)
    if reference_src is not None:
        result["digest100"] = configuration_digest(workspace, src=reference_src)
        result["digestEqual"] = result["digest100"] == result["digestUnderTest"]
    return result
