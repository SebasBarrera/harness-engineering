# Benchmarks

> These are **synthetic microbenchmarks** of local harness operations, run without warm-up on
> shared CI runners. They quantify the runtime overhead of the harness mechanisms in the recorded
> environment. They do not measure developer productivity, code quality or statistical
> significance.

## What is measured

`harness benchmark run --iterations N` times, in one process:

| Operation | What it exercises |
|---|---|
| `stateTransition` | One transition decision of the normative state machine |
| `gateEvaluation` | One gate evaluation in the positional `GateInput` form (one passed mandatory input) |
| `canonicalHash` | Canonical JSON digest (SHA-256) of a small payload |
| `artifactPutDeduplicated` | Storing identical bytes in the content-addressed artifact store |
| `eventAppend` | Appending one event to the SQLite hash chain |
| `processDirect` / `processGoverned` | Launching `python -c pass` directly versus through the governed process runner (capabilities, containment, bounded output) |

Each operation reports `iterations`, `meanMs`, `medianMs`, `p95Ms`, `operationsPerSecond` and
`totalMs`; `processGovernanceOverhead` reports the absolute and relative median overhead of the
governed launch. The process pair uses `max(5, min(30, iterations // 50))` samples (20 at 1,000
iterations) instead of `iterations`.

`harness benchmark scenarios --iterations N` compares, on generated Python and Node.js projects,
a direct patch-and-test path with the complete governed path (all phases, validation, review, gate,
an immediate programmatic approval and closure). Human waiting time is excluded.

## How CI runs them

`benchmarks.yml` runs on pushes to `develop` and `main`, weekly and on demand:

1. `harness benchmark run --iterations 1000` five times and `harness benchmark scenarios
   --iterations 3` once.
2. `scripts/benchmark_report.py` renders one self-contained HTML report: median and p95 per
   operation (log scale), direct versus governed launch, throughput, dispersion across the five
   repetitions, environment and methodology, and the thesis reference point. Every chart has an
   equivalent data table.
3. The median of each operation is stored on the `gh-pages` branch by
   `github-action-benchmark` (`customSmallerIsBetter`), which draws the per-commit trend.
4. A regression above 150 % comments on the commit but **does not fail** the workflow: shared
   runners are noisy, and a failed benchmark would block unrelated work.

Published pages: the latest report at `/benchmarks/` and the trend at `/benchmarks/trend/` of the
documentation site.

## Thesis reference point

The thesis reported, for the `v0.8.0` cut, 1,000 iterations per operation without warm-up and 20
samples for process launches (`docs/benchmarks/thesis-reference.json`):

| Operation | Median (ms) |
|---|---:|
| State transition | 0.00096 |
| Gate evaluation | 0.00153 |
| Canonical digest | 0.0228 |
| Event append | 0.281 |
| Deduplicated artifact put | 0.0549 |
| Governed process launch overhead | 1.21 ms (10.09 %; 11.23 % in a second run) |

Environments differ (the thesis machine versus GitHub-hosted runners), so compare orders of
magnitude, not exact values.

## Run locally

```bash
for i in 1 2 3 4 5; do harness benchmark run --iterations 1000 --output bench/micro-$i.json; done
harness benchmark scenarios --iterations 3 --output bench/scenarios.json
python scripts/benchmark_report.py --micro bench/micro-*.json --scenarios bench/scenarios.json \
  --reference docs/benchmarks/thesis-reference.json --output bench/report.html
```

Before 0.9.0, the microbenchmark failed on macOS with `path escapes workspace` because the default
`/var/folders/...` temporary directory goes through the `/var` → `/private/var` symlink (issue #19).
