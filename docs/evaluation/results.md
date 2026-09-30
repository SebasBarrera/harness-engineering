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

Descriptive statistics only; the design is formative and the sample does not support significance
testing. Thesis chapter 7 reports the protocol, the tables and the threats to validity.
