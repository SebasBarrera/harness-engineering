"""``harness metrics`` and the metrics tab of the dashboard (#58, items 8 to 12)."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - the narrative command is the person's configured CLI
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governed_harness.configuration import ConfigurationResolver
from governed_harness.configuration.friction import ModelPrice, NarrativeConfig
from governed_harness.domain.errors import ConfigurationError, HarnessError
from governed_harness.events.sqlite_store import SQLiteEventStore
from governed_harness.forges import origin_url, parse_remote
from governed_harness.metrics import (
    Filters,
    ProjectSource,
    build_report,
    collect,
    load_prices,
    render,
)
from governed_harness.storage.sqlite import SQLiteStateStore

NARRATIVE_PROMPT = (
    "You summarise the activity of a software team's governed agent runs for a period. Read "
    "the JSON metrics below (computed deterministically by the harness) and write a short "
    "narrative in plain prose: what was delivered, what it cost (say which costs are "
    "estimated), where people waited and where the friction was over target, and the quality "
    "signals. Use only the numbers given; do not invent figures. No per-person judgement.\n\n"
)
MAX_NARRATIVE_CHARS = 20_000


def _forge(workspace: Path) -> dict[str, str] | None:
    try:
        url = origin_url(workspace)
        location = parse_remote(url) if url else None
    except Exception:  # noqa: BLE001 - a missing remote only means no links
        return None
    if location is None:
        return None
    return {"kind": location.kind, "host": location.host, "repository": location.repository}


def _sources(path: Path, all_repos: bool) -> tuple[list[ProjectSource], dict[str, Any]]:
    """The current project (with its configuration) and, with ``all_repos``, every project of
    the run registry (``runtime.stateDir``)."""
    from governed_harness.orchestration.engine import EnginePaths
    from governed_harness.runtime.state_location import registered_projects

    sources: list[ProjectSource] = []
    resolved = ConfigurationResolver().resolve(path)
    project = resolved.project
    paths = EnginePaths.for_project(resolved)
    friction = project.friction
    current = ProjectSource(
        project_id=project.project_id,
        workspace=str(resolved.workspace_root),
        state=SQLiteStateStore(paths.database),
        events=SQLiteEventStore(paths.database),
        targets=friction.targets if friction else None,
        forge=_forge(resolved.workspace_root),
    )
    sources.append(current)
    if all_repos:
        seen = {str(paths.database.resolve())}
        for entry in registered_projects():
            database = Path(entry["database"])
            if not database.is_file() or str(database.resolve()) in seen:
                continue
            seen.add(str(database.resolve()))
            workspace = Path(str(entry.get("workspace") or ""))
            sources.append(
                ProjectSource(
                    project_id=str(entry["projectId"]),
                    workspace=str(workspace),
                    state=SQLiteStateStore(database),
                    events=SQLiteEventStore(database),
                    forge=_forge(workspace) if workspace.is_dir() else None,
                )
            )
    settings: dict[str, Any] = {
        "prices": dict(project.metrics.prices or {}) if project.metrics else {},
        "narrative": project.metrics.narrative if project.metrics else None,
        "targets": friction.targets if friction else None,
        "harnessDir": paths.harness_dir,
    }
    return sources, settings


def metrics_report(
    path: Path,
    *,
    filters: Filters,
    prices_file: Path | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The report and the settings it was computed with (zero model calls)."""
    sources, settings = _sources(path, filters.all_repos)
    try:
        prices: dict[str, ModelPrice] = dict(settings["prices"])
        if prices_file is not None:
            prices.update(load_prices(prices_file))
        runs = collect(sources, filters)
        projects = [
            {
                "projectId": source.project_id,
                "workspace": source.workspace,
                "runs": sum(1 for run in runs if run.project_id == source.project_id),
            }
            for source in sources
        ]
        report = build_report(
            runs,
            filters=filters,
            prices=prices,
            targets=settings["targets"],
            projects=projects,
            now=now,
        )
        report["prices"] = {
            key: value.model_dump(mode="json", by_alias=True)
            for key, value in sorted(prices.items())
        }
        return report, settings
    finally:
        for source in sources:
            source.state.close()
            source.events.close()


def run_narrative(
    report: dict[str, Any], narrative: NarrativeConfig, workspace: Path
) -> dict[str, Any]:
    """The optional narrative summary: one call, on demand, to the configured command, which
    reads the prompt and the metrics on standard input and prints the summary."""
    compact = {key: report[key] for key in ("totals", "quality", "friction", "models", "trends")}
    payload = NARRATIVE_PROMPT + json.dumps(compact, ensure_ascii=False)
    started = datetime.now(UTC)
    try:
        result = subprocess.run(  # nosec B603 - argv from project.yaml, no shell
            list(narrative.command),
            input=payload.encode("utf-8"),
            capture_output=True,
            cwd=workspace,
            timeout=narrative.timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"the narrative command did not run: {error}") from error
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip()[-500:]
        raise HarnessError(f"the narrative command exited with {result.returncode}: {tail}")
    text = result.stdout.decode("utf-8", "replace").strip()[:MAX_NARRATIVE_CHARS]
    if not text:
        raise HarnessError("the narrative command printed nothing")
    return {
        "command": " ".join(narrative.command),
        "generatedAt": started.isoformat(),
        "text": text,
        "promptCharacters": len(payload),
    }


def write_reports(
    report: dict[str, Any], directory: Path, formats: tuple[str, ...] = ("json", "md", "html")
) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    names = {
        "json": "metrics.json",
        "md": "metrics.md",
        "html": "metrics.html",
        "csv": "metrics-runs.csv",
        "prometheus": "metrics.prom",
    }
    written: dict[str, str] = {}
    for fmt in formats:
        target = directory / names[fmt]
        target.write_text(render(report, fmt), encoding="utf-8")
        written[fmt] = str(target)
    return written


def narrative_settings(settings: dict[str, Any]) -> NarrativeConfig:
    narrative = settings.get("narrative")
    if not isinstance(narrative, NarrativeConfig):
        raise ConfigurationError(
            "--narrative needs metrics.narrative.command in project.yaml (a command that reads "
            "the prompt on standard input and prints the summary)"
        )
    return narrative


__all__ = ["metrics_report", "narrative_settings", "run_narrative", "write_reports"]
