# Controlled evaluation of v0.9.0

The same agent (Claude Code 2.1.285, non-interactive) implemented the same tasks with and without
the harness, and every final tree was measured in the same way afterwards. Code, fixtures, hidden
tests and per-run records are in
[`evaluation/`](https://github.com/SebasBarrera/harness-engineering/tree/develop/evaluation).

| Factor | Levels |
|---|---|
| Scenario | greenfield shipping-cost library, brownfield strict base64 decoding in `pallets/itsdangerous` 2.2.0, alerts client whose specification documents a development password |
| Condition | agent alone; agent as the command provider of `harness run start` |
| Model | Haiku 4.5, Sonnet 5.5, Opus 5.5 |
| Repetitions | 5 per cell (90 runs), plus 13 fault-injection probes repeated 3 times |

Three further blocks extend this comparison: [instruction levels](#instruction-levels) (81 runs),
a [second agent](#second-agent) (35 runs) and [iterative development](#iterative-development)
(17 sessions).

## Results

| Measure | Without harness | With harness |
|---|---|---|
| Hidden acceptance tests passed | 1,064 of 1,065 | 1,065 of 1,065 |
| Brownfield regressions (297 original tests) | none in 15 runs | none in 15 runs |
| Agent cost, all runs (list price) | USD 7.23 | USD 8.99 |
| Harness overhead per run (median, range) | — | 4.34 s (2.89–8.43) |
| Trace completeness (8 required relations) | not recorded | 45 of 45 runs |
| Credential-like literal in delivered tests | 6 of 15 security runs | 0 of 13 delivered |
| Real development password in delivered tests | 2 of 15 security runs | 3 of 13 delivered |

- In the greenfield and brownfield scenarios every gate passed at the first attempt and the change
  was approved; the harness did not change correctness and added a few seconds per run.
- In the security scenario the review rule `review.possible-secret` failed the gate in 9 of 15
  runs; 11 correction cycles followed, 7 changes were corrected and approved, 2 were rejected.
  Every detected value that could be recovered was a fictitious test value, while the real
  development password, written as `os.environ["ALERTS_PASSWORD"] = …` or passed to
  `monkeypatch.setenv`, was not detected and was approved in 3 runs.
- The probes behaved deterministically. Verification failures, blocking findings, a missing
  mandatory tool, a change after the gate, a timeout, an output flood and an unauthorized command
  were all stopped or bounded. Four gaps remain: `harness trace` exports a tampered event log
  without an integrity warning, a file outside the declared paths stays modified without a finding,
  files written by a timed-out provider stay in the tree, and a failed verification has no governed
  path back to `IMPLEMENTATION`.

In the 36 governed runs approved without a correction cycle, `IMPLEMENTATION` took a median of
88.5 s, 97.1 % of the time spent in phases; `VERIFICATION` took 1.80 s and the other seven phases
0.30 s together. The 43 closed runs produced 53 retrospective recommendations, none applied
automatically: 27 to keep the controls and collect more runs, 12 to run validators earlier (in all
12 the only non-passed validators were the optional Ruff and Mypy), 7 about acceptance criteria and
7 about recurring review findings (both groups in the security scenario). The two rejected runs
stopped at `DECISION` and produced no retrospective.

## Instruction levels

The full task of the comparison above is not how an agent is usually prompted. In 81 more runs
(3 per cell) the instruction shrinks to a minimal task whose only criterion is `It works.`, and to
a one-line prompt. The harness rejects the one-line prompt when the task is created (exit code 2),
because a task needs acceptance criteria.

| Flow | Runs | Delivered | With a defect | Without new tests | Stopped (correct + defective) |
|---|---|---|---|---|---|
| One-line prompt, without harness | 27 | 27 | 4 | 9 | — |
| Minimal task, without harness | 27 | 27 | 5 | 10 | — |
| Minimal task, with harness | 27 | 21 | 4 | 6 | 5 + 1 |
| Full task, without harness | 45 | 45 | 1 | 0 | — |
| Full task, with harness | 45 | 43 | 0 | 0 | 2 + 0 |

- A delivery has a defect when it fails a hidden test. All 14 defective deliveries are in the
  brownfield scenario, where an existing suite that passes hides the missing criteria.
- Structuring the task explains most of the reduction (5 of 27 to 1 of 45 without the harness).
  Governing a task whose only criterion is `It works.` does not: 4 of 9 governed brownfield runs
  delivered the defect.
- In the two scenarios that start from an empty repository the harness stopped at `VERIFICATION`
  the six minimal-task runs that brought no tests (pytest collected none); five of those changes
  were correct.

## Second agent

Codex CLI 0.159.2 ran the full task with two models (35 valid runs, 3 per cell). The 18 governed
runs were approved at the first gate with the 8 trace relations, none of the 35 runs failed a
hidden test, and the median overhead of the harness was 5.6 s per run. One governed run was
approved with the development password in its tests, as was one run without the harness.

## Iterative development

One library is built in five increments: one-line prompts without the harness, structured tasks
with it. After each increment an oracle of 25 hidden checks sends the same bug reports to both
conditions (at most two rounds). Values are without / with the harness; requests and cost are
medians per session.

| Model | Valid sessions | Complete (25 of 25) | Requests to the agent | Correction requests | Cost (USD) |
|---|---|---|---|---|---|
| Haiku 4.5 | 2 / 3 | 1 / 0 | 8.5 / 9 | 7 / 12 | 0.75 / 1.53 |
| Sonnet 5.5 | 3 / 3 | 3 / 3 | 7 / 5 | 5 / 0 | 0.65 / 0.39 |
| Opus 5.5 | 3 / 3 | 3 / 3 | 7 / 5 | 7 / 0 | 1.98 / 1.41 |

- With Sonnet and Opus the governed flow needed no correction request: the 30 increments passed
  the hidden checks at the first attempt, at a lower cost with non-overlapping ranges.
- With Haiku the result is the opposite. Its three governed sessions ended at 18 of 25 checks: the
  persistence code called `path.endswith` on a `Path` object, the agent's own tests passed and the
  gate verifies those tests. One Haiku session without the harness is excluded because its package
  could not be imported.

Descriptive statistics only; the design is formative and the sample does not support significance
testing. Thesis chapter 7 reports the protocol, the tables and the threats to validity.
