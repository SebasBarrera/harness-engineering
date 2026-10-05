from __future__ import annotations

import json
import sys
import weakref
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer

from governed_harness import __version__
from governed_harness.application import HarnessApplication
from governed_harness.application.exceptions import ExceptionOptions
from governed_harness.application.hints import default_hint
from governed_harness.cli.render import render_human, wants_json
from governed_harness.domain.enums import (
    DecisionKind,
    MemoryLevel,
    RecommendationDecision,
    ResultStatus,
)
from governed_harness.domain.errors import ConfigurationError, HarnessError

app = typer.Typer(no_args_is_help=True, help="Governed Agent Harness CLI")
config_app = typer.Typer(help="Configuration commands")
task_app = typer.Typer(help="Task commands")
run_app = typer.Typer(help="Execution lifecycle commands")
gate_app = typer.Typer(help="Human gate commands")
evidence_app = typer.Typer(help="Evidence commands")
findings_app = typer.Typer(help="Finding commands")
memory_app = typer.Typer(help="Governed memory commands")
recommendation_app = typer.Typer(help="Retrospective recommendation commands")
plugins_app = typer.Typer(help="Plugin and extension commands")
benchmark_app = typer.Typer(help="Benchmark commands")
api_app = typer.Typer(help="Local API and web dashboard")
artifact_app = typer.Typer(help="Artifact store commands")
exceptions_app = typer.Typer(help="Exception ledger commands")
rules_app = typer.Typer(help="Rule and validator health across runs")
outcome_app = typer.Typer(help="Outcomes after a run (incidents, reverts, hotfixes)")
plan_app = typer.Typer(help="Decomposition of large tasks into governed sub-tasks")
acceptance_app = typer.Typer(help="Independent, frozen acceptance tests")
budget_app = typer.Typer(help="Governed budget of agent calls")
routing_app = typer.Typer(help="Model and effort routing of agent calls")
verification_app = typer.Typer(
    help="Verification ladder: plan, preflight, certification and deferred items"
)
pr_app = typer.Typer(
    help="Pull and merge requests on GitHub, GitLab, Bitbucket, Azure DevOps, Gitea"
)
standards_app = typer.Typer(help="Language standards packs: cards and tools")
architecture_app = typer.Typer(help="Architecture: survey, options, ADR and layer rules")
project_app = typer.Typer(help="What the harness detects about the project")
mcp_app = typer.Typer(help="Embedded mode: the harness as an MCP server for an agent session")
app.add_typer(config_app, name="config")
app.add_typer(task_app, name="task")
app.add_typer(run_app, name="run")
app.add_typer(gate_app, name="gate")
app.add_typer(evidence_app, name="evidence")
app.add_typer(findings_app, name="findings")
app.add_typer(memory_app, name="memory")
app.add_typer(recommendation_app, name="recommendation")
app.add_typer(plugins_app, name="plugins")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(api_app, name="api")
app.add_typer(artifact_app, name="artifact")
app.add_typer(exceptions_app, name="exceptions")
app.add_typer(rules_app, name="rules")
app.add_typer(outcome_app, name="outcome")
app.add_typer(plan_app, name="plan")
app.add_typer(acceptance_app, name="acceptance")
app.add_typer(budget_app, name="budget")
app.add_typer(routing_app, name="routing")
app.add_typer(pr_app, name="pr")
app.add_typer(verification_app, name="verification")
app.add_typer(standards_app, name="standards")
app.add_typer(architecture_app, name="architecture")
app.add_typer(project_app, name="project")
app.add_typer(mcp_app, name="mcp")


ACTOR_HELP = (
    "Identifier of the person acting (recorded, not authenticated). Defaults to the Git user "
    "under governance.deciderIdentity: git, otherwise human.local. Ids of agents, validators "
    "and the harness (agent.*, validator.*, harness.*) are refused with exit code 5."
)


JSON_OPTION: Any = typer.Option(
    None,
    "--json/--no-json",
    help="Print JSON, or readable text with --no-json (default: JSON unless standard output "
    "is a terminal)",
)

RUN_OPTION: Any = typer.Option(
    ...,
    "--run",
    help="Run (execution) identifier, `latest` or a unique prefix such as run_1a2b",
)
RUN_OPTION_LATEST: Any = typer.Option(
    "latest",
    "--run",
    help="Run (execution) identifier, `latest` or a unique prefix such as run_1a2b",
)

_OUTPUT: dict[str, bool | None] = {"json": None}
"""The global --json/--no-json choice of the current invocation (None: decide by terminal)."""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"harness {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the harness version and exit",
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Governed Agent Harness CLI. Output is JSON when standard output is not a terminal (or
    with --json) and readable text on a terminal (or with --no-json)."""
    _OUTPUT["json"] = json_output


def _plain(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [
            item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item
            for item in value
        ]
    return value


def _json_wanted(json_output: bool | None = None) -> bool:
    return wants_json(json_output if json_output is not None else _OUTPUT["json"])


def _emit(value: object, json_output: bool | None = None, kind: str | None = None) -> None:
    value = _plain(value)
    if _json_wanted(json_output):
        typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    else:
        typer.echo(render_human(value, kind))


_ACTING: weakref.WeakSet[HarnessApplication] = weakref.WeakSet()


def _acting() -> HarnessApplication:
    """The application for a human act; its notices (for example a Git identity that could
    not be used) are printed on standard error by ``_call``."""
    application = HarnessApplication()
    _ACTING.add(application)
    return application


def _flush_notices() -> None:
    # One application may serve several calls (gate decide resolves the run first), so its
    # notices are printed and cleared, and it stays registered while it is in use.
    for application in list(_ACTING):
        for notice in application.notices:
            typer.echo(f"warning: {notice}", err=True)
        application.notices.clear()


Hint = Callable[[BaseException], str | None]


def _report_error(payload: dict[str, Any]) -> None:
    if wants_json(_OUTPUT["json"], sys.stderr):
        typer.echo(json.dumps(payload, indent=2), err=True)
        return
    typer.echo(f"Error: {payload['error']}", err=True)
    if payload.get("hint"):
        typer.echo(f"Hint: {payload['hint']}", err=True)


def _call[T](operation: Callable[[], T], hint: Hint | None = None) -> T:
    try:
        return operation()
    except HarnessError as error:
        payload: dict[str, Any] = {"status": "ERROR", "error": str(error)}
        advice = (hint(error) if hint else None) or default_hint(error)
        if advice:
            payload["hint"] = advice
        _report_error(payload)
        raise typer.Exit(code=error.exit_code) from error
    except Exception as error:
        _report_error({"status": "ERROR", "errorType": type(error).__name__, "error": str(error)})
        raise typer.Exit(code=1) from error
    finally:
        _flush_notices()


def _exit_for_execution(status: ResultStatus, phase: str) -> None:
    if status is ResultStatus.PASSED:
        return
    if status is ResultStatus.CANCELLED:
        raise typer.Exit(code=130)
    if status is ResultStatus.BLOCKED and phase == "DECISION":
        raise typer.Exit(code=4)
    if status in {
        ResultStatus.BLOCKED,
        ResultStatus.FAILED,
        ResultStatus.INCONCLUSIVE,
        ResultStatus.TIMED_OUT,
    }:
        raise typer.Exit(code=6)
    if status is ResultStatus.ERROR:
        raise typer.Exit(code=1)
    if status is ResultStatus.INTERRUPTED:
        raise typer.Exit(code=6)


@app.command()
def init(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing project configuration"),
    gitignore: bool = typer.Option(
        True,
        "--gitignore/--no-gitignore",
        help="Add .harness/ to the project's .gitignore (state, artifacts and copies of the code "
        "live there)",
    ),
    example_task: bool = typer.Option(
        True,
        "--example-task/--no-example-task",
        help="Write an example task to .harness/task.example.yaml",
    ),
    agent_skills: bool = typer.Option(
        False,
        "--agent-skills",
        help="Also write the skill of the governed flow for Claude Code "
        "(.claude/skills/harness/SKILL.md) and Codex (.codex/skills/harness/SKILL.md)",
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Create .harness/project.yaml for a repository, using the detected technology profiles,
    add .harness/ to .gitignore and write an example task. Prints the detected profiles and the
    next commands."""
    _emit(
        _call(
            lambda: HarnessApplication().init(
                path,
                force=force,
                gitignore=gitignore,
                example_task=example_task,
                agent_skills=agent_skills,
            )
        ),
        json_output,
        kind="init",
    )


@app.command()
def inspect(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Detect the technology profiles of a repository (read-only), with confidence and
    evidence."""
    _emit(_call(lambda: HarnessApplication().inspect(path)), json_output)


@app.command()
def doctor(
    path: Path | None = typer.Option(
        None, "--path", help="Also validate the project in this directory"
    ),
    install_hooks: bool = typer.Option(
        False,
        "--install-hooks",
        help="Run the hook installation command the project declares "
        "(environment.gitHooks.install) before the checks",
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Check the local environment (Python and Git required; Node.js and npm reported as
    NOT_APPLICABLE when absent; Git identity reported) and, with --path, the project: its
    configuration, the agent CLI of the configured provider on PATH, the agent sandbox mechanism,
    the validators, the baseline commit and whether .harness/ is ignored by Git. Each check that
    is not PASSED says how to fix it. Exit code 2 when a required check FAILED; a WARNING does
    not fail."""
    result = _call(lambda: HarnessApplication().doctor(path, install_hooks=install_hooks))
    _emit(result, json_output, kind="doctor")
    if result["status"] != "PASSED":
        raise typer.Exit(code=2)


@config_app.command("validate")
def config_validate(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Validate the project configuration and print the resolved profiles, workflow,
    validators, capabilities and policies, the settings that are declared but not applied
    (`declarative`) and a warning for each one the project relies on (`warnings`)."""
    _emit(_call(lambda: HarnessApplication().validate_config(path)), json_output)


@config_app.command("lint")
def config_lint(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Report contradictions between the harness configuration and the agent instruction files
    (AGENTS.md, CLAUDE.md, .cursorrules, .cursor/rules, .github/copilot-instructions.md): tool
    versions, coverage thresholds, statements about the tests and instructions to bypass a
    control (--no-verify, a forced push, git add -A). Each conflict names the source that wins by
    `instructions.precedence`. Exit 6 when there is any issue."""
    result = _call(lambda: HarnessApplication().config_lint(path))
    _emit(result, json_output)
    if result["status"] != "PASSED":
        raise typer.Exit(code=6)


@task_app.command("create")
def task_create(
    file: Path = typer.Option(
        ..., "--file", exists=True, dir_okay=False, help="Task file (YAML or JSON)"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Validate a task file (YAML or JSON) and persist it as a versioned task. A task needs
    at least one acceptance criterion (exit code 2 otherwise), except under
    `intake.criteriaPolicy: enforce`: there a task without criteria is stored with
    `criteriaPending: true` and INTENT asks for its criteria (rule C0). Under
    `governance.pinTaskRevision: true` a task id that has an open run is refused with exit code 5:
    revise it with `task clarify` during INTENT or use a new id."""
    _emit(_call(lambda: HarnessApplication().create_task(path, file)), json_output)


@task_app.command("list")
def task_list(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """List the tasks persisted in the project."""
    _emit(_call(lambda: HarnessApplication().list_tasks(path)), json_output, kind="tasks")


@task_app.command("show")
def task_show(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show one persisted task."""
    _emit(_call(lambda: HarnessApplication().get_task(path, task)))


@task_app.command("questions")
def task_questions(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the clarification questions INTENT asked about a task: the open request (asked
    about the current revision of the task), every earlier request and the recorded answers."""
    _emit(_call(lambda: HarnessApplication().list_clarifications(path, task)))


@task_app.command("clarify")
def task_clarify(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    file: Path = typer.Option(
        ...,
        "--file",
        exists=True,
        dir_okay=False,
        help="Answers file (YAML or JSON): answers by question id, optional criteria and "
        "requirement changes",
    ),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Answer the open clarification questions of a task and store the revised task. Run
    `harness run continue` afterwards to assess the revision in INTENT. An unknown question id
    or an empty answer exits with 2, no open request with 3, and a task with a run past INTENT
    or an actor id of an agent, validator or the harness with 5."""
    _emit(
        _call(lambda: _acting().clarify_task(path, task_id=task, answers_file=file, actor_id=actor))
    )


@task_app.command("confirm")
def task_confirm(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    digest: str = typer.Option(
        ..., "--digest", help="Digest of the operational contract shown by task questions"
    ),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    pre_approve: bool = typer.Option(
        False,
        "--pre-approve",
        help="Under friction.preAuthorization: also approve the run in advance, applied at "
        "DECISION only if the gate passed, the ChangeSet has no risk factor and the task is size "
        "S; otherwise you are asked as usual",
    ),
    pre_approve_hours: int | None = typer.Option(
        None,
        "--pre-approve-hours",
        min=1,
        help="Validity of the pre-authorised approval in hours (default "
        "friction.preAuthorization.defaultHours, at most maxHours)",
    ),
    rationale: str = typer.Option(
        "", "--rationale", help="Why you approve in advance (recorded with --pre-approve)"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Confirm the operational contract of a task's current revision, bound to its digest
    (`intake.operationalContract`). Under `enforce` INTENT waits for it; then `harness run
    continue`. With --pre-approve the same act records an approval in advance bound to the
    contract digest and the condition gate passed, no risk factor, size S. A stale digest, a
    non-human actor or --pre-approve without `friction.preAuthorization.mode: allow` exits 5."""
    _emit(
        _call(
            lambda: _acting().confirm_contract(
                path,
                task_id=task,
                digest=digest,
                actor_id=actor,
                pre_approve=pre_approve,
                hours=pre_approve_hours,
                rationale=rationale,
            )
        )
    )


@app.command("do")
def do(
    text: str = typer.Argument(..., help="The change to make, in one sentence or more"),
    criterion: list[str] | None = typer.Option(
        None,
        "--criterion",
        help="An acceptance criterion (repeatable; default: the text itself)",
    ),
    owned: list[str] | None = typer.Option(
        None, "--owned", help="A workspace path the change may touch (repeatable)"
    ),
    title: str | None = typer.Option(None, "--title", help="Task title (default: first line)"),
    provider: str | None = typer.Option(
        None, "--provider", help="Agent provider id; defaults to the project agentProvider"
    ),
    pre_approve: bool = typer.Option(
        False,
        "--pre-approve",
        help="Approve in advance (friction.preAuthorization): applied at DECISION only if the "
        "gate passed, the ChangeSet has no risk factor and the task is size S",
    ),
    pre_approve_hours: int | None = typer.Option(
        None, "--pre-approve-hours", min=1, help="Validity of the pre-authorised approval"
    ),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="On a terminal, answer the questions and decide in the same command",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Make a change from TEXT in one command: create the task (the text is its intent and,
    without --criterion, its acceptance criterion), run it and, on a terminal, answer INTENT's
    questions and show the decision brief and ask for the decision. No task file is needed.
    Small, risk-free tasks take the fast lane (`friction.fastLane`). With --pre-approve the
    approval you give now is applied only when the gate passed, there is no risk factor and the
    task is size S. Exit codes as `run start`: 0 closed, 4 a decision is pending, 6 stopped."""
    application = _acting()
    result = _call(
        lambda: application.do(
            path,
            text,
            criteria=tuple(criterion or ()),
            owned_paths=tuple(owned or ()),
            title=title,
            provider=provider,
            pre_approve=pre_approve,
            hours=pre_approve_hours,
            actor_id=actor,
        )
    )
    run_id = result["run"]["executionId"]
    task_id = result["task"]["taskId"]
    terminal = interactive and sys.stdin.isatty() and sys.stdout.isatty()
    while terminal and result["questions"]:
        typer.echo(f"INTENT asks about {task_id}:")
        answers: dict[str, str] = {}
        for question in result["questions"]:
            answer = ""
            while not answer.strip():
                answer = str(typer.prompt(f"{question['questionId']}: {question['text']}"))
            answers[question["questionId"]] = answer.strip()
        _call(partial(application.answer_questions, path, task_id=task_id, answers=answers))
        execution = _call(lambda: application.continue_run(path, run_id))
        result["run"] = {
            "executionId": execution.execution_id,
            "status": execution.status.value,
            "currentPhase": execution.current_phase.value,
            "changeSetDigest": execution.change_set_digest,
        }
        result["execution"] = execution.model_dump(mode="json", by_alias=True)
        result["questions"] = (
            _open_questions(application, path, run_id)
            if execution.current_phase.value == "INTENT"
            else []
        )
    status = ResultStatus(result["run"]["status"])
    phase = result["run"]["currentPhase"]
    if terminal and status is ResultStatus.BLOCKED and phase == "DECISION":
        decision, digest, reason = _interactive_decision(
            application, path, run_id, None, None, None
        )
        checked = _interactive_checklist(application, path, run_id, [])
        record, execution = _call(
            lambda: application.decide_gate(
                path,
                execution_id=run_id,
                decision=decision,
                change_set_digest=digest,
                actor_id=actor,
                rationale=reason,
                checked_items=tuple(checked),
            ),
            hint=_decide_hint(application, path, run_id),
        )
        result["decision"] = record.model_dump(mode="json", by_alias=True)
        result["execution"] = execution.model_dump(mode="json", by_alias=True)
        status, phase = execution.status, execution.current_phase.value
    elif status is ResultStatus.BLOCKED and phase == "DECISION":
        result["next"] = f"harness review --run {run_id}; harness gate decide --run {run_id}"
    elif status is ResultStatus.BLOCKED and phase == "INTENT":
        result["next"] = f"harness task questions --task {task_id}"
    _emit(result, json_output)
    _exit_for_execution(status, phase)


def _open_questions(application: HarnessApplication, path: Path, run_id: str) -> list[Any]:
    status = _call(lambda: application.status(path, run_id))
    task_id = status["execution"]["taskId"]
    request = _call(lambda: application.list_clarifications(path, task_id))["openRequest"]
    if not request or status["execution"]["status"] != "BLOCKED":
        return []
    return [
        {"questionId": item["questionId"], "text": item["text"]}
        for item in request.get("questions") or []
    ]


@run_app.command("start")
def run_start(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    provider: str | None = typer.Option(
        None, "--provider", help="Agent provider id; defaults to the project agentProvider"
    ),
    isolate: str | None = typer.Option(
        None,
        "--isolate",
        help="worktree: run in a Git worktree of its own on a new branch from the updated base "
        "branch; none: in place (default: workspace.isolation.mode)",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Create a run for a task and execute the normative phases until a stop condition. Exit
    code 4 means the automated phases finished and a human decision is pending; 6 means a
    validation, policy or blocking condition stopped the run. With --isolate worktree a branch or
    worktree directory that already exists stops the run (exit 6); nothing is reset or deleted."""
    execution = _call(lambda: HarnessApplication().start_run(path, task, provider, isolate=isolate))
    _emit(execution, json_output, kind="execution")
    _exit_for_execution(execution.status, execution.current_phase.value)


@run_app.command("continue")
def run_continue(
    run: str = RUN_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Resume a run from its persisted state, for example after fixing the environment or after
    REQUEST_CHANGES."""
    execution = _call(lambda: HarnessApplication().continue_run(path, run))
    _emit(execution, json_output, kind="execution")
    _exit_for_execution(execution.status, execution.current_phase.value)


@run_app.command("cancel")
def run_cancel(
    run: str = RUN_OPTION,
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Cancel a run and terminate its active process group. The cancellation is recorded as an
    event."""
    execution = _call(lambda: _acting().cancel_run(path, run, actor))
    _emit(execution, kind="execution")


@run_app.command("cleanup")
def run_cleanup(
    run: str = RUN_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Remove the worktree of a finished isolated run (`run start --isolate worktree`). Only a
    worktree the harness created and without uncommitted changes is removed; its branch is
    kept. Anything else exits 5."""
    _emit(_call(lambda: HarnessApplication().cleanup_run(path, run)))


@verification_app.command("show")
def verification_show(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the verification plan of a run (the rung each criterion requires, the rungs this
    environment reaches and why), its preflight (READY, PARTIAL or UNAVAILABLE), the
    certification of its ChangeSet, the deferred items and the manual checklist."""
    _emit(_call(lambda: HarnessApplication().verification(path, run)))


@verification_app.command("decide")
def verification_decide(
    run: str = RUN_OPTION,
    continue_uncertified: bool = typer.Option(
        False,
        "--continue-uncertified",
        help="Continue without the rungs the preflight found unreachable (required)",
    ),
    rationale: str = typer.Option(..., "--rationale", help="Justification recorded"),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    continue_after: bool = typer.Option(
        True, "--continue/--no-continue", help="Resume the run after recording the decision"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Decide on a run whose preflight is UNAVAILABLE: continue it uncertified for what this
    environment cannot verify (those criteria end WAIVED, never certified; the unavailable probes
    are not run). Recorded on the run with the person and the rationale. A run that does not
    wait on such a preflight, or a non-human actor, exits 5."""
    if not continue_uncertified:
        _report_error(
            {
                "status": "ERROR",
                "error": "--continue-uncertified is required",
                "hint": "Or fix the environment and run harness run continue.",
            }
        )
        raise typer.Exit(code=2)
    result = _call(
        lambda: _acting().decide_verification(
            path,
            execution_id=run,
            rationale=rationale,
            actor_id=actor,
            continue_after=continue_after,
        )
    )
    _emit(result)
    execution = result.get("execution")
    if execution:
        _exit_for_execution(ResultStatus(execution["status"]), execution["currentPhase"])


@plan_app.command("show")
def plan_show(
    run: str = RUN_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the decomposition PLANNING proposed for a run (`planning.decomposition`), its
    digest, its status and the sub-tasks completed so far."""
    _emit(_call(lambda: HarnessApplication().plan(path, run)), kind="plan")


@plan_app.command("decide")
def plan_decide(
    run: str = RUN_OPTION,
    decision: DecisionKind = typer.Option(
        ..., "--decision", case_sensitive=False, help="APPROVE or REJECT"
    ),
    digest: str = typer.Option(
        ..., "--digest", help="Digest of the proposed plan shown by plan show"
    ),
    rationale: str = typer.Option(..., "--rationale", help="Justification recorded"),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    continue_after: bool = typer.Option(
        True, "--continue/--no-continue", help="Resume the run after recording the decision"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Approve or reject the proposed decomposition, bound to its digest. APPROVE runs the
    sub-tasks in order, each with its own verification and gate; REJECT keeps the task whole.
    A stale digest, a non-human actor or a decision other than APPROVE or REJECT exits 5."""
    result = _call(
        lambda: _acting().decide_plan(
            path,
            execution_id=run,
            decision=decision,
            digest=digest,
            rationale=rationale,
            actor_id=actor,
            continue_after=continue_after,
        )
    )
    _emit(result, kind="plan")
    execution = result.get("execution")
    if execution:
        _exit_for_execution(ResultStatus(execution["status"]), execution["currentPhase"])


@routing_app.command("calibrate")
def routing_calibrate(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Report the cost per approved task of every routing decision recorded in the project
    (`agentRouting`), by provider family, call kind, task size, model and effort, and suggest
    the cheapest implement rung per size among groups with at least two approved runs. Nothing
    is applied: a person edits `agentRouting.tables`."""
    _emit(_call(lambda: HarnessApplication().routing_calibration(path)), kind="routing")


@budget_app.command("show")
def budget_show(
    run: str = RUN_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the usage of a run and of its task (reported tokens and cost, measured wall time of
    the agent calls) against the `budget` limits, the limits a person raised and what remains."""
    _emit(_call(lambda: HarnessApplication().budget(path, run)), kind="budget")


@budget_app.command("raise")
def budget_raise(
    run: str = RUN_OPTION,
    scope: str = typer.Option(..., "--scope", help="call, task or run"),
    metric: str = typer.Option(..., "--metric", help="costUsd, tokens or wallSeconds"),
    limit: float = typer.Option(..., "--to", help="The new limit; it must be above the current"),
    rationale: str = typer.Option(..., "--rationale", help="Justification recorded"),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Raise a budget limit of a run blocked with budget.exceeded; the raise is recorded on the
    run's event chain with the person and the rationale, then `harness run continue` resumes the
    run. A non-human actor, a lower limit or a missing rationale exits 5."""
    _emit(
        _call(
            lambda: _acting().raise_budget(
                path,
                execution_id=run,
                scope=scope,
                metric=metric,
                limit=limit,
                rationale=rationale,
                actor_id=actor,
            )
        ),
        kind="budget",
    )


@acceptance_app.command("show")
def acceptance_show(
    run: str = RUN_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the acceptance tests proposed for a run (`verification.acceptanceTests`), their
    digest, their status and, once approved, the frozen files and their digests."""
    _emit(_call(lambda: HarnessApplication().acceptance(path, run)), kind="acceptance")


@acceptance_app.command("decide")
def acceptance_decide(
    run: str = RUN_OPTION,
    decision: DecisionKind = typer.Option(
        ..., "--decision", case_sensitive=False, help="APPROVE or REJECT"
    ),
    digest: str = typer.Option(..., "--digest", help="Digest shown by acceptance show"),
    rationale: str = typer.Option(..., "--rationale", help="Justification recorded"),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    continue_after: bool = typer.Option(
        True, "--continue/--no-continue", help="Resume the run after recording the decision"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Approve or reject the proposed acceptance tests, bound to their digest. APPROVE writes
    and freezes them, runs them once on the workspace before the change and resumes the run;
    every later VERIFICATION checks that they are unchanged and pass. A stale digest or a
    non-human actor exits 5."""
    result = _call(
        lambda: _acting().decide_acceptance(
            path,
            execution_id=run,
            decision=decision,
            digest=digest,
            rationale=rationale,
            actor_id=actor,
            continue_after=continue_after,
        )
    )
    _emit(result, kind="acceptance")
    execution = result.get("execution")
    if execution:
        _exit_for_execution(ResultStatus(execution["status"]), execution["currentPhase"])


@app.command("check")
def check(
    run: str | None = typer.Option(
        None,
        "--run",
        help="Run whose task and baseline the diff checks use (default: the latest run that "
        "wrote a check state)",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Run the validators of the gate and the enabled diff checks on the workspace, without
    recording anything (`runtime.gateContract`). The agent can run it before it finishes; the
    implement request carries the exact command. Exit 0 when every mandatory validator passes
    and no enforced check reports a blocking problem, 6 otherwise."""
    result = _call(lambda: HarnessApplication().check(path, run))
    _emit(result, json_output, kind="check")
    if result["status"] != "PASSED":
        raise typer.Exit(code=6)


@run_app.command("quarantine")
def run_quarantine(
    run: str = RUN_OPTION,
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Keep the changes of a run that stopped without approval as a quarantined patch and
    restore the workspace to its baseline (`governance.stopTheLine`). This releases a line
    blocked by `stopTheLine: block`. A run that is closed or still open for a decision is
    refused (exit 5)."""
    _emit(_call(lambda: _acting().quarantine_run(path, run, actor)), kind="quarantine")


@run_app.command("list")
def run_list(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the runs of the project, newest first."""
    _emit(_call(lambda: HarnessApplication().list_runs(path)), kind="runs")


@app.command()
def status(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Show the full status projection of a run: phases, validations, findings, gate, human
    decision, event-chain check and metrics."""
    _emit(_call(lambda: HarnessApplication().status(path, run)), json_output, kind="status")


@app.command()
def trace(
    run: str = RUN_OPTION_LATEST,
    format: str = typer.Option(
        "markdown", "--format", help="markdown, json, jsonl, sarif or codequality"
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write to this file instead of standard output"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Export the trace of a run as Markdown, JSON, JSONL, SARIF or a GitLab Code Quality
    report (codequality: the findings, with the same fingerprints as SARIF). Under
    `governance.verifyRecords: true` the run is verified first (as `harness verify`) and a run
    that does not verify is not exported (exit code 6)."""
    data = _call(lambda: HarnessApplication().trace(path, run, format))
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        typer.echo(str(output))
    else:
        typer.echo(data.decode("utf-8", "replace"), nl=False)


@app.command()
def verify(
    run: str | None = typer.Option(
        None, "--run", help="Run (execution) identifier; without it, every run of the workspace"
    ),
    bundle: Path | None = typer.Option(
        None,
        "--bundle",
        dir_okay=False,
        help="Verify a portable evidence bundle instead (no workspace needed)",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Verify the record of a run (or of every run): the event chain, the head of the chain
    against its anchor outside .harness (`governance.chainAnchor`), every record that has an
    event against that event, the execution's pointers and every referenced artifact against its
    digest. Prints a report and never repairs anything. With --bundle, verify an evidence bundle
    of `harness export` instead: its entries against the manifest, the event chain, the artifacts
    and the decision records. Exit code 0 when everything verifies, 6 when any check fails, 3 for
    an unknown run or bundle."""
    if bundle is not None:
        if run is not None:
            _report_error({"status": "ERROR", "error": "use --run or --bundle, not both"})
            raise typer.Exit(code=2)
        report = _call(lambda: HarnessApplication.verify_bundle(bundle))
    else:
        report = _call(lambda: HarnessApplication().verify(path, run))
    _emit(report)
    if not report["valid"]:
        raise typer.Exit(code=6)


@app.command()
def export(
    bundle: Path = typer.Option(
        ..., "--bundle", dir_okay=False, help="Write the portable evidence bundle (tar.gz) here"
    ),
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Export a run as a portable evidence bundle: its event chain, records, every artifact they
    reference and a manifest with the digest of each entry. `harness verify --bundle` and
    `harness verify-approval --bundle` check it without the workspace. A run whose event chain
    does not verify is not exported (exit code 5; 6 under `governance.verifyRecords` when the
    record does not verify)."""
    _emit(_call(lambda: HarnessApplication().export_bundle(path, run, bundle)))


@app.command("verify-approval")
def verify_approval(
    base: str = typer.Option(
        ..., "--base", help="Revision the change starts from (the pull request base)"
    ),
    head: str = typer.Option("HEAD", "--head", help="Revision to verify (the merged tree)"),
    bundle: list[Path] | None = typer.Option(
        None, "--bundle", help="Evidence bundle with the approval (repeatable)"
    ),
    workspace: bool = typer.Option(
        True,
        "--workspace/--no-workspace",
        help="Also look for the approval in the workspace's .harness/state.db",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Repository directory"
    ),
) -> None:
    """Check in CI that the range --base..--head is a ChangeSet a person approved: recompute its
    ChangeSet digest from the two revisions and require an unexpired APPROVE or
    APPROVE_EXCEPTION bound to that digest in a bundle (or the workspace record). Any other
    change in the range fails. Exit code 0 when it passes, 5 when no approval matches."""
    report = _call(
        lambda: HarnessApplication.verify_approval(
            path, base=base, head=head, bundles=tuple(bundle or ()), use_workspace=workspace
        )
    )
    _emit(report)
    if report["status"] != "PASSED":
        raise typer.Exit(code=5)


@pr_app.command("publish")
def pr_publish(
    pull_request: int = typer.Option(..., "--pr", min=1, help="Pull request number"),
    run: str = RUN_OPTION_LATEST,
    repository: str | None = typer.Option(
        None, "--repository", help="owner/name (default: delivery.publisher or the origin remote)"
    ),
    transport: str | None = typer.Option(
        None,
        "--transport",
        help="gh (the GitHub CLI) or api (HTTPS with the token in delivery.publisher.tokenEnv, "
        "GITHUB_TOKEN by default)",
    ),
    sarif: bool | None = typer.Option(
        None, "--sarif/--no-sarif", help="Upload the run's findings to code scanning"
    ),
    commit: str | None = typer.Option(
        None, "--commit", help="Commit the SARIF report belongs to (default: the PR head)"
    ),
    forge: str | None = typer.Option(
        None,
        "--forge",
        help="github, gitlab, bitbucket, azure-devops or gitea (default: delivery.forge, else "
        "detected from the origin remote)",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Post the decision brief of a run on a pull or merge request (one comment per run,
    updated when published again) and attach the run's findings: SARIF to GitHub code scanning,
    a Code Insights report on Bitbucket; GitLab and Azure DevOps read the report from CI
    artifacts. GitHub is the default; another forge is detected from the origin remote or set
    with --forge or delivery.forge. Information only: nothing is decided on the forge."""
    _emit(
        _call(
            lambda: HarnessApplication().publish_pull_request(
                path,
                run,
                pull_request=pull_request,
                repository=repository,
                transport=transport,
                sarif=sarif,
                commit_sha=commit,
                forge=forge,
            )
        )
    )


@pr_app.command("create")
def pr_create(
    run: str = RUN_OPTION_LATEST,
    base: str | None = typer.Option(
        None, "--base", help="Target branch (default: delivery.forge.baseBranch)"
    ),
    head: str | None = typer.Option(
        None, "--head", help="Source branch (default: the closure branch of the run)"
    ),
    title: str | None = typer.Option(None, "--title", help="Title (default: the task title)"),
    label: list[str] | None = typer.Option(
        None, "--label", help="Label, added to delivery.forge.labels (repeatable)"
    ),
    draft: bool | None = typer.Option(
        None, "--draft/--ready", help="Open it as a draft (default: delivery.forge.draft)"
    ),
    forge: str | None = typer.Option(
        None, "--forge", help="github, gitlab, bitbucket, azure-devops or gitea"
    ),
    repository: str | None = typer.Option(
        None, "--repository", help="Repository path on the forge (default: the origin remote)"
    ),
    transport: str | None = typer.Option(
        None, "--transport", help="cli (gh or glab) or api (HTTPS, token from the environment)"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Open a pull or merge request from the closure branch of a run (push the branch first),
    with the repository's pull request template and the decision brief as its description and
    the configured labels. The creation is recorded as a delivery.pull-request.created event.
    Nothing is approved on the forge: the approval is the run's digest-bound decision."""
    _emit(
        _call(
            lambda: HarnessApplication().create_pull_request(
                path,
                run,
                head=head,
                base=base,
                title=title,
                labels=tuple(label or ()),
                draft=draft,
                forge=forge,
                repository=repository,
                transport=transport,
            )
        )
    )


@pr_app.command("status")
def pr_status(
    commit: str = typer.Option(..., "--commit", help="Commit whose status is set"),
    run: str = RUN_OPTION_LATEST,
    target_url: str | None = typer.Option(
        None, "--target-url", help="Link shown with the status (for example the CI job)"
    ),
    forge: str | None = typer.Option(
        None, "--forge", help="github, gitlab, bitbucket, azure-devops or gitea"
    ),
    repository: str | None = typer.Option(
        None, "--repository", help="Repository path on the forge (default: the origin remote)"
    ),
    transport: str | None = typer.Option(
        None, "--transport", help="cli (gh or glab) or api (HTTPS, token from the environment)"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Set the commit status governed-harness from a run: success once it closed approved,
    pending while it waits for a human decision, failure otherwise."""
    _emit(
        _call(
            lambda: HarnessApplication().pull_request_status(
                path,
                run,
                commit_sha=commit,
                target_url=target_url,
                forge=forge,
                repository=repository,
                transport=transport,
            )
        )
    )


@pr_app.command("forge")
def pr_forge(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the forge the workspace resolves to (delivery.forge or the origin remote), its API
    URL, transport and the environment variable its token is read from. Calls nothing."""
    _emit(_call(lambda: HarnessApplication().forge(path)))


@standards_app.command("show")
def standards_show(
    lang: str | None = typer.Option(
        None, "--lang", help="One pack (python, typescript, java, ...) with all its cards"
    ),
    file: list[str] | None = typer.Option(
        None,
        "--file",
        help="A workspace path: show the cards an implement call and the review checklist "
        "would get for it (repeatable)",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Show the language standards packs: the detected and effective packs (repository
    overrides applied), their tools and the validators added for the tools the repository
    configures; with --lang every card of one pack; with --file the cards selected for those
    files. Works without a project configuration. Reads only."""
    _emit(
        _call(lambda: HarnessApplication().standards(path, pack=lang, files=tuple(file or ()))),
        json_output,
    )


@architecture_app.command("show")
def architecture_show(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Show the architecture of the project: the configured section, whether the project is
    new or existing, the cached survey or the ADR, the layer rules in force with their
    forbidden imports, and the source layout the survey cache is keyed by."""
    _emit(_call(lambda: HarnessApplication().architecture(path)), json_output)


@architecture_app.command("decide")
def architecture_decide(
    run: str = RUN_OPTION,
    digest: str = typer.Option(..., "--digest", help="Digest shown by the blocked phase"),
    rationale: str = typer.Option(..., "--rationale", help="Justification recorded"),
    decision: DecisionKind | None = typer.Option(
        None,
        "--decision",
        case_sensitive=False,
        help="APPROVE or REJECT the layer rules a survey inferred",
    ),
    option: str | None = typer.Option(
        None, "--option", help="The architecture option chosen for a new project"
    ),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    continue_after: bool = typer.Option(
        True, "--continue/--no-continue", help="Resume the run after recording the decision"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Decide the architecture, bound to the digest of the proposal: approve or reject the
    layer rules a survey inferred (existing project), or choose one of the options of a new
    project, which is recorded as an ADR. The approved rules are enforced as forbidden
    dependencies in VERIFICATION. A stale digest or a non-human actor exits 5."""
    result = _call(
        lambda: _acting().decide_architecture(
            path,
            execution_id=run,
            digest=digest,
            rationale=rationale,
            decision=decision,
            option=option,
            actor_id=actor,
            continue_after=continue_after,
        )
    )
    _emit(result)
    execution = result.get("execution")
    if execution:
        _exit_for_execution(ResultStatus(execution["status"]), execution["currentPhase"])


@architecture_app.command("refresh")
def architecture_refresh(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Mark the cached architecture survey stale: the next run surveys the project again (one
    call). Approved rules stay in force until a new survey is decided."""
    _emit(_call(lambda: HarnessApplication().refresh_architecture(path)))


@mcp_app.command("serve")
def mcp_serve(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Serve the harness to an agent session over stdio (Model Context Protocol, JSON-RPC 2.0,
    one message per line). Tools: project, status, inbox, task create, questions and clarify
    (the person's answers, marked as relayed), run start (provider session by default) and
    continue, check, review and standards. No tool decides for a person: gate, acceptance,
    plan and architecture decisions stay in the terminal. Register it, for example, with
    claude mcp add harness -- harness mcp serve."""
    from governed_harness.embedded.mcp_server import serve_stdio

    raise typer.Exit(code=serve_stdio(path.resolve()))


@project_app.command("show")
def project_show(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool | None = JSON_OPTION,
) -> None:
    """Show what the harness detects about the project, deterministically and without an
    agent call: new or existing (and why), profiles, standards packs, testing strategy,
    architecture status, project setup answers and forge."""
    _emit(_call(lambda: HarnessApplication().project(path)), json_output)


@app.command()
def gc(
    apply: bool = typer.Option(
        False, "--apply", help="Delete what the report lists; without it nothing is deleted"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Apply the retention settings to runs that ended (closed, cancelled or rejected) longer
    ago than them: `retention.artifactDays` deletes the run's artifacts that no kept run uses and
    records a `retention.artifacts.pruned` event; `retention.eventDays` removes the run, its
    events, records and artifacts. Open runs and memory records are never touched. Without
    --apply only the report is printed."""
    _emit(_call(lambda: HarnessApplication().gc(path, apply=apply)))


@evidence_app.command("list")
def evidence_list(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the evidence records of a run with their artifact references and digests."""
    _emit(_call(lambda: HarnessApplication().list_evidence(path, run)))


@evidence_app.command("attach")
def evidence_attach(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False, help="File to attach"),
    run: str | None = typer.Option(None, "--run", help="Run the evidence is about"),
    task: str | None = typer.Option(
        None, "--task", help="Task the attachment is intake context of (with --manual)"
    ),
    item: str | None = typer.Option(
        None, "--item", help="Deferred item (D-CRITERION) to close, or checklist item"
    ),
    manual: bool = typer.Option(
        False,
        "--manual",
        help="A person's attachment (screenshot, video, log) instead of a test or CI report",
    ),
    evidence_format: str = typer.Option(
        "auto", "--format", help="auto, junit, sarif or ci-status (deferred items)"
    ),
    case: str | None = typer.Option(
        None, "--case", help="Only the JUnit test cases whose name contains this text"
    ),
    commit: str | None = typer.Option(
        None, "--commit", help="Commit the evidence is about (checked against the item's)"
    ),
    note: str | None = typer.Option(None, "--note", help="What the attachment shows"),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Attach evidence. With --run and --item D-CRITERION, close a deferred verification
    with a JUnit, SARIF or CI status report (bound to the item's ChangeSet digest and commit;
    an expired item exits 5). With --manual, store a person's attachment bound to the run's
    ChangeSet (and to a checklist item with --item), or to a task revision as intake context
    the agent receives (--task)."""
    _emit(
        _call(
            lambda: _acting().attach_evidence(
                path,
                file=file,
                execution_id=run,
                task_id=task,
                item=item,
                manual=manual,
                evidence_format=evidence_format,
                case=case,
                commit=commit,
                note=note,
                actor_id=actor,
            )
        )
    )


@findings_app.command("list")
def findings_list(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the structured findings of a run with severity, rule and location."""
    _emit(_call(lambda: HarnessApplication().list_findings(path, run)), kind="findings")


def _memory_value(raw: str) -> dict[str, Any]:
    """A JSON object is stored as given; any other text is stored under the key ``text``."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw}
    return parsed if isinstance(parsed, dict) else {"text": raw}


def _memory_deadline(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ConfigurationError(f"--valid-until is not an ISO 8601 date: {raw}") from error
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@memory_app.command("add")
def memory_add(
    level: MemoryLevel = typer.Option(
        ..., "--level", case_sensitive=False, help="Scope of the record"
    ),
    key: str = typer.Option(..., "--key", help="Stable name of the record within its level"),
    value: str = typer.Option(
        ..., "--value", help="JSON object, or plain text stored under the key 'text'"
    ),
    task: str | None = typer.Option(None, "--task", help="Task identifier; required for TASK"),
    run: str | None = typer.Option(None, "--run", help="Run identifier; required for EPHEMERAL"),
    valid_until: str | None = typer.Option(
        None, "--valid-until", help="ISO 8601 instant after which the record no longer applies"
    ),
    supersedes: str | None = typer.Option(
        None, "--supersedes", help="Identifier of the record this one replaces"
    ),
    sensitive: bool = typer.Option(
        False, "--sensitive", help="Withhold the value from the context manifest and the agent"
    ),
    approve: bool = typer.Option(
        False, "--approve", help="Record the entry as approved by the acting person"
    ),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Record a memory entry with its provenance. NORMATIVE, PROJECT and RETROSPECTIVE entries
    enter a context only once approved."""
    _emit(
        _call(
            lambda: _acting().add_memory(
                path,
                level=level,
                key=key,
                value=_memory_value(value),
                actor_id=actor,
                task_id=task,
                execution_id=run,
                valid_until=_memory_deadline(valid_until),
                supersedes=supersedes,
                sensitive=sensitive,
                approved=approve,
            )
        )
    )


@memory_app.command("list")
def memory_list(
    task: str | None = typer.Option(None, "--task", help="Evaluate the status for this task"),
    run: str | None = typer.Option(None, "--run", help="Evaluate the status for this run"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the memory records of the project with their status: active, superseded, expired,
    unapproved, limit or out_of_scope."""
    _emit(_call(lambda: HarnessApplication().list_memory(path, task_id=task, execution_id=run)))


@memory_app.command("manifest")
def memory_manifest(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the context manifest recorded for a run: the records it applied, their digest and
    the candidates it excluded with the reason."""
    _emit(_call(lambda: HarnessApplication().memory_manifest(path, run)))


@memory_app.command("approve")
def memory_approve(
    memory: str = typer.Option(..., "--memory", help="Memory record identifier"),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Approve a proposed record. The approval is a new record that supersedes the proposal;
    approving a record that needs no approval or is already approved exits with code 5."""
    _emit(_call(lambda: _acting().approve_memory(path, memory_id=memory, actor_id=actor)))


@memory_app.command("invalidate")
def memory_invalidate(
    memory: str = typer.Option(..., "--memory", help="Memory record identifier"),
    reason: str = typer.Option(..., "--reason", help="Why the record no longer applies"),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Invalidate a record without deleting it: it stops entering any context and the actor and
    the reason stay on record."""
    _emit(
        _call(
            lambda: _acting().invalidate_memory(
                path, memory_id=memory, actor_id=actor, reason=reason
            )
        )
    )


@gate_app.command("decide")
def gate_decide(
    run: str = RUN_OPTION,
    decision: DecisionKind | None = typer.Option(
        None,
        "--decision",
        case_sensitive=False,
        help="Human decision; asked interactively when omitted on a terminal",
    ),
    change_set_digest: str | None = typer.Option(
        None,
        "--change-set-digest",
        help="Current ChangeSet digest shown by status or review (sha256:...); confirmed "
        "interactively when omitted on a terminal",
    ),
    rationale: str | None = typer.Option(
        None,
        "--rationale",
        help="Justification recorded with the decision; asked interactively when omitted",
    ),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    continue_after: bool = typer.Option(
        True, "--continue/--no-continue", help="Resume the run after recording the decision"
    ),
    expires_in: str | None = typer.Option(
        None,
        "--expires-in",
        help="APPROVE_EXCEPTION under review.exceptions: validity such as 14d, 36h or 2w "
        "(default review.exceptionDays)",
    ),
    expires_at: str | None = typer.Option(
        None, "--expires-at", help="APPROVE_EXCEPTION: ISO 8601 instant the exception expires"
    ),
    scope: list[str] | None = typer.Option(
        None,
        "--scope",
        help="APPROVE_EXCEPTION: findings covered, as rule or rule:path (repeatable; default: "
        "the blocking findings of the gate, by fingerprint)",
    ),
    alternative_evidence: str | None = typer.Option(
        None,
        "--alternative-evidence",
        help="APPROVE_EXCEPTION: evidence that compensates for the blocking findings",
    ),
    follow_up: str | None = typer.Option(
        None, "--follow-up", help="APPROVE_EXCEPTION: issue or task that will remove the exception"
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Show the decision brief and confirm the ChangeSet digest even when every option "
        "is given",
    ),
    acknowledge_risk: list[str] | None = typer.Option(
        None,
        "--acknowledge-risk",
        help="APPROVE or APPROVE_EXCEPTION under verification.riskFactors: a risk factor of the "
        "ChangeSet the decider acknowledges (repeatable; required for every factor whose "
        "action is acknowledge)",
    ),
    check: list[str] | None = typer.Option(
        None,
        "--check",
        help="Under review.manualChecklist: a checklist item the person verified (repeatable); "
        "APPROVE needs every item of the run",
    ),
    change_request: list[str] | None = typer.Option(
        None,
        "--change-request",
        help="REQUEST_CHANGES under review.structuredChanges: a blocking item as "
        "'description::condition' where the condition is test:NODE_ID (a pytest node id), "
        "absent:REGEX or text (repeatable)",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Record a human decision bound to the current ChangeSet digest. A decision for a stale
    digest, or APPROVE over a gate that did not pass, is rejected with exit code 5. On a
    terminal, omitting --decision, --change-set-digest or --rationale shows the decision brief,
    asks for the missing values and asks the person to type the start of the digest the decision
    binds to; a digest that is not confirmed records nothing (exit 5). Without a terminal the
    three options are required (exit 2). Under `governance.confirmDecisionDigest: true` the
    same confirmation is asked on a terminal even when every option is given. An actor id of an
    agent, validator or the harness is refused with exit 5; without --actor the decider is the
    Git user under `governance.deciderIdentity: git` (human.local, with a warning, when Git has
    no identity)."""
    application = _acting()
    run_id = _call(lambda: application.resolve_run(path, run))
    missing = decision is None or change_set_digest is None or rationale is None
    terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if missing and not (interactive or terminal):
        _report_error(
            {
                "status": "ERROR",
                "error": "missing --decision, --change-set-digest or --rationale",
                "hint": "Pass the three options, or run `harness gate decide --run "
                f"{run_id}` on a terminal (or with --interactive) to decide interactively.",
            }
        )
        raise typer.Exit(code=2)
    exception = ExceptionOptions(
        expires_in=expires_in,
        expires_at=expires_at,
        scope=tuple(scope or ()),
        alternative_evidence=alternative_evidence,
        follow_up=follow_up,
    )
    confirm = terminal and _digest_confirmation_configured(path)
    checked = list(check or ())
    if missing or interactive or confirm:
        decision, change_set_digest, rationale = _interactive_decision(
            application, path, run_id, decision, change_set_digest, rationale
        )
        if decision is DecisionKind.APPROVE_EXCEPTION and _exceptions_enabled(path):
            exception = _interactive_exception(exception)
        checked = _interactive_checklist(application, path, run_id, checked)
    assert decision is not None and change_set_digest is not None and rationale is not None
    chosen, digest, reason = decision, change_set_digest, rationale
    record, execution = _call(
        lambda: application.decide_gate(
            path,
            execution_id=run_id,
            decision=chosen,
            change_set_digest=digest,
            actor_id=actor,
            rationale=reason,
            continue_after=continue_after,
            exception=exception,
            acknowledged_risks=tuple(acknowledge_risk or ()),
            change_requests=tuple(change_request or ()),
            checked_items=tuple(checked),
        ),
        hint=_decide_hint(application, path, run_id),
    )
    _emit(
        {
            "decision": record.model_dump(mode="json", by_alias=True),
            "execution": execution.model_dump(mode="json", by_alias=True),
        },
        kind="decision",
    )
    _exit_for_execution(execution.status, execution.current_phase.value)


def _interactive_checklist(
    application: HarnessApplication, path: Path, run_id: str, checked: list[str]
) -> list[str]:
    """Ask about each checklist item of the run that is not ticked yet."""
    brief = _call(lambda: application.review(path, run_id))
    for item in brief.get("checklist") or []:
        if item["itemId"] in checked:
            continue
        answer = str(
            typer.prompt(f"Checked {item['itemId']}: {item['text']}? (yes/no)", default="no")
        )
        if answer.strip().lower() in {"y", "yes"}:
            checked.append(item["itemId"])
    return checked


def _digest_confirmation_configured(path: Path) -> bool:
    """``governance.confirmDecisionDigest``: confirm the digest on a terminal every time."""
    try:
        return bool(
            HarnessApplication().validate_config(path)["governance"]["confirmDecisionDigest"]
        )
    except Exception:  # noqa: BLE001 - decide reports configuration errors itself
        return False


def _exceptions_enabled(path: Path) -> bool:
    try:
        return bool(HarnessApplication().validate_config(path)["review"]["exceptions"])
    except Exception:  # noqa: BLE001 - decide reports configuration errors itself
        return False


def _interactive_exception(exception: ExceptionOptions) -> ExceptionOptions:
    """Ask for what an exception records when the options were not given."""

    def ask(question: str) -> str | None:
        answer = typer.prompt(question, default="", show_default=False)
        return str(answer).strip() or None

    return replace(
        exception,
        expires_in=exception.expires_in
        if exception.expires_in or exception.expires_at
        else ask("Expires in (e.g. 14d, 36h, 2w; empty for the project default)"),
        alternative_evidence=exception.alternative_evidence
        or ask("Alternative evidence (optional)"),
        follow_up=exception.follow_up or ask("Follow-up issue or task (optional)"),
    )


DIGEST_CONFIRM_CHARS = 12
"""Hex characters of the ChangeSet digest a person types to confirm an interactive decision."""


def _interactive_decision(
    application: HarnessApplication,
    path: Path,
    run_id: str,
    decision: DecisionKind | None,
    change_set_digest: str | None,
    rationale: str | None,
) -> tuple[DecisionKind, str, str]:
    brief = _call(lambda: application.review(path, run_id))
    typer.echo(render_human(brief, "review"))
    typer.echo("")
    current = brief["run"]["changeSetDigest"]
    if not brief["run"]["awaitingDecision"] or not current:
        _report_error(
            {
                "status": "ERROR",
                "error": f"run {run_id} is not waiting for a decision "
                f"({brief['run']['status']} in {brief['run']['currentPhase']})",
                "hint": "; ".join(brief["next"]) or None,
            }
        )
        raise typer.Exit(code=5)
    if decision is None:
        # Validated here rather than with click.Choice: click is not a declared dependency,
        # and recent Typer versions no longer install it.
        allowed = [item.value for item in DecisionKind]
        while decision is None:
            choice = str(typer.prompt(f"Decision ({', '.join(allowed)})")).strip().upper()
            if choice in allowed:
                decision = DecisionKind(choice)
            else:
                typer.echo(f"Choose one of: {', '.join(allowed)}", err=True)
    while not (rationale or "").strip():
        rationale = typer.prompt("Rationale (what you checked and why)")
    if change_set_digest is not None and change_set_digest != current:
        # Let the engine reject it with its usual message and exit code.
        return decision, change_set_digest, str(rationale)
    expected = current.removeprefix("sha256:")
    typed = typer.prompt(
        f"Type the first {DIGEST_CONFIRM_CHARS} characters of the digest to bind this "
        f"{decision.value} to {current}"
    )
    typed = typed.strip().lower().removeprefix("sha256:")
    if len(typed) < DIGEST_CONFIRM_CHARS or not expected.startswith(typed):
        _report_error(
            {
                "status": "ERROR",
                "error": "the ChangeSet digest was not confirmed; no decision was recorded",
                "hint": f"Type at least {DIGEST_CONFIRM_CHARS} characters of {current}.",
            }
        )
        raise typer.Exit(code=5)
    return decision, current, str(rationale)


def _decide_hint(application: HarnessApplication, path: Path, run_id: str) -> Hint:
    def hint(error: BaseException) -> str | None:
        message = str(error)
        try:
            status = application.status(path, run_id)
        except Exception:  # noqa: BLE001 - a hint must never hide the original error
            return None
        execution = status["execution"]
        if "does not match the current ChangeSet" in message:
            prior = (
                ""
                if status["humanDecision"]
                else " No decision has been recorded on the current digest."
            )
            return (
                f"The current ChangeSet digest is {execution['changeSetDigest']}.{prior} Review "
                f"it with `harness review --run {run_id}` and decide on that digest (or omit "
                "--change-set-digest on a terminal to confirm it interactively)."
            )
        if "accepted only in DECISION" in message:
            return (
                f"The run is {execution['status']} in {execution['currentPhase']}. Fix the cause "
                f"and `harness run continue --run {run_id}`, or `harness run cancel --run "
                f"{run_id}`; `harness review --run {run_id}` shows what did not pass."
            )
        if "APPROVE is only valid" in message:
            return (
                "The gate did not pass: decide REQUEST_CHANGES with what must change, REJECT, or "
                f"APPROVE_EXCEPTION with a rationale. `harness review --run {run_id}` lists the "
                "blocking findings."
            )
        return None

    return hint


@app.command()
def inbox(
    json_output: bool | None = JSON_OPTION,
    approve: list[str] | None = typer.Option(
        None,
        "--approve",
        help="Batch: APPROVE a run as RUN=sha256:DIGEST, the digest the inbox shows (repeatable)",
    ),
    reject: list[str] | None = typer.Option(
        None, "--reject", help="Batch: REJECT a run as RUN=sha256:DIGEST (repeatable)"
    ),
    request_changes: list[str] | None = typer.Option(
        None,
        "--request-changes",
        help="Batch: REQUEST_CHANGES on a run as RUN=sha256:DIGEST (repeatable)",
    ),
    decisions: Path | None = typer.Option(
        None,
        "--decisions",
        exists=True,
        dir_okay=False,
        help="Batch file (YAML or JSON): a list of run, decision, changeSetDigest and an "
        "optional rationale",
    ),
    batch: bool = typer.Option(
        False,
        "--batch",
        help="On a terminal: decide every pending decision of the inbox one after the other",
    ),
    rationale: str | None = typer.Option(
        None, "--rationale", help="Batch: the rationale recorded with each decision"
    ),
    actor: str | None = typer.Option(None, "--actor", help=ACTOR_HELP, show_default=False),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the runs of the project that wait for a person, oldest first: a decision in
    DECISION (gate status, digest, blocking findings) or answers to clarification questions in
    INTENT, each with the next command. With --approve, --reject, --request-changes,
    --decisions or --batch it records several decisions, each bound to its own ChangeSet digest:
    a stale digest or a refused decision is reported for that run (the others are recorded) and
    the command exits 5 when any was refused."""
    from governed_harness.application.friction import (
        BatchItem,
        load_batch_file,
        parse_batch_item,
    )

    items: list[BatchItem] = []
    for values, kind in (
        (approve, DecisionKind.APPROVE),
        (reject, DecisionKind.REJECT),
        (request_changes, DecisionKind.REQUEST_CHANGES),
    ):
        for value in values or ():
            items.append(_call(partial(parse_batch_item, value, kind)))
    if decisions is not None:
        import yaml

        raw = _call(lambda: yaml.safe_load(decisions.read_text(encoding="utf-8")))
        items.extend(_call(lambda: load_batch_file(raw)))
    if batch:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            _report_error(
                {
                    "status": "ERROR",
                    "error": "--batch needs a terminal",
                    "hint": "Use --approve RUN=DIGEST or --decisions FILE without a terminal.",
                }
            )
            raise typer.Exit(code=2)
        items.extend(_interactive_batch(path))
    if not items:
        _emit(_call(lambda: HarnessApplication().inbox(path)), json_output, kind="inbox")
        return
    if rationale is None and any(item.rationale is None for item in items):
        if sys.stdin.isatty() and sys.stdout.isatty():
            rationale = str(typer.prompt("Rationale recorded with each decision"))
        else:
            _report_error(
                {
                    "status": "ERROR",
                    "error": "a batch decision needs --rationale (or a rationale per decision)",
                }
            )
            raise typer.Exit(code=2)
    reason = rationale or ""
    result = _call(lambda: _acting().decide_batch(path, items, rationale=reason, actor_id=actor))
    _emit(result, json_output)
    if result["refused"]:
        raise typer.Exit(code=5)


def _interactive_batch(path: Path) -> list[Any]:
    """Ask, for each run waiting for a decision, what to decide; each answer is bound to the
    digest shown next to it."""
    from governed_harness.application.friction import BatchItem

    entries = [
        item
        for item in _call(lambda: HarnessApplication().inbox(path))
        if item.get("kind") == "decision" and item.get("changeSetDigest")
    ]
    choices = {
        "a": DecisionKind.APPROVE,
        "r": DecisionKind.REQUEST_CHANGES,
        "j": DecisionKind.REJECT,
    }
    items: list[Any] = []
    for entry in entries:
        typer.echo(
            f"{entry['executionId']}  {entry['taskTitle']}\n  gate {entry['gateStatus']}, "
            f"{entry['blockingFindings']} blocking finding(s), digest {entry['changeSetDigest']}"
        )
        answer = ""
        while answer not in {"a", "r", "j", "s"}:
            answer = (
                str(typer.prompt("  [a]pprove, [r]equest changes, re[j]ect, [s]kip", default="s"))
                .strip()
                .lower()[:1]
            )
        if answer != "s":
            items.append(BatchItem(entry["executionId"], choices[answer], entry["changeSetDigest"]))
    if items:
        confirmed = str(
            typer.prompt(
                f"Record {len(items)} decision(s), each bound to the digest shown? (yes/no)",
                default="no",
            )
        )
        if confirmed.strip().lower() not in {"y", "yes"}:
            raise typer.Exit(code=5)
    return items


@app.command()
def metrics(
    fmt: str | None = typer.Option(
        None,
        "--format",
        help="html, md, json, csv (one row per run) or prometheus; printed, or written to "
        "--output. Without it, metrics.json, metrics.md and metrics.html are written to "
        "--output-dir",
    ),
    output: Path | None = typer.Option(None, "--output", help="File for --format"),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Directory of the three files written without --format (default .harness/metrics)",
    ),
    open_report: bool = typer.Option(
        False, "--open", help="Open the HTML report in the default browser"
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only runs created since DATE (YYYY-MM-DD, ISO 8601, 7d, 2w, 12h)"
    ),
    task: str | None = typer.Option(None, "--task", help="Only the runs of this task"),
    model: str | None = typer.Option(None, "--model", help="Only agent calls of this model"),
    agent: str | None = typer.Option(
        None, "--agent", help="Only agent calls of this agent provider id"
    ),
    all_repos: bool = typer.Option(
        False,
        "--all-repos",
        help="Every repository of the run registry (runtime.stateDir), not only this one",
    ),
    prices: Path | None = typer.Option(
        None,
        "--prices",
        exists=True,
        dir_okay=False,
        help="Price table (YAML or JSON, USD per million input, output and cache tokens by "
        "model id) for providers that report tokens without a cost; overrides metrics.prices",
    ),
    narrative: bool = typer.Option(
        False,
        "--narrative",
        help="Add a narrative summary: one call to metrics.narrative.command, on demand",
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Local metrics of the governed runs, with zero model calls: tokens (input, output, cache)
    and cost by agent, model, task and phase, models used, lines by agent invocation and by
    person, issues resolved and features delivered, time per task, phase, agent call and human
    wait, quality, friction against the targets per size and daily and weekly trends. Costs of
    providers that report only tokens are estimated from the price table and labelled
    estimated. The HTML file is self-contained (inline CSS and SVG, nothing fetched). No
    per-person indicator is computed."""
    import webbrowser

    from governed_harness.application.metrics import write_reports
    from governed_harness.metrics import FORMATS, Filters, parse_since, render

    if fmt is not None and fmt not in FORMATS:
        _report_error(
            {"status": "ERROR", "error": f"--format is one of {', '.join(FORMATS)}, got {fmt!r}"}
        )
        raise typer.Exit(code=2)
    try:
        filters = Filters(
            since=parse_since(since),
            task=task,
            model=model,
            agent=agent,
            all_repos=all_repos,
        )
    except ValueError as error:
        _report_error({"status": "ERROR", "error": str(error)})
        raise typer.Exit(code=2) from error
    report, settings = _call(
        lambda: HarnessApplication().metrics(
            path, filters=filters, prices_file=prices, narrative=narrative
        )
    )
    html_file: Path | None = None
    if fmt is None:
        directory = output_dir or Path(settings["harnessDir"]) / "metrics"
        written = write_reports(report, directory)
        html_file = Path(written["html"])
        typer.echo(
            json.dumps(
                {"files": written, "totals": report["totals"], "filters": report["filters"]},
                indent=2,
            )
        )
    else:
        text = render(report, fmt)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            typer.echo(json.dumps({"file": str(output), "format": fmt}))
            if fmt == "html":
                html_file = output
        else:
            typer.echo(text, nl=False)
    if open_report:
        if html_file is None:
            directory = output_dir or Path(settings["harnessDir"]) / "metrics"
            html_file = Path(write_reports(report, directory, ("html",))["html"])
        webbrowser.open(html_file.resolve().as_uri())


@app.command()
def registry(json_output: bool | None = JSON_OPTION) -> None:
    """List the projects whose run registry lives in the state directory
    (`runtime.stateDir: auto`, `$HARNESS_STATE_DIR` or the user's data directory) with their
    latest runs: the runs of several repositories in one place."""
    _emit(_call(lambda: HarnessApplication().registry()), json_output)


@rules_app.command("health")
def rules_health(
    since: int | None = typer.Option(
        None, "--since", min=1, help="Only runs created in the last N days"
    ),
    json_output: bool | None = JSON_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show how each rule and validator behaved across the runs of the project: how often it
    fired, blocked a gate, was excepted, fired on a ChangeSet that was later corrected, fired in
    a rejected run or in a run later linked to an outcome. Read-only; nothing is changed and no
    figure is per person."""
    _emit(
        _call(lambda: HarnessApplication().rule_health(path, since_days=since)),
        json_output,
        kind="health",
    )


@outcome_app.command("record")
def outcome_record(
    run: str = RUN_OPTION,
    kind: str = typer.Option(..., "--kind", help="INCIDENT, REVERT, HOTFIX, REGRESSION or OTHER"),
    summary: str = typer.Option(..., "--summary", help="What happened"),
    reference: str | None = typer.Option(
        None, "--reference", help="Link or identifier of the incident, revert or fix"
    ),
    observed_at: str | None = typer.Option(
        None, "--observed-at", help="ISO 8601 instant it happened (default: now)"
    ),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    json_output: bool | None = JSON_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Link something that happened after a run (an incident, a revert, a hotfix, a regression)
    to it. The outcome feeds `harness rules health` and the causal retrospective; nothing is
    applied. An actor id of an agent, validator or the harness exits with 5."""
    _emit(
        _call(
            lambda: _acting().record_outcome(
                path,
                execution_id=run,
                kind=kind,
                summary=summary,
                reference=reference,
                observed_at=observed_at,
                actor_id=actor,
            )
        ),
        json_output,
    )


@outcome_app.command("list")
def outcome_list(
    run: str | None = typer.Option(None, "--run", help="Only the outcomes of this run"),
    json_output: bool | None = JSON_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the outcomes linked to the runs of the project, oldest first."""
    _emit(_call(lambda: HarnessApplication().list_outcomes(path, run)), json_output)


@exceptions_app.command("list")
def exceptions_list(
    status: str = typer.Option("all", "--status", help="all, active or expired"),
    expiring_within: int | None = typer.Option(
        None, "--expiring-within", min=0, help="Only active exceptions expiring within N days"
    ),
    json_output: bool | None = JSON_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the exceptions of the project (review.exceptions): who granted each, on which run
    and digest, its scope, alternative evidence and follow-up, when it expires and the runs whose
    gate relied on it. An expired exception no longer covers any finding."""
    _emit(
        _call(
            lambda: HarnessApplication().list_exceptions(
                path, status=status, expiring_within=expiring_within
            )
        ),
        json_output,
        kind="exceptions",
    )


@app.command()
def review(
    run: str = RUN_OPTION_LATEST,
    diff: bool = typer.Option(False, "--diff", help="Include the ChangeSet diff (redacted)"),
    json_output: bool | None = JSON_OPTION,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Show the decision brief of a run: what was asked, what changed, the risks with file and
    line, what was verified on which ChangeSet digest and what was not, active exceptions,
    retries and corrections, what changed since the last decision and the exact decide
    command. It only reads the record."""
    _emit(
        _call(lambda: HarnessApplication().review(path, run, include_diff=diff)),
        json_output,
        kind="review",
    )


@artifact_app.command("show")
def artifact_show(
    reference: str = typer.Argument(
        ..., help="artifact://sha256/<hex>, sha256:<hex> or a unique hex prefix (6+ characters)"
    ),
    describe: bool = typer.Option(
        False, "--describe", help="Print the descriptor (digest, size, media type, metadata)"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Print an artifact's content after verifying its digest, or its descriptor with
    --describe. Artifacts are stored redacted; a digest mismatch exits with 1."""
    descriptor, data = _call(lambda: HarnessApplication().show_artifact(path, reference))
    if describe:
        _emit(descriptor)
        return
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.flush()


@app.command()
def retrospect(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Derive non-mutating observations and recommendations from a closed run. Nothing is
    applied automatically."""
    _emit(_call(lambda: HarnessApplication().retrospect(path, run)))


@recommendation_app.command("list")
def recommendation_list(
    run: str = RUN_OPTION_LATEST,
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the retrospective recommendations of a run with the decision recorded for each."""
    _emit(_call(lambda: HarnessApplication().list_recommendations(path, run)))


@recommendation_app.command("decide")
def recommendation_decide(
    run: str = RUN_OPTION,
    recommendation: str = typer.Option(
        ..., "--recommendation", help="Recommendation identifier shown by retrospect"
    ),
    decision: RecommendationDecision = typer.Option(
        ..., "--decision", case_sensitive=False, help="Decision on the recommendation"
    ),
    rationale: str = typer.Option(
        ..., "--rationale", help="Justification recorded with the decision"
    ),
    statement: str | None = typer.Option(
        None, "--statement", help="Edited text of the recommendation; required with EDIT"
    ),
    actor: str | None = typer.Option(
        None,
        "--actor",
        help=ACTOR_HELP,
        show_default=False,
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Accept, edit or reject a retrospective recommendation. The decision is kept as
    retrospective memory: an accepted or edited recommendation enters the context of later
    runs, a rejected one stays as history. A second decision on the same recommendation exits
    with code 5; nothing is applied to rules, gates or configuration."""
    _emit(
        _call(
            lambda: _acting().decide_recommendation(
                path,
                execution_id=run,
                recommendation_id=recommendation,
                decision=decision,
                actor_id=actor,
                rationale=rationale,
                statement=statement,
            )
        )
    )


@plugins_app.command("list")
def plugins_list() -> None:
    """List the built-in extensions and the external plugin protocol version."""
    _emit(
        {
            "builtIns": [
                {"id": "profile/python", "kind": "technology-profile", "version": "1.0"},
                {"id": "profile/node", "kind": "technology-profile", "version": "1.0"},
                {"id": "agent/simulated", "kind": "agent-provider", "version": "1"},
                {"id": "review/independent", "kind": "validator", "version": "1"},
            ],
            "externalProtocol": "1.0",
        }
    )


@benchmark_app.command("run")
def benchmark_run(
    output: Path | None = typer.Option(
        None, "--output", help="Write to this file instead of standard output"
    ),
    iterations: int = typer.Option(
        1000, "--iterations", min=10, max=100000, help="Iterations per in-process operation"
    ),
) -> None:
    """Run the synthetic microbenchmarks (hashing, transitions, gates, events, artifacts,
    process launch)."""
    from governed_harness.benchmark.runner import run_benchmarks

    result = _call(lambda: run_benchmarks(iterations=iterations))
    data = json.dumps(result, indent=2, sort_keys=True).encode("utf-8")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        typer.echo(str(output))
    else:
        typer.echo(data.decode())


@benchmark_app.command("scenarios")
def benchmark_scenarios(
    output: Path | None = typer.Option(
        None, "--output", help="Write to this file instead of standard output"
    ),
    iterations: int = typer.Option(
        3, "--iterations", min=1, max=100, help="Repetitions per scenario"
    ),
) -> None:
    """Compare a direct patch-and-test path with the complete governed path on generated Python
    and Node.js projects."""
    from governed_harness.benchmark.scenarios import run_scenario_benchmarks

    result = _call(lambda: run_scenario_benchmarks(iterations=iterations))
    data = json.dumps(result, indent=2, sort_keys=True).encode("utf-8")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        typer.echo(str(output))
    else:
        typer.echo(data.decode())


@api_app.command("serve")
def api_serve(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address; keep the loopback default"),
    port: int = typer.Option(8765, "--port", min=1, max=65535, help="TCP port"),
) -> None:
    """Serve the local API and web dashboard. There is no authentication: keep it bound to
    127.0.0.1."""
    import uvicorn

    from governed_harness.api import create_app

    uvicorn.run(create_app(path), host=host, port=port, log_level="info")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
