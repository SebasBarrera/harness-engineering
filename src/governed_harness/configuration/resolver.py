from __future__ import annotations

import shutil
from pathlib import Path

from governed_harness.configuration.loader import (
    find_project_config,
    load_builtin_profile,
    load_builtin_workflow,
    load_project_config,
)
from governed_harness.configuration.models import (
    CapabilityRule,
    ResolvedConfiguration,
    TechnologyProfileDefinition,
    ValidatorDefinition,
)
from governed_harness.domain.errors import ConfigurationError
from governed_harness.profiles.detectors import detect_profiles

CORE_POLICIES = {
    "requireHumanDecision": True,
    "approvalDigestBinding": True,
    "mandatoryNonSuccessBlocks": True,
    "retrospectiveAutoApply": False,
    "repositoryContentTrusted": False,
    "destructiveActionsDefault": "deny",
    "findingBlockSeverities": ["HIGH", "CRITICAL"],
}


class ConfigurationResolver:
    def resolve(self, start: Path) -> ResolvedConfiguration:
        config_path = find_project_config(start)
        project = load_project_config(config_path)
        workspace_root = (config_path.parent / project.workspace.root).resolve(strict=True)
        requested = list(project.profiles)
        if requested == ["auto"] or "auto" in requested:
            detections = [result for result in detect_profiles(workspace_root) if result.confidence > 0]
            if not detections:
                raise ConfigurationError("no supported technology profile detected")
            best = detections[0]
            requested = [best.profile_id]
        profiles = tuple(load_builtin_profile(profile_id) for profile_id in requested)
        workflow = load_builtin_workflow(project.workflow)
        capabilities = self._resolve_capabilities(project.capabilities.grants, profiles)
        validators = self._resolve_validators(project.validators, profiles)
        policies = self._resolve_policies(project.policies, profiles)
        return ResolvedConfiguration(
            project=project,
            workspace_root=workspace_root,
            profiles=profiles,
            workflow=workflow,
            effective_capabilities=capabilities,
            effective_validators=validators,
            effective_policies=policies,
            source_files=(str(config_path), "builtin:workflow/default", *(f"builtin:profile/{p.profile_id}" for p in profiles)),
        )

    @staticmethod
    def _resolve_capabilities(
        explicit: tuple[CapabilityRule, ...], profiles: tuple[TechnologyProfileDefinition, ...]
    ) -> tuple[CapabilityRule, ...]:
        merged: dict[tuple[str, bool], set[str]] = {}
        for profile in profiles:
            for capability, scopes in profile.capabilities.items():
                merged.setdefault((capability, False), set()).update(scopes)
        for rule in explicit:
            merged.setdefault((rule.capability, rule.approval_required), set()).update(rule.scope)
        return tuple(
            CapabilityRule(capability=capability, scope=tuple(sorted(scopes)), approvalRequired=approval)
            for (capability, approval), scopes in sorted(merged.items())
        )

    @staticmethod
    def _resolve_validators(
        requested: tuple[str, ...], profiles: tuple[TechnologyProfileDefinition, ...]
    ) -> tuple[ValidatorDefinition, ...]:
        available: dict[str, ValidatorDefinition] = {}
        defaults: list[str] = []
        for profile in profiles:
            defaults.extend(profile.default_validators)
            available.update({validator.validator_id: validator for validator in profile.validators})
        selected = list(requested) if requested else defaults
        missing = [validator_id for validator_id in selected if validator_id not in available]
        if missing:
            raise ConfigurationError(f"validators not defined by selected profiles: {missing}")
        return tuple(available[validator_id] for validator_id in dict.fromkeys(selected))

    @staticmethod
    def _resolve_policies(
        project_policies: dict[str, object], profiles: tuple[TechnologyProfileDefinition, ...]
    ) -> dict[str, object]:
        resolved: dict[str, object] = dict(CORE_POLICIES)
        for profile in profiles:
            resolved.update(profile.policies)
        # Locked core policies may only remain true/deny. Other values are project-configurable.
        for key, value in project_policies.items():
            if key in {"requireHumanDecision", "approvalDigestBinding", "mandatoryNonSuccessBlocks"} and value is not True:
                raise ConfigurationError(f"project configuration may not weaken locked policy {key}")
            if key == "retrospectiveAutoApply" and value is not False:
                raise ConfigurationError("retrospectiveAutoApply is a locked false policy")
            resolved[key] = value
        return resolved


def executable_available(argv0: str) -> bool:
    if "/" in argv0 or "\\" in argv0:
        return Path(argv0).exists()
    return shutil.which(argv0) is not None
