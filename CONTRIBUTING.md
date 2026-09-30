# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,api]'
make schemas
make test
```

## Architectural rules

1. Domain and orchestration code must not import technology profiles, package managers, web frameworks, provider SDKs or CI vendors.
2. New stacks are added through profiles, validators or adapters; adding one must not modify core state semantics.
3. Every privileged operation requires an actor and capability grant.
4. Every externally visible record must have a versioned model/schema and provenance where applicable.
5. Mandatory non-success states fail closed.
6. Validators produce results; only the core gate engine decides the global status.
7. An agent/provider cannot approve its own output.
8. Human approvals are always digest-bound and become stale after changes.
9. Retrospective logic may propose but never apply governance changes.
10. Security claims must distinguish application-level authorization from OS-level enforcement.

## Required change evidence

A pull request should include:

- rationale and affected invariant or extension contract;
- tests for the success path and at least one failure/blocking path;
- schema regeneration when a public model changes;
- documentation for new commands, profiles or security assumptions;
- benchmark comparison when changing event, artifact, process or gate hot paths.

## Commands

```bash
make test-fast
make test
make coverage
make benchmark
make benchmark-scenarios
make build
```

`ruff` and `mypy` are optional technology-profile validators in the runtime, but contributors should run them when available.

## Schema changes

Edit the typed boundary model, then run:

```bash
PYTHONPATH=src python scripts/generate_schemas.py
git diff -- schemas/v1 src/governed_harness/resources/schemas/v1
PYTHONPATH=src pytest tests/contract
```

Breaking changes require a new schema/protocol major version rather than silently changing historical meaning.
