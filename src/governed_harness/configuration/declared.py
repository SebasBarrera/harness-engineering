"""Settings that are declared but not applied, reported by ``harness config validate`` (#51).

Each one is marked ``x-declarative`` in the generated JSON Schema (or listed here when it is a
policy or a setting whose effect depends on a ``governance`` key). ``config validate`` lists
them all under ``declarative`` and adds a warning for each one the project actually relies on.
"""

from __future__ import annotations

from typing import Any

from governed_harness.configuration.models import ResolvedConfiguration

DECLARATIVE_SETTINGS: dict[str, str] = {
    "workspace.units": "Declared units are not used by the engine.",
    "policies.repositoryContentTrusted": "No component reads it; repository content is always "
    "treated as untrusted.",
    "policies.destructiveActionsDefault": "No component reads it; destructive actions follow "
    "the capability grants.",
    "policies.ambiguousPackageManager": "No component reads it; the Node.js profile always uses "
    "npm.",
    "workflow.phases[].allowedCapabilities": "Capabilities are granted per run (issue #4).",
    "workflow.phases[].validators": "The validators come from the profiles and the project.",
    "workflow.invariants": "Names of invariants the engine enforces in code.",
}

_PROJECT_KEYS = {
    "workspace.units": ("workspace", "units"),
}
_POLICY_KEYS = ("repositoryContentTrusted", "destructiveActionsDefault", "ambiguousPackageManager")


def _present(raw: dict[str, Any], path: tuple[str, ...]) -> bool:
    value: Any = raw
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return value not in (None, [], {})


def declared_settings_report(
    resolved: ResolvedConfiguration, raw: dict[str, Any]
) -> tuple[list[dict[str, str]], list[str]]:
    """``(declarative, warnings)``: every declarative setting with its reason, and a warning for
    each one this project sets (or receives from a profile) and for each declared setting that a
    ``governance`` key would apply but that key is off."""
    declarative = [{"key": key, "reason": reason} for key, reason in DECLARATIVE_SETTINGS.items()]
    warnings: list[str] = []
    for key, path in _PROJECT_KEYS.items():
        if _present(raw, path):
            warnings.append(f"{key} is declarative: {DECLARATIVE_SETTINGS[key]}")
    for key in _POLICY_KEYS:
        if key in resolved.effective_policies and key in _project_or_profile_policies(resolved):
            warnings.append(
                f"policies.{key} is declarative: {DECLARATIVE_SETTINGS[f'policies.{key}']}"
            )
    governance = resolved.project.governance_settings
    if not governance.apply_workflow_settings:
        warnings.append(
            "workflow maxAttempts, timeoutSeconds and exitGate are declared but not applied; "
            "set governance.applyWorkflowSettings: true"
        )
    if not governance.enforce_workflow:
        # Since #3 these take effect under governance.enforceWorkflow.
        warnings.append(
            "workflow exitGate, dependsOn and parallelizable are declared but not enforced; "
            "set governance.enforceWorkflow: true"
        )
        if _present(raw, ("runtime", "maxParallel")):
            warnings.append(
                "runtime.maxParallel is declarative without governance.enforceWorkflow: phases "
                "and validators run one at a time"
            )
    if not governance.apply_network_policy and not resolved.project.runtime.allow_network:
        warnings.append(
            "runtime.allowNetwork: false is not enforced; set governance.applyNetworkPolicy: "
            "true (it applies to the agent sandbox under runtime.agentSandbox: enforce)"
        )
    if (
        governance.apply_network_policy
        and resolved.project.runtime.effective_agent_sandbox == "off"
    ):
        warnings.append(
            "governance.applyNetworkPolicy has no effect while runtime.agentSandbox is off"
        )
    profile_policies = {
        key
        for key in ("missingTestCommand", "missingTestScript", "coverage")
        if key in resolved.effective_policies
    }
    if profile_policies and not governance.apply_profile_policies:
        warnings.append(
            f"policies {', '.join(sorted(profile_policies))} are declared but not applied; set "
            "governance.applyProfilePolicies: true"
        )
    coverage = resolved.effective_policies.get("coverage")
    if (
        governance.apply_profile_policies
        and isinstance(coverage, dict)
        and not any(profile.technology == "python" for profile in resolved.profiles)
    ):
        warnings.append("policies.coverage.minimumPercent is applied to Python projects only")
    if resolved.project.retention:
        warnings.append("retention is applied only when harness gc --apply runs")
    return declarative, warnings


def _project_or_profile_policies(resolved: ResolvedConfiguration) -> set[str]:
    keys = set(resolved.project.policies)
    for profile in resolved.profiles:
        keys |= set(profile.policies)
    return keys
