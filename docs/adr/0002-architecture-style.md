# ADR 0002: Hybrid architecture with ports, profiles and isolated extensions

- Status: Accepted provisionally
- Date: 2026-08-30

## Decision

Use a hybrid architecture:

1. technology-agnostic core and application layer;
2. trusted built-in primitive adapters for filesystem, processes, Git and local stores;
3. declarative technology profiles for defaults and composition;
4. in-process trusted extension implementations during the MVP;
5. a versioned out-of-process JSON protocol for future untrusted or independently released plugins;
6. generated project configuration as a bootstrap aid, never as an authority that can weaken invariants.

## Why not a universal monolith

It would accumulate conditional logic for incompatible ecosystems and make security review of the core increasingly difficult.

## Why not one harness per technology

It would duplicate state, evidence, approval and traceability semantics and make empirical comparison unreliable.

## Consequences

The extension SDK is intentionally narrow. Simple thresholds and commands remain configuration, not plugins. External plugins cannot access internal persistence or decide global gates.
