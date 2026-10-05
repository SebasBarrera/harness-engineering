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
  snapshot: git
  baseline: manifest
  snapshotCache: true
  isolation:
    mode: none
    branch: harness/{taskId}-{runId}
    fetch: true
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
  allowNetwork: true
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
  extendedRedaction: true
  gateContract: true
  reproduceFirst: true
  stateDir: auto
retention:
  artifactDays: 30
  eventDays: 365
  orphanArtifacts: true
intake:
  criteriaPolicy: enforce
  ambiguityReview: agent
  validateAnswers: true
  operationalContract: batch
  interruptions:
    target: 2
    stopConditions:
    - unresolvable-ambiguity
    - scope-contradiction
    - destructive-collision
  projectSetup: ask
verification:
  requirementTraceability: enforce
  outputParsers: true
  interface: enforce
  architecture:
    maxModuleLines: 800
    maxFunctionLines: 80
    maxComplexity: 15
    severity: MEDIUM
  securityPatterns: true
  constraints: enforce
  ratchet: enforce
  differential: true
  weakenedControls: enforce
  testQuality:
    assertions: true
    interfaceTests: true
    diffCoverage: 80
    flakyReruns: 1
    severity: MEDIUM
  secrets: context
  riskFactors:
    newDependency: acknowledge
    authentication: acknowledge
    destructiveMigration: block
    publicContract: acknowledge
    network: acknowledge
    floatMoney: block
    sensitiveLogging: block
    deletedWithoutTests: inform
  acceptanceTests:
    mode: agent
  ladder:
    mode: enforce
    defaultLevel: L1
    deferredExpiryDays: 14
    preflight: true
    capabilityDetection: true
  mutation:
    mode: warn
    maxHunks: 10
    maxSeconds: 300
  principles:
    mode: enforce
    duplicationWindow: 6
    maxInheritanceDepth: 3
    unusedPublic: true
    boyScout: true
    checklist: true
    severity: MEDIUM
review:
  exceptions: true
  exceptionDays: 30
  agentReview: enforce
  structuredChanges: true
  manualChecklist: true
  panel:
    mode: enforce
    maxFindings: 20
    parallel: 4
    budget:
      baseTokens: 6000
      tokensPerLine: 30
      maxTokens: 60000
    cache:
      ttlDays: 14
      maxEntries: 500
    runTools: true
    autoFix:
      mode: scoped
      maxAttempts: 2
    secondOpinion:
      mode: 'off'
    evidenceRefs: true
    comment: false
    mcpServers: []
retrospective:
  causal: true
planning:
  decomposition: agent
  threshold: 12
  granularity: adaptive
  coarseModels:
  - claude-sonnet-5-5
  - claude-opus-5-5
  - gpt-6.1-sol
context:
  manifest: auto
  maxFiles: 40
  maxBytes: 400000
  locate:
    mode: agent
budget:
  perCall:
    costUsd: 25
    wallSeconds: 7200
  perTask:
    costUsd: 200
  perRun:
    costUsd: 100
    tokens: 500000000
    wallSeconds: 43200
  warnAt: 0.8
memory:
  learnFromFindings: auto
  autoApproveRecurring: false
agentRouting:
  mode: tiered
  thresholds:
    requirements:
    - 5
    - 15
    files:
    - 5
    - 20
    loc:
    - 2000
    - 10000
  maxEscalations: 2
governance:
  deciderIdentity: git
  confirmDecisionDigest: true
  trustedHosts:
  - 127.0.0.1
  - localhost
  - ::1
  verifyRecords: true
  chainAnchor: file
  pinTaskRevision: true
  protectExcludedPaths: true
  workspaceLease: true
  applyWorkflowSettings: true
  decisionExpiryHours: 72
  applyProfilePolicies: true
  applyNetworkPolicy: true
  stopTheLine: restore
  phasePermissions: true
  phaseCapabilities: true
  applyRepositoryPolicies: true
toolchain:
  profileDetection: all
  interpreter: auto
  extendedProfiles: true
provenance:
  agentSnapshots: true
  selfReport: true
delivery:
  closureCommit: branch
  stage: true
  push: false
  pullRequest:
    create: false
    draft: true
  comment: notClean
environment:
  dirtyTree: warn
  baseline: report
instructions:
  files:
  - AGENTS.md
  - CLAUDE.md
  - .cursorrules
  - .cursor/rules
  - .github/copilot-instructions.md
  precedence:
  - harness
  - AGENTS.md
  - CLAUDE.md
  - .cursorrules
  - .cursor/rules
  - .github/copilot-instructions.md
standards:
  packs:
  - auto
  cards: auto
  maxCards: 12
  tools: detect
testing:
  strategy: auto
  featuresDirectory: features
architecture:
  mode: agent
  refresh: manual
  enforce: enforce
```

Since 1.1 the CLI `harness init` also adds `.harness/` to `.gitignore` and writes
`.harness/task.example.yaml` (`--no-gitignore` and `--no-example-task` skip them); the
`notifications` section is never written, because it needs a URL.

## Fields

| Field | Default | Effect |
|---|---|---|
| `configVersion` | required, `"1.0"` | Configuration format version. |
| `projectId` | required | Identifier used on tasks and runs. `init` derives it from the directory name. |
| `workspace.root` | `..` | Workspace root, relative to `.harness/`. |
| `workspace.units` | `[]` | Declared units (`unitId`, `root`, `profile`). **Declarative: not used by the engine.** |
| `workspace.snapshot`, `workspace.baseline`, `workspace.snapshotCache` | 1.0.0 behaviour when absent; `init` writes `git`, `manifest`, `true` | How the workspace is listed, how the baseline is stored and whether file digests are cached. See [large repositories](#large-repositories). |
| `profiles` | `["auto"]` | `auto` selects the detected profiles; otherwise list profile ids (`python_default`, `node_default`). |
| `workflow` | `default_development` | The normative workflow. Only the built-in workflow exists; its phase order is fixed (see issue #3). |
| `capabilities.default` | `deny` | Only `deny` is accepted. |
| `capabilities.grants` | `[]` | Extra grants: `capability`, `scope` (globs or command names), `approvalRequired`, `conditions` (for example `allowDelete`). They are **added** to the profile grants (issue #4). |
| `validators` | `[]` | Validator ids to run; empty means the profile defaults. An id not defined by a selected profile is a configuration error. |
| `policies` | see below | Policy overrides; locked policies can only be restated with their locked value. |
| `agentProvider` | `simulated` | Default provider for `run start` (`--provider` overrides it). |
| `agentProviders` | `{}` | Named providers: `kind` (`command`, or since 1.1 a built-in adapter `claude-code`, `codex`, `gemini-cli`, `aider`), `command` (argv list, not empty; required for `command`, optional for an adapter), optional `model`, and since 1.1 `args` (extra arguments of an adapter), `passEnv` and `env`. See [external agents](../guides/external-agents.md). |
| `runtime.commandTimeoutSeconds` | `900` | Timeout of agent-provider processes. Validators use their own `timeoutSeconds` from the profile. |
| `runtime.maxOutputBytes` | `1000000` | Bound on captured stdout/stderr per process (applied after capture, issue #9). |
| `runtime.maxParallel` | `2` | **Declarative: not used by the engine** (phases run sequentially); `x-declarative` in the schema. |
| `runtime.allowNetwork` | `false`; `init` writes `true` | Under `governance.applyNetworkPolicy` and `runtime.agentSandbox: enforce`, `false` denies the agent outbound network connections (see [declared settings](#declared-settings)); otherwise **declarative**. Validators are never confined. `init` writes `true` because agent CLIs call their model API. |
| `runtime.agentSandbox` | `off` when the key is absent; `init` writes `enforce` | Write confinement of command-provider (agent) processes: `enforce` or `off`. See [agent sandbox](#agent-sandbox). |
| `runtime.sandboxWritePaths` | none when absent; `init` writes the list above | Paths the agent may write besides the workspace and `$TMPDIR`: absolute or starting with `~/`, no `$` variables, an optional single trailing `*` for a name prefix. |
| `runtime.verificationCorrections` | `0` when absent; `init` writes `2` | Automatic corrections (0 to 10) after a failed `VERIFICATION` of a command provider's change. Its presence, with any value, also turns on the unsupported-claim check. See [provider feedback loop](#provider-feedback-loop). |
| `runtime.providerFeedback` | `false` when absent; `init` writes `true` | Send a `feedback` block to the command provider on the attempt that follows a failed `VERIFICATION` or a `REQUEST_CHANGES` decision. |
| `runtime.unsupportedClaimSeverity` | `MEDIUM` | Severity of the `agent.unsupported-claim` finding (`INFO` to `CRITICAL`). |
| `runtime.providerRetries` | `0` when absent; `init` writes `3` | Repetitions (0 to 10) of a command-provider call that failed with a transient cause. |
| `runtime.providerRetryDelaySeconds` | `0` when absent; `init` writes `60` | Wait before each repetition (0 to 3600 seconds). |
| `runtime.providerTransientPatterns` | the default list below | Case-insensitive texts that mark a failed call as transient. |
| `runtime.extendedRedaction` | `false` when absent; `init` writes `true` | Also redact model-API keys (`sk-ant-`, `sk-`, `sk-proj-`, `AIza`), Slack tokens (`xox?-`), JSON Web Tokens and the password of a URL (`scheme://user:password@host`) from every stored artifact and from the agent's summary. |
| `retention` | written by `init` | `artifactDays` and `eventDays`, applied by `harness gc --apply` (see [declared settings](#declared-settings)); since 1.1 `orphanArtifacts` (see [large repositories](#large-repositories)). |
| `intake.criteriaPolicy` | `warn` when the section is absent; `init` writes `enforce` | What INTENT does with acceptance criteria that cannot be observed: `enforce`, `warn` or `off`. Only `enforce` accepts a task without acceptance criteria. See [acceptance-criteria policy](#acceptance-criteria-policy). |
| `verification.requirementTraceability` | `off` when the section or the key is absent; `init` writes `enforce` | What VERIFICATION does with identified requirements that no test names: `enforce`, `warn` or `off`. See [requirement traceability](#requirement-traceability). |
| `governance.*` | 1.0.0 behaviour when absent; `init` writes every key | Decider identity and confirmation, trusted API hosts and the other integrity settings. See [governance](#governance). |
| `verification.outputParsers` | `false` when the key is absent; `init` writes `true` | Parse the output of a failing command validator into one finding per reported problem, with path, line and the tool's rule. See [located findings](#located-findings). |
| `review.exceptions` | `false` when the section or the key is absent; `init` writes `true` | `APPROVE_EXCEPTION` records an exception with an expiry, a scope, optional alternative evidence and a follow-up; while it is in force later runs do not block on the findings it covers. See [exceptions](#exceptions). |
| `review.exceptionDays` | `30`; `init` writes `30` | Validity of an exception when the decision sets none (1 to 365 days). |
| `retrospective.causal` | `false` when the section or the key is absent; `init` writes `true` | Retrospective by cause, also for rejected and cancelled runs. See [retrospective by cause](#retrospective-by-cause). |
| `notifications.webhooks` | none when absent; `init` writes none | URLs notified when a run waits for a decision, finishes or gets an exception. See [notifications](#notifications). |
| `intake.ambiguityReview`, `intake.clarifyAgent`, `intake.validateAnswers` | off when absent; `init` writes `agent` and `true` | Agent review of ambiguity and completeness in INTENT and the check of a person's answers. See [better agent results](../guides/agent-results.md#intent-ambiguity-and-completeness-37). |
| `verification.interface`, `architecture`, `securityPatterns`, `constraints`, `ratchet`, `invariants`, `differential`, `weakenedControls`, `testQuality`, `secrets`, `sarif`, `riskFactors`, `acceptanceTests` | off when absent; `init` writes all but `invariants` and `sarif` | Deterministic checks of the ChangeSet, the comparison with the baseline and frozen acceptance tests. See [better agent results](../guides/agent-results.md#verification-deterministic-checks-40-52). |
| `review.agentReview`, `review.reviewer`, `review.structuredChanges` | off when absent; `init` writes `enforce` and `true` | Second-agent review in INDEPENDENT_REVIEW and blocking items of REQUEST_CHANGES. |
| `review.panel` | off when absent (the single reviewer of `review.agentReview`); `init` writes the section | The review panel (#57): reviewers by domain over diff slices, the layered rule catalog, the output contract, the recomputed verdict, cache, budget, scoped auto-fix; also `harness review-code`. Keys: `mode`, `reviewers`, `maxFindings`, `parallel`, `budget`, `cache`, `provider`, `fallbackProvider`, `consistencyChecks`, `runTools`, `autoFix`, `secondOpinion`, `evidenceRefs`, `comment`, `baseBranches`, `mcpServers`. See the [review panel guide](../guides/review-panel.md). |
| `runtime.gateContract`, `runtime.reproduceFirst` | off when absent; `init` writes `true` | The gate contract and permissions in the implement request; reproduce-first and empty corrections. |
| `governance.stopTheLine`, `governance.phasePermissions` | off when absent; `init` writes `restore` and `true` | What happens to the changes of a run that stops unapproved; per-call permissions. |
| `governance.applyRepositoryPolicies` | off when absent; `init` writes `true` | Applies `policies.repositoryContentTrusted` and `policies.destructiveActionsDefault` (#5). See [repository policies](#repository-policies). |
| `governance.phaseCapabilities` | off when absent; `init` writes `true` | Capabilities per phase (#4): the project narrows the profiles' grants, each phase allows only its `allowedCapabilities`, an agent call outside IMPLEMENTATION is read-only. See [capabilities per phase](#capabilities-per-phase). |
| `planning`, `context`, `budget`, `memory`, `agentRouting` | off when absent; `init` writes each section | Decomposition, context manifest, governed budget, lessons and model routing. See [better agent results](../guides/agent-results.md). |
| `toolchain.*` | 1.0.0 behaviour when absent; `init` writes `profileDetection: all` and `interpreter: auto` | Project profiles and validators, several profiles per repository and the project's Python interpreter. See [project toolchain](#project-toolchain). |
| `provenance.*` | 1.0.0 behaviour when absent; `init` writes both keys | Provenance of every ChangeSet file and the agent's self-report. See [provenance](#provenance). |
| `delivery.*` | the harness never commits when absent; `init` writes `closureCommit: branch` | The closure commit with trailers and the defaults of `harness pr publish`. See [delivery](#delivery). |
| `verification.ladder`, `verification.probes`, `verification.mutation`, `review.manualChecklist` | off when absent; `init` writes all but `probes` | The verification ladder, behaviour probes, light mutation and the manual checklist. See [verification ladder](#verification-ladder). |
| `intake.operationalContract`, `intake.interruptions`, `context.locate` | off when absent; `init` writes `batch`, the budget and `agent` | The operational contract, the interruption budget and localisation in INTENT. See [intake contract](#operational-contract-and-interruptions). |
| `environment`, `workspace.isolation`, `runtime.stateDir`, `toolchain.extendedProfiles` | 1.0.0 behaviour when absent; `init` writes each | Environment preflight, worktree isolation (mode `none`), the run registry outside the workspace and the extended built-in profiles. See [delivery hygiene](#delivery-hygiene). |
| `delivery.stage`, `push`, `pullRequest`, `comment`, `instructions` | off when absent; `init` writes `stage: true`, `push: false`, no pull request, `comment: notClean` and the instruction files | Complete delivery as the operational contract authorises it, and `harness config lint`. See [complete delivery](#complete-delivery). |
| `delivery.forge` | GitHub through `delivery.publisher` when absent; `init` writes none (the forge is detected from `origin`) | The forge of `harness pr publish`, `pr create` and `pr status`: GitHub, GitLab, Bitbucket, Azure DevOps or Gitea. See [forges](../guides/forges.md). |
| `standards.*` | off when absent; `init` writes `packs: [auto]`, `cards: auto`, `maxCards: 12`, `tools: detect` | Language standards packs: cards for the agent, the review checklist and the validators of the tools the repository configures. See [engineering standards](../guides/engineering.md#standards-packs). |
| `verification.principles` | off when absent; `init` writes `mode: enforce` with every proxy on and `severity: MEDIUM` | Engineering principles as deterministic proxies and a checklist in the review call. See [engineering principles](../guides/engineering.md#engineering-principles). |
| `testing.*` | off when absent; `init` writes `strategy: auto` | The testing strategy: detected, asked, `tdd` (red, green, refactor evidence) or `bdd` (Gherkin scenarios). See [testing strategy](../guides/engineering.md#testing-strategy). |
| `architecture.*` | off when absent; `init` writes `mode: agent`, `refresh: manual`, `enforce: enforce` | The architecture: configured layers, a cached survey of an existing project or options for a new one, enforced as forbidden dependencies. See [architecture](../guides/engineering.md#architecture). |
| `intake.projectSetup` | off when absent; `init` writes `ask` | INTENT asks the architecture, testing strategy and standards of a new project, or what detection could not establish (rule `P1`). See [new and existing projects](../guides/engineering.md#new-and-existing-projects). |

## Located findings

Without `verification.outputParsers` a failing command validator records one finding
(`<validator>.failed`, `HIGH` when mandatory, `MEDIUM` when optional) without a location, as in
1.0.0. With `outputParsers: true` it also records one finding per problem the tool reported,
with rule `<validator>.<tool rule>` (for example `python.ruff.F401`, `python.mypy.return-value`,
`python.pytest.test-failed`), the path relative to the workspace and the line. The formats are
recognized by content: SARIF 2.1.0, ESLint JSON, Ruff JSON, JUnit XML (printed, or written to a
file named with `--junitxml`, `--junit-xml`, `--output-file` or `-o` inside the workspace), and
the text output of Ruff (concise and full), Mypy, TypeScript `tsc` and pytest (`FAILED`/`ERROR`
summary lines, with the line of the test taken from the traceback). Since #56 also Checkstyle
XML (Checkstyle, ktlint, detekt, golangci-lint, SwiftLint, PHPStan), RuboCop JSON and Cargo JSON
messages (Clippy); MSBuild diagnostics (`dotnet build`) only with `parser: msbuild`. Errors keep the severity of
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

- The decision carries an expiry: `--expires-in 14d` (also `36h`, `2w`) or
  `--expires-at <ISO 8601>` on `harness gate decide --decision APPROVE_EXCEPTION`, or, with
  neither, `review.exceptionDays`. An expiry must be in the future and at most 365 days away.
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
rejected run or in a run later linked to an outcome, with a fixed-rule signal
(`often excepted when it blocks`, `fired in runs later linked to an outcome`,
`led to corrections`), and per
validator how many results did not pass. It needs no setting and writes nothing.
`harness outcome record --run R --kind KIND --summary TEXT` (kinds `INCIDENT`, `REVERT`,
`HOTFIX`, `REGRESSION`, `OTHER`; optional `--reference` and `--observed-at`) links what
happened after a run to it (schema
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
| `repositoryContentTrusted` | `false` | Yes; applied under `governance.applyRepositoryPolicies` (see [repository policies](#repository-policies)); otherwise declarative and named by `config validate` |
| `destructiveActionsDefault` | `deny` | Yes; applied under `governance.applyRepositoryPolicies` (see [repository policies](#repository-policies)); otherwise declarative and named by `config validate` |

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

## Governance

The `governance` section protects the human decision, the workspace and the record. Every key is
optional and `harness init` writes all of them; a `project.yaml` without the section (or without
a key) keeps the 1.0.0 behaviour, and its configuration snapshot is serialized without them, so
its digest does not change. `harness config validate` shows the effective values under
`governance`.

### Who decides

A human decision (`gate decide`, including `APPROVE_EXCEPTION`, `recommendation decide`,
`memory approve`, `memory invalidate`, `memory add --approve`, `task clarify`) is refused with exit
code 5 when its actor id is in the namespace the harness gives to agents (`agent.*`), validators
(`validator.*`) or itself (`harness.*`), or is one of those words alone. The local API answers the
same request with 403. This rule applies to every project, with or without the section: it closes
a defect where `gate decide --actor agent.claude-code --decision APPROVE_EXCEPTION` was recorded
as a human decision and closed the run. The actor id is still not authenticated: the rule stops a
process with access to the terminal from deciding under its own identity, not a person or a
process that types another one.

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `deciderIdentity` | `default` | `git` | `git`: when a human act has no `--actor` (API: no `actor_id`), the actor is the Git user of the workspace, recorded as `actorId` (the e-mail address in lower case, characters outside `[a-z0-9_.-]` replaced by `-`) and `displayName` (`Name <email>`). A gate decision records where the id came from as `identitySource`: `explicit` (`--actor`), `git`, or `fallback` when Git has no usable `user.email` or `user.name` (a CI runner, a fresh machine): the default id below is recorded instead and a warning on standard error (API: `warnings` in the response) says how to set the identity; the command does not fail. `default`: `human.local` (CLI) and `human.web` (API). |
| `confirmDecisionDigest` | `false` | `true` | On a terminal, `gate decide` always goes through the interactive confirmation of `--interactive`, even when every option is given: it shows the decision brief and asks for the first 12 hexadecimal characters of the ChangeSet digest; a wrong answer exits with 5 and records nothing. Without a terminal (scripts, CI) nothing is asked. |
| `trustedHosts` | every host | `127.0.0.1`, `localhost`, `::1` | The local API answers only requests whose `Host` header is one of these names (400 otherwise), which stops DNS rebinding from a web page. |

### The record

The events of a run are the audit authority; the `records` table that `status`, the API and the
gate read is a projection of them. `harness verify --run <id>` (or every run without `--run`)
checks, without repairing anything:

- the event chain: no sequence gap, each event linked to the digest of the one before it, each
  digest recomputed from its envelope;
- the anchor of the chain head (below), when one is configured: `matched`, `absent`, `truncated`
  (the chain no longer contains the anchored event) or `rewritten` (it contains another one);
- every record that has an event of its own (decisions, gates, ChangeSets, validations,
  findings, evidence, tool and agent invocations, clarification requests and records), rebuilt
  from the event and compared with the stored record (`differs`, `missing`, `no-event`), the run's
  pointers (task, configuration digests, decision, gate, ChangeSet digest) and the phase results;
- every artifact the run's artifact and evidence records reference: present and with the digest
  of its URI (`missing`, `content-differs`, `digest-differs`).

It prints a JSON report and exits with 0 when everything verifies and 6 otherwise.
`harness status` no longer aborts on a broken chain in any project: it reports `eventChainValid: false` and
the reason in `eventChainError` (before, an edited event made it exit with 1).

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `verifyRecords` | `false` | `true` | `harness trace` (every format, and the API trace route) verifies the run first and refuses to export a run that does not verify (exit 6, API 409); `status` adds `recordsValid` and, when false, `verificationSummary`. |
| `chainAnchor` | `off` | `file` | After every command that appends events to a run (`run start`, `run continue`, `gate decide`, `run cancel`, `task clarify`), the sequence and digest of the chain head are copied outside `.harness`. `file`: a JSON file per workspace under `$HARNESS_ANCHOR_DIR`, else `$XDG_DATA_HOME/governed-harness/anchors`, else `~/Library/Application Support/governed-harness/anchors` (macOS), `%LOCALAPPDATA%\governed-harness\anchors` (Windows) or `~/.local/share/governed-harness/anchors`. `git-note`: a Git note under `refs/notes/governed-harness` of the workspace repository, attached to a blob named after the run. `off`: no anchor. A chain that does not verify is never anchored, and a failed write does not stop the run (verify then reports `absent`). |

Neither anchor is tamper-proof: a process with your permissions can rewrite the anchor as well as
`state.db`. It turns a silent truncation into an edit of two places, and a deleted anchor shows up
as `absent`.

### The task of a run

In 1.0.0 every phase re-read the stored task, so `harness task create` with the id of a task whose
run waited in `DECISION` replaced the task under the run, silently.

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `pinTaskRevision` | `false` | `true` | `run start` stores the task revision as an artifact and records its digest (`taskDigest`, `taskRevisionRef`) in `run.created`; every phase works on that revision, and only `task clarify` (during `INTENT`) replaces it. `task create` with the id of a task that has an open run (not closed, cancelled or rejected) exits with 5 and names the run. `SPECIFICATION` freezes the acceptance-contract digest (requirements, acceptance criteria and constraints); `gate decide` records it in the decision as `acceptanceContractDigest` and refuses with 5 when the run's task no longer produces it, and a decision bound to another contract does not let `DECISION` pass. |

### What the ChangeSet leaves out

The ChangeSet excludes `.git`, `.harness`, `.venv`, `venv`, `node_modules`, `dist`, `build`,
caches and symbolic links. In an evaluation an agent wrote `.git/hooks/pre-commit`,
`venv/lib/dep.py` and `dist/payload.py`; the ChangeSet showed one file and the gate reached
`DECISION` with two `MEDIUM` findings. A Git hook runs the agent's code when the person commits,
and a changed dependency changes what the tests run.

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `protectExcludedPaths` | `false` | `true` | Before and after the agent invocations of every `IMPLEMENTATION` attempt (simulated and command providers, with or without the sandbox) the harness fingerprints every file below `.git`, `.harness`, `.venv`, `venv`, `node_modules`, `dist` and `build` at any depth and every symbolic link in the workspace (size, modification time and SHA-256 of the first 8 KiB; the target of a link). The comparison is `IMPLEMENTATION` evidence. Any added, modified or deleted path is a `CRITICAL` finding `workspace.out-of-changeset-write` of `harness.workspace-guard` that names the paths, and every later gate of the run gets a failed mandatory validation `harness.workspace-guard` carrying it: the gate is `FAILED`, `APPROVE` exits with 5 and only `REJECT`, `REQUEST_CHANGES` or `APPROVE_EXCEPTION` with a rationale remain. Under `runtime.agentSandbox: enforce` the sandbox also keeps `.harness` and `.git` read-only (a `deny file-write*` after the allowed paths on macOS, a read-only bind on Linux), recorded as `protectedPaths` in the sandbox evidence; and the profiles' `filesystem.write` grants on `.harness/**` and `.git/**` are dropped from the resolved capabilities. |

The harness's own files are not watched: `.harness/state.db*`, `.harness/artifacts/`,
`.harness/lease.json*`, Git's `index`, `*.lock` files, `.git/objects/` and the
`refs/notes/governed-harness` notes. Caches such as `__pycache__` are rewritten by every test run
and are not watched either. A build the agent runs on purpose (`dist`, `build`) is reported too:
inspect it and decide with an exception. A write that restores size, modification time and the
first 8 KiB of a file is not detected.

### One harness at a time, and recovery after a crash

Two `run start` on one workspace overwrote each other's files and both runs failed; a `SIGTERM`
during `IMPLEMENTATION` left the phase `RUNNING` while the agent kept writing, and `run continue`
implemented the change again on top of it.

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `workspaceLease` | `false` | `true` | `run start`, `run continue`, `gate decide` and `task clarify` hold `.harness/lease.json` (token, pid, host, command, run, heartbeat every 10 s) while they run. Another process that finds it exits with 5 and names the holder; a lease whose process no longer exists on this host, or whose heartbeat is more than 60 s old, is taken over. `run cancel` and the read-only commands take no lease. While the lease is held, `SIGTERM` stops the command like Ctrl-C: the agent's or validator's process group is terminated, the phase and the run are recorded as `INTERRUPTED` and the command exits with 143. The runner records each process group it starts (flag `process:<run>`). `run continue` on a run that a killed harness left first recovers it: a phase still `RUNNING` becomes `INTERRUPTED`, the recorded process groups that still run on this host are terminated, and the files an interrupted `IMPLEMENTATION` attempt changed are restored to the snapshot taken when that attempt started (deleted when they did not exist), all recorded in a `run.recovered` event; then the phase runs again as a new attempt. A changed binary file cannot be restored from the snapshot: the run is then `BLOCKED` with the paths. |

Restoring undoes every change to the workspace since the interrupted attempt started, including
one a person made meanwhile.

### Declared settings

Several settings were declared, written by `init` or shipped in the built-in workflow and profiles,
and read by nothing. The ones with a clear meaning now take effect behind these keys; the rest are
marked `x-declarative` in the generated JSON Schema, and `harness config validate` lists them under
`declarative` and adds a line to `warnings` for each one the project relies on (for example
`runtime.maxParallel`, or `maxAttempts` while `applyWorkflowSettings` is off).

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `applyWorkflowSettings` | `false` | `true` | The built-in workflow's per-phase settings apply. `maxAttempts`: once a phase has that many failed attempts (`FAILED`, `ERROR`, `TIMED_OUT`, `INTERRUPTED`; a `BLOCKED` wait in `DECISION` is not a failure), it is not started again: the run is `BLOCKED` with the reason and a `phase.attempts.exhausted` event (for example a fourth `VERIFICATION` after two automatic corrections). `timeoutSeconds`: the wall-clock budget of one attempt; the agent process and each validator get at most the time left, and an attempt that ends after it is `TIMED_OUT`. `exitGate`: recorded with every attempt in `phase.completed` (`exitGate`, `exitGateMet`, with `timeoutSeconds` and `maxAttempts`); the condition itself is evaluated by the phase. |
| `decisionExpiryHours` | none | `72` | A human decision gets `expiresAt` that many hours after it is recorded (1 to 8760), unless it records an exception under `review.exceptions`, whose expiry (`--expires-in`, `--expires-at` or `review.exceptionDays`) is then the decision's `expiresAt`: there is one expiry per decision; a decision that expired before `DECISION` used it (for example one recorded with `--no-continue`) leaves `DECISION` `BLOCKED` until a new decision is recorded. |
| `applyProfilePolicies` | `false` | `true` | The profile policies `missingTestCommand` (Python: a missing executable or module of a mandatory validator) and `missingTestScript` (Node.js: a missing package script) set the status of the unavailable mandatory validator, `BLOCKED` (the profiles' value) or `FAILED`; any other value is a configuration error. A project policy `coverage: {minimumPercent: N}` (0 to 100) adds the mandatory validator `python.coverage` to `VERIFICATION` of a Python project: it runs `python -m coverage run -m pytest -q` (the tests run a second time) and `python -m coverage report --fail-under=N`, keeping the data under `.harness/coverage/`; without the `coverage` package it is `BLOCKED` (or the `missingTestCommand` status). The profile's `coverage: optional_for_research_prototype` is not a threshold and stays declarative. |
| `applyNetworkPolicy` | `false` | `true` | `runtime.allowNetwork: false` denies the agent outbound IP connections under `runtime.agentSandbox: enforce`: `(deny network-outbound (remote ip "*:*"))` in the Seatbelt profile (local sockets stay allowed), `--unshare-net` with `bwrap`, and `network: denied` in the sandbox evidence. |

`harness gc` applies `retention` to runs that ended (closed, cancelled or rejected) longer ago than
the setting, counted from the run's last update; open runs and memory records are never touched.
`artifactDays` deletes the run's artifacts that no kept run references and records a
`retention.artifacts.pruned` event listing them, so `harness verify` treats them as pruned, not
missing; `eventDays` removes the run with its events, records, flags and artifacts. Without
`--apply` it only prints what it would remove. `gc` is a command a person runs; nothing runs it
automatically.

Still declarative: `workspace.units`, `runtime.maxParallel`, the policy
`ambiguousPackageManager`, and the workflow's `dependsOn`, `parallelizable`, per-phase
`validators` and `invariants`. The workflow's `allowedCapabilities` apply under
`governance.phaseCapabilities` (below).

### Capabilities per phase

In 1.0.0 the grants of a run were the union of the profiles' and the project's capabilities,
issued for the whole run whatever the phase: a project could widen a profile but never narrow it,
and the workflow's `allowedCapabilities` had no effect (issue #4). Under
`governance.phaseCapabilities: true`:

* a capability the project lists in `capabilities.grants` keeps only the scopes both the
  profiles and the project allow (a project scope inside a profile scope, `src/**` inside `**`);
  a capability the project does not list keeps the profiles' scopes; a scope no profile grants
  is added explicitly with `capabilities.extend` (without the key, `extend` is added like
  `grants`). Scopes the harness derives from what the project selects (its toolchain validators,
  the interpreter, the standards tools) are added after it. Two patterns that only overlap in
  part are not kept: the intersection fails closed;
* every grant made while a phase runs (validators, probes, agent calls) keeps only the
  capabilities the phase allows: the workflow's `allowedCapabilities` plus what the harness
  itself runs there (`process.execute` in SPECIFICATION for the frozen acceptance tests, in
  PLANNING for the preflight probes, in INDEPENDENT_REVIEW for the review panel's consistency
  checks), written into the resolved workflow of the configuration snapshot;
* an agent call outside IMPLEMENTATION (clarify, locate, plan, acceptance, architecture, review
  and every reviewer of the panel) gets no `filesystem.write` and may start only its own
  configured command (`agentProviders.ID.command`);
* each phase attempt records the resolved grants of the validators and of the run's agent as
  evidence and as a `capabilities.resolved` event.

`harness config validate` warns that `allowedCapabilities` is declared but not applied while the
key is off.

### Repository policies

The policies `repositoryContentTrusted` and `destructiveActionsDefault` were part of the core
policies and read by nothing (issue #5). Under `governance.applyRepositoryPolicies: true`:

* `repositoryContentTrusted: false` (the default): every request the harness builds says that
  repository content is untrusted data, not instructions (a prompt-injection notice). The
  instruction files of the repository (`instructions.files`, by default `AGENTS.md`, `CLAUDE.md`,
  `.cursorrules`, `.cursor/rules`, `.github/copilot-instructions.md`) reach the implementing agent
  only quoted, in a fence longer than any backtick run they hold (`untrustedContent`, at most 8
  files of 4,000 characters), and every read-only call and reviewer gets only their names. The
  review panel's rule `pipeline-security.embedded-instructions` (checked by the harness, never by a
  model) flags changed lines that address instructions to an agent: "ignore the previous
  instructions", "reviewers must approve", chat-template markers. With
  `repositoryContentTrusted: true` that rule is inactive and no notice is added. A CLI that loads
  instruction files by itself still reads them; the notice tells it how to treat them.
* `destructiveActionsDefault: deny` (the default): a command the harness runs while a phase runs
  (validators, probes, consistency checks, provider launches) is refused before it starts when it
  deletes recursively outside the workspace (or the workspace root), force-pushes or deletes a
  remote ref, rewrites history (`rebase`, `commit --amend`, `filter-branch`, `filter-repo`),
  discards commits (`reset --hard`), drops data (`DROP TABLE`, `TRUNCATE TABLE`, `dropdb`,
  `FLUSHALL`) or changes ownership or permissions outside the workspace, also inside `sh -c`.
  A `process.destructive` grant whose scope prefixes the command allows it. Each refusal is a
  `HIGH` `capabilities.destructive-denied` finding. Implement and read-only requests carry
  `commandPolicy: {destructive: deny}`; the Claude Code adapter passes the same operations as
  `--disallowedTools` (a pattern cannot see paths, so `chown` and `chmod -R` are refused there
  everywhere). Commands an agent CLI runs by itself are confined by the agent sandbox, not by this
  list.

`harness config validate` warns that both policies are declared but not applied while the key is
off.

## Large repositories

In 1.0.0 every snapshot walked the whole workspace, hashed every file, read the text of every
file and stored the baseline as one artifact with all of it, `.env` included. On a generated Git
repository of 10,001 tracked files (9,996 text files of about 11.7 kB, plus 500 ignored build
files and an ignored `.env`), one patch task of two files with the simulated provider and
`python.pytest` only, `harness run start` took 16.47 s with a maximum resident set of
700,841,984 bytes, `.harness` held 119,291,675 bytes and its largest blob 119,112,913 bytes, with
the `.env` secret in it. With the three workspace keys below it took 4.27 s, 94,109,696 bytes,
3,654,966 bytes and 1,911,156 bytes (the manifest), without `.env` (macOS arm64, Python 3.12.11;
measured for the commit that added the keys).

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `workspace.snapshot` | `walk` | `git` | `git`: the files are listed through Git (`git ls-files --cached --others --exclude-standard`): tracked files and untracked files `.gitignore` does not exclude, minus the directories the ChangeSet always excludes and symbolic links. Ignored files are never read, hashed or stored, and a change to one is not in the ChangeSet: under `governance.protectExcludedPaths` the guard also fingerprints the ignored files outside those directories, so an agent's write to an ignored `.env` is a `CRITICAL` `workspace.out-of-changeset-write` finding. A workspace that is not a Git repository is walked. |
| `workspace.baseline` | `text` | `manifest` | `manifest`: the baseline (and the snapshot an `IMPLEMENTATION` attempt starts from) is stored as a manifest of digests with the Git `HEAD`; the text is kept only for files Git cannot give back (untracked or modified at that moment). The text of any other file is read from that `HEAD` when, and only when, the file enters a diff; a blob whose SHA-256 is not the recorded digest (a Git filter changed it) diffs as binary. |
| `workspace.snapshotCache` | `false` | `true` | The digest of each file is kept in `.harness/cache/snapshot-cache.json` with its size, modification time, change time and inode, and a file whose four values did not change is not hashed again. A process cannot set the change time (`utime` updates it), so restoring a file's modification time does not hide an edit. The digest of the cache file is kept in the state database after every write, and a cache file that does not match it is ignored. Entries younger than two seconds are not cached. |
| `retention.orphanArtifacts` | `false` | `true` | `harness gc` also deletes the artifacts that no record, flag or event of any run or of the project references (left by an interrupted write or by runs removed earlier). An artifact written in the last hour is never an orphan. The report lists them under `orphans`, with `orphansRemoved` and `orphanBytes`. |

The same change produces the same ChangeSet digest under either baseline when no stored text was
redacted (a test runs one task on two copies of a workspace and compares the digests). Under
`text` the baseline stores redacted text, so a file holding a secret-like string is diffed from its
redacted form; under `manifest` it is diffed from the content Git has.

## Project toolchain

The `toolchain` section lets a repository declare its own profiles and validators. Every key is
optional; absent keys keep the 1.0.0 behaviour and the configuration digest.

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `profilePaths` | none | none | YAML files, or directories of `*.yaml` files, relative to the workspace root, each a profile in the format of the built-in ones (`profileVersion`, `profileId`, `technology`, `detectors`, `validators`, `defaultValidators`, `policies`, `capabilities`). `profiles` may name them by `profileId`, and `auto` detects them by their `detectors` (a profile without detectors applies to every workspace). The id of a built-in profile (`python`, `python_default`, `node`, `node_default`) is refused. |
| `profileDetection` | `best` | `all` | Under `profiles: [auto]`, `all` selects every detected profile (several profiles per repository: a Python service with a Node.js front end gets both sets of validators); `best` selects the one with the highest confidence. |
| `interpreter` | `system` | `auto` | `auto` runs the validators whose command starts with `python` or `python3` with the project's interpreter: `.venv` or `venv` in the workspace (by absolute path), else `uv run --no-sync python` with `uv.lock` and `uv` on `PATH`, else `poetry run python` with `poetry.lock` and `poetry` on `PATH`. The interpreter prefix gets its own `process.execute` grant. |
| `validators` | none | none | Validators of the project. An entry with the id of a selected validator replaces it (for example `python.pytest` with `[uv, run, pytest, tests/unit]`); any other entry is added. Each needs a `command`; besides the keys of a profile validator (`mandatory`, `whenAvailable`, `timeoutSeconds`) it may set `parser`, `severity`, `failureSeverity` and `passEnv`. The exact command gets a `process.execute` grant. |

The keys a validator of a profile or of the project may set since 1.1:

| Key | Default | Effect |
|---|---|---|
| `parser` | follow `verification.outputParsers` | Parse the output of a failing run with one format: `sarif`, `junit`, `ruff`, `mypy`, `eslint`, `tsc`, `pytest`, `checkstyle`, `rubocop`, `cargo`, `msbuild`; `auto` recognizes every format but `msbuild`; `none` never parses. See [located findings](#located-findings). |
| `severity` | error as the failure finding, warning `LOW`, note `INFO` | Severity of a parsed issue by its level, for example `{error: HIGH, warning: MEDIUM}`. |
| `failureSeverity` | `HIGH` when mandatory, `MEDIUM` otherwise | Severity of the finding of a failing run. |
| `passEnv` | none | Variables of the harness's environment the command receives as they are; their values are redacted from every artifact. |

```yaml
toolchain:
  interpreter: auto
  validators:
    - id: project.bandit
      command: [bandit, -r, src, -f, sarif, -o, bandit.sarif]
      mandatory: false
      parser: sarif
      severity: {error: HIGH, warning: MEDIUM}
```

## Provenance

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `agentSnapshots` | `false` | `true` | Before and after every agent invocation the harness records the files of the ChangeSet scope that differ from the baseline, as a manifest of digests (`provenance.agent.snapshot` events). A difference that appears between two invocations, or after the last one, was made by no invocation: each such file is a `provenance.out-of-band-edit` event. When the run reaches `DECISION`, the provenance of every ChangeSet file (`AGENT` with the invocation that wrote it, or `OUT_OF_BAND`) is recorded as `DECISION` evidence and a `component_provenance` record (`schemas/v1/component-provenance.schema.json`), and `harness review` shows it. It is attribution, not a gate condition: an edit by the person who reviews is legitimate, and the record says that it happened. |
| `selfReport` | `false` | `true` | The request asks the agent for a self-report: assumptions, alternatives discarded, low-confidence areas and unrequested changes. A command provider receives `selfReport` in the request and may answer `selfReport` next to `status`; a built-in adapter asks for a fenced JSON block with `harnessSelfReport` at the end of the answer. The report is stored as data of quality `REPORTED` (`agent_self_report` record, `schemas/v1/agent-self-report.schema.json`), contrasted with the ChangeSet of the same invocation (declared paths that are not in it, unrequested changes that are) and shown by `harness review`; what could not be read is listed under `problems`. It is never a check. |

## Delivery

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `closureCommit` | `off` | `branch` | At `CLOSURE` the approved ChangeSet is written as one commit whose parent is `HEAD`, built in a temporary index from `HEAD` plus the ChangeSet files. `branch` creates the branch `branch` (default `harness/{runId}`) and does not touch the working tree, the index or the current branch; `head` moves the current branch to the commit and refreshes the index entries of the ChangeSet paths. The commit carries the trailers `Harness-Run`, `Harness-Task`, `Harness-ChangeSet`, `Harness-Decision` (`<decision id> <decision>`) and, for `APPROVE_EXCEPTION`, `Harness-Exception` with the rationale. Its diff against `HEAD` is recomputed before any ref points to it and must be the approved digest. |
| `branch` | `harness/{runId}` | none | Branch of `closureCommit: branch`; `{runId}` and `{taskId}` are replaced. |
| `publisher` | `{kind: github, transport: gh}` | none | Defaults of `harness pr publish`: `transport` (`gh` or `api`), `repository` (`owner/name`; default: the `origin` remote), `tokenEnv` (default `GITHUB_TOKEN`, read by `api`), `apiUrl` and `sarif`. |

The commit uses the repository's Git configuration (identity and signing). In a repository without
an identity it is written by `Governed Agent Harness <harness@localhost.invalid>`. When the
workspace is not a Git repository, has no commit, or its `HEAD` does not hold the baseline of a
ChangeSet file (the run started on uncommitted changes, for example those of an earlier run), no
commit is written and a `delivery.commit.skipped` event says why: a commit on `HEAD` would carry
more than the approved ChangeSet. When the workspace no longer holds the approved content, a
branch of the same name holds another commit, or Git refuses an operation, `CLOSURE` is `BLOCKED`
with the reason (exit 6) and `run continue` tries again. See [CI integration](../guides/ci-integration.md)
for `harness verify-approval`, the evidence bundle and `harness pr publish`.

## Verification ladder

The keys of this section (since 1.1, issue #55) are optional: a `project.yaml` without them keeps
the earlier behaviour and its configuration digest, and a task without the new fields keeps its
task digest. `harness init` writes them; `harness config validate` shows the effective values
under `ladder`. Guide: [verification ladder](../guides/verification-ladder.md).

| Rung | Evidence that reaches it, per acceptance criterion |
|---|---|
| `L0` static | Every mandatory validator of the gate passed on the ChangeSet digest (VERIFICATION passed). |
| `L1` unit | An L1 validator of the profile passed and a test of the workspace names the criterion (its id or a name listed in `verification.tests`), or a frozen acceptance test that names it passed. |
| `L2` integration | The same for a test under an `integration` directory with an L2 validator, or an L2 probe. |
| `L3` executable behaviour | A probe linked to the criterion ran on every variant and its assertions held. |
| `L4` external environment | An L4 probe passed, or a deferred verification was closed with passing evidence. |
| `L5` human | A person ticked the criterion's manual item in a decision on the digest. |

Nothing is credited by omission: a check that did not run, was skipped, waived or unavailable
adds no rung. A criterion declares what it requires in the task file:

```yaml
acceptanceCriteria:
  - criterionId: ac_cli
    text: The command line prints the discounted total as JSON.
    verification: {level: L3, probe: cli}
  - criterionId: ac_e2e
    text: The checkout flow shows the discount end to end.
    verification: {level: L4, deferred: CI job e2e}
  - criterionId: ac_look
    text: The receipt shows the discount line.
    verification: {level: L5, manual: The receipt layout shows the discount line}
```

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `verification.ladder.mode` | off | `enforce` | `enforce`: a criterion whose declared rung is not reached is a `HIGH` finding `certification.level-not-reached` of the mandatory validation `harness.certification`, so VERIFICATION fails (and enters the correction loop) and the gate cannot pass; a preflight `UNAVAILABLE` stops PLANNING. `warn`: the finding is `LOW` and the preflight only reports. A criterion without a declaration requires `defaultLevel`; its gap is reported and never blocks. |
| `verification.ladder.defaultLevel` | `L1` | `L1` | The rung of a criterion that declares none, for the certification status. |
| `verification.ladder.deferredExpiryDays` | `14` | `14` | Validity of a pending deferred verification. |
| `verification.ladder.preflight` | off | `true` | Run the probes and the frozen acceptance tests on a scratch copy of the baseline in PLANNING. |
| `verification.ladder.capabilityDetection` | off | `true` | Run the read-only detection commands of the profiles' capabilities. Without it a capability is planned as available and marked not checked; certification still needs the validators' results. |
| `verification.probes` | none | none | Probes of the project; a task may declare more under `probes` (a task probe replaces a project probe with the same id). |
| `verification.mutation` | off | `mode: warn`, `maxHunks: 10`, `maxSeconds: 300` | Discriminating evidence and light mutation; `enforce` makes `tests.change-not-exercised` and `tests.broken` `HIGH` and the validation `harness.mutation` mandatory. |
| `review.manualChecklist` | off | `true` | The `manual` items of the criteria and the task's `checklist` are ticked in DECISION; `APPROVE` needs them all (exit 5 otherwise). |

The run's **certification** is recorded at the end of VERIFICATION, again after the decision
(with the ticked items) and after evidence is attached: `CERTIFIED` (every criterion reached its
rung), `PARTIAL` (some did, or wait for deferred evidence or a person) or `NOT_CERTIFIED`. The gate
carries it as a reason code (`CERTIFICATION_CERTIFIED`, `CERTIFICATION_PARTIAL` or
`CERTIFICATION_NOT_CERTIFIED`); `harness review`, `harness verification show`, the dashboard and
the pull request comment show it per criterion.

### Probes

```yaml
probes:
  - id: cli
    command: [python, -m, sample.cli, "{amount}"]
    cwd: .
    output: json            # or text
    timeoutSeconds: 60
    level: L3               # L2, L3 (default) or L4
    passEnv: [API_BASE_URL] # variables passed as they are; values are redacted
    variants:
      - {name: below, values: {amount: "50"}, env: {PYTHONPATH: src}}
      - {name: above, values: {amount: "200"}, env: {PYTHONPATH: src}}
    assertions:
      - {kind: exitCode, equals: 0}
      - {kind: jsonPath, path: "$.total", present: true}
      - {kind: differs, path: "$.total"}
      - {kind: order, path: "$.items[*].id", order: ascending}
      - {kind: text, matches: "total"}
```

A probe runs without a shell, once per variant (`variants`, or the cartesian product of a
`matrix` of placeholder values, or one `default` variant), with the harness's process runner: a
project probe's executable is granted by the configuration, a task probe's must already be
granted. Assertions: `exitCode` (`equals`), `jsonPath` (`present`, `equals` or `matches`;
`$`, `.name`, `['name']`, `[N]`, `[*]`), `differs` (the value, or the whole text output, is not
the same in every variant), `order` (`ascending`/`descending`, or `before` and `after`) and
`text` (`matches` or `contains`). A probe whose command is missing, refused or too slow is
`UNAVAILABLE` and its validation `BLOCKED`, never `PASSED`; a probe that ran and printed
something other than JSON fails its JSON assertions. Under `enforce` a probe validation is
mandatory.

### Preflight and the decision to continue uncertified

PLANNING records the verification plan (`harness verification show`): for each criterion the
rung it requires, the rungs this environment reaches (the profiles' capabilities, the linked
probes on the baseline) and why, and the route (`local`, `deferred`, `manual` or
`unreachable`). The run is `READY`, `PARTIAL` (a declared rung only reachable after the run or
by a person, or a probe that already passes on the baseline) or `UNAVAILABLE` (a probe or the
frozen acceptance tests cannot run on the baseline, or a declared rung cannot be reached at
all). Under `enforce`, `UNAVAILABLE` stops PLANNING (`BLOCKED`, exit 6, listed in the inbox)
until the environment is fixed (`harness run continue`) or a person decides:

```bash
harness verification decide --run RUN --continue-uncertified --rationale "No device lab here"
```

The decision is recorded on the run; the unreachable criteria end `WAIVED` (never certified)
and the unavailable probes are not run (`NOT_APPLICABLE`).

### Deferred verification

A criterion with `deferred` gets, at the end of VERIFICATION, a pending item `D-<criterion>`
bound to the ChangeSet digest and, at CLOSURE, to the closure commit. It expires after
`deferredExpiryDays` (the inbox warns two days before and lists expired items). Evidence closes
it:

```bash
harness evidence attach --run RUN --item D-ac_e2e --file e2e-junit.xml
harness evidence attach --run RUN --item D-ac_e2e --file status.json --format ci-status
```

JUnit passes with at least one test case and none failed (`--case TEXT` narrows it to matching
test cases); SARIF passes with no `error` result; a CI status (`state` or `conclusion`, with
`sha`) passes on `success`. Evidence about another commit, an expired item and an item already
closed are refused (exit 5). The certification is recorded again.

### Light mutation

After the mandatory validators passed, the new or changed test files run on a scratch copy where
the changed source files have their baseline content: failing there and passing on the change
is *discriminating*, passing in both is *weak* (`tests.weak`, `LOW`), failing on the change is
*broken*. Then each changed hunk of the source (a block separated by blank lines; blank and
comment-only hunks are skipped) is reverted, one at a time, in a scratch copy, and the relevant
tests run (the changed test files, else the mandatory test command); a hunk whose reversion no
test notices is `tests.change-not-exercised` at its line. The test command comes from the
profile's `mutationCommand` (`{paths}` is replaced by the test files): Python and Node.js have
one; a profile without it is recorded as not run.

### Manual checklist and attachments

```bash
harness gate decide --run RUN --decision APPROVE --change-set-digest DIGEST \
  --rationale "Checked the receipt" --check ac_look --check CL-1
harness evidence attach --run RUN --item ac_look --file receipt.png --manual
harness evidence attach --task TASK --file bug.png --manual --note "What the customer saw"
```

Interactive decisions ask about each item. An attachment is stored as an artifact (text files
redacted) bound to the run's ChangeSet digest and, with `--item`, to a checklist item; with
`--task` it is intake context bound to the task revision, and the implement request lists it as
`attachments` (file name, media type, digest, note and the path to read it from).

### Profiles and their capabilities

`resources/verification/capabilities.yaml` declares, for each built-in profile, what it offers
per rung, the validators that establish it, the read-only detection (a command that must exit 0
and may have to print a match, or a path that must exist) and the fallback. A project profile
(`toolchain.profilePaths`) declares its own under `verification: {capabilities, mutationCommand}`.
Detection never installs anything.

| Profile | L0 | L1 | L2 | L3 detection |
|---|---|---|---|---|
| `python_default` | Ruff, Mypy | pytest | `tests/integration` | probes |
| `node_default` | lint, typecheck scripts | test script | `test/integration` | probes |
| `go_default` | `go vet` | `go test` | | probes |
| `rust_default` | `cargo check`, `cargo clippy` | `cargo test` | | probes |
| `jvm_gradle` | Gradle `check` without tests | Gradle `test` | | probes |
| `jvm_maven` | `mvn compile` | `mvn test` | | probes |
| `swift_default` | `swift build` | `swift test` | | probes and `xcrun simctl list devices available` |
| `android_default` | `lintDebug` | `testDebugUnitTest` | | probes and `adb devices` |
| every profile | | | | L4: probes and `docker info` |

## Operational contract and interruptions

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `intake.operationalContract` | off | `batch` | INTENT records the contract summary of the task revision (objective, examples, scope, out of scope, definition of done, verification level, branch, push, pull request, comment, coverage threshold), each item from the task's `contract`, else the task, else the project, else `missing`, with a digest bound to the task digest. `batch`: when INTENT asks questions anyway, the request carries the contract in a `contract` section, answered in the same file. `enforce`: INTENT also waits until no item is missing and a person confirmed the summary. |
| `intake.interruptions` | none | `target: 2` and the three stop conditions | The human interactions of a run (decisions, answers, plan and acceptance decisions, budget raises, preflight decisions, confirmations, attachments) are counted (metric `human.interactions`) and reported against the target; `unresolvable-ambiguity` (questions again after a clarification), `scope-contradiction` (a ChangeSet path outside the contract's `scopePaths`: a `HIGH` `contract.scope-contradiction` finding whose validation is `BLOCKED`, so the run stops for a person instead of a correction) and `destructive-collision` (an isolation collision) are recorded as `stop.condition` events. |
| `context.locate` | off | `mode: agent` | A read-only `locate` call (provider protocol 1.1) for tasks the router classifies M or L, once per task revision (cached by its digest, reused by later runs), on the router's `locate` rung, else the bottom of the escalation ladder. Ambiguity comes back as questions (rule `A3`); the locations go to the implement request (`locations`) and rank first in the context manifest. |

The answers file may carry the contract and its confirmation:

```yaml
answers:
  Q-1: apply_discount(100, 100, 0.1) returns 90.
contract:
  verificationLevel: L1
  createPullRequest: no
  examples: |
    apply_discount(100, 100, 0.1) -> 90
confirmContract: true
```

`harness task confirm --task TASK --digest DIGEST` confirms the current summary on its own.

## Delivery hygiene

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `environment.tools` | none | none | Each tool prints its version (`command`) matching `pattern`; otherwise DISCOVERY is `BLOCKED`. |
| `environment.variables` | none | none | Each named variable must be set (only the name is recorded). |
| `environment.gitHooks` | none | none | `required` hooks must be present and executable in the directory Git uses; the reason names `install`, which only `harness doctor --install-hooks` runs. Hooks are never bypassed. |
| `environment.dirtyTree` | `allow` | `warn` | Uncommitted changes outside `.harness/`: `block` stops DISCOVERY, `warn` records a `LOW` finding. |
| `environment.baseline` | off | `report` | The mandatory validators run on a scratch copy of the baseline in DISCOVERY: `require` stops the run when one does not pass, `report` records a `LOW` finding; the comparison with the baseline (`verification.differential`) reuses the result. |
| `workspace.isolation` | none | `mode: none`, the branch template, `fetch: true` | `mode: worktree` (or `run start --isolate worktree`) runs the phases in a Git worktree under `directory` (default `.harness/worktrees/RUN`) on a new branch (`branch`, default `harness/{taskId}-{runId}`, or the contract's) from `base` (default the current branch), fetched from `remote` (default `origin`) first. An existing branch or directory blocks the run (exit 6, `stop.condition` `destructive-collision`); nothing is reset or deleted, and `run continue` tries again. The closure commit goes on the worktree's branch; `harness run cleanup` removes a finished run's worktree (never its branch). `init` keeps `none`: the documented flows and the README quickstart work in place. |
| `runtime.stateDir` | the workspace's `.harness/` | `auto` | The state database and the artifact store of the project move to `auto` (`$HARNESS_STATE_DIR`, else `$XDG_DATA_HOME/governed-harness/state`, else the platform's application-data directory) or the path given, in a directory named after the project id and the repository (its Git common directory, shared by its worktrees) with a `registry.json`. The agent sandbox keeps it read-only; `harness registry` and `GET /api/registry` list the projects and their latest runs. `.harness/` keeps the configuration, the lease, caches and scratch copies. |
| `toolchain.extendedProfiles` | off | `true` | Detect the built-in Go, Rust, JVM (Gradle, Maven), Swift and Android profiles. |

## Complete delivery

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `delivery.stage` | off | `true` | When the change is not pushed, CLOSURE stages only the run's files (`git add -- PATHS`, `git rm --cached` for deleted ones; never `add -A`). |
| `delivery.push` | off | `false` | Push the closure commit's branch to the remote with the repository's hooks (never `--no-verify`, never forced). A refusal stops CLOSURE (`BLOCKED`); `run continue` tries again. The task's `contract.push` overrides it. |
| `delivery.pullRequest` | none | `create: false`, `draft: true` | `create` the pull or merge request after the push, and `draft` (over `delivery.forge.draft`). The forge, the repository, the base (`delivery.forge.baseBranch`, else the remote's default branch), the labels and the template (followed by the decision brief) are the forge layer's: GitHub, GitLab, Bitbucket, Azure DevOps or Gitea, detected from `origin` unless `delivery.forge` names it (see [forges](../guides/forges.md)). Created once per run; the contract's `createPullRequest` overrides `create`. |
| `delivery.comment` | `never` | `notClean` | Comment the brief on that pull request through the same forge (one comment per run, updated on a retry) when the run is not clean (an exception, a gate that did not pass, a certification that is not `CERTIFIED`), `always` or `never`; the contract's `comment` overrides it. |
| `instructions` | the default files, `harness` first | the default files and precedence | `harness config lint` reads `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.cursor/rules` and `.github/copilot-instructions.md` and reports conflicting tool versions (also against `.python-version`, `requires-python`, `.nvmrc`, `engines.node` and `go.mod`), conflicting coverage thresholds (also against `diffCoverage`), instructions to skip the tests and instructions to bypass a control (`--no-verify`, a forced push, `git add -A`, `\|\| true`, `--exit-zero`, `continue-on-error`, `HUSKY=0`, `SKIP=`), each with the source that wins by `precedence`. Exit 6 when there is an issue. |

## Technology profiles

Profiles are built into the package (`src/governed_harness/resources/profiles/`). Detection is
read-only and reports a confidence with the marker files it found.

| Profile | Detection markers (weight) | Mandatory validator | Optional validators (when available) |
|---|---|---|---|
| `python_default` | `pyproject.toml` (0.80), `requirements.txt` (0.45), `pytest.ini` (0.35) | `python.pytest`: `python -m pytest -q` | `python.ruff`: `python -m ruff check .`, `python.mypy`: `python -m mypy .` |
| `node_default` | `package.json` (0.80), `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` (0.35 each) | `node.test`: `npm test --silent` (requires a `test` script) | `node.lint`: `npm run lint --silent`, `node.typecheck`: `npm run typecheck --silent` (only if the scripts exist) |
| `go_default` (`extendedProfiles`) | `go.mod` (0.80), `go.sum` (0.20) | `go.test`: `go test ./...` | `go.vet`: `go vet ./...` |
| `rust_default` (`extendedProfiles`) | `Cargo.toml` (0.80), `Cargo.lock` (0.20) | `rust.test`: `cargo test --quiet` | `rust.check`, `rust.clippy` |
| `jvm_gradle` (`extendedProfiles`) | `build.gradle`, `build.gradle.kts` (0.70), settings files (0.20), `gradlew` (0.10) | `gradle.test`: `./gradlew test --quiet` | `gradle.check`: `./gradlew check -x test --quiet` |
| `jvm_maven` (`extendedProfiles`) | `pom.xml` (0.80), `mvnw` (0.10) | `maven.test`: `mvn --batch-mode --quiet test` | `maven.compile` |
| `swift_default` (`extendedProfiles`) | `Package.swift` (0.80), `Package.resolved` (0.10) | `swift.test`: `swift test` | `swift.build` |
| `android_default` (`extendedProfiles`) | `app/src/main/AndroidManifest.xml` (0.80), `src/main/AndroidManifest.xml` (0.60), `gradlew` (0.10) | `android.unit`: `./gradlew testDebugUnitTest --quiet` | `android.lint` |

An absent optional validator is recorded as `NOT_APPLICABLE`. An absent mandatory executable is
`BLOCKED`; a mandatory `python -m <module>` whose module is missing currently runs and is reported
as `FAILED` (issue #1).
