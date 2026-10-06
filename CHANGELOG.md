# Changelog

## Unreleased

- Run lifecycle, wave 9 (findings of the 2.0.0 evaluation). Fixes that restore the documented
  behaviour apply to every project:
  - `harness plan decide --decision APPROVE --no-continue` exits 0 once the approval is recorded,
    as `gate decide --no-continue` does; it exited 6 because the run still showed its wait in
    PLANNING (#83). A `REJECT` still ends the run and exits 6.
  - `harness run continue` on a run a person rejected reports the closed run (`FAILED`, exit 6)
    instead of evaluating again the baseline that stop the line restored and asking for a new
    decision (#83).
  - `harness inbox` (text and `--json`, and `GET /api/inbox`) lists every wait before DECISION:
    a plan at the plan-approval checkpoint (`plan`), a proposed decomposition
    (`decomposition`), proposed acceptance tests (`acceptance`), architecture options or
    inferred layer rules (`architecture`) and an operational contract to confirm (`contract`),
    next to the deferred verifications and the preflight, each with its `kind`, the `digest` the
    answer binds to and the command that answers it; before, `run start` exited 6 on a plan
    approval while the inbox was empty (#73). A decision entry gains `digest` and
    `acknowledgeRisks` (the risk factors an `APPROVE` must acknowledge); a batch file accepts
    `acknowledgeRisks` per decision and `--batch` asks for each factor on the terminal.
  - Under `planning.granularity: adaptive`, a run whose corrections are spent returns to
    PLANNING to be decomposed only when the implementing model is in `planning.coarseModels`,
    as documented; any other model's run went back to PLANNING too, so an agent that repeated
    its change (`agent.empty-correction`) looped through decomposition (#77). It now stops in
    VERIFICATION with a `terminalReason` naming the failing validators and the empty
    correction. A run that moves on after a stop no longer keeps the earlier `terminalReason`.
  - An approved acceptance test whose proposed path already holds a file (the frozen test of an
    earlier run in the same workspace, or a file of the project) is written under a run-unique
    name next to it (`renamed` in `acceptance show` and in `acceptance.tests.decided`) instead
    of overwriting it; the later run overwrote the earlier frozen file and its agent was blamed
    with a HIGH `weakened.test-deleted` (#82).
  - Embedded mode with acceptance tests: the frozen files the harness writes on approval no
    longer count as the session's edits, so IMPLEMENTATION waits for the session instead of
    passing at once; stop the line keeps the frozen files of a run that can still be continued
    (`keptPaths` in the quarantine record) instead of deleting them, so the next verification
    does not report `acceptance.modified` (#81).
  - Re-verification after a change outside the run (#78), behind the new key
    `verification.reverifyOnChange`, which `harness init` writes as `true`. `run continue` on a
    run waiting in DECISION whose ChangeSet changed after its gate was evaluated records the
    change as evidence (`out-of-band-change`, event `verification.reverify.authorized`) and runs
    VERIFICATION and the independent review again on the new ChangeSet, instead of leaving an
    `INCONCLUSIVE` gate that forced a new run. A `project.yaml` without the key keeps that
    fail-closed behaviour and its configuration digest.

- Low friction for small changes and local metrics, wave 8 (#58). Every behaviour change is
  behind the optional `friction` section, which `harness init` writes; a `project.yaml` without
  it keeps the 1.0.0 behaviour and configuration digest (`harness config validate` shows it
  under `friction`). Guides: `docs/guides/low-friction.md`, `docs/guides/local-metrics.md`.
  - Fast lane (`friction.fastLane`): at INTENT the router's size and the task's risk flags decide
    the lane; size `S` without a risk flag (and without probes, a checklist or a criterion
    verified beyond `L1`) skips the agent ambiguity review, the decomposition, the preflight on
    the baseline and light mutation, and runs the agent review only on a signal. The lane and why
    are recorded (`lane.classified`, `lane-decision` evidence); a risk factor in the ChangeSet or
    a ChangeSet larger than `S` returns the run to the full flow (`lane.escalated`). Mandatory
    validators and the human decision are never skipped.
  - Faster verification in the fast lane: the affected Python tests first (a failure stops the
    attempt; the full suite runs before the gate), validators side by side up to
    `runtime.maxParallel`, and a `PASSED` result reused for the same validator, ChangeSet,
    baseline and configuration (`validator.reused`).
  - `harness do TEXT`: creates the task from text, runs it and, on a terminal, asks INTENT's
    questions and the decision after the brief; no task file.
  - Approval in advance (`friction.preAuthorization`): `harness do --pre-approve` and
    `harness task confirm --pre-approve` record a human decision before the ChangeSet exists,
    bound to the contract digest and to the condition gate passed, no risk factor, size `S`,
    with an expiry (new contract `pre-authorization.schema.json`, 38 schemas); DECISION applies
    it only when the condition holds and asks the person otherwise. Confirming the contract and
    approving in advance count as one interaction.
  - Batch decisions: `harness inbox --approve`, `--reject`, `--request-changes RUN=DIGEST`,
    `--decisions FILE` and `--batch`, each bound to its own digest; a refused one exits 5.
  - Change types (`friction.changeTypes`): documentation-only or configuration-only ChangeSets,
    detected from their paths, need no new tests or requirement traceability.
  - Friction targets per size (`friction.targets`) and `harness metrics` (no key needed): tokens
    (input, output, cache) and cost by agent, model, task and phase, models used, lines by agent
    invocation and by person, issues resolved and features delivered with links, time per task,
    phase, agent call and human wait, quality, friction against the targets and daily and weekly
    trends, for one repository or every repository of the run registry (`--all-repos`), as
    JSON, Markdown, CSV, Prometheus text or one self-contained HTML file; zero model calls and no
    per-person indicator. Costs of providers that report only tokens come from `metrics.prices`
    or `--prices` and are labelled estimated. `--narrative` adds one on-demand call to
    `metrics.narrative.command`. The dashboard of `harness api serve` gains a Metrics tab
    (`GET /api/metrics`, `GET /api/metrics/report`). Usage gains `cacheTokens`, reported by the
    Claude Code and Codex adapters.
  - Measured with `scripts/measure_friction.py` (fixture provider that calls no model; tokens are
    the provider's estimate, request characters divided by four, recorded by the harness; two
    small tasks each): recorded tokens 5792 and 5147 per task before, 3703 and 2956 in the fast
    lane, 3739 and 2982 in the fast lane with the approval in advance; agent calls 5 and 4
    before, 3 and 2 after (no `clarify` and no `review` call). Commands the person typed: 3 per
    task before (`task create`, `run start`, `gate decide`), 2 in the fast lane (`do`,
    `gate decide`) and 1 with the approval in advance (`do --pre-approve`); recorded human
    interactions 1 per task in all three. Wall-clock seconds of those commands on the
    measuring machine: 7.68 and 5.49 before, 4.39 and 4.36 in the fast lane, 3.65 and 5.56 with
    the approval in advance (one run each; not a benchmark).
  - Known limits: the affected-test selection reads Python imports and names only; the change
    type is decided from paths; a reused validator result assumes the validator is
    deterministic; the HTML report was viewed in one browser (Chrome, dark scheme), the dashboard
    tab only through tests and a syntax check of its script.
- Plan-approval checkpoint in PLANNING (closes #8). The workflow declared `approval.request`
  and the exit gate `plan_authorized` for PLANNING, but only decomposition plans were approved.
  Under `friction.planApproval: risk` (written by `harness init`) a task the router sizes `L` or
  that carries a risk flag waits after PLANNING for a person to approve its plan, bound to a
  plan digest that ignores step identifiers (`harness plan show` and `harness plan decide` with
  `APPROVE` or `REJECT` and the digest; `run start` exits 6 while it waits, like the other waits
  before DECISION, and `REJECT` ends the run). Other tasks skip it and the reason is recorded
  (`plan.approval.skipped`); a pre-authorised approval in force for the run's contract covers it
  (`plan.approval.covered`). `always` asks for every task. A `project.yaml` without the key keeps
  the 1.0.0 behaviour.
- Local API authentication, roles and decision audit (#18). The optional `api` section, written
  by `harness init`, makes `harness api serve` require `Authorization: Bearer TOKEN` on every
  route, the dashboard included (it asks for the token once and keeps it in the tab's
  `sessionStorage`); tokens are compared in constant time, come from environment variables named
  in the section (the start token is generated and printed once on standard error when
  `api.tokenEnv` is not set) and are never logged or stored. Roles: `viewer` reads, `reviewer`
  also decides, `admin` also reads `GET /api/config`; users are declared with an id, a role and
  the name of their token's variable. The authenticated user is the recorded decider (a different
  `actor_id` is refused with 403) and each API decision is appended to
  `.harness/audit/api-decisions.jsonl` without the token. A `project.yaml` without the section
  keeps the 1.0.0 behaviour (no authentication) and its configuration digest.
- The workflow's `exitGate`, `dependsOn` and `parallelizable` take effect under
  `governance.enforceWorkflow`, which `harness init` writes; a `project.yaml` without it keeps
  the 1.0.0 behaviour and configuration digest (closes #3). After a phase attempt passes, its exit
  gate and the condition of the transition it takes are evaluated from the run's records; an
  unmet one leaves the run `BLOCKED` with the reason and a `phase.exit_gate.unmet` event, and an
  unknown name is a configuration error. The built-in DECISION gate `decision_recorded` is the same
  check as its transition condition `decision_approved` (`REQUEST_CHANGES` and `REJECT` leave
  DECISION through `gate decide`). A phase starts only when its `dependsOn` phases passed, and the
  next phase comes from the workflow graph. In a parallelizable `VERIFICATION`, validators
  declared `parallelSafe` (new optional validator key) run at once up to `runtime.maxParallel`,
  recorded in the declared order; phases and the sub-tasks of a decomposition stay sequential on
  the run's one workspace, recorded in `workflow.schedule`. `dependsOn`, `parallelizable` and
  `runtime.maxParallel` are no longer marked declarative.
- Review panel with layered rule catalogs, wave 7 (#57). The single second reviewer read the whole
  diff in one request and its verdict was the model's. Under the new `review.panel` (written by
  `harness init`; absent keeps the single reviewer and the configuration digest) the second
  review is a panel of reviewers by domain (quality, architecture, resilience, tests,
  concurrency, pipeline security, plus the project's), each over its diff slice and only when its
  signal is present. Guide: `docs/guides/review-panel.md`.
  - Rule catalog in three layers merged with precedence project > language pack > built-in, with
    `supersedes` and inactive rules (missing tool or file, no reviewer); rules a tool verifies
    are checked by the harness or the linter, never by a model; `harness review rules sync`
    writes each project reviewer's rules block and `--check` reports drift.
  - Fixed output contract, reportable locations from the unified diff (findings elsewhere
    dropped and counted), out-of-catalog findings blocking only with concrete evidence, a
    verdict the harness recomputes, `UNKNOWN` answers retried once on the fallback provider and
    blocking when they persist, configured and executed models recorded.
  - Global and per-reviewer cache (the per-reviewer key leaves the mode out), budget proportional
    to the slice, reviewers in parallel, read-only sandbox, tool allowlist and strict MCP
    allowlist per reviewer; per-reviewer routing entries (`agentRouting.tables.FAMILY.reviewers`).
  - Consistency checks of the project before any reviewer, scoped auto-fix of errors on lines the
    agent wrote (provenance), an optional second opinion on blocking findings and
    `harness review variance`.
  - `harness review-code --mode hook|manual|staged` with base resolution (explicit, pull or merge
    request base, branch convention, merge base; hook mode aborts when the base cannot be
    fetched), `harness review hook install`, evidence as `refs/harness/review/pass/SHA` checked
    by `harness review verify` without models, one pull or merge request comment per passing
    result. `harness review` keeps the decision brief and gains the subcommands.
  - Token cost, measured with `scripts/measure_agent_tokens.py --compare review` (fixture
    provider, estimated input tokens = request characters / 4): on a one-line change with a new
    test the panel called two reviewers and used about 2,500 estimated input tokens per review
    against about 1,640 for the single reviewer; a re-review after a test-only change called only
    the tests reviewer (1,400), the quality answer coming from the cache.
- Repository policies applied (#5). `policies.repositoryContentTrusted` and
  `policies.destructiveActionsDefault` were read by nothing. Under the new
  `governance.applyRepositoryPolicies` (written by `harness init`; absent keeps the earlier
  behaviour and configuration digest), repository content is untrusted: requests carry a
  prompt-injection notice, the repository's instruction files reach the implementing agent only
  quoted (reviewers get their names) and the review panel flags instructions addressed to an
  agent in changed files; destructive commands (recursive deletes outside the workspace, force
  pushes, history rewrites, dropped data, ownership or permission changes outside the workspace)
  are refused unless a `process.destructive` grant allows them, each attempt a `HIGH` finding,
  and the Claude Code adapter passes them as disallowed tools. Neither policy is declarative any
  more.
- Capabilities per phase (#4). The grants of a run were the union of the profiles' and the
  project's capabilities for the whole run, so a project could not narrow a profile and the
  workflow's `allowedCapabilities` had no effect. Under the new `governance.phaseCapabilities`
  (written by `harness init`; absent keeps the earlier behaviour and configuration digest) the
  project narrows the profiles (`capabilities.extend` adds a scope explicitly), every grant made
  while a phase runs keeps only what the phase allows, an agent call outside IMPLEMENTATION is
  read-only and may start only its own command, and each phase attempt records its resolved
  grants (`capabilities.resolved`). `allowedCapabilities` is no longer marked declarative.
- Every call kind has a routing entry (#59). With `agentRouting.mode: tiered` and a Claude Code
  or Codex provider an `acceptance` call failed, because the routing tables had no `acceptance`
  entry. The default tables now route every call kind of provider protocol 1.1 (`acceptance` and
  `architecture` have their own rungs), and a table without the entry of a call kind uses the
  implement rung of the task size for it, with a warning in the decision
  (`fallback:implement:KIND:SIZE`) and an `agent.routing.fallback` event.
- Verification ladder, certification and delivery hygiene, wave 5 (#55). Every key is optional:
  a `project.yaml` without it keeps the earlier behaviour and configuration digest, a task without
  the new fields keeps its digest, and `harness init` writes them (`harness config validate`
  shows them under `ladder`). Guide: `docs/guides/verification-ladder.md`.
  - Ladder and certification: criteria declare their rung (`L0`-`L5`) under `verification`
    with `level`, `probe`, `tests`, `deferred` and `manual`; VERIFICATION certifies the ChangeSet
    per criterion from recorded
    evidence only (`CERTIFIED`, `PARTIAL`, `NOT_CERTIFIED`), a declared rung not reached is a
    `HIGH` `certification.level-not-reached` finding under `enforce`, and the gate, the brief, the
    dashboard and the pull request comment show the certification.
  - Probes: commands with generic assertions (exit code, JSON path present, absent, equal or
    matching, differs between variants, order, text) over a variant matrix; an unavailable probe
    is `BLOCKED`, never `PASSED`.
  - Preflight in PLANNING: probes and frozen acceptance tests on a scratch copy of the baseline
    and the verification plan (required and reachable rungs, and why); `UNAVAILABLE` waits for
    `harness verification decide --continue-uncertified` (criteria end `WAIVED`).
  - Deferred verification bound to the digest and the closure commit, closed by
    `harness evidence attach` with JUnit, SARIF or a CI status, with expiry and inbox entries.
  - Discriminating evidence and light mutation (`verification.mutation`): new tests classified
    on the baseline, each changed block reverted in a scratch copy (`tests.change-not-exercised`,
    `tests.weak`, `tests.broken`).
  - Profiles: verification capabilities per rung with read-only detection (simulator, emulator,
    container engine); built-in Go, Rust, JVM (Gradle, Maven), Swift and Android profiles under
    `toolchain.extendedProfiles`.
  - Manual checklist ticked in `gate decide --check` (and interactively); a person's attachments
    bound to a run or, as intake context, to a task revision.
  - Operational contract in the one clarification message (`intake.operationalContract`),
    `harness task confirm`, the interruption budget and its stop conditions.
  - Read-only `locate` call kind (protocol 1.1) for M and L tasks, once per task revision, on the
    cheapest rung; its locations feed the implement request and the context manifest.
  - Environment preflight in DISCOVERY and `harness doctor` (tools, variables, Git hooks with
    `--install-hooks`, dirty tree, baseline).
  - Worktree isolation (`run start --isolate worktree`, `harness run cleanup`); collisions block
    and nothing is reset.
  - Run registry outside the workspace (`runtime.stateDir`), read-only for the agent sandbox,
    listed by `harness registry` and `GET /api/registry`.
  - Complete delivery: staging only the run's files, a push that honours the hooks, the pull or
    merge request through the forge layer of wave 6 (`delivery.forge`: base, labels, template;
    `delivery.pullRequest.draft`) and a comment when the run is not clean, as the contract
    authorises.
  - `harness config lint`: tool versions, coverage thresholds, test statements and forbidden
    flags across `AGENTS.md`, `CLAUDE.md`, Cursor rules and Copilot instructions, with precedence.
  - Contracts: `task`, `clarification-request`, `clarification-record`, `human-decision`,
    `agent-invocation` and `project-config` gain optional fields left out when absent; new
    schemas `certification`, `deferred-verification` and `human-attachment`.
- Forges, standards, principles, testing strategy, architecture and embedded mode, wave 6
  (#56). Every setting is optional: a `project.yaml` without it keeps the 1.0.0 behaviour and
  configuration digest, and `harness init` writes them (`harness config validate` shows them
  under `engineering`). Guides: `docs/guides/forges.md`, `docs/guides/engineering.md`,
  `docs/guides/embedded-mode.md`.
  - Forges: GitHub, GitLab (REST v4 or `glab`), Bitbucket (REST 2.0, Code Insights), Azure
    DevOps and Gitea behind one interface, detected from the `origin` remote or set in
    `delivery.forge`: `harness pr publish` on any forge, `harness pr create` (template, labels,
    draft), `harness pr status` and `harness pr forge`; `harness trace --format codequality`
    (GitLab Code Quality); CI templates for GitHub Actions, GitLab CI, Jenkins and Azure
    Pipelines with `harness verify-approval`. Tokens only from the environment.
  - Language standards packs for Python, JavaScript, TypeScript, Node.js, React, Angular, Vue,
    Java, Kotlin, Go, Rust, Swift, C#, PHP and Ruby (96 cards), with repository overrides that
    take precedence, `harness standards show`, the cards of the touched files in the implement
    request, the cards no tool verifies in the review call, and optional validators for the pack
    tools the repository configures; new parsers for Checkstyle XML, RuboCop JSON, Cargo JSON and
    MSBuild diagnostics.
  - Engineering principles as deterministic proxies (`verification.principles`: duplication,
    nesting, module size, inheritance depth, unused public API, reformat-only files) and a
    principles checklist inside the existing review call.
  - Testing strategy (`testing.strategy`): detected or asked; TDD records red, green and
    refactor evidence (`harness.tdd`); BDD writes the criteria as Gherkin scenarios a person
    approves, frozen and run with the project's BDD runner.
  - Architecture: one cached survey per existing project or options for a new one (provider call
    kind `architecture`), a person's approval or choice (`harness architecture show`, `decide` and
    `refresh`, an ADR for a new project) and layer rules enforced as forbidden dependencies in
    every language the packs know (`architecture.layer-violation`).
  - New and existing projects are told apart deterministically (`harness project show`), and
    `intake.projectSetup: ask` asks what is not established (rule `P1`).
  - Embedded mode: `harness mcp serve` (MCP over stdio; no tool decides for a person), the
    `session` provider and `harness init --agent-skills` (skills for Claude Code and Codex).
  - Token cost, measured with `scripts/measure_agent_tokens.py` (fixture provider, estimated
    tokens recorded by the harness): 4670 and 4735 tokens per run before, 6359 (with the one
    architecture survey) and 5575 after; no extra agent call besides the survey.
  - Fixed: the frozen acceptance tests no longer count as changes outside a task's ownedPaths.
  - Known limits: the forge providers, the pack tools and the MCP server are tested against
    recorded request shapes, fake transports and fixture runners, not against the live forges,
    linters or agents; the testing strategy detected under `auto` is read once per command, so a
    repository that gains feature files during a run switches to BDD on the next command.

- Better agent results, wave 2 (#52, closes #37, #38, #39, #40, #41, #42, #43, #44; implements
  the proposal of #7 behind a setting). The motivating figures come from the thesis evaluation
  (reported there, not re-measured here). Every setting is optional: a `project.yaml` without
  it keeps the 1.0.0 behaviour and configuration digest, and `harness init` writes them all
  (`harness config validate` shows them under `agentResults`). Guide:
  `docs/guides/agent-results.md`.
  - Provider protocol 1.1: read-only request kinds `clarify`, `acceptance`, `plan` and `review`
    with rendered instructions and a `result` object; a read-only call that changes the
    workspace is undone and recorded as a HIGH finding. Invocations record `callKind` and
    `effort`. Adapters that speak only 1.0 must read `kind` before enabling these settings.
  - INTENT (#37): agent review of ambiguity and completeness, once per task revision,
    questions grouped by category (rule `A1`); answers that cite documents or ids the task and
    the workspace lack get a question (rule `A2`).
  - SPECIFICATION: independent acceptance tests written from the criteria by a separate call,
    approved by a person (`harness acceptance show|decide`), frozen and checked to fail before
    and pass after the change.
  - PLANNING (#39): decomposition of tasks above `planning.threshold` requirements into
    sub-tasks that partition them, approved by a person (`harness plan show|decide`), each with
    its own verification, correction budget and gate; adaptive granularity splits a capable
    model's coarse attempt only after it fails.
  - IMPLEMENTATION: the gate contract and `harness check` (records nothing), per-call
    permissions derived from the capability grants, a bounded context manifest (#41), active
    lessons (#43), the remaining budget (#42) and the routing decision (#44).
  - VERIFICATION (#40): declared interface conformance, architecture limits (pre-existing
    violations are LOW), severe security patterns, verifiable task constraints, weakened
    controls, test quality (assertion-free tests, changed-line coverage, a reordered rerun),
    context-aware secrets, SARIF reports with tool and rule versions, invariant commands and
    risk factors that block, require `--acknowledge-risk` in the decision or inform; a failing
    mandatory validator is compared with the baseline and only introduced failures block
    (`PREEXISTING_ERROR`, `INTRODUCED_ERROR`, #7), and a Ruff/Mypy ratchet keeps optional
    validators from getting worse.
  - Corrections: reproduce-first corrections after REQUEST_CHANGES, an `agent.empty-correction`
    finding, and REQUEST_CHANGES with blocking items (`--change-request
    "description::condition"`).
  - INDEPENDENT_REVIEW (#38): a second agent after the deterministic checks pass, once per
    ChangeSet digest; HIGH and CRITICAL findings block and return to the agent within the
    correction budget (trigger `REVIEW_FINDINGS`).
  - Stop the line: a run that stops unapproved has its changes quarantined as a patch and the
    baseline restored, or new runs wait for `harness run quarantine`; changes outside
    `ownedPaths` are HIGH findings.
  - Budget (#42): limits per call, task and run with a warning threshold;
    `budget.exceeded` blocks the next agent call until `harness budget raise`.
  - Lessons (#43): findings that caused a correction become proposed project memory.
  - Routing (#44): `agentRouting: tiered` chooses model and effort per call kind and task size,
    escalates effort before model after quality failures, and records every decision;
    `harness routing calibrate` reports cost per approved task.
  - Contracts regenerated (no new schema file): `agent-invocation`, `clarification-record`,
    `clarification-request`, `human-decision`, `project-config` and `provider-feedback` gain
    optional fields that are left out when absent.
- Approval valid for what gets merged (#54). A decision bound the digest of the workspace
  ChangeSet, but a team merges a pull request. Under `delivery.closureCommit` (written by
  `harness init` as `branch`), `CLOSURE` writes the approved ChangeSet as one commit on
  `harness/<run id>` (or on the current branch with `head`), built in a temporary index without
  touching the working tree, with the trailers `Harness-Run`, `Harness-Task`,
  `Harness-ChangeSet`, `Harness-Decision` and, for an exception, `Harness-Exception`; its diff is
  recomputed and must be the approved digest before any ref moves. A run that started on
  uncommitted changes gets no commit and the reason is recorded. `harness export --bundle`
  writes a portable evidence bundle (event chain, records, artifacts, manifest of digests; new
  contract `evidence-bundle-manifest.schema.json`) and `harness verify --bundle` checks it
  without the workspace. `harness verify-approval --base --head [--bundle]` recomputes the
  ChangeSet digest of a commit range and exits with 0 only when an unexpired approval in a
  verified bundle (or the workspace record) is bound to it, and with 5 otherwise.
  `harness pr publish` posts the decision brief (one comment per run) and the SARIF report on a
  GitHub pull request through the GitHub CLI or the REST API with a token from the environment.
  Without the key the harness never commits, as in 1.0.0.
- Project-defined validators and profiles (#54). A project could only pick validator ids of the
  built-in profiles. Under `toolchain`: `profilePaths` loads profiles from the repository,
  `profileDetection: all` selects every detected profile (written by `harness init`),
  `interpreter: auto` runs the Python validators with `.venv`/`venv`, `uv run --no-sync` or
  `poetry run` (written by `harness init`), and `validators` replaces or adds validators with a
  command, a named output parser (`sarif`, `junit`, `ruff`, `mypy`, `eslint`, `tsc`, `pytest`),
  a severity mapping, a failure severity and `passEnv`. Without the section the resolved
  configuration and its digest are unchanged.
- Built-in agent adapters and provider environment (#54). `agentProviders.<id>.kind` accepts
  `claude-code`, `codex`, `gemini-cli` and `aider`: the harness runs the CLI in its
  non-interactive mode under the same grant and sandbox, renders the task, plan, memory and
  correction feedback as a prompt, and records the session, tokens and cost the CLI reports as
  `REPORTED` usage. The adapters are tested against fake CLIs, not live agents. A provider
  declares `passEnv` and `env` (literal or `{fromEnv: NAME}`; a missing `fromEnv` blocks
  `IMPLEMENTATION` before the provider starts); only names are recorded and the values are
  redacted from every artifact and from the agent's summary. `runtime.extendedRedaction`
  (written by `harness init`) adds model-API keys, Slack tokens, JSON Web Tokens and URL
  passwords to the redaction rules. The adapter template of the external-agents guide, which
  failed with `KeyError: 'AGENT_COMMAND'` because the provider only received `PATH`, `HOME`,
  `LANG` and `TMPDIR`, now declares `passEnv`.
- Large repositories (#54). On a generated repository of 10,001 tracked files a run took
  16.47 s, peaked at 700,841,984 bytes and stored a 119,112,913-byte baseline holding an ignored
  `.env`. Under `workspace.snapshot: git`, `workspace.baseline: manifest` and
  `workspace.snapshotCache` (all written by `harness init`) files are listed through Git
  (ignored files are never read or stored, and the out-of-ChangeSet guard watches them), the
  baseline is a manifest of digests whose text comes from Git when a file enters a diff, and
  digests are cached by size, modification time, change time and inode in a sealed cache; the
  same run took 4.27 s, 94,109,696 bytes and a 1,911,156-byte manifest, without `.env`.
  `harness gc` gains `retention.orphanArtifacts` (written by `harness init`) for artifacts
  nothing references, and now finds artifact references inside flags that hold JSON.
- Provenance per component and agent self-report (#54). Under `provenance.agentSnapshots`
  (written by `harness init`) the files of the ChangeSet scope are recorded before and after
  every agent invocation; every ChangeSet file is attributed to the invocation that wrote it or
  recorded as an out-of-band edit (`provenance.out-of-band-edit` events, `DECISION` evidence,
  new contract `component-provenance.schema.json`), and `harness review` shows it. Under
  `provenance.selfReport` the agent is asked for its assumptions, discarded alternatives,
  low-confidence areas and unrequested changes, stored as `REPORTED` data and contrasted with
  the ChangeSet (new contract `agent-self-report.schema.json`; 34 schemas), never as a check.
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
  `--actor` is given (otherwise `human.local` and `human.web` as before) and records in a gate
  decision where the id came from (`identitySource`: `explicit`, `git`, or `fallback` when Git has
  no usable identity, in which case the default id is used with a warning instead of failing);
  `confirmDecisionDigest`, which on a terminal sends every `gate decide` through the interactive
  confirmation (the decision brief, then the first 12 characters of the digest; exit 5 on a wrong
  answer), even when every option is given; and
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
