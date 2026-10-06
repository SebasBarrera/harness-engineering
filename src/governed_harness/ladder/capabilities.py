"""Verification capabilities of the selected profiles and their detection (#55, item 6).

Each technology offers some rungs of the ladder: static checks, unit tests, integration tests
with the repository's own doubles, executable behaviour through probes, sometimes a simulator
or an emulator. The built-in profiles' capabilities are in
``resources/verification/capabilities.yaml``; a project profile declares its own under
``verification``. Detection is read-only: a command that must exit 0 (``adb devices``,
``xcrun simctl list``, ``docker info``) or a path that must exist. Nothing is ever installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 - catalog probes with fixed argv, no shell
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from governed_harness.configuration.ladder import (
        CapabilityDetection,
        ProfileVerification,
        VerificationCapability,
    )
    from governed_harness.configuration.models import TechnologyProfileDefinition

ANY_PROFILE = "*"
_OUTPUT_CHARS = 500


def load_catalog() -> dict[str, ProfileVerification]:
    """The verification capabilities of the built-in profiles, by profile id."""
    from governed_harness.configuration.ladder import ProfileVerification
    from governed_harness.configuration.loader import resource_text

    raw = yaml.safe_load(resource_text("verification", "capabilities.yaml")) or {}
    return {str(key): ProfileVerification.model_validate(value) for key, value in raw.items()}


def profile_verification(
    profiles: Iterable[TechnologyProfileDefinition], catalog: dict[str, ProfileVerification]
) -> list[tuple[str, ProfileVerification]]:
    """The verification of each selected profile (its own declaration, else the catalog's),
    followed by the capabilities every profile shares."""
    found: list[tuple[str, ProfileVerification]] = []
    for profile in profiles:
        declared = profile.verification or catalog.get(profile.profile_id)
        if declared is not None:
            found.append((profile.profile_id, declared))
    shared = catalog.get(ANY_PROFILE)
    if shared is not None:
        found.append((ANY_PROFILE, shared))
    return found


@dataclass(frozen=True)
class DetectionResult:
    description: str
    available: bool
    output: str = ""
    checked: bool = True
    """False when the command was not run (``capabilityDetection`` off): the capability is
    planned as available but nothing is credited without the validators' results."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.description,
            "available": self.available,
            "checked": self.checked,
            "output": self.output,
        }


@dataclass(frozen=True)
class CapabilityStatus:
    profile_id: str
    level: str
    provides: str
    validators: tuple[str, ...]
    probes: bool
    available: bool
    detections: tuple[DetectionResult, ...] = field(default_factory=tuple)
    fallback: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "level": self.level,
            "provides": self.provides,
            "validators": list(self.validators),
            "probes": self.probes,
            "available": self.available,
            "detections": [item.as_dict() for item in self.detections],
            "fallback": self.fallback,
        }


Runner = Callable[[tuple[str, ...], Path, int], tuple[int | None, str]]


def run_detection(argv: tuple[str, ...], cwd: Path, timeout: int) -> tuple[int | None, str]:
    """Run a detection command without a shell; ``(None, reason)`` when it cannot start."""
    if shutil.which(argv[0]) is None and not (cwd / argv[0]).exists():
        return None, f"{argv[0]} not found"
    try:
        result = subprocess.run(  # nosec B603 - argv from the trusted catalog or project profile
            list(argv),
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout} s"
    except OSError as error:
        return None, str(error)
    text = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace").strip()
    return result.returncode, text


def detect(
    item: CapabilityDetection, workspace: Path, timeout: int, runner: Runner = run_detection
) -> DetectionResult:
    if item.path is not None:
        target = workspace / item.path
        return DetectionResult(f"path {item.path}", target.exists())
    argv = tuple(item.command or ())
    code, output = runner(argv, workspace, timeout)
    available = code == 0
    if available and item.expect is not None:
        available = re.search(item.expect, output, re.MULTILINE) is not None
    return DetectionResult(" ".join(argv), available, output[-_OUTPUT_CHARS:])


def capability_statuses(
    declared: list[tuple[str, ProfileVerification]],
    workspace: Path,
    validator_ids: set[str],
    *,
    run_detections: bool,
    timeout: int,
    runner: Runner = run_detection,
) -> list[CapabilityStatus]:
    """Every capability of the selected profiles with its availability: its validators are
    configured (any of them) and every detection passes. Without ``run_detections`` a
    detection command is not run: the capability is planned as available and marked not
    checked. The certification never credits a rung from a plan, only from the validators'
    results."""
    statuses: list[CapabilityStatus] = []
    for profile_id, verification in declared:
        for capability in verification.capabilities or ():
            statuses.append(
                _status(
                    profile_id,
                    capability,
                    workspace,
                    validator_ids,
                    run_detections,
                    timeout,
                    runner,
                )
            )
    return statuses


def _status(
    profile_id: str,
    capability: VerificationCapability,
    workspace: Path,
    validator_ids: set[str],
    run_detections: bool,
    timeout: int,
    runner: Runner,
) -> CapabilityStatus:
    detections: list[DetectionResult] = []
    for item in capability.detect or ():
        if item.command is not None and not run_detections:
            detections.append(
                DetectionResult(
                    " ".join(item.command),
                    True,
                    "not run (capabilityDetection is off)",
                    checked=False,
                )
            )
        else:
            detections.append(detect(item, workspace, timeout, runner))
    validators = tuple(capability.validators or ())
    configured = not validators or any(item in validator_ids for item in validators)
    available = configured and all(item.available for item in detections)
    return CapabilityStatus(
        profile_id,
        capability.level.value,
        capability.provides,
        validators,
        bool(capability.probes),
        available,
        tuple(detections),
        capability.fallback,
    )


__all__ = [
    "ANY_PROFILE",
    "CapabilityStatus",
    "DetectionResult",
    "capability_statuses",
    "detect",
    "load_catalog",
    "profile_verification",
    "run_detection",
]
