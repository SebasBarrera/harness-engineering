from __future__ import annotations

import re
from pathlib import Path

import yaml

from governed_harness.configuration.agent_results import (
    DEFAULT_COARSE_MODELS,
    DEFAULT_CONTEXT_MAX_BYTES,
    DEFAULT_CONTEXT_MAX_FILES,
    DEFAULT_DECOMPOSITION_THRESHOLD,
    DEFAULT_RISK_ACTIONS,
    DEFAULT_SIZE_THRESHOLDS,
)
from governed_harness.configuration.api import DEFAULT_API_TOKEN_ENV, DEFAULT_API_TOKEN_ROLE
from governed_harness.configuration.engineering import (
    DEFAULT_DUPLICATION_WINDOW,
    DEFAULT_FEATURES_DIRECTORY,
    DEFAULT_INHERITANCE_DEPTH,
    DEFAULT_MAX_CARDS,
)
from governed_harness.configuration.friction import (
    DEFAULT_FAST_LANE_SKIP,
    DEFAULT_FRICTION_TARGETS,
    DEFAULT_PRE_AUTHORIZATION_HOURS,
)
from governed_harness.configuration.ladder import (
    DEFAULT_DEFERRED_EXPIRY_DAYS,
    DEFAULT_INSTRUCTION_FILES,
    DEFAULT_INTERRUPTION_TARGET,
    DEFAULT_ISOLATION_BRANCH,
    DEFAULT_MUTATION_HUNKS,
    DEFAULT_MUTATION_SECONDS,
    STOP_CONDITIONS,
)
from governed_harness.configuration.models import (
    DEFAULT_DECISION_EXPIRY_HOURS,
    DEFAULT_EXCEPTION_DAYS,
    DEFAULT_SANDBOX_WRITE_PATHS,
    DEFAULT_TRUSTED_HOSTS,
)
from governed_harness.domain.actors import DEFAULT_API_ACTOR
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
            # Runs stay in place unless run start --isolate worktree (or mode: worktree): the
            # README quickstart and the documented flows inspect the workspace itself.
            "isolation": {"mode": "none", "branch": DEFAULT_ISOLATION_BRANCH, "fetch": True},
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
            "gateContract": True,
            "reproduceFirst": True,
            "stateDir": "auto",
        },
        "retention": {"artifactDays": 30, "eventDays": 365, "orphanArtifacts": True},
        "intake": {
            "criteriaPolicy": "enforce",
            "ambiguityReview": "agent",
            "validateAnswers": True,
            "operationalContract": "batch",
            "interruptions": {
                "target": DEFAULT_INTERRUPTION_TARGET,
                "stopConditions": list(STOP_CONDITIONS),
            },
            "projectSetup": "ask",
        },
        "verification": {
            "requirementTraceability": "enforce",
            "outputParsers": True,
            "interface": "enforce",
            "architecture": {
                "maxModuleLines": 800,
                "maxFunctionLines": 80,
                "maxComplexity": 15,
                "severity": "MEDIUM",
            },
            "securityPatterns": True,
            "constraints": "enforce",
            "ratchet": "enforce",
            "differential": True,
            "weakenedControls": "enforce",
            "testQuality": {
                "assertions": True,
                "interfaceTests": True,
                "diffCoverage": 80,
                "flakyReruns": 1,
                "severity": "MEDIUM",
            },
            "secrets": "context",
            "riskFactors": dict(DEFAULT_RISK_ACTIONS),
            "acceptanceTests": {"mode": "agent"},
            "ladder": {
                "mode": "enforce",
                "defaultLevel": "L1",
                "deferredExpiryDays": DEFAULT_DEFERRED_EXPIRY_DAYS,
                "preflight": True,
                "capabilityDetection": True,
            },
            "mutation": {
                "mode": "warn",
                "maxHunks": DEFAULT_MUTATION_HUNKS,
                "maxSeconds": DEFAULT_MUTATION_SECONDS,
            },
            "principles": {
                "mode": "enforce",
                "duplicationWindow": DEFAULT_DUPLICATION_WINDOW,
                "maxInheritanceDepth": DEFAULT_INHERITANCE_DEPTH,
                "unusedPublic": True,
                "boyScout": True,
                "checklist": True,
                "severity": "MEDIUM",
            },
        },
        "review": {
            "exceptions": True,
            "exceptionDays": DEFAULT_EXCEPTION_DAYS,
            "agentReview": "enforce",
            "structuredChanges": True,
            "manualChecklist": True,
        },
        "retrospective": {"causal": True},
        "planning": {
            "decomposition": "agent",
            "threshold": DEFAULT_DECOMPOSITION_THRESHOLD,
            "granularity": "adaptive",
            "coarseModels": list(DEFAULT_COARSE_MODELS),
        },
        "context": {
            "manifest": "auto",
            "maxFiles": DEFAULT_CONTEXT_MAX_FILES,
            "maxBytes": DEFAULT_CONTEXT_MAX_BYTES,
            "locate": {"mode": "agent"},
        },
        # Wide on purpose: limits only at the extremes (#42).
        "budget": {
            "perCall": {"costUsd": 25, "wallSeconds": 7200},
            "perTask": {"costUsd": 200},
            "perRun": {"costUsd": 100, "tokens": 500_000_000, "wallSeconds": 43_200},
            "warnAt": 0.8,
        },
        "memory": {"learnFromFindings": "auto", "autoApproveRecurring": False},
        "agentRouting": {
            "mode": "tiered",
            "thresholds": {key: list(value) for key, value in DEFAULT_SIZE_THRESHOLDS.items()},
            "maxEscalations": 2,
        },
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
            "stopTheLine": "restore",
            "phasePermissions": True,
            "enforceWorkflow": True,
        },
        "toolchain": {"profileDetection": "all", "interpreter": "auto", "extendedProfiles": True},
        "provenance": {"agentSnapshots": True, "selfReport": True},
        # Nothing leaves the machine unless the task's contract (or a person) authorises it;
        # the forge of the pull request is detected from the origin remote (delivery.forge
        # only to override it, #56).
        "delivery": {
            "closureCommit": "branch",
            "stage": True,
            "push": False,
            "pullRequest": {"create": False, "draft": True},
            "comment": "notClean",
        },
        "environment": {"dirtyTree": "warn", "baseline": "report"},
        "instructions": {
            "files": list(DEFAULT_INSTRUCTION_FILES),
            "precedence": ["harness", *DEFAULT_INSTRUCTION_FILES],
        },
        # Wave 6 (#56): standards packs, testing strategy and architecture.
        "standards": {
            "packs": ["auto"],
            "cards": "auto",
            "maxCards": DEFAULT_MAX_CARDS,
            "tools": "detect",
        },
        "testing": {"strategy": "auto", "featuresDirectory": DEFAULT_FEATURES_DIRECTORY},
        "architecture": {"mode": "agent", "refresh": "manual", "enforce": "enforce"},
        # Wave 8 (#58): a fast lane for small, risk-free tasks, approval in advance under a
        # condition, documentation-only and configuration-only changes, friction targets.
        "friction": {
            "fastLane": {
                "mode": "auto",
                "skip": list(DEFAULT_FAST_LANE_SKIP),
                "verification": {"affectedTestsFirst": True, "parallel": True, "cache": True},
            },
            "preAuthorization": {
                "mode": "allow",
                "defaultHours": DEFAULT_PRE_AUTHORIZATION_HOURS,
                "maxHours": 72,
            },
            "changeTypes": True,
            "planApproval": "risk",
            "targets": {size: dict(values) for size, values in DEFAULT_FRICTION_TARGETS.items()},
        },
        # #18: harness api serve requires a bearer token on every route. The token of the
        # person who starts it comes from tokenEnv, or is generated and shown once; more people
        # go under users, each with an id, a role and the NAME of their token's variable.
        "api": {
            "auth": "token",
            "tokenEnv": DEFAULT_API_TOKEN_ENV,
            "tokenUser": DEFAULT_API_ACTOR,
            "tokenRole": DEFAULT_API_TOKEN_ROLE,
            "users": [],
        },
    }
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return config_path
