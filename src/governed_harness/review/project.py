"""The review panel of one project: its settings, catalog, reviewers, routing, consistency checks
and linters (#57). Shared by INDEPENDENT_REVIEW and ``harness review-code``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governed_harness.agents.routing import provider_family, select_reviewer
from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.models import ResolvedConfiguration
from governed_harness.configuration.review import ReviewPanelConfig
from governed_harness.domain.enums import ActorType, ResultStatus
from governed_harness.domain.models import Actor
from governed_harness.review.checks import CHECKS
from governed_harness.review.contract import ReviewFinding
from governed_harness.review.diff import Locations
from governed_harness.review.panel import Route
from governed_harness.review.reviewers import Reviewer, load_reviewers, reviewer_domains
from governed_harness.review.rules import (
    HARNESS_TOOL,
    Catalog,
    InactiveRule,
    Rule,
    builtin_rules,
    configured_tools,
    merge,
    pack_rules,
    project_rules,
)
from governed_harness.runtime import CancellationToken
from governed_harness.runtime.process_runner import CommandSpec, ProcessResult, SafeProcessRunner
from governed_harness.standards import PackTool, ProjectStandards, project_standards

CONSISTENCY_ACTOR = Actor(
    actor_type=ActorType.TOOL, actor_id="validator.review.consistency", version="1"
)
TOOLS_ACTOR = Actor(actor_type=ActorType.TOOL, actor_id="validator.review.tools", version="1")
_OUTPUT_TAIL = 2000


@dataclass(frozen=True)
class ReviewSetup:
    settings: ReviewPanelConfig
    catalog: Catalog
    reviewers: list[Reviewer]
    standards: ProjectStandards | None
    tools: frozenset[str]

    @property
    def packs(self) -> tuple[str, ...]:
        return tuple(item.pack_id for item in self.standards.packs) if self.standards else ()


def panel_settings(resolved: ResolvedConfiguration) -> ReviewPanelConfig:
    review = resolved.project.review
    return (review.panel if review is not None else None) or ReviewPanelConfig()


def _standards(resolved: ResolvedConfiguration, workspace: Path) -> ProjectStandards | None:
    config = resolved.project.standards
    technologies = tuple(item.technology for item in resolved.profiles)
    if config is not None:
        return project_standards(
            workspace,
            packs=config.packs,
            overrides=config.overrides_path,
            disabled=config.disabled or (),
            technologies=technologies,
        )
    return project_standards(workspace, packs=None, overrides=None, technologies=technologies)


def review_setup(resolved: ResolvedConfiguration, workspace: Path | None = None) -> ReviewSetup:
    workspace = workspace or resolved.workspace_root
    settings = panel_settings(resolved)
    standards = _standards(resolved, workspace)
    tools = configured_tools(
        standards, workspace, [item.validator_id for item in resolved.effective_validators]
    )
    layers: list[Rule] = [*builtin_rules()]
    if standards is not None:
        layers.extend(pack_rules(standards.packs))
    layers.extend(project_rules(workspace))
    catalog = merge(
        layers,
        available_tools=tools,
        reviewer_domains=reviewer_domains(workspace, settings.reviewers),
        workspace=workspace,
    )
    catalog = _known_checks(catalog)
    if resolved.effective_policies.get("repositoryContentTrusted") is True:
        catalog = _trusted_content(catalog)
    reviewers = load_reviewers(workspace, catalog, enabled=settings.reviewers)
    return ReviewSetup(settings, catalog, reviewers, standards, frozenset(tools))


def _known_checks(catalog: Catalog) -> Catalog:
    """A rule naming a harness check that does not exist is inactive, never silently passed."""
    active: list[Rule] = []
    inactive = list(catalog.inactive)
    for rule in catalog.rules:
        unknown = [name for tool, name in rule.tools if tool == HARNESS_TOOL and name not in CHECKS]
        if unknown:
            inactive.append(InactiveRule(rule, f"unknown harness check {unknown[0]}"))
        else:
            active.append(rule)
    return Catalog(tuple(active), tuple(sorted(inactive, key=lambda item: item.rule.rule_id)))


def _trusted_content(catalog: Catalog) -> Catalog:
    """Under ``policies.repositoryContentTrusted: true`` (#5) instructions in changed content
    are not flagged."""
    active: list[Rule] = []
    inactive = list(catalog.inactive)
    for rule in catalog.rules:
        if (HARNESS_TOOL, "embedded-instructions") in rule.tools:
            inactive.append(
                InactiveRule(
                    rule, "repository content is trusted (policies.repositoryContentTrusted)"
                )
            )
        else:
            active.append(rule)
    return Catalog(tuple(active), tuple(sorted(inactive, key=lambda item: item.rule.rule_id)))


def panel_context(
    resolved: ResolvedConfiguration, workspace: Path
) -> tuple[dict[str, Any], str | None]:
    """What ``governance.applyRepositoryPolicies`` adds to every reviewer request (#5): the
    names of the instruction files as untrusted context and the prompt-injection notice."""
    from governed_harness.capabilities.repository import (
        UNTRUSTED_NOTICE,
        repository_policies,
        untrusted_context,
    )

    policies = repository_policies(resolved)
    if not policies.untrusted:
        return {}, None
    context = untrusted_context(workspace, policies.instruction_files, include_content=False)
    return {"untrustedContent": context}, UNTRUSTED_NOTICE


def reviewer_route(resolved: ResolvedConfiguration) -> Route:
    """The model and effort of a reviewer on a provider: the reviewer's own ``models`` entry
    (provider id or family) and ``effort``, else the routing table (``reviewers``, ``review``)
    under ``agentRouting: tiered``, else the provider's default."""
    project = resolved.project
    policy = project.agent_routing

    def route(reviewer: Reviewer, provider_id: str) -> tuple[str | None, str | None]:
        configured = project.agent_providers.get(provider_id)
        command = (*(configured.command or ()), configured.kind) if configured else ()
        family = provider_family(provider_id, command, policy.families if policy else None)
        model = reviewer.model_for(provider_id, family)
        effort = reviewer.spec.effort
        if model is None:
            decision = select_reviewer(reviewer.reviewer_id, policy, family=family)
            model = decision.model
            effort = effort or decision.effort
        return model, effort

    return route


def consistency_runner(
    resolved: ResolvedConfiguration,
    workspace: Path,
    runner: SafeProcessRunner,
    *,
    execution_id: str,
    cancelled: Callable[[], bool] = lambda: False,
) -> Callable[[], list[dict[str, Any]]] | None:
    """The project's consistency checks (``review.panel.consistencyChecks``), run before any
    reviewer with the project's capability grants; ``None`` without any."""
    checks = panel_settings(resolved).consistency_checks or ()
    if not checks:
        return None

    def run() -> list[dict[str, Any]]:
        grants = grants_from_rules(execution_id, CONSISTENCY_ACTOR, resolved.effective_capabilities)
        results: list[dict[str, Any]] = []
        for check in checks:
            try:
                outcome = runner.run(
                    CommandSpec(
                        argv=check.command,
                        cwd=workspace,
                        timeout_seconds=float(check.timeout_seconds or 300),
                        max_output_bytes=resolved.project.runtime.max_output_bytes,
                    ),
                    actor=CONSISTENCY_ACTOR,
                    grants=grants,
                    cancellation=CancellationToken(cancelled),
                )
            except (PermissionError, OSError, ValueError) as error:
                results.append({"id": check.check_id, "status": "BLOCKED", "summary": f"{error}"})
                continue
            tail = (outcome.stdout + outcome.stderr).decode("utf-8", "replace")[-_OUTPUT_TAIL:]
            results.append(
                {
                    "id": check.check_id,
                    "status": "PASSED" if outcome.status is ResultStatus.PASSED else "FAILED",
                    "exitCode": outcome.exit_code,
                    "command": list(check.command),
                    "summary": " ".join(tail.split())[-500:],
                }
            )
        return results

    return run


def linter_runner(
    resolved: ResolvedConfiguration,
    setup: ReviewSetup,
    workspace: Path,
    runner: SafeProcessRunner,
    *,
    execution_id: str,
) -> Callable[[list[Rule], Locations], list[ReviewFinding]] | None:
    """Outside a governed run (``review.panel.runTools``): run each pack tool the repository
    configures that verifies an active rule, once, and keep the diagnostics of those rules on
    reportable lines."""
    standards = setup.standards
    if not setup.settings.run_tools or standards is None:
        return None

    def run(rules: list[Rule], locations: Locations) -> list[ReviewFinding]:
        wanted = _wanted_rules(rules, setup.tools)
        if not wanted or not locations.lines:
            return []
        grants = grants_from_rules(execution_id, TOOLS_ACTOR, resolved.effective_capabilities)
        limit = resolved.project.runtime.max_output_bytes
        findings: list[ReviewFinding] = []
        for pack_tool in (tool for pack in standards.packs for tool in pack.tools):
            if pack_tool.tool_id not in wanted or not pack_tool.command:
                continue
            try:
                outcome = runner.run(
                    CommandSpec(
                        argv=pack_tool.command,
                        cwd=workspace,
                        timeout_seconds=600.0,
                        max_output_bytes=limit,
                    ),
                    actor=TOOLS_ACTOR,
                    grants=grants,
                )
            except (PermissionError, OSError, ValueError):
                continue
            findings.extend(
                _tool_findings(pack_tool, wanted[pack_tool.tool_id], outcome, workspace, locations)
            )
            del wanted[pack_tool.tool_id]
        return findings

    return run


def _wanted_rules(rules: list[Rule], tools: frozenset[str]) -> dict[str, dict[str, Rule]]:
    """``tool -> tool rule name -> rule`` for the active rules a configured tool verifies."""
    wanted: dict[str, dict[str, Rule]] = {}
    for rule in rules:
        for tool, name in rule.tools:
            if tool in tools and name:
                wanted.setdefault(tool, {})[name] = rule
    return wanted


def _tool_findings(
    pack_tool: PackTool,
    rules: dict[str, Rule],
    outcome: ProcessResult,
    workspace: Path,
    locations: Locations,
) -> list[ReviewFinding]:
    """The diagnostics of one tool run that belong to a wanted rule, on a reportable line."""
    from governed_harness.validators.parsers import parse_output

    issues = parse_output(
        outcome.stdout.decode("utf-8", "replace"),
        outcome.stderr.decode("utf-8", "replace"),
        workspace,
        parser=pack_tool.parser or "auto",
    )
    findings: list[ReviewFinding] = []
    for issue in issues:
        matched = rules.get(issue.rule)
        if matched is None or not issue.path or not issue.line:
            continue
        if not locations.allows(issue.path, "new", issue.line):
            continue
        findings.append(
            ReviewFinding(
                reviewer=pack_tool.tool_id,
                file=issue.path,
                side="new",
                line=issue.line,
                rule=matched.rule_id,
                severity="error" if matched.blocking else "suggestion",
                issue=issue.message,
                priority=matched.priority,
                source=f"tool:{pack_tool.tool_id}",
            )
        )
    return findings


__all__ = [
    "ReviewSetup",
    "consistency_runner",
    "linter_runner",
    "panel_context",
    "panel_settings",
    "review_setup",
    "reviewer_route",
]
