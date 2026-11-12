# Delivery status of the 0.8.0 cut

Version: 0.8.0 research beta (the cut the thesis documents, tag `v0.8.0`)
Delivery date: 2026-08-30

This file records the 0.8.0 delivery and is not updated with later releases. The current version
is 2.0.1: see [README.md](README.md), [CHANGELOG.md](CHANGELOG.md) and the
[trust model](docs/architecture/trust-model.md) for what it implements and does not guarantee.

## Verified in this environment

- 86 tests collected across unit, contract, integration, security, end-to-end and performance suites.
- The full pytest run reached 100% with no failing test output before the enclosing verification command timed out while proceeding to optional lint/type-check steps.
- The built wheel starts successfully from an isolated installation target.
- `harness doctor --json` reports PASSED for Python, Git, Node and npm.
- Wheel and source distribution are included under `dist/`.
- Reproducible benchmark output is included under `reports/benchmark-results.json`.

## Implemented

- Typed domain model and normalized statuses.
- Normative workflow and fail-closed state machine.
- SQLite event store with hash chaining and JSONL export.
- Content-addressed artifact store with digest verification.
- Configuration snapshots and versioned schemas.
- Capability authorization and controlled process execution.
- Git/filesystem ChangeSet capture and digest-bound human decisions.
- Python and Node technology profiles.
- Deterministic and external-command agent providers.
- Validators, independent review, automatic/human gates and resumption.
- Deterministic memory selection, telemetry and retrospective.
- CLI, local API/web dashboard, JSON/Markdown/JSONL/SARIF reporting.
- Plugin protocol/SDK prototype, fixtures and security tests.

## Known limitations

- This is a research beta, not a production security boundary.
- OS/container-level sandboxing, network isolation and resource cgroups are not implemented.
- Provider-specific model session/token integrations are not bundled.
- Git write operations and cryptographically signed attestations are deferred.
- Generic production DAST, GraphRAG, distributed execution and multi-tenancy are deferred.
- Ruff and Mypy were not available in the execution environment, so their optional checks were not rerun for this delivery.
