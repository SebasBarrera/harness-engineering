# Architecture

## Selected model

The implementation uses a hybrid architecture:

1. a technology-neutral, deterministic core;
2. trusted local primitive adapters;
3. declarative technology profiles;
4. versioned external extension protocols;
5. project configuration that can narrow or assemble behavior;
6. locked core policies that project content cannot weaken.

This avoids a universal core filled with stack conditionals while preserving one comparable execution, evidence and decision model across technologies.

## Dependency rule

```text
CLI / Local Web / API
          |
      Application
          |
   Orchestration Engine -------- Reporting
          |
 Domain / State Machine / Gates / Capabilities
          |
 Ports and normalized contracts
   |          |          |          |
Runtime   Persistence  Validators  Agent/Plugin adapters
   |          |          |          |
Filesystem  SQLite    pytest/npm  command JSON process
Process     Artifacts  review     external plugin protocol
Git
```

Allowed dependencies point inward. In particular:

- domain imports no CLI, web, profile, tool, framework or provider package;
- orchestration consumes normalized adapters and definitions, not package-manager semantics;
- validators do not decide the global gate;
- reporters do not change state;
- plugins cannot access internal run persistence;
- repository content is never promoted to core policy.

## Core responsibilities

The core owns:

- task and execution identity;
- normative phase order and transition guards;
- status, error and finding taxonomies;
- immutable configuration/workflow/policy digests;
- action actor and provenance;
- ChangeSet ownership and digest;
- evidence and artifact references;
- gate evaluation and approval binding;
- invalidation, correction, resumption, cancellation and closure;
- traceability and retrospective proposal boundaries.

The core does not implement `pytest`, npm, a model API, GitHub, a particular scanner or an organizational rule pack.

## Normative execution

```text
INTENT
  -> DISCOVERY
  -> SPECIFICATION
  -> PLANNING
  -> IMPLEMENTATION
  -> VERIFICATION
  -> INDEPENDENT_REVIEW
  -> DECISION
  -> CLOSURE
  -> RETROSPECTIVE (separate, non-mutating)
```

Only `PASSED` advances a required phase. A validator may return `NOT_APPLICABLE` only when it is optional and records the reason. A mandatory non-success prevents advancement. `DECISION` remains `BLOCKED` until an explicit human record is bound to the exact current ChangeSet digest.

A correction returns the run to implementation and invalidates verification, review, gate and decision artifacts derived from the previous digest.

## Persistence

The local MVP keeps one SQLite database under `.harness/state.db`:

- `events`: append-only events, ordered per execution and linked by hash;
- `records`: typed JSON projections for tasks, runs, phases, invocations, validations, findings, decisions and retrospective records;
- `flags`: internal resumability markers such as baseline and plan IDs.

Artifacts live under `.harness/artifacts/` in a SHA-256 content-addressed store. Writes are temporary-file + `fsync` + atomic replace. Metadata records media type, size, redaction and digest.

The event chain makes tampering detectable, not impossible. A production deployment would add external or signed attestations.

## ChangeSet model

The engine captures a workspace baseline before mutation, then computes a deterministic diff after implementation. For structured patch tasks, only declared patch paths are owned by the run. For external providers, `metadata.ownedPaths` can define ownership. Unrelated pre-existing or concurrent user changes do not enter the ChangeSet or approval digest.

The persisted diff is redacted before storage. Security review receives the unredacted diff only in memory, preventing the redactor from concealing a credential from the validator while avoiding secret persistence.

## Extension model

### Technology profile

A profile is declarative and supplies detection markers, validator definitions, default commands, policies and capability scopes. Python and Node.js profiles are built in.

### Agent provider

- `simulated`: deterministic patch application for repeatable research.
- `command`: launches a configured command, sends task/plan JSON through stdin and requires a normalized JSON response.

A provider proposes or applies a candidate change. It never approves its own result.

### External plugin protocol

External plugins use a versioned one-request/one-response JSON protocol over `stdin/stdout`. The client enforces timeout, command capability, a single response line, schema parsing and request-ID correlation. The protocol separates process exit from check status.

### Validator

A validator returns a normalized `ValidationResult`, optional `Finding` records and tool invocations. Mandatory status and finding severity are consolidated by the core `GateEngine`.

## Configuration resolution

```text
core locked policies
  + profile defaults
  + project configuration that may tighten controls
  -> immutable resolved snapshot and digests per execution
```

Operational configuration can select providers, validators and scopes. It cannot disable human-decision requirements, digest binding, fail-closed mandatory checks or the prohibition on automatic retrospective mutation.

## Versioning

- core and CLI: semantic versioning;
- public schemas: major version directory and stable `$id`;
- project configuration: `configVersion`;
- workflow: `workflowVersion` and digest;
- external plugin protocol: independent `protocolVersion`;
- profiles: independent `profileVersion` included in the configuration snapshot.
