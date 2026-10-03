# Exit codes

Every `harness` command maps a harness condition to a stable exit code. The CLI and the API use
the same application layer, so a condition always gets the same code. Codes come from the error
hierarchy in `src/governed_harness/domain/errors.py` and from the run status mapping in
`src/governed_harness/cli/main.py`.

| Code | Name | When | Verified with |
|---:|---|---|---|
| `0` | success | The command completed. For `run start`, `run continue` and `gate decide`, the run reached `CLOSURE` or the decision was recorded. | quickstart flow |
| `1` | harness or integration error | `HarnessError`, or any unexpected exception (printed as JSON with `errorType`). A run whose status is `ERROR` also exits with 1. | code |
| `2` | configuration error | `ConfigurationError`: no `.harness/project.yaml` in the directory, invalid configuration, a task file that cannot be parsed, an attempt to weaken a locked policy, a failed `doctor` check, a task memory record without its task, an `EDIT` of a recommendation without the edited text, or a `task clarify` answers file with an unknown question id, an empty answer or an unknown field. | `harness status` in a directory without a project; memory and clarification flows |
| `3` | not found | `NotFoundError`: unknown run, task, memory record or recommendation, a run without a retrospective or context manifest, or a task without an open clarification request. | `harness status --run run_missing` |
| `4` | waiting for a human decision | The automated phases finished and the run stopped at `DECISION`: automation reached the limit of its authority. | `harness run start` in every demo flow |
| `5` | policy violation | `PolicyViolationError` from `gate decide`: the digest is not the current ChangeSet digest (stale approval), `APPROVE` over a gate that did not pass (`FAILED`, `INCONCLUSIVE`, ...), the run is not in `DECISION`, or `APPROVE_EXCEPTION` without a rationale. Also approving a memory record that is already approved or needs no approval, deciding a recommendation twice, and `task clarify` on a task with a run past `INTENT` or with an actor id of an agent, validator or the harness. | later-change, review-exception, memory and clarification flows |
| `6` | blocked | The run stopped because a validation, policy, blocking, timeout or inconclusive condition held (`BLOCKED`, `FAILED`, `INCONCLUSIVE` or `TIMED_OUT` outside `DECISION`), including `INTENT` blocked by open clarification questions under `intake.criteriaPolicy: enforce`. | broken-baseline and clarification flows (`run start`) |
| `130` | cancelled | The run status is `CANCELLED` (`harness run cancel`, or cancellation during execution). | code |

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
