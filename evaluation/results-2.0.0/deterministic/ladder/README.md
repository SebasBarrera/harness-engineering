# N01-a: verification ladder on a seeded corpus

16 seeded changes on the inventory library of block D (`evaluation/corpus/ladder`, parts A and B
in the baseline), each one task in patch mode applied by the `simulated` provider, with the
configuration `harness init` writes (certification under `verification.ladder` with
`defaultLevel: L1`, light mutation under `verification.mutation` in `warn` mode). Eight changes are
defective (the `path.endswith` defect of the Haiku sessions with its criterion at L1 and at L3, a
save that drops the history while no test names the round-trip criterion, a `total_value` that
returns a float with a test that does not exercise it, a test without assertions and a test that
passes on the baseline, a command line with wrong output at L1 and at L3) and eight are correct
(at L1, L2 and L3, four of them with evidence that does not reach the declared rung).

Truth per case (`cases.yaml`): `defective` (re-checked by the hidden tests of the case's part on a
separate copy with the change applied: `oracle`, `defectiveObserved`, `truthConsistent`) and
`evidenceAdequate` (the tests or probes really reach the declared rung). Signals per case:
`certification` (the run is `NOT_CERTIFIED` or `PARTIAL`, or a `certification.*` finding),
`mutation` (`tests.change-not-exercised`, `tests.weak`, `tests.broken`), `testQuality` (other
`tests.*` findings), `gateNotPassed` (the gate is not `PASSED`, including a run that stopped before
DECISION) and `any`. The summary gives sensitivity and specificity of each signal against both
truths. No decision is taken; waits before DECISION are answered by the simulated person of the
directory README (here: an `UNAVAILABLE` preflight would be continued uncertified).

Command (worktree code):

```bash
SCRATCH=/private/tmp/claude-502/-Users-jbarrerapuli-Documents-Repos-Proyecto-de-grado/ceca23e5-5679-47dc-ab0b-75aa4166d2c7/scratchpad
$SCRATCH/venvs/eval/bin/python evaluation/deterministic/ladder_corpus.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/ladder --work $SCRATCH/work/ladder-final
```

Files: `ladder-corpus.jsonl` (one record per case: run status and phase, gate, certification per
criterion with its evidence, findings, oracle), `ladder-summary.json` (table and rates),
`environment.json`.
