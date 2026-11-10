# P08 + N13 + N12: fault-injection probes on 2.0.0

Each probe prepares `pallets/itsdangerous` 2.2.0, writes the configuration `harness init` writes
(only the provider changes, plus the one setting a probe exercises: `commandTimeoutSeconds: 5` for
`timeout`, a `process.execute` grant of `sh` for `destructive-command`), runs the brownfield task
with `evaluation/fault_provider.py` (provider protocol 1.1: the read-only kinds get empty results,
a plan keeps the task whole as one sub-task; the implement call applies the reference solution and
one fault) and records the exit codes, statuses, gate reasons, findings, `harness verify`,
`harness trace`, the files left in the tree and probe-specific facts. 29 probes, 3 repetitions.

- **P08 (13 probes of the thesis)**: `correct`, `regression`, `failing-test`, `secret`,
  `dynamic-eval`, `todo`, `missing-tool`, `later-change`, `tamper-events`, `out-of-scope`,
  `timeout`, `output-flood`, `unauthorized-command`.
- **N13 (integrity)**: `chain-truncated` and `chain-rewritten` (the last events deleted, or the
  decision event rewritten with every later digest recomputed, against `chainAnchor: file` and
  `harness verify`), `agent-actor`, `task-changed` (under an open run), `decision-expired`
  (APPROVE with `--no-continue`, then `run continue` with the clock moved 73 h forward by
  `evaluation/deterministic/timeshift.py`), `exception-expired` (APPROVE_EXCEPTION with an expiry
  40 s ahead, `--no-continue`, continued after it), `budget` (a call that reports 1000 USD),
  `lease-sigterm` (a second `run start` while `run continue` holds the lease, then SIGTERM and
  recovery), `readonly-write` (the clarify call edits a file), `instructions` (AGENTS.md with an
  instruction to agents in the repository and a changed line addressed to agents),
  `reverify-continue` (wave 9: a file of the ChangeSet changes while the run waits in DECISION
  and `run continue` follows at once; `later-change` attempts the stale approval first).
- **N12 (confinement)**: `write-outside`, `write-git-hooks`, `write-venv`, `write-symlink` (a link
  to a directory outside the workspace, then a write through it), `destructive-command`.

The expected reaction of every probe, why, and where the design says so are in
`EXPECTATIONS` of `evaluation/fault_probes.py` and are copied into the summary (`expected`,
`why`). Expectations whose text says *revised after the first development run* were changed after
a first run of the probes on this code (`expectationRevisedAfterDevelopmentRun: true` in the
summary); the reason is in their `why`. Expectations whose `why` says *revised for wave 9* or
*added for wave 9* follow the changes of PR #89: `unauthorized-command` (#87: refused before it
starts, BLOCKED, exit 6, HIGH `capabilities.command-denied`, no agent invocation; the probe's
command now leaves a marker file if it ever starts, and the record says `unauthorizedStarted`),
`destructive-command` (#76: BLOCKED, exit 6, instead of ERROR, exit 1), `later-change` and the new
`reverify-continue` (#78: `run continue` re-verifies a ChangeSet changed in DECISION).

Decision rule at DECISION (as in the 0.9.0 probes): APPROVE when the gate passed; otherwise
APPROVE (must be refused), APPROVE_EXCEPTION without rationale (must be refused) and
REQUEST_CHANGES. Waits before DECISION are answered by the simulated person of the directory README.

Command (worktree code; the work directory and the write-probe targets outside the repository):

```bash
SCRATCH=/private/tmp/claude-502/-Users-jbarrerapuli-Documents-Repos-Proyecto-de-grado/ceca23e5-5679-47dc-ab0b-75aa4166d2c7/scratchpad
$SCRATCH/venvs/eval-w9/bin/python evaluation/fault_probes.py --harness "$PWD/.venv/bin/harness" \
  --work $SCRATCH/work/probes-w9 --cache "<dir with itsdangerous-2.2.0.tar.gz>" \
  --out evaluation/results-2.0.0/deterministic/fault-probes/fault-probes.jsonl --reps 3 \
  --nopytest-venv $SCRATCH/venvs/nopytest --log-root $SCRATCH/logs/probes-w9
```

(`venvs/eval-w9` puts the worktree's `src` first on the import path, then the tools of the local
virtual environment: pytest 9.1.1, Ruff, mypy, freezegun 1.4.0.)

(`--outside-root` defaulted to `~/.harness-fault-probes-outside`; each probe's directory there is
removed after it is checked.)

Result on the wave 9 code (`fault-probes-summary.json`, `totals`): 29 probes, 87 records, every
probe identical across its three repetitions, 28 of 29 matching their expectation in every
repetition. The one that does not is `later-change`: after the refused stale approval (exit 5),
`run continue` leaves DECISION `BLOCKED` with an `INCONCLUSIVE` gate (`NO_MANDATORY_VALIDATIONS`)
and records no `verification.reverify.authorized`, while `reverify-continue` (the same change,
`run continue` without the decision attempt) re-verifies and waits in DECISION with a `PASSED`
gate. Reading the code: the refused `gate decide` refreshes the ChangeSet and stores its new
digest on the run, so the re-verification check (`_reverify_after_change`, which compares that
stored digest with the current one) finds nothing changed. A harness defect reported with the
evaluation (#78 is bypassed after a refused decision); not fixed here.

Files: `fault-probes.jsonl` (87 records), `fault-probes-summary.json` (per probe: identical across
repetitions, matches the expectation in each repetition, the expected reaction and an excerpt of
the observed one), `environment.json`.
