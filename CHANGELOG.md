# Changelog

## 0.8.1 - 2026-09-30

Repository infrastructure, documentation and quality release. No change to the behavior evaluated
in the thesis on 0.8.0; behavioral defects stay tracked in the milestone *backlog — thesis-impact*.

- Quality without behavior change: `py.typed` marker, explicit re-exports, Ruff and mypy
  `--strict` with no findings, `ruff format`, help texts for every CLI command, `--path` defaults
  resolved at invocation time, `python -m build` instead of `setup.py`.
- Tests: fixture repositories isolated from the developer's Git configuration; the fake token of
  the redaction test built at runtime; `scripts/demo_flows.py` checks the documented flows and exit
  codes.
- CI/CD: hardened test, build, lint, security, CodeQL, Scorecard, dependency review, Docker,
  SonarQube Cloud, benchmarks, docs-smoke, Pages and release workflows; actions pinned by SHA;
  releases with an SPDX SBOM and build-provenance attestations.
- Container image: multi-stage, pinned base image, non-root user, published to GHCR on release tags.
- Documentation: new README (English and Spanish), MkDocs site with greenfield, brownfield, CI and
  external-agent guides, generated CLI and task-file references, configuration, API and exit-code
  references, metrics, monitoring, benchmarks, provenance and ADR 0006; HTML reports for coverage,
  benchmarks and process metrics.
- Removed from the tree (kept in the v0.8.0 tag and release): the stale `web/` prototype,
  `reports/`, `MANIFEST.sha256` and `FILE_INVENTORY.md`.

## 0.8.0 - 2026-08-31

Research-beta implementation of the governed agent harness:

- complete nine-phase normative run engine;
- digest-bound human decisions and stale-approval invalidation;
- SQLite event chain and typed projections;
- content-addressed, redacted artifact store;
- deny-by-default capabilities and controlled process execution;
- Python and Node.js technology profiles;
- simulated and structured-command agent providers;
- deterministic independent review and fail-closed gates;
- deterministic memory selection, telemetry and non-mutating retrospective;
- CLI, local FastAPI dashboard, JSON/Markdown/JSONL/SARIF reporting;
- external plugin protocol and SDK echo implementation;
- schema generation, contract tests, security tests, end-to-end fixtures and benchmarks.
