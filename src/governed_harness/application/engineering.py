"""Application side of the wave 6 settings (#56): standards, architecture, project detection.

Read-only reports (``harness standards show``, ``harness architecture show``,
``harness project show``) and the architecture decision of a person (digest-bound)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.engineering import StandardsConfig
from governed_harness.domain.actors import require_human_actor
from governed_harness.domain.enums import ActorType, DecisionKind
from governed_harness.domain.models import Actor
from governed_harness.intake.project_kind import detect_project_kind
from governed_harness.orchestration.architecture import (
    ADR_FILE,
    SURVEY_FILE,
    effective_rules,
    read_state,
    source_layout,
)
from governed_harness.orchestration.engineering import detect_testing
from governed_harness.standards import (
    BUILTIN_PACKS,
    describe_pack,
    project_standards,
    select_cards,
    tool_validators,
)

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices


def standards_report(
    workspace: Path,
    config: StandardsConfig | None,
    technologies: tuple[str, ...],
    existing_validators: list[str],
    *,
    pack: str | None = None,
    files: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The effective packs of a workspace (or one pack), the cards for some files and the
    tools the repository configures."""
    settings = config or StandardsConfig()
    packs = (pack,) if pack else settings.packs
    standards = project_standards(
        workspace,
        packs=packs,
        overrides=settings.overrides_path,
        disabled=settings.disabled or (),
        technologies=technologies,
    )
    report: dict[str, Any] = {
        "configured": config is not None,
        "detected": list(standards.detected),
        "packs": [describe_pack(item, include_cards=bool(pack)) for item in standards.packs],
        "digest": standards.digest,
        "available": list(BUILTIN_PACKS),
        "toolValidators": [
            {"pack": pack_id, "tool": tool.tool_id, "validatorId": definition.validator_id}
            for pack_id, tool, definition in tool_validators(
                standards, workspace, existing_validators
            )
        ],
    }
    if files:
        report["selection"] = {
            "files": list(files),
            "implement": [
                item.compact() for item in select_cards(standards, files, settings.card_limit)
            ],
            "reviewChecklist": [
                item.card_id
                for item in select_cards(standards, files, settings.card_limit, review_only=True)
            ],
        }
    return report


def architecture_report(services: EngineServices) -> dict[str, Any]:
    project = services.resolved.project
    harness_dir = services.paths.harness_dir
    rules = effective_rules(project.architecture, harness_dir)
    state = read_state(harness_dir)
    survey = harness_dir / SURVEY_FILE
    adr = harness_dir / ADR_FILE
    return {
        "configured": project.architecture.model_dump(mode="json", by_alias=True)
        if project.architecture
        else None,
        "projectKind": detect_project_kind(services.paths.workspace).as_dict(),
        "state": state,
        "rules": rules.as_dict() if rules else None,
        "forbiddenImports": [
            {"from": source, "to": target} for source, target in rules.forbidden_pairs()
        ]
        if rules
        else [],
        "layout": source_layout(services.paths.workspace),
        "survey": survey.read_text(encoding="utf-8") if survey.is_file() else None,
        "adr": adr.read_text(encoding="utf-8") if adr.is_file() else None,
    }


def decide_architecture(
    services: EngineServices,
    execution_id: str,
    *,
    digest: str,
    actor_id: str,
    rationale: str,
    decision: DecisionKind | None,
    option: str | None,
    continue_after: bool,
) -> dict[str, Any]:
    """A person approves or rejects the inferred rules, or chooses an option (an ADR)."""
    from governed_harness.orchestration.engine import RunEngine

    require_human_actor(actor_id, "decide the architecture")
    engine = RunEngine(services)
    state = engine.results.architecture.decide(
        engine.get_execution(execution_id),
        actor=Actor(actor_type=ActorType.HUMAN, actor_id=actor_id),
        digest=digest,
        rationale=rationale,
        decision=decision,
        option=option,
    )
    result: dict[str, Any] = {"executionId": execution_id, "architecture": state}
    if continue_after:
        result["execution"] = engine.continue_execution(execution_id).model_dump(
            mode="json", by_alias=True
        )
    else:
        engine.anchor_chain(execution_id)
    return result


def refresh_architecture(services: EngineServices) -> dict[str, Any]:
    from governed_harness.orchestration.engine import RunEngine

    return RunEngine(services).results.architecture.refresh()


def project_report(services: EngineServices) -> dict[str, Any]:
    """What the harness detects about the project, deterministically and without a call."""
    from governed_harness.application.forges import forge_report
    from governed_harness.orchestration.engine import RunEngine

    engine = RunEngine(services)
    workspace = services.paths.workspace
    technologies = tuple(item.technology for item in services.resolved.profiles)
    project = services.resolved.project
    standards = engine.results.engineering.standards()
    strategy = (
        engine.results.engineering.strategy(project.project_id)
        if engine.results.engineering.configured
        else detect_testing(workspace, technologies)
    )
    rules = effective_rules(project.architecture, services.paths.harness_dir)
    return {
        "projectKind": detect_project_kind(workspace).as_dict(),
        "profiles": [item.profile_id for item in services.resolved.profiles],
        "standards": {
            "packs": [item.pack_id for item in standards.packs] if standards else None,
            "detected": list(standards.detected)
            if standards
            else list(
                project_standards(
                    workspace, packs=None, overrides=None, technologies=technologies
                ).detected
            ),
        },
        "testing": strategy.as_dict(),
        "architecture": {
            "status": (read_state(services.paths.harness_dir) or {}).get("status"),
            "layers": [item.name for item in rules.layers] if rules else [],
        },
        "projectSetup": engine.results.engineering.setup_record(project.project_id) or None,
        "forge": forge_report(services),
    }


def _effective_packs(
    standards: Any, workspace: Path, technologies: tuple[str, ...]
) -> list[str] | None:
    if standards is None:
        return None
    return [
        item.pack_id
        for item in project_standards(
            workspace,
            packs=standards.packs,
            overrides=standards.overrides_path,
            disabled=standards.disabled or (),
            technologies=technologies,
        ).packs
    ]


def _testing_strategy(testing: Any, workspace: Path, technologies: tuple[str, ...]) -> str:
    """The configured strategy; ``auto`` (or none) detects it; without ``testing``, off."""
    if not testing:
        return "off"
    if testing.strategy not in {None, "auto"}:
        return str(testing.strategy)
    return detect_testing(workspace, technologies).strategy


def engineering_summary(resolved: Any) -> dict[str, Any]:
    """Effective wave 6 settings for ``harness config validate`` (absent: 1.0.0 behaviour)."""
    project = resolved.project
    workspace = resolved.workspace_root
    technologies = tuple(item.technology for item in resolved.profiles)
    standards = project.standards
    principles = project.verification.principles if project.verification else None
    architecture = project.architecture
    forge = project.delivery_settings.forge
    return {
        "projectKind": detect_project_kind(workspace).kind,
        "standards": {
            "cards": standards.cards if standards else "off",
            "tools": standards.tools if standards else "off",
            "packs": _effective_packs(standards, workspace, technologies),
        },
        "principles": principles.mode if principles else "off",
        "testing": {"strategy": _testing_strategy(project.testing, workspace, technologies)},
        "architecture": {
            "mode": architecture.mode if architecture else "off",
            "style": architecture.style if architecture else None,
            "layers": len(architecture.layers or ()) if architecture else 0,
        },
        "projectSetup": project.intake.project_setup if project.intake else None,
        "forge": forge.kind if forge and forge.kind else "auto",
    }


__all__ = [
    "architecture_report",
    "decide_architecture",
    "engineering_summary",
    "project_report",
    "refresh_architecture",
    "standards_report",
]
