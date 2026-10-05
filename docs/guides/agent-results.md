# Better agent results

Since 1.1 the harness can do more than verify what an agent delivered: it can ask an agent to
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
fails if a frozen file changed (`acceptance.modified`, HIGH) or the tests do not pass.

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
only when that attempt fails its corrections.

## IMPLEMENTATION: what the agent receives

- `runtime.gateContract`: the `gate` block lists the validators (command, mandatory or optional),
  the enabled checks with their policies, the review rules, the blocking severities, the
  absolute workspace path and the command `harness check --path <workspace> --run <runId>`.
  `harness check` runs the same validators and diff checks on the workspace and records nothing
  (exit 0 when they pass, 6 otherwise); it reads the configuration and a check state file the
  harness writes before the call, so it runs inside the agent sandbox.
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
finding for every changed path outside them.

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
call kind's rung for `clarify`, `plan`, `review`; for `implement` the rung of the task size (`S`,
`M`, `L` by requirements, owned files and their lines, `thresholds`; a risk flag raises `S` to
`M`; a decomposed task is `L`). A failed verification or blocking review findings move the next
call one rung up the ladder (effort before model), at most `maxEscalations`; transient failures
retry at the same rung. `families` maps a provider id to `claude-code` or `codex` (otherwise the
command name decides); `tables` override the starting tables. Every decision is evidence
(`agent.routing.decided`) with its inputs, rule and policy digest. `fixed` keeps the provider's
own model.

The starting tables come from a research note of 2026-10-04 (Claude Code `--model`/`--effort`,
Codex `-m`/`model_reasoning_effort`); they are a starting point, and whether an account can use
a model is not checked. `harness routing calibrate` reports the cost per approved task of the
recorded decisions and suggests a table; nothing is applied.

## Where each setting is tested

| Setting | Tests |
|---|---|
| Request kinds, `intake.ambiguityReview`, `intake.validateAnswers` | `tests/integration/test_agent_clarify_review.py` |
| `verification.*` checks, risk factors, change requests, `differential` | `tests/integration/test_verification_checks.py`, `tests/integration/test_verification_checks_more.py`, `tests/unit/test_checks_structure.py`, `tests/unit/test_checks_diff_quality.py` |
| `verification.acceptanceTests` | `tests/integration/test_acceptance_tests.py` |
| `governance.stopTheLine`, `runtime.gateContract`, `governance.phasePermissions`, `harness check` | `tests/integration/test_stop_line_and_contract.py` |
| `runtime.reproduceFirst` | `tests/integration/test_reproduce_first.py` |
| `review.agentReview` | `tests/integration/test_agent_review.py` |
| `planning` | `tests/integration/test_decomposition.py` |
| `context`, `memory` | `tests/integration/test_context_and_lessons.py`, `tests/unit/test_context_manifest.py` |
| `budget` | `tests/integration/test_budget.py` |
| `agentRouting` | `tests/integration/test_agent_routing.py`, `tests/unit/test_routing.py` |
| The defaults `harness init` writes | `tests/integration/test_agent_results_defaults.py` and the `agent-results` flow of `scripts/demo_flows.py` |
