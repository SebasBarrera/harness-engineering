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

Notes on this run:

- `tests` and `demo` ran while the fault-probe suite ran in another process (their seconds share
  the machine); `benchmark` and `large` ran after it, with no other suite running.
- The two failures of `tests` (`tests/integration/test_declared_settings.py`,
  `test_config_validate_reports_declarative_settings` and `test_schema_marks_declarative_fields`)
  come from the wave-7 snapshot merged into this branch (`11c05bb`): the tests still expect
  `allowedCapabilities` to be declarative, which wave 7 (#4) changed; the wave-7 branch fixed the
  tests later (`3d6cbe5`, "expect allowedCapabilities to be applied, not declarative"), after this
  branch was created. The re-run on the v2.0.0 wheel and its tag settles it.
- Local absolute paths in the result files were replaced by `<repo>`, `<work>`, `<tmp>` and `~`
  with `evaluation/deterministic/sanitize_paths.py`.
- In all six `large` runs `run start` stopped in VERIFICATION (exit 6, `BLOCKED`) under the full
  `init` configuration; the script deleted each workspace after measuring it and did not record
  the blocking reason, so the seconds and the resident set cover INTENT to VERIFICATION, not a run
  that reached DECISION as in the CHANGELOG measurement.
