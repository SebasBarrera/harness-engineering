# Deterministic evaluation of 2.0.0 (no model calls)

The suites of this directory call no model: every agent is a fixture command provider
(`evaluation/fault_provider.py`, `evaluation/deterministic/review_fixture_reviewer.py`, the
fixtures inside `friction.py`) or the harness's `simulated` provider. Each suite writes an
`environment.json` with the harness executable, its version (`harness --version`), the wheel and
its SHA-256 when one is given (`--wheel` or `$HARNESS_WHEEL`), the repository head and a digest of
the evaluation code.

| Suite | Test ids | Script | Results |
|---|---|---|---|
| Fault probes (13 of the thesis + 11 integrity + 5 confinement, 3 repetitions) | P08, N13, N12 | `evaluation/fault_probes.py` | `fault-probes/` |
| Verification-ladder corpus | N01-a | `evaluation/deterministic/ladder_corpus.py` | `ladder/` |
| Review-panel corpus | N02-a (hook for N02-b) | `evaluation/deterministic/review_corpus.py` | `review/` |
| Friction with the fixture provider | N03-a | `evaluation/deterministic/friction.py` | `friction/` |
| INTENT equivalence | P24 | `evaluation/deterministic/intent_equivalence.py` | `intent-equivalence/` |
| Metrics validity | N04 | `evaluation/deterministic/metrics_validity.py` | `metrics-validity/` |
| Technical verification | Block 0: P03, P04, P06, P07, N16 | `evaluation/deterministic/block0_technical.py` | `block0/` |

Each subdirectory has a README with the exact command that produced it. Raw run directories
(workspaces, run registries) are not kept in Git: the suites write them under `--work`.

## Declared simulation

Waits for a person before DECISION (INTENT questions, acceptance tests, a plan approval or a
decomposition, an unavailable preflight) are answered by a simulated person,
`human.reviewer-simulated` (`evaluation/deterministic/common.py`, `simulated_person`): it answers
questions with "keep the behaviour the task describes", approves acceptance tests and plans, and
continues an unavailable preflight uncertified. It reads the wait from `harness inbox --json`,
which since wave 9 (#73) lists every wait before DECISION with its kind and digest (a `plan show`
fallback is kept for a harness without it). At DECISION each suite applies its own declared rule
(see its README). This is a simulation of a person, not a person.

## Environment of these results

Run on the worktree code of the branch `test/evaluation-2-0-0-deterministic` with wave 9 merged
(origin/develop, the wave-7 branch and `fix/wave9-hardening` at `de84849`, PR #89), whose package
still declares version `1.0.0` (`harness --version` prints `harness 1.0.0`); 2.0.0 is not tagged
yet. Python 3.12.11 on macOS arm64; the projects' validators ran with pytest 9.1.1, Ruff 0.16.7,
mypy and freezegun 1.4.0 from a local virtual environment. Every suite was re-run on that code on
2026-10-06 (the first results, on the code before wave 9, are in the history of this branch).

What wave 9 changed in these results (each subdirectory's README has the detail): the fault
probes `unauthorized-command` (refused before it starts, #87) and `destructive-command` (BLOCKED,
exit 6, #76), the new probe `reverify-continue` and the re-verification expected of
`later-change` (#78); no probe leaves `.gitignore` changed any more (#86, init writes
`.git/info/exclude`); the review corpus's identical re-review reports 0 model calls on a global
cache hit (#75); the ladder corpus's command-line probe has its second variant back (#74).

## Re-running on the v2.0.0 wheel

```bash
# 1. A virtual environment with the wheel and the tools the projects' validators use.
python3.12 -m venv "$W/venv"
"$W/venv/bin/pip" install governed_agent_harness-2.0.0-py3-none-any.whl \
  pytest pytest-cov freezegun==1.4.0 ruff mypy coverage
python3.12 -m venv --without-pip "$W/nopytest"          # for the missing-tool probe
export HARNESS_BIN="$W/venv/bin/harness" HARNESS_WHEEL="$PWD/governed_agent_harness-2.0.0-py3-none-any.whl"
PY="$W/venv/bin/python"; R=evaluation/results-2.0.0/deterministic
# The brownfield archive (SHA-256 7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b):
mkdir -p "$W/cache" && curl -sSfL -o "$W/cache/itsdangerous-2.2.0.tar.gz" \
  https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz

# 2. The suites (work directories outside any repository: Ruff reads the closest configuration).
"$PY" evaluation/fault_probes.py --work "$W/probes" --cache "$W/cache" --out $R/fault-probes/fault-probes.jsonl \
  --reps 3 --project-venv "$W/venv" --nopytest-venv "$W/nopytest"
"$PY" evaluation/deterministic/ladder_corpus.py --out $R/ladder --work "$W/ladder" --project-venv "$W/venv"
"$PY" evaluation/deterministic/review_corpus.py --out $R/review --work "$W/review" --project-venv "$W/venv"
"$PY" evaluation/deterministic/friction.py --out $R/friction --work "$W/friction" --project-venv "$W/venv"
"$PY" evaluation/deterministic/intent_equivalence.py --out $R/intent-equivalence
# 3. Block 0 alone on the machine (it times benchmarks), from a checkout of the tag v2.0.0:
"$PY" evaluation/deterministic/block0_technical.py --repo "$W/v2.0.0" --python "$PY" \
  --project-python "$PY" --ruff "$W/venv/bin/ruff" --out $R/block0 --work "$W/block0"
# 4. N04 on the friction runs and the demonstration projects of block 0:
"$PY" evaluation/deterministic/metrics_validity.py --out $R/metrics-validity \
  --runs-glob "$W/friction/friction-*" --demo-workdir "$W/block0/demo" --demo-state "$W/block0/demo-state"
```

`--harness` overrides `$HARNESS_BIN`; every suite records `harness --version` and the wheel's
SHA-256. The expired-decision probe moves the clock of one `harness run continue` with
`evaluation/deterministic/timeshift.py`, run by the harness's interpreter (`--harness-python`,
default `python` next to the harness executable).
