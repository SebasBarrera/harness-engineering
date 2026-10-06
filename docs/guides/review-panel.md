# Review panel

Since 2.0 (#57) the second review of a change is a panel of reviewers by domain instead of one
reviewer that reads the whole diff. The machinery is generic; the rules come in three layers:

- **A, built-in** (`src/governed_harness/resources/review/rules.yaml`): language-neutral rules for
  tests, resilience, concurrency, pipeline security, quality and architecture;
- **B, language packs**: the cards of the [standards packs](engineering.md#standards-packs) of the
  project, read as rules; each pack's `review.yaml` gives a card its domain, says which cards
  block and declares the pack's review signals;
- **C, project**: rules the team writes for its framework and its business in
  `.harness/review/rules/DOMAIN.md`.

`review.panel` (written by `harness init`) turns it on. A `project.yaml` without it keeps the
single reviewer of `review.agentReview` and its configuration digest. The panel runs in
INDEPENDENT_REVIEW of a governed run and, outside runs, with `harness review-code`.

The cost rule: **a model reviews only what a tool cannot check, only where the change gives it
something to review, and never twice**.

- A rule verified by a tool (`verifiedBy: tool:harness:CHECK` for the harness's own checks,
  `tool:TOOL:RULE` for a linter rule) is checked deterministically and never sent to a model.
- A reviewer runs only when its diff slice changed and its signal is present.
- Every reviewer answer is cached; a repeated review of the same diff is one cache read.
- The budget of a reviewer is proportional to the size of its slice.
- The verdict is recomputed by the harness from the findings; the report is deterministic.

## Reviewers

Six reviewers ship with the harness (`resources/review/agents/ID.md`): `quality`,
`architecture`, `resilience`, `tests`, `concurrency` and `pipeline-security`. A project adds or
replaces reviewers in `.harness/review/agents/ID.md`; `review.panel.reviewers` limits the ones
that may run. A reviewer is Markdown with a YAML frontmatter:

```markdown
---
id: payments
domain: payments
title: Payments
models: {claude-code: MODEL, codex: MODEL}
effort: high
timeoutSeconds: 900
maxBudget: 40000
modes: [run, hook, manual, staged]
diffSlice: [src/payments/**]
activation: changed
patterns: ['\bcharge\(']
tools: [Read, Grep, Glob]
mcpServers: []
maxFindings: 10
---
Review the payment flows of the change.

<!-- BEGIN HARNESS REVIEW RULES (harness review rules sync; do not edit) -->
<!-- END HARNESS REVIEW RULES -->
```

- `diffSlice`: `sources`, `tests`, `pipeline`, `all` or path globs. `pipeline` holds CI
  definitions, hooks, scripts, `Makefile` and `Dockerfile`, plus the files each pack names.
- `activation`: `changed` (the slice is not empty), `always`, or `signal:NAME`: a pack signal
  such as `concurrency` (concurrency primitives) or `external-calls` (calls that leave the
  process) on a changed line of the slice. `patterns` add regular expressions of the project.
  No changed test, no `tests` reviewer; no concurrency primitive, no `concurrency` reviewer; no
  script, hook or CI file, no `pipeline-security` reviewer.
- `models`: a model per provider family or provider id; without one, `agentRouting` decides
  (the `reviewers` entry of the family table, else the `review` rung, see
  [routing](agent-results.md#routing-44)).
- Between the markers is the generated block of the rules of the reviewer's domain. Only
  `harness review rules sync` writes it; `harness review rules sync --check` exits with 6 when a
  project reviewer's block drifted from the catalog, and a review reports the drift. A built-in
  reviewer the project did not copy renders its block when it is loaded.

The harness adds a fixed output contract to every request: one JSON object with `verdict`,
`findings` (`file`, `side` `new` or `old`, `line`, `rule`, `severity` `error` or `suggestion`,
`issue`, `evidence`) and `summary`; at most `maxFindings`, errors first by rule priority.

## Rules

A rule has an id (`domain.name`), a domain, a severity (`blocking` or `warning`), `when`,
`exceptions`, good and bad examples, a confirmation budget (`budget: {tool: read, maxReads: 2}`:
a reviewer that cannot confirm a finding within it reports nothing, the catalog prefers false
negatives), `supersedes`, a `priority` (1 first) and `verifiedBy` (`ai` or tool ids). A project
rule file:

````markdown
---
domain: resilience
---

## project.client-timeout: The service client sets a timeout

- severity: blocking
- priority: 3
- when: a change calls the service client
- exceptions: health probes; local stubs
- supersedes: resilience.external-call-limits
- budget: 1 read

Every call through the service client passes an explicit timeout.

Bad:

```text
client.get(url)
```

Good:

```text
client.get(url, timeout=5)
```
````

The layers merge by id with precedence C > B > A; a rule that `supersedes` others removes them.
A rule whose input is missing is inactive, with the reason, in `harness review rules show`: its
tool is not configured in the repository, a file it `requires` is absent, no reviewer covers its
domain, or (under `policies.repositoryContentTrusted: true`) it flags instructions in content.

The built-in deterministic checks are `tautological-assertion`, `weakened-gates`,
`dangerous-paths`, `temporary-files`, `secrets` and `embedded-instructions`. Outside a run, with
`review.panel.runTools`, the panel runs once each pack tool the repository configures that
verifies an active rule (Ruff for `tool:ruff:B006`) and keeps its diagnostics of those rules on
changed lines; inside a run the validators of VERIFICATION already ran them.

## What the harness decides

- **Reportable locations** come from the unified diff: `new` for an added line with its line
  number in the new file, `old` for a removed line with its number in the old file; context lines
  only advance the counters; a deleted file is reportable on its old side. A finding elsewhere is
  dropped and counted (`droppedOutside`).
- A finding on a `warning` rule is a suggestion at most. A finding on a rule outside the catalog
  is an error only when its `evidence` quotes a changed line near it; otherwise it is a
  suggestion (`downgraded`).
- The verdict: `FAIL` with any error or failed consistency check, `UNKNOWN` when a reviewer never
  gave a valid answer, `PASS_WARN` with suggestions only, `PASS` otherwise. Under
  `review.panel.mode: enforce`, `FAIL` and `UNKNOWN` block.
- An invalid answer is `UNKNOWN` and gets one retry on `review.panel.fallbackProvider` (else the
  same provider); a persistent `UNKNOWN` blocks. The configured and the executed model of each
  reviewer are recorded.
- `review.panel.consistencyChecks` (commands of the project) run before any reviewer; when one
  fails, no model is called.
- `review.panel.secondOpinion: {mode: blocking}` asks another provider to confirm the blocking
  findings of the reviewers; an unconfirmed one becomes a suggestion. `harness review variance --runs N`
  runs the same review N times without cache and reports the verdict stability, the
  frequency of each finding and the mean pairwise agreement.

## Cache, budget, isolation

- **Cache** (`.harness/review/cache`, `review.panel.cache.ttlDays` and `maxEntries`): a global key
  (mode, diff hash, reviewer definitions and catalog, runner version, skip flags, forced model,
  provider, fallback, options) and a per-reviewer key without the mode (version, base, provider,
  fallback, forced model, reviewer, model, slice hash, prompt hash, message hash with the
  reportable locations and the diff, definition hash, runner version, MCP configuration hash,
  options), so a manual review and the pre-push hook reuse each other's answers. A review served
  from the global cache calls no model: its report has `cache.global: hit`, `tokens.total` and
  `tokens.modelCalls` 0, every reviewer that answered marked `cache: hit`, and the cached
  review's numbers under `tokens.cachedFrom` (`total`, `modelCalls`). A reviewer served from the
  per-reviewer cache is likewise `cache: hit` with 0 tokens and is not counted in `modelCalls`.
- **Budget**: `baseTokens + tokensPerLine × changed lines`, at most `maxTokens` or the reviewer's
  `maxBudget`, sent in the request and compared with the reported usage (`overBudget`). Inside a
  run the governed budget (`budget`) applies to every reviewer call too.
- **Isolation**: under `runtime.agentSandbox: enforce` a reviewer's process runs in the agent
  sandbox with the whole workspace read-only; the workspace is compared before and after the
  batch and a batch that changed it is undone and discarded. The request carries the reviewer's
  tool allowlist and the MCP servers of `review.panel.mcpServers` it names (definitions from the
  repository's `.mcp.json`, never recorded). The Claude Code adapter runs a reviewer with
  `--permission-mode default`, `--allowedTools`, `--strict-mcp-config`, `--mcp-config` and
  `--setting-sources project`; the Codex adapter with `--sandbox read-only`. These command lines
  follow the CLIs' documentation and are tested against fake CLIs, not against the live agents.

## In a governed run

The panel replaces the single reviewer in INDEPENDENT_REVIEW with the same cost rule (no reviewer
while a blocking deterministic validator fails, again only when the ChangeSet digest changed).
The report, every request and answer and each reviewer's sandbox are evidence;
`review.panel.completed` records the verdict, the tokens and the cache. Findings are recorded
under `review.agent`: errors are `HIGH` under `enforce` (`MEDIUM` under `warn`), suggestions
`LOW`; an `UNKNOWN` reviewer blocks the validation.

**Scoped auto-fix** (`review.panel.autoFix: {mode: scoped, maxAttempts: N}`): only errors on lines
the agent wrote in the run go back to IMPLEMENTATION as feedback: an added line of a file whose
provenance is the agent's (`provenance.agentSnapshots`), never a file a person also edited, never
a removed line, never a suggestion; at most `maxAttempts` times (`review.autofix.requested`,
`review.autofix.declined`). The other errors stay for the person who decides.

## Outside a run: `harness review-code`

```bash
harness review-code                       # the branch against its base
harness review-code --mode staged         # the index against HEAD
harness review hook install               # pre-push: harness review-code --mode hook
harness review verify --sha HEAD          # CI: check the evidence, no model
harness review rules show                 # the catalog, inactive rules, reviewers
harness review rules sync --check         # drift of the generated blocks (exit 6)
```

The base is, in order: `--base`, the pull or merge request base the CI exposes
(`GITHUB_BASE_REF`, `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `BITBUCKET_PR_DESTINATION_BRANCH`,
`SYSTEM_PULLREQUEST_TARGETBRANCH`), the branch convention (`review.panel.baseBranches` globs,
then `release/*` and `hotfix/*` against the default branch and every other branch against
`develop` when it exists), then the merge base with the default branch. In hook mode the base is
fetched from `origin` first and a failed fetch aborts the review (exit 6).

A `PASS` or `PASS_WARN` review of a commit is recorded as `refs/harness/review/pass/SHA` (or
`pass-warn/SHA`), a blob with the report; `harness review verify` checks without any model that
the report is bound to the commit, that its digest matches its content and that its diff is the
diff between its base and the commit (exit 6 otherwise). Push the refs to let CI verify them
(`git push origin 'refs/harness/review/*'`). With `--comment` (or `review.panel.comment`) and a
pull or merge request number (`--pull-request` or the CI environment) one comment per passing
result is posted through the [forge layer](forges.md): none on a cache hit, none on a `FAIL` or
`UNKNOWN` review. Exit code 6 when the verdict is `FAIL` or `UNKNOWN` under `enforce`.

## Token cost

`scripts/measure_agent_tokens.py --compare review` runs the quickstart's Python project twice
with the same two tasks, once with the single reviewer of #38 (`review.panel` removed) and once
with the panel, everything else as `harness init` writes it. A fixture command provider calls no
model and reports as usage an **estimate**: the characters of the request JSON divided by four
(not a tokenizer). Each task changes one line of `src/sample/pricing.py` and adds a test.

| Configuration | Run | Review calls | Estimated input tokens of the review | Recorded tokens of the run |
|---|---|---:|---:|---:|
| single reviewer | task_first | 1 | 1,637 | 7,342 |
| single reviewer | task_second | 1 | 1,640 | 6,341 |
| panel | task_first | 2 (quality 1,073, tests 1,424) | 2,497 | 8,238 |
| panel | task_second | 2 (quality 1,074, tests 1,425) | 2,499 | 7,239 |

On this small change the panel costs about 52 % more input tokens per review than the single
reviewer: two reviewers instead of one, each with its rules block and the output contract. The
architecture, resilience, concurrency and pipeline-security reviewers were not called: no
declaration, import, external call, concurrency primitive or pipeline file changed, and the
rules the harness checks itself (tautological assertions, weakened gates, credentials) were not
sent to any model.

Where the panel saves is the next review of the same branch. With `harness review-code` on the
same project (same estimate), a first review called quality (1,021) and tests (1,378), 2,399 in
all; after a commit that only added a test, the second review called the tests reviewer alone
(1,400) and took the quality reviewer's answer from the per-reviewer cache; a repeated review of
an unchanged diff calls no reviewer at all (global cache). The single reviewer re-reads the
whole diff on every change of the ChangeSet. These figures come from the estimate of a fixture
provider; the tokens of a live model depend on its tokenizer, on the files a reviewer opens to
confirm a finding and on its answer.

## Where it is tested

| What | Tests |
| --- | --- |
| Diff, catalog, reviewers, signals, contract, cache, panel | `tests/unit/test_review_framework.py` |
| Reviewer isolation in the adapters | `tests/unit/test_native_request_kinds.py` |
| `harness review-code` and the `harness review` subcommands | `tests/integration/test_review_code.py` |
| The panel in INDEPENDENT_REVIEW, scoped auto-fix | `tests/integration/test_review_panel_run.py` |
| Demonstration flows | `scripts/demo_flows.py review-panel`, `review-cache`, `review-unknown`, `review-hook` |
