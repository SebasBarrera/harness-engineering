# N04: validity of `harness metrics` against an external measurement

`evaluation/deterministic/metrics_validity.py` compares, for each run directory, the document of
`harness metrics --format json` with what was measured outside the harness:

- **measured run directories**: the 24 runs of the friction suite (`../friction`), whose fixture
  provider logged every call it received with the tokens it reported and its own wall seconds
  (`agent-calls.jsonl`), and whose runner timed the typed commands (`runner-clock.json`). For the
  agent runs of the other evaluators the same script reads the `agentlib` records
  (`agent-calls/call-*.json`: input, output, cache-read and cache-creation tokens, cost, wall
  seconds).
- **demonstration projects**: every project left by `scripts/demo_flows.py all --workdir` in
  block 0 (no external side: checked for internal consistency, cost labels and per-person
  indicators only).

Per metric (agent calls, input, output and cache tokens, reported cost, seconds of the agent calls,
run wall seconds against the runner clock): harness value, external value, absolute and relative
difference, equal or not; coverage (runs where a value was available on both sides). Checks on each
document: per-kind calls sum to the total, per-run calls and tokens sum to the totals, estimated
costs kept apart from reported ones, and no per-person indicator (no key grouping by actor, no human
actor id of the run's events in the document, RD-16).

Note on time: the harness times an agent call from its process launch; the fixture times itself
from its own start, so the agent-call seconds differ by the interpreter's start-up. The runner clock
includes the harness's own process start and the commands' output handling.

Command (worktree code, after the friction suite and block 0):

```bash
SCRATCH=/private/tmp/claude-502/-Users-jbarrerapuli-Documents-Repos-Proyecto-de-grado/ceca23e5-5679-47dc-ab0b-75aa4166d2c7/scratchpad
.venv/bin/python evaluation/deterministic/metrics_validity.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/metrics-validity \
  --runs-glob "$SCRATCH/work/friction-w9/friction-*" \
  --demo-workdir $SCRATCH/work/block0-w9/demo --demo-state $SCRATCH/work/block0-w9/demo-state
```

Files: `metrics-validity.jsonl` (one record per run directory or demo project),
`metrics-validity-summary.json`, `environment.json`.
