# ADR 0005: Technology extension model

- Status: Accepted provisionally
- Date: 2026-08-30

## Decision

Technology support is split across four mechanisms:

- **Detector:** read-only identification with confidence and evidence.
- **Profile:** declarative defaults for commands, validators, policies and capabilities.
- **Extension implementation:** executable behavior for complex detection, validation or integration.
- **Project configuration:** project-specific selection, unit roots, command overrides and thresholds.

The MVP ships Python and Node.js profiles to prove that the core is not tied to a single ecosystem. Both must pass the same contract test kit and produce the same normalized result schemas.

## Plugin safety

A plugin may return results and evidence references. It may not mutate the run store, change the workflow, grant itself capabilities or turn a missing mandatory check into success. Protocol and schema failures fail closed.
