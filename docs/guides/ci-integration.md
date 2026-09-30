# Using the harness in CI

The harness is a local control plane, not a CI service. In a pipeline it is useful in two ways:
as a **verification step** that runs a governed task and publishes its evidence, and as a
**signal** whose exit code distinguishes "blocked" from "done, waiting for a person".

## Exit codes in a pipeline

| Code | Meaning for the pipeline |
|---:|---|
| 0 | The run reached `CLOSURE` (a decision was already recorded). |
| 4 | The automated phases passed; a human decision is pending. **Not a failure**: report it. |
| 6 | Blocked by a validation, policy, timeout or inconclusive result. Fail the job. |
| 1, 2, 3, 5 | Harness, configuration, not-found or policy error. Fail the job. |

See the [exit-code reference](../reference/exit-codes.md) for the full list.

## Example: a manually triggered governed run on GitHub Actions

The workflow below runs a committed task, publishes the Markdown trace in the job summary, uploads
the SARIF trace to code scanning and keeps `.harness/` as an artifact. It passes `actionlint` and
`zizmor`; it has not been run as part of this repository's CI, so adapt the install step to your
project (here it assumes a `test` extra).

```yaml
name: governed-change

on:
  workflow_dispatch:
    inputs:
      task:
        description: Task id in the committed task file
        required: true

permissions:
  contents: read

jobs:
  govern:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - name: Install the harness and the project's test dependencies
        run: |
          python -m pip install "governed-agent-harness @ https://github.com/SebasBarrera/harness-engineering/releases/download/v0.8.1/governed_agent_harness-0.8.1-py3-none-any.whl"
          python -m pip install -e ".[test]"
      - name: Run the governed task
        id: run
        env:
          TASK: ${{ inputs.task }}
        run: |
          [ -f .harness/project.yaml ] || harness init   # init exits 2 if the file exists
          harness task create --file task.yaml
          set +e
          harness run start --task "$TASK" > run.json
          code=$?
          set -e
          run_id=$(python -c "import json;print(json.load(open('run.json'))['executionId'])")
          echo "run_id=${run_id}" >> "$GITHUB_OUTPUT"
          harness trace --run "$run_id" --format sarif --output harness.sarif
          harness trace --run "$run_id" --format markdown --output trace.md
          cat trace.md >> "$GITHUB_STEP_SUMMARY"
          case "$code" in
            4) echo "::notice::Automated phases passed; a human decision is pending." ;;
            0) echo "Run closed." ;;
            *) echo "::error::Harness exit code ${code}"; exit "$code" ;;
          esac
      - uses: github/codeql-action/upload-sarif@2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2 # v4.38.2
        if: always()
        with:
          sarif_file: harness.sarif
          category: governed-agent-harness
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        if: always()
        with:
          name: harness-state
          path: .harness/
          include-hidden-files: true
```

## Limits to keep in mind

- **State is local to the runner.** `.harness/` (SQLite state, event chain, artifacts) lives in the
  job workspace. The run stops at `DECISION`; a person can download the `harness-state` artifact and
  decide locally with `harness gate decide`, or the pattern can run on a persistent (self-hosted)
  workspace. A hosted runner cannot keep a run open between jobs.
- **Not a sandbox.** In CI the harness runs the project's commands with the job's permissions and
  network. Keep `permissions` minimal and do not expose secrets to the job.
- **Human decisions are never automated.** A pipeline must not call `harness gate decide` on
  behalf of a person; `requireHumanDecision` is a locked policy.
- **Token and cost metrics** stay `NOT_AVAILABLE` unless the agent provider reports them.
