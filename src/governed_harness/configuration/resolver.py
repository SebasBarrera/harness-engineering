from __future__ import annotations

import shutil
from pathlib import Path

from governed_harness.configuration.engineering import StandardsConfig
from governed_harness.configuration.loader import (
    BUILTIN_PROFILE_IDS,
    EXTENDED_PROFILES,
    find_project_config,
    load_builtin_profile,
    load_builtin_workflow,
    load_extended_profiles,
    load_project_config,
    load_yaml,
)
from governed_harness.configuration.models import (
    CapabilityRule,
    ResolvedConfiguration,
    TechnologyProfileDefinition,
    ToolchainConfig,
    ValidatorDefinition,
    WorkflowDefinition,
)
from governed_harness.configuration.policies import validate_profile_policies
from governed_harness.configuration.workflow_rules import validate_enforced_workflow
from governed_harness.domain.errors import ConfigurationError
from governed_harness.profiles.detectors import detect_profiles
from governed_harness.profiles.interpreter import discover_python, with_interpreter

PROTECTED_WRITE_ROOTS = (".harness", ".git")
"""Directories whose write scopes are dropped under ``governance.protectExcludedPaths``."""


def _protected_scope(scope: str) -> bool:
    return any(scope == root or scope.startswith(f"{root}/") for root in PROTECTED_WRITE_ROOTS)


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
        toolchain = project.toolchain_settings
        project_profiles, profile_files = load_project_profiles(
            workspace_root,
            toolchain.profile_paths or (),
            reserved=frozenset(EXTENDED_PROFILES) if toolchain.extended_profiles else frozenset(),
        )
        # Since #55: the built-in Go, Rust, JVM, Swift and Android profiles are detected under
        # toolchain.extendedProfiles; without it detection is the 1.0.0 one.
        extended = load_extended_profiles() if toolchain.extended_profiles else {}
        requested = list(project.profiles)
        if requested == ["auto"] or "auto" in requested:
            detections = [
                result
                for result in detect_profiles(
                    workspace_root, [*project_profiles.values(), *extended.values()]
                )
                if result.confidence > 0
            ]
            if not detections:
                raise ConfigurationError("no supported technology profile detected")
            if toolchain.profile_detection == "all":
                requested = [item.profile_id for item in detections]
            else:
                requested = [detections[0].profile_id]
        profiles = tuple(
            project_profiles[profile_id]
            if profile_id in project_profiles
            else load_builtin_profile(profile_id)
            for profile_id in dict.fromkeys(requested)
        )
        workflow = load_builtin_workflow(project.workflow)
        if project.governance_settings.enforce_workflow:
            # Since #3: every gate and condition the engine will evaluate must be known.
            validate_enforced_workflow(workflow)
        validators = self._resolve_validators(project.validators, profiles)
        grants = (*project.capabilities.grants, *(project.capabilities.extend or ()))
        if toolchain.validators or toolchain.interpreter == "auto":
            validators, extra_scopes = self._project_toolchain(
                validators, toolchain, workspace_root
            )
            grants = (
                *grants,
                *(
                    CapabilityRule(capability="process.execute", scope=(scope,))
                    for scope in extra_scopes
                ),
            )
        standards = project.standards
        if standards is not None and standards.tools_enabled:
            validators, tool_scopes = self._standards_tools(
                validators, standards, workspace_root, profiles
            )
            grants = (
                *grants,
                *(
                    CapabilityRule(capability="process.execute", scope=(scope,))
                    for scope in tool_scopes
                ),
            )
        if project.governance_settings.phase_capabilities:
            # #4: the project narrows the profiles; the scopes derived from the validators the
            # project selects are added after it, and each phase allows what it declares.
            derived = grants[len(project.capabilities.grants) :]
            capabilities = self._narrowed_capabilities(
                project.capabilities.grants, derived, profiles
            )
            workflow = self._phase_workflow(workflow)
        else:
            capabilities = self._resolve_capabilities(grants, profiles)
        if project.governance_settings.protect_excluded_paths:
            capabilities = self._without_protected_writes(capabilities)
        policies = self._resolve_policies(project.policies, profiles)
        if project.governance_settings.apply_profile_policies:
            validate_profile_policies(policies)
        return ResolvedConfiguration(
            project=project,
            workspace_root=workspace_root,
            profiles=profiles,
            workflow=workflow,
            effective_capabilities=capabilities,
            effective_validators=validators,
            effective_policies=policies,
            source_files=(
                str(config_path),
                "builtin:workflow/default",
                *(
                    f"builtin:profile/{p.profile_id}"
                    for p in profiles
                    if p.profile_id not in project_profiles
                ),
                *profile_files,
            ),
        )

    @classmethod
    def _narrowed_capabilities(
        cls,
        explicit: tuple[CapabilityRule, ...],
        derived: tuple[CapabilityRule, ...],
        profiles: tuple[TechnologyProfileDefinition, ...],
    ) -> tuple[CapabilityRule, ...]:
        from governed_harness.capabilities.phase import intersect_capabilities

        profile_rules = cls._resolve_capabilities((), profiles)
        narrowed = intersect_capabilities(profile_rules, explicit)
        return cls._resolve_capabilities((*narrowed, *derived), ())

    @staticmethod
    def _phase_workflow(workflow: WorkflowDefinition) -> WorkflowDefinition:
        """The workflow whose phases also allow what the harness itself runs in them
        (``HARNESS_PHASE_CAPABILITIES``), recorded in the resolved configuration."""
        from governed_harness.capabilities.phase import HARNESS_PHASE_CAPABILITIES

        phases = tuple(
            phase.model_copy(
                update={
                    "allowed_capabilities": tuple(
                        dict.fromkeys(
                            (
                                *phase.allowed_capabilities,
                                *HARNESS_PHASE_CAPABILITIES.get(phase.phase_id.value, ()),
                            )
                        )
                    )
                }
            )
            for phase in workflow.phases
        )
        return workflow.model_copy(update={"phases": phases})

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
            CapabilityRule(
                capability=capability, scope=tuple(sorted(scopes)), approvalRequired=approval
            )
            for (capability, approval), scopes in sorted(merged.items())
        )

    @staticmethod
    def _without_protected_writes(
        capabilities: tuple[CapabilityRule, ...],
    ) -> tuple[CapabilityRule, ...]:
        """Drop write grants on the harness state and on Git (``governance.protectExcludedPaths``):
        the built-in profiles grant ``filesystem.write`` on ``.harness/**``."""
        kept: list[CapabilityRule] = []
        for rule in capabilities:
            if rule.capability == "filesystem.write":
                scope = tuple(item for item in rule.scope if not _protected_scope(item))
                if not scope:
                    continue
                rule = rule.model_copy(update={"scope": scope})
            kept.append(rule)
        return tuple(kept)

    @staticmethod
    def _resolve_validators(
        requested: tuple[str, ...], profiles: tuple[TechnologyProfileDefinition, ...]
    ) -> tuple[ValidatorDefinition, ...]:
        available: dict[str, ValidatorDefinition] = {}
        defaults: list[str] = []
        for profile in profiles:
            defaults.extend(profile.default_validators)
            available.update(
                {validator.validator_id: validator for validator in profile.validators}
            )
        selected = list(requested) if requested else defaults
        missing = [validator_id for validator_id in selected if validator_id not in available]
        if missing:
            raise ConfigurationError(f"validators not defined by selected profiles: {missing}")
        return tuple(available[validator_id] for validator_id in dict.fromkeys(selected))

    @staticmethod
    def _project_toolchain(
        validators: tuple[ValidatorDefinition, ...],
        toolchain: ToolchainConfig,
        workspace_root: Path,
    ) -> tuple[tuple[ValidatorDefinition, ...], tuple[str, ...]]:
        """Apply ``toolchain.validators`` (replace a selected validator with the same id, add
        the others) and ``toolchain.interpreter``. Returns the validators and the
        ``process.execute`` scopes their commands need: the exact command of each project
        validator and the discovered interpreter prefix."""
        by_id = {item.validator_id: item for item in validators}
        scopes: list[str] = []
        for item in toolchain.validators or ():
            by_id[item.validator_id] = item
            scopes.append(" ".join(item.command or ()))
        interpreter = discover_python(workspace_root) if toolchain.interpreter == "auto" else None
        resolved = tuple(
            item.model_copy(update={"command": with_interpreter(item.command, interpreter)})
            for item in by_id.values()
        )
        if interpreter is not None and any(
            item.command and item.command[: len(interpreter.argv)] == interpreter.argv
            for item in resolved
        ):
            scopes.append(interpreter.grant_scope)
        return resolved, tuple(dict.fromkeys(scope for scope in scopes if scope))

    @staticmethod
    def _standards_tools(
        validators: tuple[ValidatorDefinition, ...],
        standards: StandardsConfig,
        workspace_root: Path,
        profiles: tuple[TechnologyProfileDefinition, ...],
    ) -> tuple[tuple[ValidatorDefinition, ...], tuple[str, ...]]:
        """``standards.tools: detect`` (#56): an optional validator for every pack tool the
        repository configures and no selected validator runs already, with a
        ``process.execute`` grant for its exact command."""
        from governed_harness.standards import project_standards, tool_validators

        packs = project_standards(
            workspace_root,
            packs=standards.packs,
            overrides=standards.overrides_path,
            disabled=standards.disabled or (),
            technologies=tuple(item.technology for item in profiles),
        )
        added = tool_validators(packs, workspace_root, [item.validator_id for item in validators])
        return (
            (*validators, *(definition for _pack, _tool, definition in added)),
            tuple(" ".join(definition.command or ()) for _pack, _tool, definition in added),
        )

    @staticmethod
    def _resolve_policies(
        project_policies: dict[str, object], profiles: tuple[TechnologyProfileDefinition, ...]
    ) -> dict[str, object]:
        resolved: dict[str, object] = dict(CORE_POLICIES)
        for profile in profiles:
            resolved.update(profile.policies)
        # Locked core policies may only remain true/deny. Other values are project-configurable.
        for key, value in project_policies.items():
            if (
                key
                in {"requireHumanDecision", "approvalDigestBinding", "mandatoryNonSuccessBlocks"}
                and value is not True
            ):
                raise ConfigurationError(
                    f"project configuration may not weaken locked policy {key}"
                )
            if key == "retrospectiveAutoApply" and value is not False:
                raise ConfigurationError("retrospectiveAutoApply is a locked false policy")
            resolved[key] = value
        return resolved


def load_project_profiles(
    workspace_root: Path, paths: tuple[str, ...], reserved: frozenset[str] = frozenset()
) -> tuple[dict[str, TechnologyProfileDefinition], tuple[str, ...]]:
    """The profiles of ``toolchain.profilePaths`` by id, and the files they came from."""
    files: list[Path] = []
    for item in paths:
        target = (workspace_root / item).resolve()
        try:
            target.relative_to(workspace_root)
        except ValueError as error:
            raise ConfigurationError(f"profile path leaves the workspace: {item}") from error
        if target.is_dir():
            files.extend(sorted(target.glob("*.yaml")))
        elif target.is_file():
            files.append(target)
        else:
            raise ConfigurationError(f"profile path not found: {item}")
    profiles: dict[str, TechnologyProfileDefinition] = {}
    for path in files:
        try:
            profile = TechnologyProfileDefinition.model_validate(load_yaml(path))
        except ConfigurationError:
            raise
        except Exception as error:
            raise ConfigurationError(f"invalid project profile {path}: {error}") from error
        if profile.profile_id in BUILTIN_PROFILE_IDS | reserved:
            raise ConfigurationError(
                f"project profile {path} uses the built-in profile id {profile.profile_id!r}"
            )
        if profile.profile_id in profiles:
            raise ConfigurationError(f"profile id {profile.profile_id!r} is declared twice")
        profiles[profile.profile_id] = profile
    return profiles, tuple(str(path) for path in files)


def executable_available(argv0: str) -> bool:
    if "/" in argv0 or "\\" in argv0:
        return Path(argv0).exists()
    return shutil.which(argv0) is not None
