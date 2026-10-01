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
