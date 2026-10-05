"""``harness pr publish``, ``harness pr create`` and ``harness pr status`` on any forge (#56).

The application layer resolves the forge (CLI options, ``delivery.forge``, the ``origin``
remote), builds the decision brief of the run and calls the provider. Nothing is decided on the
forge: a comment, a report and a commit status inform the reviewers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from governed_harness.configuration.engineering import ForgeConfig
from governed_harness.delivery.publisher import render_brief_markdown
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import Execution, Finding, GateEvaluation, Task
from governed_harness.forges import (
    ResolvedForge,
    Transport,
    open_forge,
    pull_request_body,
    read_template,
    render_code_quality,
    resolve_forge,
    status_for,
)
from governed_harness.reporting import TraceReporter

from .exceptions import brief_exceptions
from .review import build_brief

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices


def forge_settings(services: EngineServices) -> ForgeConfig:
    return services.resolved.project.delivery_settings.forge or ForgeConfig()


def _resolved(
    services: EngineServices,
    *,
    kind: str | None,
    repository: str | None,
    transport: str | None,
) -> ResolvedForge:
    return resolve_forge(
        services.paths.workspace,
        forge_settings(services),
        kind=kind,
        repository=repository,
        transport=transport,
    )


def _brief(services: EngineServices, run_id: str) -> dict[str, Any]:
    execution = services.state.get("execution", run_id, Execution)
    return build_brief(services, run_id, exceptions=brief_exceptions(services, execution))


def publish_on_forge(
    services: EngineServices,
    run_id: str,
    *,
    pull_request: int,
    kind: str | None = None,
    repository: str | None = None,
    transport: str | None = None,
    reports: bool | None = None,
    commit_sha: str | None = None,
    transport_override: Transport | None = None,
) -> dict[str, Any]:
    """The decision brief as a comment and the findings as the forge's quality report."""
    resolved = _resolved(services, kind=kind, repository=repository, transport=transport)
    settings = forge_settings(services)
    forge = open_forge(resolved, transport_override)
    comment = forge.upsert_comment(
        pull_request, run_id, render_brief_markdown(_brief(services, run_id))
    )
    findings = services.state.list("finding", Finding, execution_id=run_id)
    send = reports if reports is not None else True
    upload = (
        forge.upload_reports(
            pull_request,
            sarif=TraceReporter().render_sarif(findings),
            code_quality=render_code_quality(findings)
            if settings.code_quality is not False
            else None,
            commit_sha=commit_sha,
        )
        if send
        else {"status": "SKIPPED", "reason": "report upload disabled"}
    )
    return {
        "publisher": resolved.location.kind,
        "forge": resolved.as_dict(),
        "repository": resolved.location.repository,
        "pullRequest": pull_request,
        "runId": run_id,
        "comment": comment,
        "reports": upload,
    }


def create_on_forge(
    services: EngineServices,
    run_id: str,
    *,
    head: str | None = None,
    base: str | None = None,
    title: str | None = None,
    labels: tuple[str, ...] = (),
    draft: bool | None = None,
    kind: str | None = None,
    repository: str | None = None,
    transport: str | None = None,
    transport_override: Transport | None = None,
) -> dict[str, Any]:
    """A pull or merge request from the run's closure branch, with the template and the
    brief as its description and the configured labels."""
    execution = services.state.get("execution", run_id, Execution)
    task = services.state.get("task", execution.task_id, Task)
    project = services.resolved.project
    settings = forge_settings(services)
    delivery = project.delivery_settings
    branch = head or delivery.branch_template.replace("{runId}", run_id).replace(
        "{taskId}", execution.task_id
    )
    target = base or settings.base_branch
    if not target:
        raise ConfigurationError("no base branch: pass --base or set delivery.forge.baseBranch")
    resolved = _resolved(services, kind=kind, repository=repository, transport=transport)
    template, template_source = read_template(
        services.paths.workspace, resolved.location.kind, settings.template
    )
    body = pull_request_body(render_brief_markdown(_brief(services, run_id)), template)
    forge = open_forge(resolved, transport_override)
    all_labels = tuple(dict.fromkeys((*(settings.labels or ()), *labels)))
    created = forge.create_pull_request(
        head=branch,
        base=target,
        title=title or task.title,
        body=body,
        labels=all_labels,
        draft=bool(draft if draft is not None else settings.draft),
    )
    services.events.append(
        run_id,
        "delivery.pull-request.created",
        {
            "forge": resolved.location.kind,
            "repository": resolved.location.repository,
            "head": branch,
            "base": target,
            "number": created.get("number"),
            "url": created.get("url"),
            "host": resolved.location.host,
            "labels": list(all_labels),
            "template": template_source,
        },
    )
    return {
        "forge": resolved.as_dict(),
        "head": branch,
        "base": target,
        "labels": list(all_labels),
        "template": template_source,
        "pullRequest": created,
    }


def status_on_forge(
    services: EngineServices,
    run_id: str,
    *,
    commit_sha: str,
    target_url: str | None = None,
    kind: str | None = None,
    repository: str | None = None,
    transport: str | None = None,
    transport_override: Transport | None = None,
) -> dict[str, Any]:
    """A commit status from the run: success once closed, pending while a person decides,
    failure otherwise."""
    execution = services.state.get("execution", run_id, Execution)
    gate = (
        services.state.get("gate", execution.gate_evaluation_id, GateEvaluation)
        if execution.gate_evaluation_id
        else None
    )
    state = status_for(
        execution.status.value,
        execution.current_phase.value,
        gate.status.value if gate else None,
    )
    description = {
        "success": f"Run {run_id} approved and closed",
        "pending": f"Run {run_id} waits for a human decision",
        "failure": f"Run {run_id}: {execution.status.value} in {execution.current_phase.value}",
    }[state]
    resolved = _resolved(services, kind=kind, repository=repository, transport=transport)
    result = open_forge(resolved, transport_override).set_status(
        commit_sha=commit_sha, state=state, description=description, target_url=target_url
    )
    return {"forge": resolved.as_dict(), "commitSha": commit_sha, "state": state, **result}


def forge_report(services: EngineServices) -> dict[str, Any]:
    """The forge the workspace resolves to (no network)."""
    settings = forge_settings(services)
    try:
        resolved = resolve_forge(services.paths.workspace, settings)
    except ConfigurationError as error:
        return {"status": "UNKNOWN", "reason": str(error)}
    return {"status": "DETECTED", **resolved.as_dict()}


__all__ = [
    "create_on_forge",
    "forge_report",
    "forge_settings",
    "publish_on_forge",
    "status_on_forge",
]
