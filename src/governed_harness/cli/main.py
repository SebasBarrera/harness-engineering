from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from governed_harness.application import HarnessApplication
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


ACTOR_HELP = (
    "Identifier of the person acting (recorded, not authenticated). Defaults to the Git user "
    "under governance.deciderIdentity: git, otherwise human.local. Ids of agents, validators "
    "and the harness (agent.*, validator.*, harness.*) are refused with exit code 5."
)


def _emit(value: object, json_output: bool = True) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    elif isinstance(value, list):
        value = [
            item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item
            for item in value
        ]
    if json_output:
        typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    else:
        typer.echo(value)


def _call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except HarnessError as error:
        typer.echo(json.dumps({"status": "ERROR", "error": str(error)}, indent=2), err=True)
        raise typer.Exit(code=error.exit_code) from error
    except Exception as error:
        typer.echo(
            json.dumps(
                {"status": "ERROR", "errorType": type(error).__name__, "error": str(error)},
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error


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


@app.command()
def init(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing project configuration"),
) -> None:
    """Create .harness/project.yaml for a repository, using the detected technology profiles."""
    _emit(_call(lambda: HarnessApplication().init(path, force=force)))


@app.command()
def inspect(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """Detect the technology profiles of a repository (read-only), with confidence and
    evidence."""
    _emit(_call(lambda: HarnessApplication().inspect(path)), json_output)


@app.command()
def doctor(
    path: Path | None = typer.Option(
        None, "--path", help="Also validate the project in this directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """Check the local environment (Python and Git required; Node.js and npm reported as
    NOT_APPLICABLE when absent) and, with --path, the project configuration. Exit code 2 when a
    required check fails."""
    result = _call(lambda: HarnessApplication().doctor(path))
    _emit(result, json_output)
    if result["status"] != "PASSED":
        raise typer.Exit(code=2)


@config_app.command("validate")
def config_validate(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """Validate the project configuration and print the resolved profiles, workflow,
    validators, capabilities and policies."""
    _emit(_call(lambda: HarnessApplication().validate_config(path)), json_output)


@task_app.command("create")
def task_create(
    file: Path = typer.Option(
        ..., "--file", exists=True, dir_okay=False, help="Task file (YAML or JSON)"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
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
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """List the tasks persisted in the project."""
    _emit(_call(lambda: HarnessApplication().list_tasks(path)), json_output)


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
        _call(
            lambda: HarnessApplication().clarify_task(
                path, task_id=task, answers_file=file, actor_id=actor
            )
        )
    )


@run_app.command("start")
def run_start(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    provider: str | None = typer.Option(
        None, "--provider", help="Agent provider id; defaults to the project agentProvider"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """Create a run for a task and execute the normative phases until a stop condition. Exit
    code 4 means the automated phases finished and a human decision is pending; 6 means a
    validation, policy or blocking condition stopped the run."""
    execution = _call(lambda: HarnessApplication().start_run(path, task, provider))
    _emit(execution, json_output)
    _exit_for_execution(execution.status, execution.current_phase.value)


@run_app.command("continue")
def run_continue(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """Resume a run from its persisted state, for example after fixing the environment or after
    REQUEST_CHANGES."""
    execution = _call(lambda: HarnessApplication().continue_run(path, run))
    _emit(execution, json_output)
    _exit_for_execution(execution.status, execution.current_phase.value)


@run_app.command("cancel")
def run_cancel(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
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
    execution = _call(lambda: HarnessApplication().cancel_run(path, run, actor))
    _emit(execution)


@run_app.command("list")
def run_list(
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the runs of the project, newest first."""
    _emit(_call(lambda: HarnessApplication().list_runs(path)))


@app.command()
def status(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
    json_output: bool = typer.Option(
        True, "--json/--no-json", help="Print JSON (default) or a plain representation"
    ),
) -> None:
    """Show the full status projection of a run: phases, validations, findings, gate, human
    decision, event-chain check and metrics."""
    _emit(_call(lambda: HarnessApplication().status(path, run)), json_output)


@app.command()
def trace(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    format: str = typer.Option("markdown", "--format", help="markdown, json, jsonl or sarif"),
    output: Path | None = typer.Option(
        None, "--output", help="Write to this file instead of standard output"
    ),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Export the trace of a run as Markdown, JSON, JSONL or SARIF. Under
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
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Verify the record of a run (or of every run): the event chain, the head of the chain
    against its anchor outside .harness (`governance.chainAnchor`), every record that has an
    event against that event, the execution's pointers and every referenced artifact against its
    digest. Prints a report and never repairs anything. Exit code 0 when everything verifies, 6
    when any check fails, 3 for an unknown run."""
    report = _call(lambda: HarnessApplication().verify(path, run))
    _emit(report)
    if not report["valid"]:
        raise typer.Exit(code=6)


@evidence_app.command("list")
def evidence_list(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the evidence records of a run with their artifact references and digests."""
    _emit(_call(lambda: HarnessApplication().list_evidence(path, run)))


@findings_app.command("list")
def findings_list(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the structured findings of a run with severity, rule and location."""
    _emit(_call(lambda: HarnessApplication().list_findings(path, run)))


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
            lambda: HarnessApplication().add_memory(
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
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
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
    _emit(
        _call(lambda: HarnessApplication().approve_memory(path, memory_id=memory, actor_id=actor))
    )


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
            lambda: HarnessApplication().invalidate_memory(
                path, memory_id=memory, actor_id=actor, reason=reason
            )
        )
    )


@gate_app.command("decide")
def gate_decide(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    decision: DecisionKind = typer.Option(
        ..., "--decision", case_sensitive=False, help="Human decision"
    ),
    change_set_digest: str = typer.Option(
        ..., "--change-set-digest", help="Current ChangeSet digest shown by status (sha256:...)"
    ),
    rationale: str = typer.Option(
        ..., "--rationale", help="Justification recorded with the decision"
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
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Record a human decision bound to the current ChangeSet digest. A decision for a stale
    digest, APPROVE over a gate that did not pass, or an actor id of an agent, validator or the
    harness is rejected with exit code 5. Under `governance.confirmDecisionDigest: true` and on a
    terminal, the decision, the gate and the changed files are shown first and the first
    characters of the ChangeSet digest must be typed to confirm (a wrong answer exits with 5 and
    records nothing)."""
    _confirm_decision(path, run, decision, change_set_digest)
    record, execution = _call(
        lambda: HarnessApplication().decide_gate(
            path,
            execution_id=run,
            decision=decision,
            change_set_digest=change_set_digest,
            actor_id=actor,
            rationale=rationale,
            continue_after=continue_after,
        )
    )
    _emit(
        {
            "decision": record.model_dump(mode="json", by_alias=True),
            "execution": execution.model_dump(mode="json", by_alias=True),
        }
    )
    _exit_for_execution(execution.status, execution.current_phase.value)


CONFIRM_PREFIX_CHARS = 12
"""Hex characters of the ChangeSet digest typed to confirm an interactive decision."""


def _interactive() -> bool:
    """Whether a person is at the terminal (standard input and error are terminals)."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def _confirm_decision(path: Path, run: str, decision: DecisionKind, change_set_digest: str) -> None:
    if not _interactive():
        return
    summary = _call(lambda: HarnessApplication().decision_summary(path, run))
    if not summary["confirmDigest"]:
        return
    current = str(summary["changeSetDigest"] or "")
    typer.echo(
        f"Run {summary['executionId']} (task {summary['taskId']}), phase "
        f"{summary['currentPhase']}\nGate: {summary['gateStatus']} "
        f"{' '.join(summary['gateReasonCodes'])}\nChangeSet {current}:",
        err=True,
    )
    for item in summary["files"]:
        typer.echo(
            f"  {item['status']:<9} {item['path']} (+{item['additions']} -{item['deletions']})",
            err=True,
        )
    expected = current.removeprefix("sha256:")[:CONFIRM_PREFIX_CHARS]
    answer = typer.prompt(
        f"Decision {decision.value} on {change_set_digest}. Type the first "
        f"{CONFIRM_PREFIX_CHARS} characters of the ChangeSet digest after 'sha256:' to confirm",
        err=True,
    )
    if not expected or answer.strip().lower() != expected:
        typer.echo(
            json.dumps(
                {
                    "status": "ERROR",
                    "error": "decision not confirmed: the typed characters do not match the "
                    "current ChangeSet digest; nothing was recorded",
                },
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=5)


@app.command()
def retrospect(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """Derive non-mutating observations and recommendations from a closed run. Nothing is
    applied automatically."""
    _emit(_call(lambda: HarnessApplication().retrospect(path, run)))


@recommendation_app.command("list")
def recommendation_list(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(
        default_factory=Path.cwd, show_default="current directory", help="Project directory"
    ),
) -> None:
    """List the retrospective recommendations of a run with the decision recorded for each."""
    _emit(_call(lambda: HarnessApplication().list_recommendations(path, run)))


@recommendation_app.command("decide")
def recommendation_decide(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
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
            lambda: HarnessApplication().decide_recommendation(
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
