# Changelog

## Unreleased

- Retrospective by cause, rule health and outcomes (#53). In the thesis evaluation the
  retrospective attributed 19 of 53 recommendations to the wrong cause (reported there, not
  re-measured here), counting optional validators without effect on the gate, and rejected runs
  got none. Under `retrospective.causal` (written by `harness init`) each
  retrospective records its trigger (`CLOSED`, `REJECTED`, `CANCELLED`, `ON_DEMAND`) and its
  causes by reason code (mandatory validator that stopped `VERIFICATION`, rule that failed a
  gate, requested changes, rejection, exception, transient provider failure, cancellation,
  outcome after the run), with subjects that are validators, rules or providers and never
  people; recommendations come from those causes, and rejected or cancelled runs get one when
  that happens. Without the key the retrospective keeps its 1.0.0 form (the new `trigger` and
  `causes` fields are left out). `harness rules health` shows per rule how often it fired,
  blocked, was excepted, led to corrections or rejections and fired in runs later linked to an
  outcome. `harness outcome record|list` links incidents, reverts, hotfixes and regressions to a
  run (new contract `outcome.schema.json`; 31 schemas).
- Located findings from validator output (#53). A failing pytest was one finding without a
  location; the failing test was three artifact hops away. Under `verification.outputParsers`
  (written by `harness init`) the output of a failing command validator is parsed into one
  finding per reported problem with path, line and the tool's rule: SARIF 2.1.0, ESLint and Ruff
  JSON, JUnit XML (printed or written with `--junitxml`) and the text of Ruff, Mypy, `tsc` and
  pytest. Errors keep the summary finding's severity, so the gate outcome does not change;
  warnings are `LOW`; at most 200 per run. Without the key only the summary finding is recorded.
  The SARIF export adds a stable `partialFingerprints` entry (`harnessFinding/v1`) and the ids of
  the finding, run and validator to every result.
- Pending-decision inbox and notifications (#53). Nothing told a person that a run was waiting
  for them. `harness inbox` and `GET /api/inbox` list the runs waiting for a decision or for
  clarification answers, oldest first, with the next command; the dashboard shows them and
  refreshes every 5 seconds. The optional `notifications.webhooks` setting (`url`, or `urlEnv` to
  keep a token out of `project.yaml` and of the configuration snapshot) receives a JSON `POST` on
  `decision.pending` (once per digest), `run.finished` (closed, rejected or cancelled) and, if
  listed, `exception.granted`, with identifiers, statuses and digests only; failures are retried
  with a bounded backoff and recorded as `notification` records (never the URL), outside the
  run's event chain, and never change the run. Without the setting nothing is sent or recorded.
- Exceptions with expiry, scope and follow-up (#53). Under the new `review.exceptions` setting
  (written by `harness init`), `APPROVE_EXCEPTION` sets the decision's `expiresAt` (it was always
  null) from `--expires-in`/`--expires-at` or `review.exceptionDays`, and records an exception
  (new contract `exception.schema.json`) with the person, rationale, run, gate, digest, scope,
  alternative evidence and follow-up, as `DECISION` evidence and an `exception.granted` event.
  The default scope is the gate's blocking findings by rule, path and fingerprint;
  `--scope RULE[:PATH]` widens it. While an exception is in force, a later run's gate does not count the
  findings it covers and says so (`EXCEPTION_APPLIED_<id>`); once it expires they block again,
  also for a run waiting in `DECISION`, and a run whose own exception expired before closing is
  `BLOCKED`. A failing mandatory validator is never covered. `harness exceptions list` and
  `GET /api/exceptions` are the ledger; the brief and the interactive decision show and ask for
  them. Without the setting `APPROVE_EXCEPTION` behaves as in 1.0.0 and the exception options are
  rejected (exit 2).
- Decision brief (#53). `harness review [--run latest] [--diff]` shows, for one ChangeSet digest,
  what was asked (intent, requirements, criteria, constraints), what changed (files and line
  counts), the gate with each reason explained, the current findings with `file:line` (blocking
  ones first), what was verified on which digest (latest attempt of each validator, requirement
  to test mapping) and what was not (validators that did not run, optional failures without
  effect on the gate, untraced requirements), retries and corrections (attempts, automatic and
  requested corrections, provider retries, validators that passed only after failing on the same
  digest, superseded findings), what changed since the last decision (files and findings by
  fingerprint) and the exact decide command. It is built from the record by fixed rules and
  writes nothing; `GET /api/runs/{run}/review` serves it and the dashboard renders it. `--run`
  accepts `latest` and a unique prefix in every command, and the read-only commands default to
  `latest`. `harness artifact show <ref|digest prefix> [--describe]` prints a stored artifact after
  verifying its digest. On a terminal `harness gate decide` without `--decision`,
  `--change-set-digest` or `--rationale` shows the brief, asks for them and requires typing the
  first 12 characters of the digest; a wrong confirmation records nothing (exit 5). Without a
  terminal the options are still required (exit 2). A rejected decision explains the way out
  (the current digest, the phase, the decisions a failed gate admits). The dashboard gains
  `REQUEST_CHANGES` and a confirmation with the digest and the files, and escapes the values it
  renders.
- Onboarding (#53). `harness --version` prints the version (it was `No such option`, exit 2). On
  a terminal every command prints readable text; JSON stays the default when standard output is
  not a terminal, `--json` forces it and `--no-json` forces text (it printed a Python `repr`),
  both per command and before the command name. `harness init` reports the detected profiles and
  the next commands, adds `.harness/` to `.gitignore` (`--no-gitignore` skips it) and writes
  `.harness/task.example.yaml` (`--no-example-task` skips it); the Python API `init` does neither
  unless asked. `harness doctor` also reports the Git identity, the baseline commit, whether
  `.harness/` is ignored, the agent CLI of the configured provider on `PATH`, the agent sandbox
  mechanism and every validator's command, each with a fix; a missing default agent CLI, a
  missing mandatory validator or a missing sandbox mechanism for a command provider fail it
  (exit 2), the rest are `WARNING`s. Errors keep their text and exit code and gain a `hint`
  (JSON) or `Hint:` line (terminal) with the command that fixes them. The README documents a
  pipx install from the release wheel (not exercised in CI).
- `INTENT` checks that acceptance criteria can be verified (#32). A task whose only criterion was
  "It works." passed `INTENT` and `SPECIFICATION` and was approved. A deterministic assessment now
  raises clarification questions with stable ids for a criterion without an observable result
  (`C1`), a quality without a measure (`C2`), a duplicate criterion (`C3`) and a short intent with
  no requirements and a single criterion that has nothing concrete to check (`T1`). The new `intake.criteriaPolicy` setting decides what
  happens: `enforce` (written by `harness init`) blocks `INTENT` (`run start` exits with 6),
  `warn` records the questions as evidence and `LOW` findings and lets the run continue, `off`
  skips the check. A `project.yaml` without the key runs with `warn` and keeps its configuration
  snapshot digest. `harness task questions` shows the open questions and `harness task clarify`
  records a person's answers, optional criterion and requirement changes, the previous and new
  task digest and the new task revision on the run's event chain; `harness run continue`
  re-assesses the revision. No phase was added. New contracts:
  `clarification-request.schema.json` and `clarification-record.schema.json`. A clarification
  flow was added to `scripts/demo_flows.py`.
- `INTENT` elicits the acceptance criteria of a task that has none (#33). A one-sentence task was
  refused by `harness task create`, so it never reached the clarification questions. Under
  `intake.criteriaPolicy: enforce` the task is now accepted and stored with
  `criteriaPending: true`; under `warn`, `off` or without the setting it is still refused with
  exit code 2 and the same message. The task model allows no criteria only together with that
  marker, which only the harness sets and which is left out of tasks with criteria, so their
  stored form and digest do not change. The new rule `C0` asks seven separate questions (results
  and how each is checked, inputs and outputs, limits, errors, behaviours in scope, out of scope,
  non-functional constraints) instead of the other rules, and a task without criteria never
  passes `INTENT` under any policy (exit 6). `harness task clarify` turns the answer about results
  into acceptance criteria, one per line (or takes `addCriteria`), the scope and other answers
  into requirements and the out-of-scope answer into constraints; a revision still without
  criteria is asked again. The task, clarification-request and clarification-record schemas gain
  `criteriaPending` and the rule id `C0`. The clarification demo flow now also runs a
  one-sentence task.
- Agent providers run write-confined (#34). In an evaluation an agent CLI wrote files outside its
  workspace: the harness's path containment covers its own file handling, not the agent process.
  The new `runtime.agentSandbox: enforce | off` wraps every command-provider invocation in
  `IMPLEMENTATION`, including those after `REQUEST_CHANGES`, in `sandbox-exec` (macOS) or `bwrap`
  (Linux) so that writes outside the workspace, `$TMPDIR` and `runtime.sandboxWritePaths` fail;
  reads, network and process execution stay allowed. `harness init` writes `enforce` with default
  write paths for agent CLIs (`/tmp`, `/var/folders`, `~/.claude`, `~/.claude.json*`, `~/.cache`,
  `~/Library/Caches`, `~/.config`, `~/.npm`). The profile digest and the allowed paths are recorded
  as `IMPLEMENTATION` evidence and an `agent.sandbox.applied` event; a host without a mechanism
  blocks `IMPLEMENTATION` with a `sandbox.unavailable` finding (`run start` exits with 6), and a
  provider that fails on a denied write gets a `sandbox.write-denied` finding. A `project.yaml`
  without the key runs with `off` and keeps its configuration snapshot digest. The simulated
  provider and the validators are not wrapped. The usage flow of `scripts/demo_flows.py` sets
  `agentSandbox: 'off'`, since it runs on hosts without a mechanism.
- `VERIFICATION` reports requirements that no test names (#35). Agents claimed that every
  requirement had tests, and the suite ran without relating requirements to tests. A new step,
  `traceability.requirements`, runs after the technology validators: it identifies a requirement by
  the token that starts its text (`A1.`, `[B12]` or `X8:` followed by a space) or by a `requirementId` written in the
  task file (generated ids do not count; requirements without an identifier are skipped and
  counted), and looks for a test whose file, class or function name contains the identifier as a
  token (`test_a1_...`, `TestA1`) or whose docstring, string constants or source contain it as a
  whole word (Python tests read with `ast`, Node tests and unparsable files as text). The new
  `verification.requirementTraceability` setting decides what an untraced requirement means:
  `enforce` (written by `harness init`) records a `HIGH` finding
  `traceability.requirement-untested`, so the gate is `FAILED` and `APPROVE` exits with 5; `warn`
  records a `LOW` finding; `off` skips the check. A `project.yaml` without the key runs with `off`
  and keeps its configuration snapshot digest. The requirement-to-test mapping is `VERIFICATION`
  evidence; new contract: `requirement-traceability.schema.json`. Because `init` enables the
  check, the tasks of the quickstart (`docs/guides/task.yaml`), `examples/task-python.yaml` and the
  demonstration flows name their requirement in the added test (`test_req_discount_at_threshold`,
  a `req_precedence:` test title), the brownfield example identifies its requirement as
  `base64_decode_rejects_non_ascii` (its patch is unchanged), and a traceability flow was added to
  `scripts/demo_flows.py`.
- A feedback loop around command providers (#36). A failed `VERIFICATION` stopped the run and a
  `REQUEST_CHANGES` decision sent the agent the same request again, so the agent never learned
  why its attempt was not accepted, an agent that reported success on a failing change left no
  trace of the gap, and a provider call that failed on an overloaded or rate-limited service
  stopped the run. New optional `runtime` settings, written by `harness init`, change that for
  command providers (the simulated provider is not affected): `verificationCorrections` (init 2)
  returns a change whose mandatory validator failed to `IMPLEMENTATION` up to N times per run,
  each cycle a `correction.authorized` event with `trigger: VERIFICATION_FAILED` that invalidates
  the failed verification, and stops as before when they are used up (`correction.exhausted`);
  `providerFeedback` (init true) adds a bounded `feedback` block to the next request (gate or
  verification status and reason codes, up to 20 findings, the last 4,000 characters of each
  failing validator stream with 16,000 in all, the decision rationale and the attempt number;
  new contract `provider-feedback.schema.json`); the same loop records an
  `agent.unsupported-claim` finding (severity `unsupportedClaimSeverity`, default `MEDIUM`) when
  the agent answered `PASSED` and verification failed; `providerRetries` (init 3) and
  `providerRetryDelaySeconds` (init 60) repeat a call whose stderr or JSON result matches
  `providerTransientPatterns` (default: timed out, connection reset, went to sleep, overloaded,
  429, 529, rate limit, usage limit), never a process killed at its timeout, each repetition an
  `agent.invocation.retried` event with evidence. Under `runtime.agentSandbox: enforce` a
  correction attempt and a repeated call run in the same sandbox as the first call. New metrics `correction.verification_cycles`,
  `agent.unsupported_claims` and `agent.transient_retries`; `correction.cycles` counts both kinds
  of correction and the retrospective keeps reporting the human-authorized ones. A
  `project.yaml` without the settings behaves as before, sends the same provider request and
  keeps its configuration snapshot digest. `harness config validate` shows the effective values
  under `feedbackLoop`.

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
