from __future__ import annotations

import re
from pathlib import Path

import yaml

from governed_harness.configuration.models import (
    DEFAULT_DECISION_EXPIRY_HOURS,
    DEFAULT_EXCEPTION_DAYS,
    DEFAULT_SANDBOX_WRITE_PATHS,
    DEFAULT_TRUSTED_HOSTS,
)
from governed_harness.domain.errors import ConfigurationError


def project_id_from_path(path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", path.name.lower()).strip("_") or "local"
    return f"project_{slug[:80]}"


def initialize_project(path: Path, *, force: bool = False) -> Path:
    target = path.resolve(strict=True)
    if not target.is_dir():
        raise ConfigurationError(f"project path is not a directory: {target}")
    harness_dir = target / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = harness_dir / "project.yaml"
    if config_path.exists() and not force:
        raise ConfigurationError(f"configuration already exists: {config_path}")
    value = {
        "configVersion": "1.0",
        "projectId": project_id_from_path(target),
        "workspace": {
            "root": "..",
            "units": [],
            "snapshot": "git",
            "baseline": "manifest",
            "snapshotCache": True,
        },
        "profiles": ["auto"],
        "workflow": "default_development",
        "capabilities": {"default": "deny", "grants": []},
        "validators": [],
        "policies": {
            "requireHumanDecision": True,
            "findingBlockSeverities": ["HIGH", "CRITICAL"],
        },
        "agentProvider": "simulated",
        "runtime": {
            "commandTimeoutSeconds": 900,
            "maxOutputBytes": 1000000,
            "maxParallel": 2,
            # Enforced under governance.applyNetworkPolicy: agent CLIs call their model API.
            "allowNetwork": True,
            "agentSandbox": "enforce",
            "sandboxWritePaths": [path for path, _ in DEFAULT_SANDBOX_WRITE_PATHS],
            "verificationCorrections": 2,
            "providerFeedback": True,
            "unsupportedClaimSeverity": "MEDIUM",
            "providerRetries": 3,
            "providerRetryDelaySeconds": 60,
            "extendedRedaction": True,
        },
        "retention": {"artifactDays": 30, "eventDays": 365, "orphanArtifacts": True},
        "intake": {"criteriaPolicy": "enforce"},
        "verification": {"requirementTraceability": "enforce", "outputParsers": True},
        "review": {"exceptions": True, "exceptionDays": DEFAULT_EXCEPTION_DAYS},
        "retrospective": {"causal": True},
        "governance": {
            "deciderIdentity": "git",
            "confirmDecisionDigest": True,
            "trustedHosts": list(DEFAULT_TRUSTED_HOSTS),
            "verifyRecords": True,
            "chainAnchor": "file",
            "pinTaskRevision": True,
            "protectExcludedPaths": True,
            "workspaceLease": True,
            "applyWorkflowSettings": True,
            "decisionExpiryHours": DEFAULT_DECISION_EXPIRY_HOURS,
            "applyProfilePolicies": True,
            "applyNetworkPolicy": True,
        },
        "toolchain": {"profileDetection": "all", "interpreter": "auto"},
        "provenance": {"agentSnapshots": True, "selfReport": True},
        "delivery": {"closureCommit": "branch"},
    }
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return config_path
