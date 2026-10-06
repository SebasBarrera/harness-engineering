# Better agent results

Since 2.0 the harness can do more than verify what an agent delivered: it can ask an agent to
review the task before work starts, hand the agent the gate it will face, compare failures with
the baseline, stop the line when a step is not approved, review the change with a second agent,
decompose large tasks, bound the context, govern the budget, learn from corrections and route
each call to a model and effort. The motivation for each setting is the evaluation of the
thesis (issues #37 to #44 and #52).

Every setting below is optional. A `project.yaml` without it keeps the 1.0.0 behaviour and its
configuration digest; `harness init` writes them all (see
[the file `harness init` writes](../reference/configuration.md#file-written-by-harness-init)).
`harness config validate` prints the effective values under `agentResults`.

## Request kinds (provider protocol 1.1)

Besides `implement`, a command provider may receive six read-only request kinds (`architecture`
since #56, see [standards, principles, testing and architecture](engineering.md); `locate` since issue #55,
see [the verification ladder](verification-ladder.md)). The request
says `"kind"`, `"readOnly": true` and carries rendered `"instructions"`; the response is the 1.0
object (`status`, `summary`, optional `usage`) with a `result` object:

| Kind | Phase | `result` | Setting |
|---|---|---|---|
| `clarify` | INTENT | `{"questions": [{"category", "target", "text"}]}` | `intake.ambiguityReview: agent` |
| `acceptance` | SPECIFICATION | `{"tests": [{"path", "content"}]}` | `verification.acceptanceTests.mode: agent` |
| `plan` | PLANNING | `{"subtasks": [{"title", "requirements", "criteria", "constraints"}]}` | `planning.decomposition: agent` |
| `review` | INDEPENDENT_REVIEW | `{"findings": [{"severity", "rule", "path", "line", "message", "evidence"}]}` | `review.agentReview` |
| `locate` | INTENT | `{"locations": [{"path", "line", "evidence", "reason"}], "questions": [{"text"}]}` | `context.locate` (since #55; M and L tasks only, once per task revision) |
| `architecture` (#56) | INTENT (`mode: advise`) or DISCOVERY (`mode: survey`) | advise: `{"options": [{"id", "style", "title", "benefits", "costs", "fit", "recommended", "layers", "allow"}]}`; survey: `{"style", "summary", "layers", "allow"}` | `architecture.mode: agent` |

A read-only call that changes the workspace is undone, recorded as a HIGH
`agent.read-only-violation` finding and its answer is discarded. A `result` that does not match
the table is a protocol error; the phase that asked is blocked. The request, the provider's
output and the usage it reports are evidence of the run, and each kind may use its own provider,
model and effort (`intake.clarifyAgent`, `verification.acceptanceTests.author`,
`planning.planner`, `review.reviewer`, `context.locate.agent`), sent in the request as
`routing`. Without one, `locate` takes the router's `locate` rung, else the bottom rung of the
family's escalation ladder: the cheapest adequate model.

### A second attempt for a broken answer (#80)

In the 2.0.0 pilot an `acceptance` call answered with something that was not JSON and
SPECIFICATION blocked at once, while the review panel already retries an invalid answer once on
its fallback provider. `runtime.contractRetry`, which `harness init` writes as `{mode: once}`
since #80, gives every read-only kind that second attempt:

```yaml
runtime:
  contractRetry:
    mode: once                  # once, or off
    fallbackProvider: second    # optional: an agentProviders entry (or simulated)
```

An answer breaks its contract when it is a protocol error (no JSON object, no `result` object)
or when the phase rejects its `result` (the checks behind `intake.agent-review-malformed`,
`acceptance.malformed`, `planning.plan-malformed`, `locate.malformed`, `architecture.malformed`
and `review.agent-malformed`). The call is then sent once more, to `fallbackProvider`
when it is set, else to the same provider; the first attempt stays recorded (its request,
output and invocation) and an `agent-contract-retry` evidence and an `agent.call.contract-retry`
event say why and where the second went. The phase uses the second answer: a valid one continues
as a valid first answer would, a broken one blocks as before, with one finding. A provider that
did not answer, a budget block or a call that changed the workspace is not retried. A
`fallbackProvider` that is not `simulated` or an entry of `agentProviders` is a configuration
error. Without the key the first broken answer blocks, as before. Transient failures of a command
provider keep their own retries (`runtime.providerRetries`).

A built-in adapter (`kind: claude-code`, `codex`, `gemini-cli`, `aider`) sends a read-only
request as a prompt (the instructions, then the request as JSON) and reads the `result` object
from the last JSON object with a `result` key in the agent's answer. The router's model and
effort become `--model` and, for Claude Code, `--effort`; for Codex,
`-c model_reasoning_effort="..."`. These paths are covered by tests against the documented
output formats, not against the live agents.

An implement request keeps the 1.0 form unless an agent-results key adds something to it; then
it is version `1.1` and may carry `kind`, `instructions`, `workspace`, `gate`, `permissions`,
`routing`, `budget`, `contextFiles`, `lessons` and `acceptanceTests`, as described below. Since issue #55
it may also carry `locations` (the answer of the `locate` call) and `attachments` (a
person's intake attachments), each only when there is something to send. See
[verification ladder](verification-ladder.md).

## INTENT: ambiguity and completeness (#37)

After the deterministic rules (C0 to C3, T1), `intake.ambiguityReview: agent` asks for
questions about ambiguities, contradictions, boundaries and errors, and about completeness:
missing flows, actors and roles, data and states, edge cases, non-functional requirements,
integrations, acceptance evidence and what is out of scope. Questions are grouped by category
(rule `A1`); under `intake.criteriaPolicy: enforce` INTENT blocks until a person answers them
with `harness task clarify`. The review runs once per task revision (keyed by its digest); an
answer with no question is evidence and the run continues.

`intake.validateAnswers` checks the answers that produced the current revision: an answer that
cites a requirement id or a document that neither the task nor the workspace contains gets a
question (rule `A2`) asking to attach or transcribe it, and the agent review of the revised task
checks the answers against the documents they cite.

### A review that converges (#79)

With the bare `ambiguityReview: agent` every answered revision gets a new review, and nothing
stops it from raising new questions: in the 2.0.0 pilot two of four governed Haiku runs asked
10, 8 and 8 questions in three rounds and never reached IMPLEMENTATION. The object form, which
`harness init` writes since #79, makes the review converge:

```yaml
intake:
  ambiguityReview:
    mode: agent
    maxRounds: 3        # rounds of agent questions a person answers (default 3)
    maxQuestions: 8     # agent questions asked at most in one round (default 8)
    onExhausted: assume # assume (continue) or block (default when absent)
```

- The `clarify` request carries `previousQuestions` (every question asked about the task in an
  earlier revision, with its rule, category, target and the answer it got, or `null`), the
  `round`, `maxRounds` and `maxQuestions`, and instructions to ask only about blocking ambiguity
  that the latest revision introduced or left open, never again about a point an answer
  settles, and nothing when the task can be implemented and verified as it stands.
- A question already asked (same rule and normalised text: case, punctuation and spacing do not
  count) is dropped, also within a round; a round keeps at most `maxQuestions`. The review's
  evidence (`agent-clarify-review`) says how many were `dropped`.
- A round is a clarification a person answered in this run that included agent questions (rule
  `A1`). When `maxRounds` rounds were answered and the agent still raises points, the review is
  exhausted (`intent.ambiguity.exhausted`, with the rounds, the open points and the action):
  - `assume`: the open points become explicit assumptions of a new task revision
    (`metadata.assumptions`: `assumptionId`, category, target, the question and the assumption
    that the implementation takes the reading most consistent with the requirements and criteria
    and states it). The revision is stored (and pinned to the run under
    `governance.pinTaskRevision`), recorded as `ambiguity-assumptions` evidence and
    `intent.assumptions.recorded`, and the run continues. The assumptions are visible in the
    task of the implement request, in the operational contract (`assumptions`, part of its
    digest), in the gate contract (`gate.assumptions`) and in the decision brief
    (`asked.assumptions` and one line each under what was not verified).
  - `block`: the open points are asked again and INTENT stays blocked, as before #79, with the
    exhaustion recorded.

The bare `agent` keeps the single-round review and its configuration digest.

## SPECIFICATION: independent, frozen acceptance tests (#52)

`verification.acceptanceTests.mode: agent` asks a separate call for pytest files written from the
criteria only, under `verification.acceptanceTests.directory` (default `tests/acceptance`).
SPECIFICATION blocks until a person decides them, bound to their digest:

```bash
harness acceptance show --run <runId>
harness acceptance decide --run <runId> --decision APPROVE --digest <digest> --rationale "..."
```

Approval writes the files, freezes their digests and runs them once on the workspace before the
change (passing there is a MEDIUM `acceptance.passes-before` finding). Every later VERIFICATION
fails if a frozen file changed (`acceptance.modified`, HIGH) or the tests do not pass. A proposed
path where a file already exists, for example the frozen test of an earlier run in the same
workspace, is never overwritten: the file is written next to it with the run's suffix
(`test_ac_1_<run>.py`), and `acceptance show` lists the new name under `renamed` (#82).

## PLANNING: decomposition of large tasks (#39)

Above `planning.threshold` requirements (default 12), `planning.decomposition: agent` asks for
sub-tasks that partition the requirements (every requirement exactly once). PLANNING blocks until
a person approves the plan, bound to its digest:

```bash
harness plan show --run <runId>
harness plan decide --run <runId> --decision APPROVE --digest <digest> --rationale "..."
```

The sub-tasks then run in order on the same workspace, each through IMPLEMENTATION and
VERIFICATION with its own correction budget and its own gate (`subtask-<n>`), recorded as
`subtask.started` and `subtask.completed` events; the task's constraints apply to every sub-task.
A sub-task that does not pass stops the ones after it; the run reaches DECISION when all passed.
`REJECT` keeps the task whole. Under `planning.granularity: adaptive` a model listed in
`planning.coarseModels` starts with the whole task, and the run returns to PLANNING to decompose
only when that attempt fails its corrections. Another model's attempt is not sent back to
PLANNING: when its corrections are spent the run stops in VERIFICATION (exit 6) with a
`terminalReason` that names the failing validators and says when the last correction changed
nothing (`agent.empty-correction`).

## IMPLEMENTATION: what the agent receives

- `runtime.gateContract`: the `gate` block lists the validators (command, mandatory or optional),
  the enabled checks with their policies, the review rules, the blocking severities and the
  absolute workspace path. `harness check` runs the same validators and diff checks on the
  workspace and records nothing (exit 0 when they pass, 6 otherwise); it reads the configuration
  and a check state file the harness writes before the call, so it runs inside the agent
  sandbox. Since #84 the contract suggests only a command the agent may run, by its capability
  grants (`process.execute`): `checkCommand`, `harness check --path <workspace> --run <runId>`,
  when they allow `harness`; otherwise `checkCommands`, the validator commands they allow (none
  when they allow none), and the instructions say to run those, or that the harness runs the
  gate after the call. Under `verification.requirementTraceability` (`enforce` or `warn`) the
  block also has `traceability`: the policy, the identifier of each requirement the check looks
  for in the tests (`identifier`, `requirementId`, the text), the requirements it does not check
  (`notChecked`: no identifier) and the naming rule it applies (`rule`: a test file, class or
  function whose name contains the identifier as whole tokens, or the identifier as a whole word
  in a test's source or strings). Since #79 it also carries the task revision's `assumptions`.
- `governance.phasePermissions`: `permissions`, derived from the capability grants of the agent
  actor: the filesystem read and write scopes, the commands, network access; no write scope for
  the read-only kinds. Recorded as evidence of the call.
- `context.manifest: auto`: `contextFiles`, files ranked deterministically (referenced by the
  task, naming its requirement ids or key terms, changed by earlier steps, declared interface
  files, paths of active lessons) and capped by `context.maxFiles` and `context.maxBytes`, with
  paths, sizes, digests and reasons. It is guidance, not a restriction.
- `memory.learnFromFindings: auto`: `lessons`, the approved lessons relevant to the task (see
  below).
- `budget`: `budget.remaining` per scope, so an adapter can cap its own call.
- `agentRouting`: `routing` with the model, the effort and the flags for the provider family.

## VERIFICATION: deterministic checks (#40, #52)

Each enabled check is a validation of the ChangeSet with located findings. Under `enforce` it is
mandatory and its blocking findings send the run through the correction loop; under `warn` its
findings are `LOW`.

| Key | Check |
|---|---|
| `interface` | A task's declared interface (`metadata.interface: {stub, module}`): names, parameter order and kinds, defaults, stub bodies. |
| `architecture` | `maxModuleLines`, `maxFunctionLines`, `maxComplexity`, `forbiddenImports` (`from`/`to` module prefixes), with `severity`; a limit the baseline already exceeded is `LOW`. |
| `securityPatterns` | Unrestricted pickle, marshal, shelve or unsafe YAML loads; stored card numbers or CVC; card numbers that pass Luhn; weak password hashing. HIGH. |
| `constraints` | Constraints recognised in the task (or listed in `metadata.checks`): `stdlib-only`, `no-clock-random`, `annotated-public-api`, `no-float-money`, `no-stubs`. |
| `weakenedControls` | Deleted tests or asserts, added skips, suppressions (`noqa`, `nosec`, `type: ignore`), lowered thresholds, broad excepts, errors turned into success. |
| `testQuality` | Assertion-free tests, declared interface methods no test calls, coverage of the changed lines (`diffCoverage`, coverage.py), and `flakyReruns` reruns with another hash seed and the test files in reverse order. |
| `secrets: context` | Secrets weighed by context: values the task declares, environment assignments, constants, test paths and dummy values; replaces the review's pattern rule. |
| `sarif` | SARIF reports of external scanners (`path`, `tool`, `required`), with tool and rule versions. |
| `invariants` | Commands (`id`, `command`) that must pass on every VERIFICATION, including each sub-task's. |
| `riskFactors` | `newDependency`, `authentication`, `destructiveMigration`, `publicContract`, `network`, `floatMoney`, `sensitiveLogging`, `deletedWithoutTests`, each `block`, `acknowledge`, `inform` or `off`. |
| `differential` | A failing mandatory validator also runs on the baseline: shared failures are `PREEXISTING_ERROR` and do not block; new ones are `INTRODUCED_ERROR` findings. |
| `ratchet` | An optional validator (Ruff, Mypy) must not report more problems than on the baseline. |

A risk factor with `acknowledge` must be acknowledged in the decision; the decision brief lists
the factors:

```bash
harness gate decide --run <runId> --decision APPROVE --change-set-digest <digest> \
  --rationale "..." --acknowledge-risk network
```

## Corrections (#52)

- `runtime.reproduceFirst`: a correction attempt that changes nothing is an
  `agent.empty-correction` finding with the agent's explanation; a correction after
  `REQUEST_CHANGES` must add or change a test that fails on the code before the correction and
  passes after it, or a HIGH `correction.not-reproduced` finding blocks the gate.
- `review.structuredChanges`: `REQUEST_CHANGES` may carry blocking items,
  `--change-request "description::condition"`, where the condition is `test:<pytest node id>`
  (must pass), `absent:<regex>` (must not be added) or `text`. Every later VERIFICATION checks
  them.

## INDEPENDENT_REVIEW: a second agent (#38)

`review.agentReview` (`enforce` or `warn`) sends the ChangeSet and the task to a reviewer. It
runs only after every blocking deterministic check passed and again only when the ChangeSet
digest changed. Under `enforce` HIGH and CRITICAL findings block the gate and, within the
correction budget, return the run to IMPLEMENTATION with the findings as feedback (trigger
`REVIEW_FINDINGS`); a reviewer that fails or answers malformed JSON blocks the gate. Under `warn`
the findings are MEDIUM at most.

## Stop the line (#52)

`governance.stopTheLine: restore` keeps the changes of a run that stops without approval
(rejected, cancelled, or failed in IMPLEMENTATION or VERIFICATION) as a quarantined patch and
restores the baseline; `block` keeps them and refuses new runs in the workspace until a person
runs `harness run quarantine --run <runId>`. A task that declares `ownedPaths` gets a HIGH
finding for every changed path outside them. The frozen acceptance tests of a run that can still
be continued (failed in IMPLEMENTATION or VERIFICATION) stay in the workspace when its changes are
restored (`keptPaths` in the quarantine record, #81); those of a rejected or cancelled run are
restored with the rest.

## Budget (#42)

`budget` sets limits per agent call, per task (all its runs) and per run, for `costUsd`, `tokens`
and `wallSeconds`, with `warnAt` (default 0.8). Usage is what providers report and the wall time
the harness measures. Crossing `warnAt` records a LOW `budget.warning` once; crossing a limit
records a HIGH `budget.exceeded` finding and no further agent call is made until a person raises
the limit:

```bash
harness budget show --run <runId>
harness budget raise --run <runId> --scope run --metric costUsd --to 150 --rationale "..."
harness run continue --run <runId>
```

## Lessons (#43)

Under `memory.learnFromFindings: auto`, when a run closes, the findings that caused a correction
are proposed as project memory records `lesson:<validator>:<rule>` with provenance. A lesson
recurring in `memory.recurrenceRuns` runs (default 2) is approved automatically only under
`memory.autoApproveRecurring`; otherwise `harness memory approve` approves it. Active lessons
relevant to a later task reach its request and their use is recorded (`lesson.applied`).

## Routing (#44)

`agentRouting.mode: tiered` chooses the model and effort of each call with a pure function: the
call kind's rung for `clarify`, `plan`, `review`, `acceptance` and `architecture` (`locate` uses
its own rung or the bottom of the ladder); for `implement` the rung of the task size (`S`,
`M`, `L` by requirements, owned files and their lines, `thresholds`; a risk flag raises `S` to
`M`; a decomposed task is `L`). Since #59 a table without the entry of a call kind uses the
implement rung of the task size for it, with the rule `fallback:implement:KIND:SIZE`, a
`warning` in the decision and an `agent.routing.fallback` event, instead of failing the call. A failed verification or blocking review findings move the next
call one rung up the ladder (effort before model), at most `maxEscalations`; transient failures
retry at the same rung. `families` maps a provider id to `claude-code` or `codex` (otherwise the
command name decides); `tables` override the starting tables. Every decision is evidence
(`agent.routing.decided`) with its inputs, rule and policy digest. `fixed` keeps the provider's
own model.

The starting tables come from a research note of 2026-10-04 (Claude Code `--model`/`--effort`,
Codex `-m`/`model_reasoning_effort`); they are a starting point, and whether an account can use
a model is not checked. `harness routing calibrate` reports the cost per approved task of the
recorded decisions and suggests a table; nothing is applied.

### Anchored at the invoking model (#85)

With the starting tables `tiered` ignores the model the person invoked: in the 2.0.0 pilot the
cells invoked with Haiku, Sonnet and Opus routed every call to the same Sonnet and Opus rungs.
`agentRouting.mode: anchored`, which `harness init` writes since #85, uses the same tables and
thresholds with the invoking model as the ceiling:

- a rung of a cheaper tier than the invoking model is kept (cheaper rungs for the simple call
  kinds and sizes);
- a rung of the invoking model's tier runs on the invoking model with the rung's effort;
- a rung above it becomes the top rung allowed (rule `anchored:ceiling:KIND:SIZE`);
- escalation (`implement`, `review`) climbs the allowed rungs up to the invoking model, never
  above (`anchored:escalation:N:KIND:SIZE`).

Claude models are ranked by name (`haiku` below `sonnet` below `opus`); the models of another
family by their first rung in its ladder. A model whose tier cannot be told allows only itself.
With the starting tables: invoked with `claude-haiku-4-5-20251001`, every call runs on it;
with `claude-sonnet-5-5`, the `plan`, `review`, `architecture` and size-`L` rungs run on
Sonnet with `high` effort; with `claude-opus-5-5`, the models and efforts are those of `tiered`.

How the invoking model is determined, for the provider that answers the call:

- `agentRouting.anchorModel` when it is set;
- else the provider's `model` (`agentProviders.<id>.model`);
- else the value of `--model` (or `-m`, `--model=...`) in its `command` or `args`, as a command
  provider that wraps a CLI is usually invoked;
- for the embedded `session` provider, whose model the harness cannot see, the same order applied
  to `agentProvider`, which answers the read-only calls of a session run (see
  [embedded mode](embedded-mode.md)); the session implements with its own model.

Without any of them each call keeps the provider's own model (rule `anchored:no-anchor:...`). A
call kind's own `model` or `effort` (`intake.clarifyAgent`, `planning.planner`, ...) and a
reviewer's own `models` entry still win, as under `tiered`. Every decision records the `anchor`.
Since #85 every reviewer of the review panel also records `agent.routing.decided` (with
`reviewer` and `attempt`), and `harness routing calibrate` counts the reviewers of one run on the
same model once.

## Where each setting is tested

| Setting | Tests |
|---|---|
| Request kinds, `intake.ambiguityReview`, `intake.validateAnswers` | `tests/integration/test_agent_clarify_review.py` |
| The converging review and its assumptions (#79) | `tests/integration/test_ambiguity_convergence.py`, `tests/unit/test_intent_convergence.py` |
| `runtime.contractRetry` (#80) | `tests/integration/test_contract_retry.py` |
| `verification.*` checks, risk factors, change requests, `differential` | `tests/integration/test_verification_checks.py`, `tests/integration/test_verification_checks_more.py`, `tests/unit/test_checks_structure.py`, `tests/unit/test_checks_diff_quality.py` |
| `verification.acceptanceTests` | `tests/integration/test_acceptance_tests.py` |
| `verification.reverifyOnChange` | `tests/integration/test_run_lifecycle.py` |
| `governance.stopTheLine`, `runtime.gateContract`, `governance.phasePermissions`, `harness check` | `tests/integration/test_stop_line_and_contract.py`, `tests/unit/test_gate_contract_terms.py` |
| `runtime.reproduceFirst` | `tests/integration/test_reproduce_first.py` |
| `review.agentReview` | `tests/integration/test_agent_review.py` |
| `planning` | `tests/integration/test_decomposition.py` |
| `context`, `memory` | `tests/integration/test_context_and_lessons.py`, `tests/unit/test_context_manifest.py` |
| `budget` | `tests/integration/test_budget.py` |
| `agentRouting` | `tests/integration/test_agent_routing.py`, `tests/unit/test_routing.py`, `tests/unit/test_routing_anchored.py`, `tests/integration/test_review_panel_run.py` (reviewer decisions) |
| The defaults `harness init` writes | `tests/integration/test_agent_results_defaults.py` and the `agent-results` flow of `scripts/demo_flows.py` |
