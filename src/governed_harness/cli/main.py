from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import typer

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, ResultStatus
from governed_harness.domain.errors import HarnessError

app = typer.Typer(no_args_is_help=True, help="Governed Agent Harness CLI")
config_app = typer.Typer(help="Configuration commands")
task_app = typer.Typer(help="Task commands")
run_app = typer.Typer(help="Execution lifecycle commands")
gate_app = typer.Typer(help="Human gate commands")
evidence_app = typer.Typer(help="Evidence commands")
findings_app = typer.Typer(help="Finding commands")
plugins_app = typer.Typer(help="Plugin and extension commands")
benchmark_app = typer.Typer(help="Benchmark commands")
api_app = typer.Typer(help="Local API and web dashboard")
app.add_typer(config_app, name="config")
app.add_typer(task_app, name="task")
app.add_typer(run_app, name="run")
app.add_typer(gate_app, name="gate")
app.add_typer(evidence_app, name="evidence")
app.add_typer(findings_app, name="findings")
app.add_typer(plugins_app, name="plugins")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(api_app, name="api")



def _emit(value: object, json_output: bool = True) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    elif isinstance(value, list):
        value = [item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item for item in value]
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
    if status in {ResultStatus.BLOCKED, ResultStatus.FAILED, ResultStatus.INCONCLUSIVE, ResultStatus.TIMED_OUT}:
        raise typer.Exit(code=6)
    if status is ResultStatus.ERROR:
        raise typer.Exit(code=1)


@app.command()
def init(
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    force: bool = typer.Option(False, "--force", help="Replace an existing project configuration"),
) -> None:
    """Create .harness/project.yaml for a repository, using the detected technology profiles."""
    _emit(_call(lambda: HarnessApplication().init(path, force=force)))


@app.command()
def inspect(
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
) -> None:
    """Detect the technology profiles of a repository (read-only), with confidence and
    evidence."""
    _emit(_call(lambda: HarnessApplication().inspect(path)), json_output)


@app.command()
def doctor(
    path: Path | None = typer.Option(None, "--path", help="Also validate the project in this directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
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
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
) -> None:
    """Validate the project configuration and print the resolved profiles, workflow,
    validators, capabilities and policies."""
    _emit(_call(lambda: HarnessApplication().validate_config(path)), json_output)


@task_app.command("create")
def task_create(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False, help="Task file (YAML or JSON)"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
) -> None:
    """Validate a task file (YAML or JSON) and persist it as a versioned task."""
    _emit(_call(lambda: HarnessApplication().create_task(path, file)), json_output)


@task_app.command("list")
def task_list(
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
) -> None:
    """List the tasks persisted in the project."""
    _emit(_call(lambda: HarnessApplication().list_tasks(path)), json_output)


@task_app.command("show")
def task_show(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """Show one persisted task."""
    _emit(_call(lambda: HarnessApplication().get_task(path, task)))


@run_app.command("start")
def run_start(
    task: str = typer.Option(..., "--task", help="Task identifier (taskId)"),
    provider: str | None = typer.Option(None, "--provider", help="Agent provider id; defaults to the project agentProvider"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
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
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
) -> None:
    """Resume a run from its persisted state, for example after fixing the environment or after
    REQUEST_CHANGES."""
    execution = _call(lambda: HarnessApplication().continue_run(path, run))
    _emit(execution, json_output)
    _exit_for_execution(execution.status, execution.current_phase.value)


@run_app.command("cancel")
def run_cancel(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    actor: str = typer.Option("human.local", "--actor", help="Identifier of the person acting (recorded, not authenticated)"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """Cancel a run and terminate its active process group. The cancellation is recorded as an
    event."""
    execution = _call(lambda: HarnessApplication().cancel_run(path, run, actor))
    _emit(execution)


@run_app.command("list")
def run_list(path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory")) -> None:
    """List the runs of the project, newest first."""
    _emit(_call(lambda: HarnessApplication().list_runs(path)))


@app.command()
def status(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Print JSON (default) or a plain representation"),
) -> None:
    """Show the full status projection of a run: phases, validations, findings, gate, human
    decision, event-chain check and metrics."""
    _emit(_call(lambda: HarnessApplication().status(path, run)), json_output)


@app.command()
def trace(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    format: str = typer.Option("markdown", "--format", help="markdown, json, jsonl or sarif"),
    output: Path | None = typer.Option(None, "--output", help="Write to this file instead of standard output"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """Export the trace of a run as Markdown, JSON, JSONL or SARIF."""
    data = _call(lambda: HarnessApplication().trace(path, run, format))
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        typer.echo(str(output))
    else:
        typer.echo(data.decode("utf-8", "replace"), nl=False)


@evidence_app.command("list")
def evidence_list(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """List the evidence records of a run with their artifact references and digests."""
    _emit(_call(lambda: HarnessApplication().list_evidence(path, run)))


@findings_app.command("list")
def findings_list(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """List the structured findings of a run with severity, rule and location."""
    _emit(_call(lambda: HarnessApplication().list_findings(path, run)))


@gate_app.command("decide")
def gate_decide(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    decision: DecisionKind = typer.Option(..., "--decision", case_sensitive=False, help="Human decision"),
    change_set_digest: str = typer.Option(..., "--change-set-digest", help="Current ChangeSet digest shown by status (sha256:...)"),
    rationale: str = typer.Option(..., "--rationale", help="Justification recorded with the decision"),
    actor: str = typer.Option("human.local", "--actor", help="Identifier of the person acting (recorded, not authenticated)"),
    continue_after: bool = typer.Option(True, "--continue/--no-continue", help="Resume the run after recording the decision"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """Record a human decision bound to the current ChangeSet digest. A decision for a stale
    digest, or APPROVE over a gate that did not pass, is rejected with exit code 5."""
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
            "decision": record.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json"),
        }
    )
    _exit_for_execution(execution.status, execution.current_phase.value)


@app.command()
def retrospect(
    run: str = typer.Option(..., "--run", help="Run (execution) identifier"),
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
) -> None:
    """Derive non-mutating observations and recommendations from a closed run. Nothing is
    applied automatically."""
    _emit(_call(lambda: HarnessApplication().retrospect(path, run)))


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
    output: Path | None = typer.Option(None, "--output", help="Write to this file instead of standard output"),
    iterations: int = typer.Option(1000, "--iterations", min=10, max=100000, help="Iterations per in-process operation"),
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
    output: Path | None = typer.Option(None, "--output", help="Write to this file instead of standard output"),
    iterations: int = typer.Option(3, "--iterations", min=1, max=100, help="Repetitions per scenario"),
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
    path: Path = typer.Option(default_factory=Path.cwd, show_default="current directory", help="Project directory"),
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
