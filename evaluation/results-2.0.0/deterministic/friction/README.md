# N03-a: friction of small changes with the fixture provider

Two small (S) changes on the sample project (the discount at the threshold and above it, with a
test that names the requirement), each also with one injected risk factor: a literal credential in
the source, an `eval` call, or a README change outside the paths the task owns (`ownedPaths` of the
task file, `--owned` of `harness do`). Three conditions, each on a fresh project with the
configuration `harness init` writes:

- `full`: without the `friction` section (the full flow): `task create`, `run start`,
  `gate decide`;
- `fast-lane`: `harness do TEXT --criterion ... --owned ...`, then `gate decide`;
- `fast-lane+pre-approval`: `harness do ... --pre-approve` (then `gate decide` only if the run
  still waits for a person).

At DECISION the measuring person types `gate decide --decision APPROVE` whatever the gate (a
refused approval exits 5 and the change is not delivered). The fixture provider (copied from
`scripts/measure_friction.py`, not modified) writes the change and answers the read-only kinds with
empty results; it reports as usage an **estimate** of its tokens (request characters / 4) that the
harness records, and it logs every call (kind, estimated tokens, its own wall seconds) to
`agent-calls.jsonl` in the run directory, next to `runner-clock.json` (the typed commands and their
seconds): N04 uses both as the external measurement.

Measures per run: commands typed, human interactions the harness recorded
(`human.interactions`), agent calls by kind (fixture log) and recorded calls and tokens
(`budget show`), seconds of the typed commands on this machine (no human time), the lane
(`lane.classified`) and its escalation (`lane.escalated`), the approval in advance, the gate,
whether the change was delivered and whether it was delivered without a person (a false fast
lane for a risk task).

Command (worktree code; run while no other suite ran, so the seconds are not shared):

```bash
SCRATCH=/private/tmp/claude-502/-Users-jbarrerapuli-Documents-Repos-Proyecto-de-grado/ceca23e5-5679-47dc-ab0b-75aa4166d2c7/scratchpad
$SCRATCH/venvs/eval/bin/python evaluation/deterministic/friction.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/friction --work $SCRATCH/work/friction-final
```

Files: `friction.jsonl` (24 records), `friction-summary.json` (per condition: risk-free medians;
with a risk factor: escalated or full lane, false fast lanes, blocked), `environment.json`.
