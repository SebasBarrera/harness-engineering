# Changelog

## Unreleased

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
- The ChangeSet diff is a valid unified diff (#50). Every line of the stored diff was followed by an
  empty line, so the artifact could not be applied with `git apply` and viewers showed it wrongly.
  The diff now ends each line once, diffs an added file from `/dev/null` and a deleted one to it,
  and marks a last line without a newline with `\ No newline at end of file`; lines are split on
  newlines only, as Git does. A test applies a recorded diff with `git apply --check` and
  `git apply`. This is a defect fix and applies to every project, with or without new settings:
  the ChangeSet digest covers the diff, so a run recorded with this version has a different
  ChangeSet digest than the same change recorded by 1.0.0. Records written by earlier versions
  are read as they are and keep their digests.
- Agents, validators and the harness can no longer record a human decision (#45). `harness gate
  decide --actor agent.claude-code --decision APPROVE_EXCEPTION` exited with 0, was recorded as a
  `HUMAN` decision and closed the run; only `task clarify` refused those namespaces. Every human
  act (`gate decide` with any decision, `recommendation decide`, `memory approve`,
  `memory invalidate`, `memory add --approve`, `task clarify`) now refuses an actor id in
  `agent.*`, `validator.*` or `harness.*` (or one of those words alone) with exit code 5, and the
  decision endpoint of the local API with 403. This is a defect fix and applies to every project.
  A new optional `governance` section, written by `harness init`, adds: `deciderIdentity: git`,
  which records the Git user (`actorId` from `user.email`, `displayName` `Name <email>`) when no
  `--actor` is given (otherwise `human.local` and `human.web` as before); `confirmDecisionDigest`,
  which on a terminal shows the gate, the ChangeSet digest and its files and asks for the first 12
  characters of the digest before `gate decide` records anything (exit 5 on a wrong answer); and
  `trustedHosts` (`127.0.0.1`, `localhost`, `::1`), which makes the local API answer 400 to any
  other `Host` header. A `project.yaml` without the section keeps the 1.0.0 behaviour and its
  configuration snapshot digest. The `--actor` options default to none in the CLI reference.
- `harness verify` and a record that is checked against its events (#49). Editing an event payload
  made `harness status` exit with 1 (`EventChainError`) instead of reporting the broken chain;
  deleting the last events and rewriting the decider in the `records` projection made `status`
  show the forged decider with `eventChainValid: true`; `trace` exported without checking
  anything. `status` now reports `eventChainValid: false` and the reason in `eventChainError` (a
  defect fix, applied to every project). The new `harness verify [--run <id>]` walks the event
  chain, rebuilds every record that has an event of its own (decisions, gates, ChangeSets,
  validations, findings, evidence, tool and agent invocations, clarifications) and compares it
  with the stored projection, checks the run's pointers and phase results and every referenced
  artifact against its digest, and prints a JSON report (exit 0, or 6 when a check fails); it
  repairs nothing. Two new `governance` keys, written by `harness init`: `verifyRecords: true`
  makes `trace` (and the API trace route) verify first and refuse a run that does not verify
  (exit 6, API 409) and adds `recordsValid` to `status`; `chainAnchor: file | git-note | off`
  copies the head of each run's chain, after every command that appends events, to a file under
  the user's data directory (`$HARNESS_ANCHOR_DIR` overrides it) or to a Git note under
  `refs/notes/governed-harness`, so `verify` reports a truncated (`truncated`) or replaced
  (`rewritten`) chain. Without the keys, `trace` exports as in 1.0.0 and nothing is anchored.
- The task of a run no longer changes under it (#48). `harness task create` with the id of a task
  whose run waited in `DECISION` replaced the task silently, and every phase re-read it. Under the
  new `governance.pinTaskRevision` (written by `harness init`) a run stores the task revision it
  was created with (`taskDigest` and `taskRevisionRef` in `run.created`) and every phase works on
  it; only `task clarify` during `INTENT` replaces it. `task create` with the id of a task that has
  an open run exits with 5. `SPECIFICATION` freezes the acceptance-contract digest, and a decision
  records it as `acceptanceContractDigest` (new optional field of `human-decision.schema.json`, left
  out of decisions recorded without the setting) and is refused with 5 when the run's task no
  longer produces it. Without the setting the 1.0.0 behaviour and digests are kept.
- Writes outside the ChangeSet are detected and `.harness` and `.git` are protected from the agent
  (#46). With the sandbox on, an agent wrote `.git/hooks/pre-commit`, `venv/lib/dep.py` and
  `dist/payload.py`; the ChangeSet listed one file and the gate reached `DECISION` with two
  `MEDIUM` findings. Under the new `governance.protectExcludedPaths` (written by `harness init`)
  every file below `.git`, `.harness`, `.venv`, `venv`, `node_modules`, `dist` and `build` and
  every symbolic link is fingerprinted before and after the agent invocations of each
  `IMPLEMENTATION` attempt (recorded as evidence); a change is a `CRITICAL` finding
  `workspace.out-of-changeset-write` naming the paths, and each later gate of the run gets a
  failed mandatory validation `harness.workspace-guard`, so `APPROVE` exits with 5. The agent
  sandbox keeps `.harness` and `.git` read-only (`protectedPaths` in its evidence) and the
  profiles' write grants on `.harness/**` are dropped from the resolved capabilities. The
  harness's own state, Git's index, locks and objects are not watched. Without the setting the
  1.0.0 behaviour, sandbox profile and configuration digest are kept.
- One harness process per workspace and a safe recovery after a crash (#47). `SIGTERM` during
  `IMPLEMENTATION` left the phase `RUNNING` while the agent kept writing, and `run continue`
  implemented the change again on top of it; two concurrent `run start` on one workspace
  interfered and both failed. Under the new `governance.workspaceLease` (written by
  `harness init`) `run start`, `run continue`, `gate decide` and `task clarify` hold a lease,
  `.harness/lease.json` (pid, host, run, heartbeat every 10 s); another process exits with 5 and
  names the holder, and a lease whose process is gone (or whose heartbeat is older than 60 s) is
  taken over. While it is held, `SIGTERM` terminates the agent's process group, records the phase
  and the run as `INTERRUPTED` (new status value in the schemas) and exits with 143. The runner
  records the process groups it starts; `run continue` on a run a killed harness left marks a
  `RUNNING` phase `INTERRUPTED`, terminates the recorded groups that still run, restores the files
  an interrupted `IMPLEMENTATION` attempt changed to the snapshot taken when it started (a
  `run.recovered` event lists them) and runs the phase again. Without the setting the 1.0.0
  behaviour and configuration digest are kept.
- Configuration that no component read is applied or reported (#51). New `governance` keys,
  written by `harness init`: `applyWorkflowSettings` applies the workflow's per-phase
  `maxAttempts` (a phase with that many failed attempts is not started again; the run is
  `BLOCKED` with a `phase.attempts.exhausted` event) and `timeoutSeconds` (the agent and the
  validators get at most the time left of the attempt; an attempt that ends later is
  `TIMED_OUT`), and records `exitGate` and `exitGateMet` with every attempt;
  `decisionExpiryHours` (init 72) sets `HumanDecision.expiresAt`, and an expired decision leaves
  `DECISION` `BLOCKED`; `applyProfilePolicies` makes `missingTestCommand` and `missingTestScript`
  (`BLOCKED` or `FAILED`) the status of an unavailable mandatory validator and turns a project
  policy `coverage: {minimumPercent: N}` into a mandatory `python.coverage` validator;
  `applyNetworkPolicy` makes `runtime.allowNetwork: false` deny the agent outbound connections
  in the sandbox (Seatbelt rule or `bwrap --unshare-net`). `harness init` now writes
  `runtime.allowNetwork: true`, since agent CLIs call their model API. The new
  `harness gc [--apply]` applies `retention.artifactDays` (prunes the artifacts of runs that ended, with a
  `retention.artifacts.pruned` event that `verify` honours) and `retention.eventDays` (removes
  the run). The remaining declared settings (`workspace.units`, `runtime.maxParallel`, three
  policies, the workflow's `dependsOn`, `parallelizable`, `allowedCapabilities`, per-phase
  `validators` and `invariants`) are marked `x-declarative` in the schemas, and
  `harness config validate` lists them under `declarative` with `warnings` for the ones the
  project relies on. Without the keys the 1.0.0 behaviour and configuration digest are kept.
- An `integrity` flow in `scripts/demo_flows.py` checks the settings above end to end: an
  `agent.*` decision exits with 5 and is not recorded, `task create` on the task of an open run
  exits with 5, `verify` exits with 0, and after an event is edited `status` reports
  `eventChainValid: false` while `verify` and `trace` exit with 6. The script keeps the chain
  anchors in a temporary directory of its own (`HARNESS_ANCHOR_DIR`).

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
