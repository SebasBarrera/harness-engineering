# Greenfield guide: a governed change on a new project

This guide takes a new Python project from an empty directory to an approved, traceable change.
Every command and output below comes from a real run with the harness installed from this
repository. Absolute paths are shortened to `…/pricing-demo`; identifiers and digests are the ones
of that run and will differ on your machine.

**You need:** Python ≥ 3.12, Git and the harness (`pip install` from a release wheel or from source;
see the [README](https://github.com/SebasBarrera/harness-engineering#installation)). For a Node.js
project you also need Node.js LTS and npm. The harness is a Python package: it is not installed with
npm or npx.

## 1. Create the project and its baseline commit

The harness computes the ChangeSet of a run against the state of the repository captured when the
run starts. Start from a clean, committed tree so that the baseline is well defined.

```bash
mkdir pricing-demo && cd pricing-demo
mkdir -p src/pricing tests
cat > src/pricing/__init__.py <<'EOF'
def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
    return subtotal
EOF
cat > tests/test_pricing.py <<'EOF'
from pricing import apply_discount


def test_below_threshold() -> None:
    assert apply_discount(99, 100, 0.1) == 99
EOF
cat > pyproject.toml <<'EOF'
[project]
name = "pricing-demo"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
EOF
printf '.harness/\n__pycache__/\n.pytest_cache/\n' > .gitignore
git init -q && git add . && git commit -qm "baseline"
```

Install `pytest` in the environment where the harness runs: the Python profile runs
`python -m pytest` with the `python` found on `PATH`. For a Node.js project, create `package.json`
with a `test` script instead (for example `"test": "node --test"`).

## 2. Initialize and inspect

```console
$ harness init
{
  "status": "PASSED",
  "configuration": "…/pricing-demo/.harness/project.yaml"
}
$ harness inspect
{
  "workspace": "…/pricing-demo",
  "detections": [
    {
      "profileId": "python_default",
      "technology": "python",
      "confidence": 0.8,
      "evidence": [
        "pyproject.toml"
      ],
      "warnings": []
    }
  ]
}
```

`harness doctor --path .` checks Python, Git, Node.js, npm, the configuration and write access to
`.harness/` (all `PASSED` in this run), and `harness config validate` prints the resolved profiles,
workflow, validators, capabilities and policies. All four commands exited with 0. The generated
configuration is described in the [configuration reference](../reference/configuration.md).

## 3. Write the task

The task states the intent, requirements and acceptance criteria. This example uses the `patch`
implementation mode with the deterministic `simulated` provider, so the change is reproducible; an
external agent is configured as shown in the [external agents guide](external-agents.md).

```yaml
# task.yaml
taskId: task_discount_rule
title: Apply a percentage discount above a threshold
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - requirementId: req_discount
    text: A subtotal at or above the threshold is reduced by the rate; below it, it is unchanged.
acceptanceCriteria:
  - criterionId: ac_below
    text: A subtotal below the threshold is unchanged.
  - criterionId: ac_at_threshold
    text: A subtotal equal to the threshold is reduced by the rate.
constraints:
  - Keep the public signature of apply_discount.
implementation:
  mode: patch
  patches:
    - path: src/pricing/__init__.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |


        def test_req_discount_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
```

All fields are described in the [task-file reference](../reference/task-file.md).

```bash
harness task create --file task.yaml     # exit 0, prints the persisted task
```

## 4. Run the normative phases

```console
$ harness run start --task task_discount_rule
…
$ echo $?
4
```

Exit code **4** is the expected result: the automated phases passed and the run stopped at
`DECISION`, waiting for a person. The run printed `executionId`
`run_f7e20c4ba06a4346b3d1659d9ae82b65`, `status: BLOCKED`, `currentPhase: DECISION` and the
ChangeSet digest `sha256:c1bed3330fbed41155a1b05ee6e6c44e8200bfc68829491764e01266b301b47a`.

Inspect what the harness observed:

```bash
harness status --run run_f7e20c4ba06a4346b3d1659d9ae82b65
```

In this run the status projection showed:

| Item | Value |
|---|---|
| Phases | `INTENT` … `INDEPENDENT_REVIEW` `PASSED`, `DECISION` `BLOCKED` |
| Validations | 5 results, all `PASSED`: `python.pytest`, `python.ruff` and `python.mypy` (Ruff and mypy were installed; without them they are `NOT_APPLICABLE`), `traceability.requirements` (requirement `req_discount` is named by `test_req_discount_at_threshold`) and the independent review |
| Findings | 0 |
| Gate | `PASSED`, reason `ALL_MANDATORY_VALIDATIONS_PASSED` |
| Event chain | 34 events, valid |

`harness findings list --run …` printed `[]`, and `harness evidence list --run …` lists the
evidence records with their content-addressed artifacts (`artifact://sha256/…`).

## 5. Decide

A decision is bound to the exact ChangeSet digest shown by `status`. If the owned files change
after the gate was evaluated, the old digest is rejected with exit code 5 (see step 7).

```console
$ harness gate decide --run run_f7e20c4ba06a4346b3d1659d9ae82b65 \
    --decision APPROVE \
    --change-set-digest sha256:c1bed3330fbed41155a1b05ee6e6c44e8200bfc68829491764e01266b301b47a \
    --actor human.reviewer \
    --rationale "Both acceptance criteria are covered by passing tests"
$ echo $?
0
```

The recorded decision carries the actor (`HUMAN`, `human.reviewer`), the rationale, the gate
evaluation id and the ChangeSet, configuration and policy digests. The run moved to `CLOSURE` with
status `PASSED`.

| Decision | Use it when |
|---|---|
| `APPROVE` | The gate `PASSED` and the evidence supports the change. Rejected (exit 5) if the gate did not pass. |
| `APPROVE_EXCEPTION` | The gate did not pass but you accept the change with a justification (required). The exception stays visible in the trace. |
| `REQUEST_CHANGES` | Send the run back to `IMPLEMENTATION`; verification, review and gate are invalidated. |
| `REJECT` | Close the run without accepting the change. |

## 6. Trace and retrospective

```console
$ harness trace --run run_f7e20c4ba06a4346b3d1659d9ae82b65 --format markdown --output trace.md
trace.md
$ head -16 trace.md
# Execution trace `run_f7e20c4ba06a4346b3d1659d9ae82b65`

- **Task:** Apply a percentage discount above a threshold (`task_discount_rule`)
- **Status:** `PASSED`
- **Current phase:** `CLOSURE`
- **ChangeSet digest:** `sha256:c1bed3330fbed41155a1b05ee6e6c44e8200bfc68829491764e01266b301b47a`
- **Configuration digest:** `sha256:51c583471f7961b308bb8bf4f1f62432a0a1a0591d1e61b15be31d0d3f2967c1`
- **Workflow digest:** `sha256:5cc582e7e4c8ffac23a1f50c4fcf36715c1c71d11bb3ea3fa95c6c00eb653732`
- **Policy digest:** `sha256:6163dac529e268a82d0c1bea5c1be272bb5984711dc7512ca8e1d87eb769da07`

## Normative phases

| Phase | Attempt | Status | Summary |
|---|---:|---|---|
| `INTENT` | 1 | `PASSED` | Intent is structured and identifiable |
| `DISCOVERY` | 1 | `PASSED` | Workspace and baseline discovered |
```

`--format json`, `jsonl` and `sarif` (SARIF 2.1.0) are also available. `harness retrospect --run …`
returns observations and recommendations with `"appliedAutomatically": false`: the harness never
changes its own rules.

The harness does not commit. After the decision, the approved change is in your working tree
(`git status` shows `src/pricing/__init__.py` and `tests/test_pricing.py` modified); review
`git diff` and commit it yourself.

## 7. What a change after the gate looks like

If an owned file changes after the gate was evaluated, the approval no longer represents the code.
This sequence is exercised in CI by `scripts/demo_flows.py later-change`:

1. `harness gate decide … --change-set-digest <old digest>` → exit **5**: `decision digest does not
   match the current ChangeSet; prior approval is stale`.
2. `harness run continue --run …` → exit **4**; the gate is now `INCONCLUSIVE` with reason
   `NO_MANDATORY_VALIDATIONS`, because the validations belong to the previous digest.
3. `APPROVE` over that gate → exit **5**. Record `REQUEST_CHANGES` with the new digest (exit 0),
   then `harness run continue` (exit 4) verifies again and `APPROVE` succeeds (exit 0).

The run then shows 2 implementation attempts, 1 correction cycle, 3 ChangeSets and 2 human
decisions in its metrics.
