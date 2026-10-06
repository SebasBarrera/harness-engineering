# Block 0: technical verification (P03, P04, P06, P07, N16)

`evaluation/deterministic/block0_technical.py` writes one summary, `block0-summary.json`, with:

- `tests` (P03): the whole suite once, `python -m pytest -W error::ResourceWarning
  --cov=governed_harness --cov-branch` (JUnit and coverage JSON; the coverage file is reduced to
  its totals): tests and outcomes per family (unit, contract, integration, security, e2e,
  performance), statements, line and branch coverage. `pytest-output.txt` keeps the end of the
  output.
- `security` (P06): the tests of `tests/security` with their outcomes, from the same JUnit report.
- `static`: `ruff check src tests scripts`, `ruff format --check src tests scripts` and `mypy`
  (strict, package mode).
- `demo` (P04): `scripts/demo_flows.py all --workdir DIR --transcript FILE`: flows, steps, steps
  whose exit code differed from the expectation, and the output checks (`[ok ]` / `[BAD]` lines with
  `check:`). `demo-transcript.json` and `demo-output.txt` are kept.
- `benchmark` (P07): `harness benchmark run` five times (`benchmark-micro-N.json`) and
  `harness benchmark scenarios` (`benchmark-scenarios.json`).
- `large` (N16): the measurement of docs/reference/configuration.md ("Large repositories"),
  repeated: a generated Git repository of 10,001 tracked files (9,996 text files of about 11.7 kB
  plus the five files of the sample project), 500 ignored build files and an ignored `.env`; one
  patch task of two files, the `simulated` provider and `python.pytest` only; the configuration
  `harness init` writes with and without the three workspace keys (`snapshot`, `baseline`,
  `snapshotCache`), three repetitions each: seconds of `run start`, maximum resident set
  (`/usr/bin/time -l`), bytes stored under `.harness` and in the run registry, the largest stored
  file and whether the `.env` secret was stored. The generator is this script's (the one behind the
  CHANGELOG figures is not in the repository), so its figures are not the CHANGELOG's.

Commands (worktree code; `static` ran first, the other sections later with no other suite running):

```bash
SCRATCH=/private/tmp/claude-502/-Users-jbarrerapuli-Documents-Repos-Proyecto-de-grado/ceca23e5-5679-47dc-ab0b-75aa4166d2c7/scratchpad
.venv/bin/python evaluation/deterministic/block0_technical.py --harness "$PWD/.venv/bin/harness" --repo . \
  --python "$PWD/.venv/bin/python" --project-python $SCRATCH/venvs/eval/bin/python --ruff "$PWD/.venv/bin/ruff" \
  --out evaluation/results-2.0.0/deterministic/block0 --work $SCRATCH/work/block0 --only static
.venv/bin/python evaluation/deterministic/block0_technical.py ... --only tests,demo,benchmark,large
```
