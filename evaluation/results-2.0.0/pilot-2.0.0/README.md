# Haiku pilot of 2.0.0 (apparatus check, one repetition)

The pilot checks that the evaluation apparatus drives every condition end to end with a real
model before the campaign. It is **not** the campaign: one model (`claude-haiku-4-5-20251001`),
one scenario (brownfield, itsdangerous 2.2.0), one repetition per condition. No claim about the
harness rests on these five records alone; the campaign repeats each cell (see the dated
amendments in `evaluation/campaign/PREREGISTRO.md`).

## How it was produced

```bash
E=<eval dir>   # venv with the evaluation tools, cache with the itsdangerous archive
EVAL_PYTHON="$E/venv/Scripts/python.exe" EVAL_WORK="$E/work5" EVAL_CACHE="$E/cache" \
EVAL_EXTRA_PATH="$PWD/src" EVAL_CORE_REFERENCE_SRC="$E/ref-1.0.0/src" \
EVAL_OUT=evaluation/results-2.0.0/pilot-2.0.0 EVAL_DRY=0 EVAL_PARALLEL=0 \
  bash evaluation/launchers/pilot.sh
```

On Windows the paths are given in mixed form (`cygpath -m`). Source: `develop` at `886926e`
(`environment.json`, `source.commit`, not dirty); the harness ran from that source tree, whose
version string still reads 1.0.0 because the version is set on the release branch.
`agentSandbox` is off: there is no OS sandbox on Windows, and `environment.json` declares it.

## Files

| File | Content |
|---|---|
| `pilot.jsonl` | One record per run: condition, prompt, harness phases and findings, measures (hidden tests), agent usage and cost, the simulated person's waits and decisions |
| `summary-blocks.json` | The same records summarized per block |
| `environment.json` | Tool versions, OS, Claude Code version, source commit |

The launcher logs (`log*.txt`), raw run directories (workspaces, run registries, agent calls)
are not kept in Git (`.gitignore`); wall times come from `pilot.jsonl`.

## Results

| Prompt | Condition | Final phase / status | Outcome | Hidden tests | Cost (USD) | Wall time (s) |
|---|---|---|---|---|---|---|
| full | direct | — | — | 24/24 | 0.19 | 177 |
| full | harness-core | CLOSURE / PASSED | approved | 24/24 | 0.12 | 162 |
| full | harness | CLOSURE / PASSED | approved | 24/24 | 0.84 | 775 |
| full | harness-anchored | VERIFICATION / FAILED | stopped | 18/24 | 1.58 | 1190 |
| casual | harness | VERIFICATION / FAILED | stopped | 18/24 | 2.81 | 2157 |

## Reading the results honestly

- **Every condition ran end to end without an apparatus failure.** The two governed runs that
  did not reach DECISION stopped in VERIFICATION on mandatory validators that the agent did not
  satisfy after its correction cycles, not on a defect of the harness or of the apparatus.
- **Those two stops are confirmed by the hidden tests**, which the harness never sees: both
  changes pass 18 of 24 hidden tests, against 24 of 24 for the approved ones. The harness
  blocked two changes that were in fact worse.
  - *harness-anchored*: new ruff violations against the baseline (`ratchet.regressed`) and mypy
    errors remained after three implementation attempts.
  - *casual*: failing acceptance tests, new ruff violations (`ratchet.regressed`) and tests of
    changed files that did not run (`tests.changed-file-not-executed`).
- **Cost:** the governed conditions cost more than the direct one (core 0.6×, full harness 4.5×,
  anchored 8.4×, casual 15×, in USD). The failing runs are the most expensive because the
  correction cycles repeat the implementation calls.
- **One repetition:** an earlier apparatus check on a pre-release branch had the anchored run
  approved with 24/24 and the casual run stopped on the per-call budget. Differences of this
  size between single runs are expected; the campaign measures them with repetitions.
