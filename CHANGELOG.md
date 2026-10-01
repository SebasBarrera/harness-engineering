# Changelog

## 1.0.0 - 2026-10-01

Additions that close gaps between the design and the prototype. The run path evaluated on 0.9.0 is
unchanged: with no memory records, the request sent to the agent provider is identical.

- Governed memory is operable (#6). `harness memory add | list | approve | invalidate` record,
  approve and invalidate entries with the acting person; an approval or an invalidation is a new
  record that supersedes the previous one, so nothing is edited in place. The context manifest
  built in `PLANNING` lists the records that entered the context and, under `excluded`, every
  candidate that did not, with its reason (`superseded`, `expired`, `unapproved`, `limit`). An
  unapproved record no longer supersedes an approved one, and supersession no longer depends on
  the order of the records. The value of a record marked as sensitive is withheld from the
  manifest, and the provenance of each record uses the camelCase names of the public contracts
  (`actorId`, `coreVersion`). The selected records reach command agent providers under `context`,
  and each agent invocation references its manifest (`contextManifestRef`).
  `harness memory manifest --run` shows the manifest recorded for a run.
- Retrospective recommendations can be decided. `harness recommendation list | decide` records
  `ACCEPT`, `EDIT` or `REJECT` with the acting person and a rationale. The decision is kept as
  retrospective memory: an accepted or edited recommendation is approved and enters the context
  of later runs; a rejected one stays as history and never enters a context. A recommendation
  takes one decision (a second one exits with 5). Rules, gates and configuration are still
  changed only by a person: `retrospectiveAutoApply` remains a locked `false` policy.
- Command agent providers can report their usage. An optional `usage` object in the response
  (`inputTokens`, `outputTokens`, `reasoningTokens`, `costUsd`) is stored as a `ResourceUsage`
  record of quality `REPORTED`, referenced from the agent invocation (`usageRef`) and summed into
  the metrics `tokens.*` and `cost.usd`. Without it the metrics stay `NOT_AVAILABLE`; a malformed
  report is a protocol error. Nothing is estimated.
- Evaluation data: a delivery counts as "without new tests" when no changed file is named like a
  pytest module, wherever it is. The previous count only looked under `tests/`, so it missed two
  runs that wrote `test_shipping.py` at the repository root and counted one whose only file under
  `tests/` was an empty `__init__.py`: the one-line prompt without the harness delivered 7 of 27
  runs without tests (not 9) and the minimal task 11 of 27 (not 10). The per-run records are
  unchanged. The core coverage of the v0.8.0 cut is labeled correctly: 80.60 % of lines and
  59.29 % of branches; 77.48 % is the combined figure.
- `harness benchmark scenarios` no longer depends on the user's Git configuration: the fixture
  repository is created with commit signing and hooks disabled, so a global `commit.gpgsign` does
  not make the baseline commit fail.

## 0.9.0 - 2026-09-30

Behavioral fixes. `v0.8.0` keeps the behavior evaluated in the thesis; every fix below has a
regression test that failed before the change.

- The gate counts the latest attempt of each validator on the current ChangeSet digest (#2).
  Earlier attempts stay in the record. In the brownfield case (`pallets/itsdangerous` 2.2.0),
  repairing the baseline and running `run continue` now leaves the gate `PASSED` and a plain
  `APPROVE` closes the run; on 0.8.0 the gate stayed `FAILED` and the case closed with
  `APPROVE_EXCEPTION`. Exit codes of `run start` (6) and `run continue` (4), the 298 tests, the
  ChangeSet digest and the 46 events are unchanged.
- A validator run as `<python> -m <module>` is `BLOCKED` when mandatory, or `NOT_APPLICABLE` when
  optional, if the module is not installed for that interpreter, instead of `FAILED` (#1).
- Process output is bounded while it is read, so memory no longer grows with the output of a
  validator or agent (#9).
- Absolute paths reached through a symlinked ancestor of the workspace (for example the macOS
  `/var` → `/private/var` temporary directory) are accepted; escapes are still rejected (#19).
- Task files without a non-empty `title` or `intent`, or with unknown fields, are rejected with
  exit code 2 (#29).
- `gate decide` prints camelCase keys like every other command (#30).
- Windows: artifacts are written without `os.fchmod`, and commands such as `npm` (`npm.cmd`) are
  resolved through `PATHEXT` after authorization. The test suite and the documented flows run on
  Windows in CI (#31).
- `scripts/demo_flows.py` adds the review-exception flow (a hardcoded secret, `APPROVE` refused
  with 5, `APPROVE_EXCEPTION` accepted); the broken-baseline flow now closes with `APPROVE`.

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
