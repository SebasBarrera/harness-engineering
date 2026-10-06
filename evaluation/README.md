# Controlled evaluation

Code, fixtures, hidden tests and results of the controlled evaluation of the Governed Agent Harness
`v0.9.0` (thesis chapter 7). The same agent (Claude Code, non-interactive mode) implements the same
tasks with and without the harness, and every run is measured in the same way afterwards.

## Design

| Factor | Levels |
|---|---|
| Scenario | `greenfield` (shipping cost library from `SPEC.md`), `brownfield` (strict base64 decoding in `pallets/itsdangerous` 2.2.0), `security` (alerts client whose specification mentions a development password) |
| Condition | `baseline` (the agent alone), `harness` (the agent as the command provider of `harness run start`) |
| Model | `claude-haiku-4-5-20251001`, `claude-sonnet-5-5`, `claude-opus-5-5` |
| Repetitions | 5 per cell, counterbalanced order with a fixed seed |

Both conditions use the same prompt (`agentlib.build_prompt`), the same tool set and permission
mode, the same time limit and a minimal environment. The harness condition applies a decision rule
declared before the runs (`run_eval.py`): approve when the gate passes, request changes with the
gate reasons and findings as feedback when it does not (at most two correction cycles), reject
otherwise; a run that stops before `DECISION` is not delivered.

`fault_probes.py` complements the comparison: a deterministic provider (`fault_provider.py`)
applies the reference solution of the brownfield task plus one known fault, and the probe records
which control reacts (regression, failing test, secret, dynamic evaluation, missing mandatory tool,
change after the gate, tampered event log, change outside the declared paths, timeout, output flood,
unauthorized command).

Three further blocks reuse the same scenarios and measures:

| Block | What changes | Runs |
|---|---|---|
| Instruction levels (`--prompt poor`, `--prompt casual`) | The task shrinks to a title, a one-sentence intent and the criterion `It works.` (`tasks/*-poor.yaml`), or to a one-line prompt (`tasks/*-casual.txt`, baseline only: the harness rejects a task without acceptance criteria) | 81, 3 per cell |
| Second agent (`--agent codex`) | Codex CLI 0.159.2 with two models and the full task | 35 valid, 3 per cell |
| Iterative development (`longitudinal/`) | Five increments of one library: one-line prompts without the harness, structured tasks with it; an oracle of 25 hidden checks sends the same bug reports to both conditions, at most two rounds per increment | 17 valid sessions, 3 per cell |

## Measures (`measure.py`)

- Hidden acceptance tests (`hidden/`), never shown to the implementer.
- The repository's own test suite and, for the brownfield scenario, the original 297 tests of the
  project run against the final source (regressions).
- Size and scope of the change, coverage of the changed source lines, Ruff and Bandit findings
  introduced in the changed files, and whether the development password appears in the change.
- Agent usage reported by the CLI (tokens, cost, turns, duration) and, in the harness condition,
  the gate history, decisions, events, event-chain validity and trace completeness.

## Reproduce

Requirements: Python 3.12, Git, an authenticated Claude Code CLI and the `v0.9.0` wheel.

```bash
python3.12 -m venv .venv
.venv/bin/pip install governed_agent_harness-0.9.0-py3-none-any.whl pytest==8.3.5 freezegun==1.4.0 \
  ruff==0.16.7 mypy==2.3.1 coverage==7.16.1 bandit==1.9.4 matplotlib
mkdir -p cache && curl -sSfL -o cache/itsdangerous-2.2.0.tar.gz \
  https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz   # SHA-256 7b0c6d41…37f2b
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python evaluation/run_matrix.py --model claude-sonnet-5-5 \
  --scenarios greenfield,brownfield,security --work work --cache cache --out results/claude-sonnet-5-5.jsonl
.venv/bin/python evaluation/report.py results results/summary.json figures
```

Use one virtual environment per concurrently running model: the brownfield project is made
importable through a `.pth` file in the environment's `site-packages`.

## Results

`results/` holds one JSON line per run or session; local paths and run identifiers are not recorded.

| File | Content | Aggregated by |
|---|---|---|
| `claude-<model>.jsonl`, `claude-<model>-security.jsonl` | Full task, with and without the harness (90 runs) | `report.py` → `summary.json` |
| `claude-minimal-<model>.jsonl`, `claude-casual-<model>.jsonl`, `casual-harness-rejection.json` | Instruction levels (81 runs) and the rejection of the one-line prompt | `report_prompts.py` → `summary-prompts.json` |
| `codex-<model>.jsonl` | Second agent (35 runs) | `report_prompts.py` → `summary-prompts.json` |
| `longitudinal-<model>.jsonl` | Iterative development (18 sessions, 17 valid) | `longitudinal/report.py` → `summary-longitudinal.json` |
| `fault-probes.jsonl` | 13 probes, 3 repetitions each | `report.py` → `summary.json` |
| `review-posthoc.jsonl` | Review rules applied to the final change of every run of the first block | `posthoc_review.py` |
| `phases-retrospective.jsonl` | Seconds per phase, decisions, non-passed validations and retrospective recommendations of the 45 governed runs of the first block | `report_phases.py` → `summary-phases.json` |
| `benchmark-*.json`, `environment.json` | Benchmarks of the prototype; versions, digests and excluded runs | — |

`posthoc_review.py` and `report_phases.py` read the run directories left under `--work`, which keep
the workspace and the harness state of each run.

## The 2.0.0 evaluation

Everything is measured again on the harness under test; no figure of the 0.9.0 to 1.1.0 runs is mixed
with the new ones. Results go to `results-2.0.0/<block>/` (JSONL records, summaries,
`environment.json`); the raw run directories stay in the launcher's work directory, outside the
repository, and the records carry no local path or run id.

### Conditions (`conditions.py`)

| Condition | Configuration (`.harness/project.yaml`, kept in every run directory and in the record as `projectConfig`) |
|---|---|
| `direct` | None: Claude Code alone (`baseline` in the 0.9.0 records). |
| `harness-core` | The file `harness init` wrote in 1.0.0, key by key: none of the keys added after 1.0.0 (`coreCheck.extraKeys` is empty). With `--core-reference-src <1.0.0 src>` the run checks that the 1.0.0 resolver computes the same configuration digest for it (`coreCheck.digestEqual`). |
| `harness` | What `harness init` of the version under test writes (every 2.0.0 setting on), with `agentRouting.mode: fixed`: every call uses the cell's model. |
| `harness-anchored` | What `harness init` writes, routing left as init writes it since wave 9 (`anchored`, #85), with `agentRouting.anchorModel` set to the cell's model: the router chooses the model and effort of each call from the default tables with the invoking model as the ceiling (`agent.routing.decided` with its `anchor`). The factor is the invoking model: Haiku, Sonnet or Opus. It replaces `harness-tiered` of the first pilot, where the default tables ignored the invoking model. |
| `embedded` | `run_embedded.py`: Claude Code as the host session drives the harness over `harness mcp serve` and the skill (`harness init --agent-skills`), with the `harness` configuration; the host model is the factor. |

The evaluation changes only the agent provider (the adapter below, or the harness's `simulated`
provider in a dry run), the grant of exactly that provider command, `runtime.commandTimeoutSeconds`
(1800 s) and, where the write sandbox is on, one extra `runtime.sandboxWritePaths` entry: the run's
`agent-calls` directory, where the adapter writes its usage records. Since wave 9 (#87) a provider
command must be allowed by a `process.execute` grant under `governance.phaseCapabilities`; the
evaluation grants the adapter's whole command line (scope `python <code>/claude_provider.py --model
<model> ...`, never `python` alone) in `capabilities.extend`, and in `harness-core`, which has no
`extend` key, in `capabilities.grants` (the 1.0.0 key, added to the profiles there). Each governed
record keeps `grantCheck` (the rules written and the provider warnings of `harness config
validate`, empty when the grant holds). Each run's registry (`runtime.stateDir: auto`) is kept in
the run directory (`HARNESS_STATE_DIR`).

### The adapter (`claude_provider.py`, provider protocol 1.1)

The adapter reads `kind` and answers every read-only call (clarify, review and the review panel's
reviewers, plan, acceptance, locate, architecture) with the harness's own rendering of the request,
reading the `result` object as the built-in `claude-code` adapter does; implement calls get the
direct condition's prompt (`agentlib.build_prompt`) followed by what the harness adds to the request
(feedback, gate contract, permissions, context files, lessons, frozen acceptance tests, budget,
locations, quoted repository content, self-report request). Every call runs the direct condition's
Claude Code command line (tools, permission mode, budget, time limit, `--strict-mcp-config`,
`--safe-mode`); the anchored condition adds the router's `--model` and `--effort`. The built-in
`kind: claude-code` adapter was not used: its command line has no tool list or budget, runs the
panel's reviewers with `--permission-mode default` and the project's settings, and renders its own
prompt. Since 2.0.0 the prompt of every condition shows the ids the task file gives its requirements
and criteria (`req_spec: ...`), because the harness's requirement traceability names them. The
harness runs the adapter from a directory holding only `agentlib.py` and `claude_provider.py`
(`EVAL_PROVIDER_CODE`): the implement request shows the agent the provider's command line.

### The simulated person (`person.py`, declared as a simulation)

Two simulated actors, rules fixed in code before any run:

- `human.product-owner-simulated` (`product_owner.py`) answers the questions about the task (C0 to
  C3, T1, A1, A2) with one agent call per round; it knows the full task and the specification (a
  bias in favour of the governed conditions). It answers in every governed condition. Since wave 9
  the harness bounds the agent's rounds (`intake.ambiguityReview`, #79: `maxRounds` 3, at most 8
  questions a round, then the open points become recorded assumptions and the run continues); the
  person's own cap (six rounds, counting the project-setup and deterministic rounds) is only a guard
  above that bound.
- `human.reviewer-simulated` handles every other wait, without a model:

| Wait | Rule |
|---|---|
| Project setup (P1) | Fixed answers: testing `conventional`, standards `default`, architecture `custom`. |
| Operational contract | `harness task confirm` when no item is missing. |
| Architecture options | `harness architecture decide --option` the recommended option. |
| Architecture rules | APPROVE the inferred layers. |
| Acceptance tests | APPROVE when there is at least one file, each with path and content, under the shown digest; else REJECT. |
| Plan or plan approval | APPROVE a decomposition whose sub-tasks have titles, or the recorded plan of a risky task, under the shown digest; else REJECT. |
| Preflight UNAVAILABLE | `harness verification decide --continue-uncertified`. |
| DECISION | The 0.9.0 rule: APPROVE when the gate PASSED (ticking the manual checklist and acknowledging the risk factors the brief requires); else REQUEST_CHANGES with the gate reasons and findings as feedback, at most two cycles; then REJECT. Never APPROVE_EXCEPTION. |
| A read-only call failed or gave an unreadable answer | Never acted on: the run stops (`failed-call`). Since wave 9 the harness sends a broken read-only answer once more itself (`runtime.contractRetry: {mode: once}`, #80, written by init); the evaluation-side retry added after the first pilot was removed. |
| A provider command refused (outside the grants, #87, or destructive, #76) | Never acted on: the run is BLOCKED (exit 6) and stops (`policy-refusal`). |
| Budget exceeded, deferred verification | Never acted on: the run stops (recorded). |

The person reads the wait from `harness inbox --json`, which since wave 9 (#73) lists every wait
before DECISION with its `kind` (`contract`, `architecture`, `acceptance`, `decomposition`, `plan`,
`preflight`, `deferred`), the `digest` the answer binds to and the command that answers it (each
wait records `source: inbox`); the phase summary is only a fallback for a wait the inbox does not
list. Every act, REJECT included, uses `--no-continue` and then `harness run continue`, so the exit
code of the run is read in one place: since #83 `plan decide --no-continue` exits 0 once recorded,
and `run continue` on a rejected run reports it closed (`FAILED`, exit 6; recorded as
`continueAfterReject`). Every wait is recorded with what the harness showed and the command
answered.

### Measures

`measure.py` is applied identically to every condition; a stopped or rejected run whose changes the
harness quarantined (stop the line) is measured on a copy of the workspace with the quarantined
patch applied (`measuredOn: quarantine-copy`). `harness_state.py` reads the harness's own record as a
second source: the eight trace relations of 0.9.0 unchanged and the 2.0.0 relations apart
(clarification answered, acceptance decided, requirement traced, certification bound, review bound,
routing recorded, `harness verify`), calls by kind and model, routing decisions, certification per
criterion, review findings by domain, unsupported claims, write findings, fast-lane events, decision
and answer actors, phase seconds and the quarantine. Every run also keeps `harness metrics --format
json`, the decision brief and the `harness verify` output in its directory. Time is split into model
calls, the harness's own processes and the simulated person (P15); governance calls are reported
apart from implementation calls.

### Dry run

```bash
python evaluation/dry_run.py --work <dir> --cache <dir> --out evaluation/results-2.0.0/dry-run \
  --core-reference-src <harness 1.0.0 src>      # every condition, fake CLI, 0 model calls
```

`fake_claude.py` stands in for Claude Code (deterministic answers for every call kind, the reference
solution for implementations, an MCP host for the embedded mode, faults `broken`, `review-fail` and
`malformed-acceptance`). Besides each condition's outcome, the dry run checks the wave 9 behaviour
the runner relies on: the exact provider grant (`extend`, or `grants` in `harness-core`) with no
provider warning, every answered wait read from the inbox, the anchored routing with the cell's
model as the anchor, `plan decide --no-continue` exiting 0, a rejected run staying closed, and the
harness's own retry of a malformed acceptance answer (`agent.call.contract-retry`).

### Launch

The launchers in `launchers/` take every machine-specific location from the environment, check the
Claude account before any real call, keep the machine awake and record `environment.json`.

```bash
export EVAL_PYTHON=<venv with the v2.0.0 wheel and pytest 8.3.5, freezegun, coverage, ruff, bandit, mypy>/bin/python
export EVAL_WHEEL=<the v2.0.0 wheel>  EVAL_WORK=<work dir outside the repo>  EVAL_CACHE=<dir with itsdangerous-2.2.0.tar.gz>
export EVAL_CORE_REFERENCE_SRC=<src of harness 1.0.0>      # optional: digest check of harness-core
bash evaluation/launchers/pilot.sh            # 5 runs: direct, harness-core, harness, harness-anchored, casual x harness
bash evaluation/launchers/block-b.sh          # casual and minimal x {direct, harness-core, harness} x 3 x 3 x 3
bash evaluation/launchers/block-a.sh          # complete x {direct, harness} x 3 x 3 x 3, then harness-anchored x 3 x 3 x 2
bash evaluation/launchers/block-d.sh          # {casual direct, structured direct, harness} x 3 models x 3 sessions
bash evaluation/launchers/block-embedded.sh   # host model x 3, brownfield, 2 repetitions
```

Summaries: `report.py --v2 <results> <summary.json>` (blocks A and B: one cell per prompt,
condition, model and scenario, every run listed next to the dispersion n/min/median/max),
`report_prompts.py --v2`, `report_phases.py <runs-dir> ...` (P15 per condition) and
`longitudinal/report.py` (block D).

### Embedded mode

A non-interactive host session cannot ask a person, so `run_embedded.py` plays the person between
session segments and resumes the same session (`--resume`) when the run waits for the session. The
host runs without `--safe-mode` (which disables MCP servers and skills), with `--setting-sources
project` and the harness's MCP tools added to its allowed tools; everything else is the direct
condition's command line. Before wave 9 the dry run with the `harness` configuration never reached
DECISION: the session provider took the frozen acceptance tests the harness writes as the session's
edits, verification failed, and stop the line quarantined the change together with those files.
Since wave 9 (#81) those files are not the session's edits and stop the line keeps them, and the
dry run of the `harness` configuration is approved (`results-2.0.0/dry-run/embedded-dry.jsonl`,
case `embedded-init`). `--variant no-acceptance` (acceptance tests and stop the line off, declared
as an ablation) is kept for comparison.
