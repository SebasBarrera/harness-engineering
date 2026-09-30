# Governed Agent Harness

A technology-neutral research implementation for governing software-development work performed with AI agents. It turns an intention into a versioned, observable execution with normative phases, explicit capabilities, structured evidence, independent validation, fail-closed gates, digest-bound human decisions, resumption and a non-mutating retrospective.

The package is a **research beta**, not a production security boundary. It is complete enough to run the full vertical slice on Python and Node.js projects, package as a wheel, expose a CLI/API, execute tests and benchmarks, and support a second provider through a structured command adapter.

## What is implemented

- Nine normative phases: `INTENT`, `DISCOVERY`, `SPECIFICATION`, `PLANNING`, `IMPLEMENTATION`, `VERIFICATION`, `INDEPENDENT_REVIEW`, `DECISION`, `CLOSURE`.
- Separate post-run `RETROSPECTIVE` workflow that never changes policy automatically.
- Strict state machine, retry attempts, cancellation, resumption and downstream invalidation.
- Versioned task, plan, run, phase, ChangeSet, evidence, finding, gate, decision, invocation, memory and retrospective models.
- JSON Schema 2020-12 interoperability contracts and a deterministic schema generator.
- SQLite WAL event store with per-run sequence numbers and a SHA-256 hash chain.
- SQLite typed projections for queryable state.
- Content-addressed artifact store with atomic writes, digest verification and secret redaction before persistence.
- Deny-by-default capability grants scoped by actor, resource, command and expiration.
- Process execution with `shell=False`, bounded output, timeouts, cancellation and process-group termination.
- Task-owned workspace snapshots and ChangeSets that exclude unrelated user changes.
- Python profile: `pytest` mandatory; `ruff` and `mypy` optional when available.
- Node.js profile: `npm test` mandatory; lint and type-check scripts optional when available.
- Deterministic simulated provider for reproducible research fixtures.
- Provider-neutral structured command adapter for an external agent CLI.
- Versioned external plugin protocol over one-shot JSON `stdin/stdout` plus an SDK echo implementation.
- Independent deterministic review with structured findings and redacted persisted evidence.
- Fail-closed gate engine. `BLOCKED`, `ERROR`, `SKIPPED`, `TIMED_OUT`, `NOT_APPLICABLE` and `INCONCLUSIVE` never become implicit success.
- Human approval/rejection/exception linked to the exact ChangeSet, configuration and policy digests.
- Automatic invalidation of a stale approval after any owned-path change.
- Deterministic context-memory selection across normative, project, task, ephemeral and retrospective levels.
- Operational telemetry with observed/derived/unavailable quality labels; provider token data is never invented.
- Markdown, JSON, JSONL and SARIF reports.
- CLI, FastAPI application API and a local dashboard capable of issuing digest-bound decisions.
- Python and Node.js end-to-end fixtures, security tests, contract tests and repeatable benchmarks.

## Install

From the source tree:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,api]'
```

From the built wheel:

```bash
python -m pip install dist/governed_agent_harness-0.8.0-py3-none-any.whl
```

Requirements:

- Python 3.12 or 3.13.
- Git for baseline metadata and fixture workflows.
- Node.js/npm only for the Node.js profile.
- Optional validators such as Ruff and mypy are detected; their absence is recorded as `NOT_APPLICABLE` when they are not mandatory.

## End-to-end CLI example

Given a Python or Node.js repository:

```bash
harness init --path /path/to/project
harness inspect --path /path/to/project
harness config validate --path /path/to/project
harness task create --path /path/to/project --file task.yaml
harness task list --path /path/to/project
```

Start the run using the task ID returned by `task create`:

```bash
harness run start --path /path/to/project --task <task-id>
```

A successful automated flow stops at `DECISION` with exit code `4`, because a human decision is intentionally pending. Inspect the exact current digest:

```bash
harness status --path /path/to/project --run <run-id>
harness findings list --path /path/to/project --run <run-id>
harness evidence list --path /path/to/project --run <run-id>
```

Approve only the digest shown by status:

```bash
harness gate decide \
  --path /path/to/project \
  --run <run-id> \
  --decision APPROVE \
  --change-set-digest sha256:<digest> \
  --actor human.reviewer \
  --rationale 'Acceptance criteria and independent evidence reviewed'
```

Render the final trace:

```bash
harness trace --path /path/to/project --run <run-id> --format markdown --output trace.md
harness trace --path /path/to/project --run <run-id> --format json --output trace.json
harness trace --path /path/to/project --run <run-id> --format sarif --output findings.sarif
harness retrospect --path /path/to/project --run <run-id>
```

## Local API and dashboard

Install the API extra, then run:

```bash
harness api serve --path /path/to/project --host 127.0.0.1 --port 8765
```

The local dashboard lists executions, shows the full status projection, and allows `APPROVE`, `APPROVE_EXCEPTION` or `REJECT` decisions using the current ChangeSet digest. The API and CLI use the same application layer; the UI contains no policy or workflow logic.

Important: the MVP dashboard has no authentication and must remain bound to localhost or a separately secured environment.

## Structured command provider

A project can register a provider-neutral command adapter in `.harness/project.yaml`:

```yaml
agentProvider: local_wrapper
agentProviders:
  local_wrapper:
    kind: command
    command: [python, examples/structured-command-agent.py]
    model: optional-model-label
```

The command reads one JSON request from standard input and returns one JSON response with a normalized status. It may modify the workspace only within the surrounding runtime's actual containment. The built-in local process runner does **not** provide strong OS sandboxing; use a container or OS sandbox adapter before running untrusted native code.

## Tests and verification

```bash
make schemas
make test
make coverage
make benchmark
make benchmark-scenarios
make build
```

The test suite covers:

- state and transition invariants;
- stale approvals and correction/resumption;
- task-owned ChangeSet isolation;
- event/artifact tamper detection;
- path traversal and symlink escape;
- secret redaction without hiding a secret from in-memory security review;
- timeout, cancellation and output bounds;
- malformed plugin and agent protocol output;
- Python and Node.js end-to-end runs;
- a functional structured-command provider;
- runtime records validated against committed JSON Schemas;
- API, CLI and report formats.

Validation results and environment-specific benchmark outputs are under `reports/`.

## Benchmarks

Two benchmark groups are available:

```bash
harness benchmark run --iterations 500 --output reports/benchmark-micro.json
harness benchmark scenarios --iterations 3 --output reports/benchmark-scenarios.json
```

The microbenchmark measures hashing, state transitions, gate evaluation, artifact deduplication, event append throughput and process-launch overhead. The scenario benchmark compares the same deterministic change and mandatory tests through:

1. a direct patch-and-test path; and
2. the complete governed path, including persistence, phases, evidence, review, gate evaluation, immediate programmatic benchmark approval and closure.

These are synthetic local measurements. They quantify runtime overhead in the recorded environment; they do not establish developer productivity, quality improvement or statistical significance.

## Repository map

- `src/governed_harness/domain`: normalized entities, enums, invariants and errors.
- `src/governed_harness/application`: use cases shared by CLI and API.
- `src/governed_harness/orchestration`: run engine, state machine and workflow semantics.
- `src/governed_harness/runtime`: process, patch, workspace and Git adapters.
- `src/governed_harness/capabilities`: capability grants and authorization.
- `src/governed_harness/events`: append-only event chain.
- `src/governed_harness/evidence`: artifact store, hashing and redaction.
- `src/governed_harness/storage`: query projections and flags.
- `src/governed_harness/validators`: command validation and independent review.
- `src/governed_harness/gates`: deterministic global decision consolidation.
- `src/governed_harness/agents`: simulated and structured-command providers.
- `src/governed_harness/plugins`: external protocol, client, registry and SDK.
- `src/governed_harness/memory`: deterministic memory persistence and selection.
- `src/governed_harness/telemetry`: operational metric projection.
- `src/governed_harness/retrospective`: evidence-to-recommendation rules.
- `src/governed_harness/reporting`: Markdown, JSON and SARIF renderers.
- `src/governed_harness/api`: local FastAPI API and dashboard.
- `src/governed_harness/cli`: Typer command surface.
- `schemas/v1`: normative interoperability contracts.
- `profiles`: technology-specific declarative defaults.
- `workflows`: versioned normative workflow.
- `tests`: unit, contract, integration, security, end-to-end and performance tests.
- `docs`: ADRs, architecture, evaluation, implementation status and validation evidence.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Command or execution completed successfully |
| `1` | Harness/internal/protocol error |
| `2` | `doctor` detected an invalid environment or configuration |
| `4` | Automated phases passed and a human decision is pending |
| `6` | Validation, policy, blocking, timeout or inconclusive result |
| `130` | Execution cancelled |

## Explicit limitations

The following are deliberately not claimed as complete production capabilities:

- strong filesystem, network, CPU or memory isolation for hostile native child processes;
- authenticated or multi-user web access;
- distributed scheduling or remote workers;
- generic DAST orchestration;
- production MCP transport and server lifecycle management;
- plugin registry, signatures and supply-chain attestation;
- automatic diff coverage for every language;
- provider-specific token/cost capture when the provider does not expose it;
- GraphRAG or semantic-memory infrastructure;
- automatic changes to phases, gates, permissions, prompts or normative memory.

See `docs/implementation-status.md` and `SECURITY.md` before using the harness outside controlled research fixtures.
