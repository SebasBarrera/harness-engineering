# ADR 0001: System boundaries

- Status: Accepted provisionally
- Date: 2026-08-30

## Context

The artifact must govern an agent-assisted development run without becoming the agent, model, IDE, source-control platform, CI system or validator itself.

## Decision

The harness is the control plane that owns intent capture, normative phases, transitions, capabilities, evidence, gates, decisions, traceability, resumption and retrospective proposals. External systems remain data-plane executors behind ports.

The core does not assume a programming language, build system, web framework, CI provider or agent provider. Git is the first SCM adapter, not a core dependency.

## Consequences

- CLI and future web/API share application use cases.
- Tool and model output is untrusted until normalized and linked to evidence.
- Repository-specific instructions cannot override core invariants.
- Supporting a new stack must not modify domain or orchestration packages.
