# Project configuration

`harness init` writes `.harness/project.yaml`. The file is validated against
`schemas/v1/project-config.schema.json` (unknown keys are rejected) and resolved together with the
technology profiles and the locked core policies into an immutable snapshot whose digests are
recorded on every run. `harness config validate` prints the resolved view.

## File written by `harness init`

This is the file produced for a new Python project (output of `harness init`, unchanged):

```yaml
configVersion: '1.0'
projectId: project_quickstart
workspace:
  root: ..
  units: []
profiles:
- auto
workflow: default_development
capabilities:
  default: deny
  grants: []
validators: []
policies:
  requireHumanDecision: true
  findingBlockSeverities:
  - HIGH
  - CRITICAL
agentProvider: simulated
runtime:
  commandTimeoutSeconds: 900
  maxOutputBytes: 1000000
  maxParallel: 2
  allowNetwork: false
  agentSandbox: enforce
  sandboxWritePaths:
  - /tmp
  - /var/folders
  - ~/.claude
  - ~/.claude.json*
  - ~/.cache
  - ~/Library/Caches
  - ~/.config
  - ~/.npm
  verificationCorrections: 2
  providerFeedback: true
  unsupportedClaimSeverity: MEDIUM
  providerRetries: 3
  providerRetryDelaySeconds: 60
retention:
  artifactDays: 30
  eventDays: 365
intake:
  criteriaPolicy: enforce
verification:
  requirementTraceability: enforce
```

## Fields

| Field | Default | Effect |
|---|---|---|
| `configVersion` | required, `"1.0"` | Configuration format version. |
| `projectId` | required | Identifier used on tasks and runs. `init` derives it from the directory name. |
| `workspace.root` | `..` | Workspace root, relative to `.harness/`. |
| `workspace.units` | `[]` | Declared units (`unitId`, `root`, `profile`). **Declarative: not used by the engine.** |
| `profiles` | `["auto"]` | `auto` selects the detected profiles; otherwise list profile ids (`python_default`, `node_default`). |
| `workflow` | `default_development` | The normative workflow. Only the built-in workflow exists; its phase order is fixed (see issue #3). |
| `capabilities.default` | `deny` | Only `deny` is accepted. |
| `capabilities.grants` | `[]` | Extra grants: `capability`, `scope` (globs or command names), `approvalRequired`, `conditions` (for example `allowDelete`). They are **added** to the profile grants (issue #4). |
| `validators` | `[]` | Validator ids to run; empty means the profile defaults. An id not defined by a selected profile is a configuration error. |
| `policies` | see below | Policy overrides; locked policies can only be restated with their locked value. |
| `agentProvider` | `simulated` | Default provider for `run start` (`--provider` overrides it). |
| `agentProviders` | `{}` | Named command providers: `kind: command`, `command` (argv list, not empty), optional `model`. See [external agents](../guides/external-agents.md). |
| `runtime.commandTimeoutSeconds` | `900` | Timeout of agent-provider processes. Validators use their own `timeoutSeconds` from the profile. |
| `runtime.maxOutputBytes` | `1000000` | Bound on captured stdout/stderr per process (applied after capture, issue #9). |
| `runtime.maxParallel` | `2` | **Declarative: not used by the engine** (phases run sequentially). |
| `runtime.allowNetwork` | `false` | **Declarative: not enforced.** The local runner is not a network sandbox (issue #5). |
| `runtime.agentSandbox` | `off` when the key is absent; `init` writes `enforce` | Write confinement of command-provider (agent) processes: `enforce` or `off`. See [agent sandbox](#agent-sandbox). |
| `runtime.sandboxWritePaths` | none when absent; `init` writes the list above | Paths the agent may write besides the workspace and `$TMPDIR`: absolute or starting with `~/`, no `$` variables, an optional single trailing `*` for a name prefix. |
| `runtime.verificationCorrections` | `0` when absent; `init` writes `2` | Automatic corrections (0 to 10) after a failed `VERIFICATION` of a command provider's change. Its presence, with any value, also turns on the unsupported-claim check. See [provider feedback loop](#provider-feedback-loop). |
| `runtime.providerFeedback` | `false` when absent; `init` writes `true` | Send a `feedback` block to the command provider on the attempt that follows a failed `VERIFICATION` or a `REQUEST_CHANGES` decision. |
| `runtime.unsupportedClaimSeverity` | `MEDIUM` | Severity of the `agent.unsupported-claim` finding (`INFO` to `CRITICAL`). |
| `runtime.providerRetries` | `0` when absent; `init` writes `3` | Repetitions (0 to 10) of a command-provider call that failed with a transient cause. |
| `runtime.providerRetryDelaySeconds` | `0` when absent; `init` writes `60` | Wait before each repetition (0 to 3600 seconds). |
| `runtime.providerTransientPatterns` | the default list below | Case-insensitive texts that mark a failed call as transient. |
| `retention` | written by `init` | **Declarative: no retention job exists.** |
| `intake.criteriaPolicy` | `warn` when the section is absent; `init` writes `enforce` | What INTENT does with acceptance criteria that cannot be observed: `enforce`, `warn` or `off`. Only `enforce` accepts a task without acceptance criteria. See [acceptance-criteria policy](#acceptance-criteria-policy). |
| `verification.requirementTraceability` | `off` when the section or the key is absent; `init` writes `enforce` | What VERIFICATION does with identified requirements that no test names: `enforce`, `warn` or `off`. See [requirement traceability](#requirement-traceability). |
| `verification.outputParsers` | `false` when the key is absent; `init` writes `true` | Parse the output of a failing command validator into one finding per reported problem, with path, line and the tool's rule. See [located findings](#located-findings). |
| `review.exceptions` | `false` when the section or the key is absent; `init` writes `true` | `APPROVE_EXCEPTION` records an exception with an expiry, a scope, optional alternative evidence and a follow-up; while it is in force later runs do not block on the findings it covers. See [exceptions](#exceptions). |
| `review.exceptionDays` | `30`; `init` writes `30` | Validity of an exception when the decision sets none (1 to 365 days). |
| `retrospective.causal` | `false` when the section or the key is absent; `init` writes `true` | Retrospective by cause, also for rejected and cancelled runs. See [retrospective by cause](#retrospective-by-cause). |
| `notifications.webhooks` | none when absent; `init` writes none | URLs notified when a run waits for a decision, finishes or gets an exception. See [notifications](#notifications). |

## Located findings

Without `verification.outputParsers` a failing command validator records one finding
(`<validator>.failed`, `HIGH` when mandatory, `MEDIUM` when optional) without a location, as in
1.0.0. With `outputParsers: true` it also records one finding per problem the tool reported,
with rule `<validator>.<tool rule>` (for example `python.ruff.F401`, `python.mypy.return-value`,
`python.pytest.test-failed`), the path relative to the workspace and the line. The formats are
recognized by content: SARIF 2.1.0, ESLint JSON, Ruff JSON, JUnit XML (printed, or written to a
file named with `--junitxml`, `--junit-xml`, `--output-file` or `-o` inside the workspace), and
the text output of Ruff (concise and full), Mypy, TypeScript `tsc` and pytest (`FAILED`/`ERROR`
summary lines, with the line of the test taken from the traceback). Errors keep the severity of
the summary finding, so the gate status is the one the summary already decides; warnings are
`LOW` and notes are not recorded. At most 200 findings are kept per validator run, and an `INFO`
finding counts the rest. A validator that passes is not parsed. The SARIF export
(`harness trace --format sarif`) carries a `partialFingerprints` entry `harnessFinding/v1` per
result (rule, validator, path and message without positions, stable across attempts and runs)
and the finding, run and validator ids in `properties`.

## Exceptions

Without `review.exceptions` an `APPROVE_EXCEPTION` decision is what it was in 1.0.0: a decision
with a rationale that closes the run, with no expiry and no effect on later runs. With
`review.exceptions: true`:

- The decision carries an expiry: `harness gate decide ... --decision APPROVE_EXCEPTION
  --expires-in 14d` (also `36h`, `2w`), `--expires-at <ISO 8601>` or, with neither,
  `review.exceptionDays`. An expiry must be in the future and at most 365 days away.
- An exception record (schema `exception.schema.json`) is stored with the person, the rationale,
  the decision, the gate and the ChangeSet digest it was granted on, the scope, the
  `--alternative-evidence` and `--follow-up` texts and its provenance; it is recorded as
  `DECISION` evidence and as an `exception.granted` event on the run's chain, before the run
  closes. The harness records the alternative evidence and the follow-up, it does not check them.
- The scope is, by default, every blocking finding of the gate, each by rule, path and
  fingerprint (rule, validator, path and message, without line numbers), so only the same problem
  is covered. `--scope RULE` or `--scope RULE:PATH` (repeatable) widens it to a rule, optionally
  in one file.
- While the exception is in force, the gate of a later run of the project does not count the
  findings it covers: they stay in the record and the gate lists them as inputs together with
  the reason code `EXCEPTION_APPLIED_<exceptionId>`. A mandatory validator that does not pass
  still fails the gate: an exception covers findings, not failing tests.
- Once it expires the findings block again, also for a run already waiting in `DECISION` (its
  gate is evaluated again when the run continues), and a run whose own `APPROVE_EXCEPTION` expired
  before it closed is `BLOCKED` until a new decision.

`harness exceptions list [--status active|expired] [--expiring-within DAYS]` is the ledger: who
granted each exception, on which run and digest, its scope, evidence and follow-up, the days left
and the runs whose gate relied on it. `harness review` shows the exceptions granted in a run or
relied on by its gate. Interactive decisions ask for the expiry, the alternative evidence and
the follow-up.

## Retrospective by cause

Without `retrospective.causal` the retrospective is the 1.0.0 one: generated at `CLOSURE` (or by
`harness retrospect`), with recommendations from run-level counts, including failures of optional
validators that had no effect on the gate. With `causal: true`:

- Each retrospective records the `trigger` (`CLOSED`, `REJECTED`, `CANCELLED`, `ON_DEMAND`) and
  its `causes`: reason code, subject (a validator, a rule or a provider, never a person), phase,
  effect, occurrences, attempts and evidence references.
- The reason codes are `MANDATORY_VALIDATOR_<STATUS>` (a mandatory validator that stopped
  `VERIFICATION`), `BLOCKING_FINDING` (a rule that failed a delivery gate),
  `CHANGES_REQUESTED`, `REJECTED`, `EXCEPTION_APPROVED`, `EXCEPTION_GRANTED` (per excepted
  rule), `PROVIDER_TRANSIENT_FAILURE`, `RUN_CANCELLED` and `POST_RUN_<KIND>` for outcomes
  recorded with `harness outcome record`. Optional validators that did not pass are named in an
  observation and are not causes.
- Recommendations come from the causes (one per validator, rule or decision that redirected the
  run); a rule that failed the gate and was excepted is flagged for its precision.
- A rejected or cancelled run gets its retrospective when the decision or the cancellation is
  recorded (stored as a record; the run's event chain is not extended).

Nothing is applied automatically, as before. `harness rules health [--since DAYS]` reads every
run of the project and shows, per rule, how often it fired, blocked a gate, was excepted (and how
often its exceptions were relied on), fired on a ChangeSet that was later corrected, fired in a
rejected run or in a run later linked to an outcome, with a fixed-rule signal (`often excepted
when it blocks`, `fired in runs later linked to an outcome`, `led to corrections`), and per
validator how many results did not pass. It needs no setting and writes nothing.
`harness outcome record --run R --kind INCIDENT|REVERT|HOTFIX|REGRESSION|OTHER --summary ...
[--reference ...] [--observed-at ...]` links what happened after a run to it (schema
`outcome.schema.json`; an actor id of an agent, validator or the harness exits with 5);
`harness outcome list` shows them.

## Notifications

`harness inbox` (and `GET /api/inbox`, the dashboard's left column) lists the runs that wait for
a person: a decision in `DECISION` (gate status, digest, blocking findings) or answers to
clarification questions in `INTENT`, oldest first. It needs no configuration. The dashboard
refreshes the inbox, the runs and the selected run every 5 seconds.

Webhooks are opt-in. `harness init` writes none, because a URL is needed:

```yaml
notifications:
  webhooks:
    - urlEnv: HARNESS_SLACK_WEBHOOK   # or url: https://hooks.example.invalid/...
      events: [decision.pending, run.finished, exception.granted]
      retries: 2                      # default 2 (0 to 10)
      timeoutSeconds: 5               # default 5
```

Each webhook needs exactly one of `url` (an `http://` or `https://` URL written in the file, so it
enters the configuration snapshot) or `urlEnv` (the name of an environment variable read when the
notification is sent, so a URL that carries a token stays out of the file and of the snapshot).
`events` defaults to `decision.pending` and `run.finished`.

| Event | Sent when |
|---|---|
| `decision.pending` | A run stops in `DECISION` waiting for a person, once per ChangeSet digest. |
| `run.finished` | A run closes (`PASSED`), is rejected or is cancelled, once per run. |
| `exception.granted` | An exception is recorded under `review.exceptions`. |

The harness sends a JSON `POST` with `event`, `occurredAt`, `harnessVersion`, `projectId`,
`executionId`, `taskId`, `taskTitle`, `status`, `currentPhase`, `changeSetDigest`, `gateStatus`
and `next` (the `harness review` command); `exception.granted` adds `exceptionId` and `expiresAt`.
It never sends a rationale, a validator output, the URL or an environment value. A response
outside 2xx or a network error is retried with a growing wait (0.5 s, 1 s, 2 s, at most 5 s). The
outcome of every notification (delivered or failed, attempts, HTTP status, webhook index; never
the URL) is stored as a `notification` record outside the run's event chain. A failed
notification never changes the run or the exit code of the command.

## Policies

Resolution order: core policies, then profile policies, then project policies.

| Policy | Core value | Project may change it? |
|---|---|---|
| `requireHumanDecision` | `true` | **No** (locked `true`) |
| `approvalDigestBinding` | `true` | **No** (locked `true`) |
| `mandatoryNonSuccessBlocks` | `true` | **No** (locked `true`) |
| `retrospectiveAutoApply` | `false` | **No** (locked `false`) |
| `findingBlockSeverities` | `["HIGH", "CRITICAL"]` | Yes: severities whose findings make the gate `FAILED` |
| `allowEmptyChangeSet` | not set (false) | Yes: allow a run whose implementation produced no change |
| `repositoryContentTrusted` | `false` | Declared; **not read by any component** (issue #5) |
| `destructiveActionsDefault` | `deny` | Declared; **not read by any component** (issue #5) |

Setting a locked policy to any other value fails with a configuration error (exit code 2), for
example `project configuration may not weaken locked policy requireHumanDecision`.

## Acceptance-criteria policy

INTENT runs a deterministic assessment of the task (no language model) and turns what it finds
into clarification questions with stable ids (`Q-1`, `Q-2`, ...):

| Rule | Asks when |
|---|---|
| `C0` no acceptance criteria | The task has no acceptance criteria (accepted only under `enforce`, see [tasks without acceptance criteria](#tasks-without-acceptance-criteria)). Seven questions, each with its own id, and no other rule: `C0` also asks what `T1` would. |
| `C1` no observable result | A criterion has fewer than four words, or only vague words ("works", "correctly", "properly", "as expected", "good", "fine", "nice", "clean", "robust", "user-friendly", "well"), and no anchor: a digit, quoted or back-quoted text, a code identifier (`name()`, `snake_case`, a path, a file name, a `CamelCase` name such as `ValueError`) or a checkable result verb (returns, raises, rejects, accepts, equals, contains, lists, stores, prints, exits, responds, creates, deletes, matches, passes, fails, at most, at least, within, before, after). |
| `C2` quality without a measure | A criterion says "fast", "quick", "performant", "efficient", "scalable", "secure", "reliable" or "responsive" without a number. |
| `C3` duplicate | Two criteria have the same text once case, spacing and trailing punctuation are folded. |
| `T1` scope without breakdown | The intent has fewer than 25 words, there are no requirements and there is exactly one criterion, and that criterion has no anchor (as in `C1`). It is asked next to a `C1` or `C2` question about the same criterion, because it asks for the scope rather than the result. |

A criterion's `verificationHint` counts as part of what it says can be observed. What happens
with the questions depends on `intake.criteriaPolicy`:

| Policy | INTENT with questions |
|---|---|
| `enforce` | `BLOCKED` with `Intent needs clarification: N question(s)`; `run start` and `run continue` exit with 6. |
| `warn` | `PASSED`; each question is also recorded as a `LOW` finding of `intake.clarification`, which the gate does not count. |
| `off` | No assessment: INTENT behaves as in 1.0.0. |

With `enforce` and `warn` the questions are stored as a `clarification-request` artifact
(INTENT evidence, schema `clarification-request.schema.json`) and an
`intent.clarification.requested` event. A task without questions is not affected by any policy.
A task without acceptance criteria is the exception: INTENT blocks it with the `C0` questions
under every policy, and records the request with policy `enforce`.

A `project.yaml` written before this section existed has no `intake` key and runs with `warn`:
the run continues as before and only the evidence and findings are added. Its configuration
snapshot is serialized without the section, so its digest does not change.

`harness task questions --task T` shows the open request. A person answers it with
`harness task clarify --task T --file answers.yaml [--actor human.id]`:

```yaml
answers:                      # required: question id -> answer
  Q-1: apply_discount(100, 100, 0.1) returns 90.
  Q-2: Only the threshold rule; rounding is out of scope.
replaceCriteria:              # optional: replace a criterion by its id
  - criterionId: ac_works
    text: A subtotal equal to the threshold is reduced by the rate.
addCriteria:                  # optional
  - A subtotal below the threshold is unchanged.
addRequirements:              # optional; stored with source "clarification"
  - Apply the discount only at or above the threshold.
```

The harness stores a new revision of the task and a clarification record (schema
`clarification-record.schema.json`): the human actor, each question with its answer, the
previous and the new task digest and both task revisions as artifacts, recorded as evidence and
as an `intent.clarified` event on the run that asked. An answer about a criterion that the file
does not replace becomes (or extends) that criterion's `verificationHint`; an answer about the
task, when the file adds no requirement or criterion, becomes a requirement with source
`clarification`. `harness run continue` then assesses the revised task in INTENT.

`task clarify` rejects an unknown question id, an empty answer or an unknown field (exit 2), a
task without an open request (exit 3), and a task that has a run past INTENT or an actor id in a
namespace the harness uses for agents, validators or itself (`agent.`, `validator.`, `harness.`)
(exit 5). Actor ids are recorded, not authenticated.

### Tasks without acceptance criteria

`harness task create` refuses a task without acceptance criteria (exit 2, `at least one
acceptance criterion is required`) under `warn`, `off` and a `project.yaml` without the
`intake` section. Under `enforce` it accepts the task and stores it with
`criteriaPending: true`, so a task can start from a single sentence and get its criteria in
INTENT. Only the harness sets the marker (a task file that contains it is refused as having an
unknown field), and the task model accepts no criteria only together with it.

INTENT then asks rule `C0`: one question per thing the criteria must settle, in this order, so
that a person can answer them one by one. Each names the task title.

| Id | Target | Asks for |
|---|---|---|
| `Q-1` | `task:results` | The observable results that show the task is done and how each is checked (input or action -> exact expected result), one per line. |
| `Q-2` | `task:interface` | The main inputs and outputs, with their formats. |
| `Q-3` | `task:limits` | The limits and boundaries (numbers, sizes, times) that must hold. |
| `Q-4` | `task:errors` | The errors or rejections expected for invalid input. |
| `Q-5` | `task:scope` | The behaviours in scope, one per line. |
| `Q-6` | `task:out-of-scope` | What is explicitly out of scope, one per line. |
| `Q-7` | `task:non-functional` | Non-functional constraints (performance, security, persistence) with their measures. |

`harness task clarify` maps the answers to `C0` questions into the task as follows. A leading
list marker (`-`, `*`, `+`, `1.`, `1)`) is removed from each line and empty lines are skipped.

| Answer to | Lands in the task as | Unless the answers file has |
|---|---|---|
| `task:results` | acceptance criteria, one per line (priority `MUST`) | `addCriteria` (then the answer is only recorded) |
| `task:scope` | requirements with source `clarification`, one per line | `addRequirements` |
| `task:interface`, `task:limits`, `task:errors`, `task:non-functional` | one requirement each, with source `clarification` | `addRequirements` |
| `task:out-of-scope` | constraints `Out of scope: <line>`, one per line | (always) |

```yaml
answers:
  Q-1: |
    - POST /rides with a valid body returns 201 and the ride id.
    - GET /rides/{id} for an unknown id returns 404.
  Q-6: Payments
```

The revision must end up with at least one acceptance criterion, from the answer to
`task:results` or from `addCriteria`. While it has none it keeps `criteriaPending: true`, and
`harness run continue` blocks INTENT again with the seven `C0` questions (exit 6). Once it has
criteria the marker is removed and INTENT assesses the criteria with the other rules, as for any
task.

## Agent sandbox

The harness contains its own file handling to the workspace, but a command provider (an agent CLI)
is a separate process with your user's permissions. With `runtime.agentSandbox: enforce` the
harness wraps every command-provider invocation in `IMPLEMENTATION` (the first one, each one
after `REQUEST_CHANGES` or an automatic correction, and each repeated call) so that the operating system denies any file write outside:

- the workspace (always);
- the resolved `$TMPDIR` of the harness process (always);
- each path in `runtime.sandboxWritePaths`, with `~` expanded and symbolic links resolved (on macOS
  `/tmp` is `/private/tmp` and `/var/folders` is `/private/var/folders`).

Reads, network access and process execution stay allowed: an agent reads the system, calls its
model API and runs tools. The simulated provider and the validators are the harness's own code and
are not wrapped.

| Platform | Mechanism |
|---|---|
| macOS | `/usr/bin/sandbox-exec -p <profile>`: `(allow default)`, `(deny file-write*)`, then `(allow file-write* ...)` for each allowed path (`subpath`, or a `regex` for a trailing `*`) and for `/dev/null`, `/dev/zero`, `/dev/stdout`, `/dev/stderr`, `/dev/ptmx`, `/dev/dtracehelper`, `/dev/fd/*` and `/dev/tty*`. |
| Linux with `bwrap` | `bwrap --ro-bind / / --dev-bind /dev /dev --die-with-parent --bind <path> <path> ... --`. A bind needs an existing source, so a path that does not exist is skipped and recorded, and a trailing `*` binds each existing file that matches. Not exercised in this repository's CI. |
| Linux without `bwrap`, Windows, others | None. `IMPLEMENTATION` is `BLOCKED` before the provider starts (`run start` exits with 6) with a `HIGH` finding `sandbox.unavailable` of `harness.sandbox`. Install bubblewrap, or set `agentSandbox: 'off'` to run the agent unconfined. |

Each confined invocation records `IMPLEMENTATION` evidence (an `agent-sandbox` artifact with the
mechanism, the platform, the profile or `bwrap` arguments, its SHA-256 digest and the allowed and
skipped paths) and an `agent.sandbox.applied` event with the digest. When the provider exits with an
error and its standard error reports a denied write (`Operation not permitted`, `Read-only file
system`, `sandbox`), the run gets a `MEDIUM` finding `sandbox.write-denied` naming the path when
the message contains one. Neither finding is attached to a validation, so neither reaches the gate.

Write paths that `init` declares, and why:

| Path | Why an agent CLI writes there |
|---|---|
| `/tmp` | Shared temporary directory: compilers, npm and git use it. |
| `/var/folders` | Per-user temporary and cache directories of macOS; `$TMPDIR` lives here. |
| `~/.claude` | Claude Code keeps its settings, session state, todos and logs here. |
| `~/.claude.json*` | Claude Code rewrites its configuration file through temporary, backup and lock files next to it. |
| `~/.cache` | XDG cache directory used by agent CLIs, pip, uv and many tools. |
| `~/Library/Caches` | Per-user cache directory of macOS (update checks, node caches). |
| `~/.config` | XDG configuration directory where agent CLIs keep state and credentials. |
| `~/.npm` | npm cache and logs: npx-launched agents and MCP servers write here. |

Add the paths your own agent setup writes to (hooks, plugin logs). A write the sandbox denies is
reported to the agent as `EPERM`/`Operation not permitted`; macOS also logs it, for example
`log show --last 5m --predicate 'sender == "Sandbox"'`. Under `sandbox-exec` a confined process
cannot execute setuid programs (`forbidden-exec-sugid`).

A `project.yaml` written before this setting existed has neither key and runs with `off`, as in
1.0.0; its configuration snapshot is serialized without them, so its digest does not change. YAML
1.1 reads a bare `off` as `false`; the harness accepts that as `off`.

## Requirement traceability

Agents tend to report that every requirement has tests. With
`verification.requirementTraceability` set, `VERIFICATION` checks it: after the technology
validators it relates each identified requirement of the task to the tests of the workspace,
deterministically and without running anything.

**Identifier of a requirement.** The token that starts its text, matching
`^\s*(?:\[ID\][.:)]?|ID[.:)])\s` with `ID` = `[A-Z]{1,3}\d{1,3}(?:\.\d+)?`: `A1. Round to
cents.`, `[B12] Reject an empty basket.`, `X8: ...`, `C3.1) ...`. A requirement without such a token
is identified by its `requirementId` when the task file sets one (`requirementId: req_discount`);
the id the harness generates when the file gives none (`req_` followed by 32 hexadecimal digits)
does not count. When both exist, the text token is used. Requirements without an identifier are
skipped; the report counts them.

**Test files.** Anywhere in the workspace: for the Python profile, `test_*.py` and `*_test.py`;
for the Node profile, `*.test.*`, `*-test.*`, `*_test.*`, the same with `spec`, `test-*`, `test.*`
and every file under a `test`, `tests` or `__tests__` directory (extensions `js`, `cjs`, `mjs`,
`ts`, `cts`, `mts`, `jsx`, `tsx`). Hidden directories, `node_modules`, `__pycache__`, `venv`, `site-packages`, `build`,
`dist` and symbolic links are not searched; a file larger than 2,000,000 bytes is listed as unread.

**A test names a requirement** when

- the name of its file, class or function contains the identifier as a token, in any case:
  `test_a1_rounding`, `test_A1`, `TestA1`, `TestA1Rounding`, `tests/test_a1.py`
  (`test_a12` does not name `A1`; a dot becomes an underscore: `test_c3_1_...` names `C3.1`); or
- its docstring, string constants (a `pytest.param(..., id="A1")`, a Node `test('A1: ...')`
  title) or source lines, comments included, contain the identifier as a whole word, case
  sensitive (`A1` matches `A1:` and `[A1]`, not `A12`, `A1.2` or `a1`).

Python tests (functions named `test*`, classes named `Test*` and their methods) are read with
`ast`; a module docstring counts for the file. A Python file that does not parse and every Node
file are read as text, and a Node match outside a test title is attributed to the file.

| Policy | Identified requirement that no test names |
|---|---|
| `enforce` | One `HIGH` finding `traceability.requirement-untested` per requirement. With the default `findingBlockSeverities` the gate is `FAILED`: `APPROVE` exits with 5, and the person decides `REQUEST_CHANGES`, `APPROVE_EXCEPTION` with a rationale, or `REJECT`. |
| `warn` | One `LOW` finding per requirement; the gate does not count it. |
| `off` | No check: VERIFICATION behaves as in 1.0.0. |

The finding names the identifier and the first 80 characters of the requirement text, for example
`No test names requirement A2: A2. A subtotal below the threshold is unchanged.` With `enforce` and
`warn` the check is recorded as a validation result of `traceability.requirements` (`PASSED` when
the check ran, mandatory under `enforce`, so it appears in `validationSummary`) and the mapping,
each requirement with its identifier, where it came from and the tests that name it (node id, file,
how it matched), is stored as `VERIFICATION` evidence of kind `TEST_REPORT` (schema
`requirement-traceability.schema.json`). The plan of `PLANNING` lists the validator.

A `project.yaml` written before this section existed has no `verification` key and runs with
`off`; its configuration snapshot is serialized without the section, so its digest does not
change.

## Provider feedback loop

The feedback-loop settings in `runtime` act on a run whose provider is a configured command
provider (see [external agents](../guides/external-agents.md)); under `agentSandbox: enforce` a
correction attempt and a repeated call run in the same sandbox as the first call. The simulated
provider is deterministic and reads no feedback, so a run with it behaves as before whatever the
settings say. A `project.yaml` without these keys behaves as 1.0.0 and its configuration snapshot
is serialized without them, so its digest does not change; `harness config validate` shows the
effective values under `feedbackLoop`.

**Automatic corrections (`verificationCorrections`).** When `VERIFICATION` ends `FAILED` because a
mandatory validator ran and failed, the run returns to `IMPLEMENTATION` instead of stopping, at
most N times per run. Each cycle is a `correction.authorized` event with `trigger:
VERIFICATION_FAILED`, the cycle number, `maxCycles`, the failed validators and the reference of the
feedback it sent; it counts in the metrics `correction.cycles` and
`correction.verification_cycles`. As after `REQUEST_CHANGES`, the failed verification stops
counting (gate and decision are cleared) and the next candidate is verified again from the start:
a run never passes `VERIFICATION` with a failing mandatory validator. A verification that is
`BLOCKED`, `TIMED_OUT`, `ERROR` or `INCONCLUSIVE` (a missing tool, a timeout) is not corrected,
because the agent cannot fix it. When the N cycles are used, the run stops at `VERIFICATION` with
`FAILED` exactly as without the setting, and a `correction.exhausted` event records the count.
The budget is per run: `harness run continue` after that re-runs `VERIFICATION` without new cycles.

**Feedback (`providerFeedback`).** The attempt after a correction carries a `feedback` block in the
provider request (schema `provider-feedback.schema.json`, described in
[the protocol](../guides/external-agents.md#the-protocol)): the gate or verification status and its
reason codes, up to 20 findings, the end of each failing mandatory validator's redacted output
(4,000 characters per stream, 16,000 in all) and, after `REQUEST_CHANGES`, the decision with its
rationale. The block is stored as an artifact and recorded as evidence before it is sent.

**Unsupported claims.** When the agent answered `PASSED` and the verification of its change then
fails, the harness records a finding `agent.unsupported-claim` (validator `harness.claim-check`,
category `agent-claim`, severity `unsupportedClaimSeverity`) with an excerpt of the agent's summary
and the failed validators, citing the agent output and the validation reports. It measures the gap
between what the agent reported and what was verified (metric `agent.unsupported_claims`). It is
not an input of the gate, which evaluates the current ChangeSet.

**Transient failures (`providerRetries`, `providerRetryDelaySeconds`,
`providerTransientPatterns`).** When a command-provider call does not pass and the end of its
standard error or standard output (which holds its JSON result) contains one of the patterns, the
harness waits the delay and sends the same request again, up to N times per attempt. A process the
runner killed at `commandTimeoutSeconds`, a cancelled call and a call that passed are never
repeated; a cancellation during the wait stops the run (`CANCELLED`). Each repetition is an
`agent.invocation.retried` event with the matched pattern and an evidence record; the failed call
stays recorded as an agent invocation. Repetitions count in `agent.transient_retries`, not as
correction cycles or implementation attempts. The default patterns are `timed out`,
`connection reset`, `went to sleep`, `overloaded`, `429`, `529`, `rate limit` and `usage limit`; a
pattern that starts or ends with a digit does not match inside a longer number.

## Technology profiles

Profiles are built into the package (`src/governed_harness/resources/profiles/`). Detection is
read-only and reports a confidence with the marker files it found.

| Profile | Detection markers (weight) | Mandatory validator | Optional validators (when available) |
|---|---|---|---|
| `python_default` | `pyproject.toml` (0.80), `requirements.txt` (0.45), `pytest.ini` (0.35) | `python.pytest`: `python -m pytest -q` | `python.ruff`: `python -m ruff check .`, `python.mypy`: `python -m mypy .` |
| `node_default` | `package.json` (0.80), `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` (0.35 each) | `node.test`: `npm test --silent` (requires a `test` script) | `node.lint`: `npm run lint --silent`, `node.typecheck`: `npm run typecheck --silent` (only if the scripts exist) |

An absent optional validator is recorded as `NOT_APPLICABLE`. An absent mandatory executable is
`BLOCKED`; a mandatory `python -m <module>` whose module is missing currently runs and is reported
as `FAILED` (issue #1).
