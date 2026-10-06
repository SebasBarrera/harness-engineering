# Exit codes

Every `harness` command maps a harness condition to a stable exit code. The CLI and the API use
the same application layer, so a condition always gets the same code. Codes come from the error
hierarchy in `src/governed_harness/domain/errors.py` and from the run status mapping in
`src/governed_harness/cli/main.py`.

| Code | Name | When | Verified with |
|---:|---|---|---|
| `0` | success | The command completed. For `run start`, `run continue` and `gate decide`, the run reached `CLOSURE` or the decision was recorded. | quickstart flow |
| `1` | harness or integration error | `HarnessError`, or any unexpected exception (printed as JSON with `errorType`). A run whose status is `ERROR` also exits with 1. | code |
| `2` | configuration error | `ConfigurationError`: no `.harness/project.yaml` in the directory, invalid configuration, a task file that cannot be parsed or has no acceptance criteria outside `intake.criteriaPolicy: enforce`, an attempt to weaken a locked policy, a failed `doctor` check, a task memory record without its task, an `EDIT` of a recommendation without the edited text, or a `task clarify` answers file with an unknown question id, an empty answer or an unknown field. | `harness status` in a directory without a project; memory and clarification flows |
| `3` | not found | `NotFoundError`: unknown run, task, memory record or recommendation, a run without a retrospective or context manifest, or a task without an open clarification request. | `harness status --run run_missing` |
| `4` | waiting for a human decision | The automated phases finished and the run stopped at `DECISION`: automation reached the limit of its authority. | `harness run start` in every demo flow |
| `5` | policy violation | `PolicyViolationError` from `gate decide`: the digest is not the current ChangeSet digest (stale approval), `APPROVE` over a gate that did not pass (`FAILED`, `INCONCLUSIVE`, ...), the run is not in `DECISION`, `APPROVE_EXCEPTION` without a rationale, an actor id of an agent, validator or the harness (`agent.*`, `validator.*`, `harness.*`), a wrong digest confirmation under `governance.confirmDecisionDigest`, or an acceptance contract that changed after `SPECIFICATION` under `governance.pinTaskRevision`. Also `task create` with the id of a task that has an open run under the same setting, a workspace whose lease another harness process holds (`governance.workspaceLease`), approving a memory record that is already approved or needs no approval, deciding a recommendation twice, and `task clarify` on a task with a run past `INTENT` or with an actor id of an agent, validator or the harness. Since 1.1: `harness verify-approval` found no unexpired approval bound to the ChangeSet digest of the range, and `harness export` of a run whose event chain does not verify. Since 1.1 also: `APPROVE` without `--acknowledge-risk` for a risk factor whose action is `acknowledge`, `plan decide` or `acceptance decide` with a stale digest or a non-human actor, `budget raise` to a limit that is not higher or without a rationale, and `run quarantine` of a run that passed or waits for a decision. Since #55: `APPROVE` while a checklist item of the run is not ticked (`--check`), `verification decide` on a run that does not wait on an `UNAVAILABLE` preflight or without a rationale, `task confirm` with a stale digest, `evidence attach` to an expired or already closed deferred item or with evidence about another commit, and `run cleanup` of a worktree with uncommitted changes. | later-change, review-exception, memory, clarification, traceability, delivery and ladder flows, agent-results tests |
| `6` | blocked | `harness verify` found a check that failed (also with `--bundle`: a problem in an evidence bundle), or `harness trace` or `harness export` under `governance.verifyRecords` refused a run that does not verify. A closure commit that cannot be created (`delivery.closureCommit`) leaves `CLOSURE` `BLOCKED`. The run stopped because a validation, policy, blocking, timeout or inconclusive condition held (`BLOCKED`, `FAILED`, `INCONCLUSIVE` or `TIMED_OUT` outside `DECISION`), including `INTENT` blocked by open clarification questions under `intake.criteriaPolicy: enforce`, and by a task without acceptance criteria (rule `C0`) under any policy. Since 1.1 also: `harness check` found a failing mandatory validator or a blocking problem of an enforced check, INTENT blocked by the agent review of the task, PLANNING or SPECIFICATION waiting for a person to decide a decomposition or acceptance tests, a phase blocked by `budget.exceeded`, and a run that is `FAILED` after a `REJECT` decision. Since #55: DISCOVERY blocked by the environment preflight, PLANNING blocked by a preflight `UNAVAILABLE`, VERIFICATION failed by a declared rung not reached or blocked by an unavailable probe or a scope contradiction, an isolation collision, a refused push, and `harness config lint` with an issue. Since #57: `harness review-code` with a `FAIL` or `UNKNOWN` verdict under `review.panel.mode: enforce` or in hook mode without its base, `harness review rules sync --check` with a drifted rules block, and `harness review verify` on evidence that does not verify. Since #76 and #87: a phase blocked because a policy refused a command before it started (a destructive provider launch under `destructiveActionsDefault: deny`, `capabilities.destructive-denied`; a provider command no grant allows under `governance.phaseCapabilities`, `capabilities.command-denied`), each with a `HIGH` finding. | broken-baseline and clarification flows (`run start`), agent-results, ladder and review panel flows |
| `130` | cancelled | The run status is `CANCELLED` (`harness run cancel`, or cancellation during execution). | code |
| `143` | terminated | Under `governance.workspaceLease`, the command received `SIGTERM`: the agent's process group was terminated and the phase and the run were recorded as `INTERRUPTED`; `run continue` recovers the run. A run command that returns an `INTERRUPTED` run exits with 6. | `tests/integration/test_workspace_lease.py` |

"Verified with" refers to `scripts/demo_flows.py`, which checks every exit code in CI (workflow
`docs-smoke`). `v0.8.0` shipped without documentation for codes 3 and 5 (issue #11).

## Using the codes in CI

The codes let a pipeline tell "the automation failed" apart from "the automation did its part and
a person must decide":

```bash
set +e
harness run start --path . --task "$TASK_ID" > run.json
code=$?
set -e
case "$code" in
  0) echo "run closed" ;;
  4) echo "automated phases passed; waiting for a human decision" ;;  # not a failure
  6) echo "blocked: inspect harness findings list"; exit 1 ;;
  *) echo "harness error ($code)"; exit "$code" ;;
esac
```

A job that treats 4 as a failure would hide the fact that the gate did its job. A job that treats
4 as success would skip the human decision, which the harness never lets automation record on its
own.
