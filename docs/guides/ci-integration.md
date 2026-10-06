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
          python -m pip install "governed-agent-harness @ https://github.com/SebasBarrera/harness-engineering/releases/download/v2.0.0/governed_agent_harness-2.0.0-py3-none-any.whl"
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

## Approval valid for what gets merged

A decision binds the digest of the ChangeSet in the workspace where the run happened; what a team
merges is a pull request. Since 2.0 three pieces connect them:

1. **The closure commit** (`delivery.closureCommit`, written by `harness init` as `branch`): at
   `CLOSURE` the approved ChangeSet becomes one commit on `harness/<run id>` with the trailers
   `Harness-Run`, `Harness-Task`, `Harness-ChangeSet` and `Harness-Decision` (and
   `Harness-Exception` for an `APPROVE_EXCEPTION`). The harness recomputes the commit's diff before
   it creates the branch; see [delivery](../reference/configuration.md#delivery).
2. **The evidence bundle**: `harness export --run <id> --bundle <file>.tar.gz` writes the run's
   event chain, records and artifacts with a manifest of digests. `harness verify --bundle <file>`
   checks it without the workspace (exit 0, or 6 with the problems).
3. **`harness verify-approval`**: recomputes the ChangeSet digest of `--base..--head` from the two
   revisions and passes (exit 0) only when an unexpired `APPROVE` or `APPROVE_EXCEPTION` in a
   verified bundle (or in the workspace's own record) is bound to exactly that digest. Any other
   change in the range (a commit after the closure commit, a file the ChangeSet excluded, a mode
   change, a symbolic link) gives another digest and the check fails (exit 5). The `Harness-*`
   trailers of the range are reported, but a trailer alone never approves.

A merge result is checked as it will be merged: if the base branch changed the same files, the
hunks of the merged diff differ from the approved ones and the check fails until the change is
run and approved again on the new base. A bundle and a trailer establish consistency, not
authenticity: anyone who can rewrite the whole bundle can rewrite it consistently.

The decision brief and the findings can be put on the pull request, from the workspace that holds
the run: `harness pr publish --run <id> --pr <number>` posts one comment per run (updated when
published again) and uploads the SARIF report to code scanning, through the GitHub CLI (`gh`) or,
with `--transport api`, HTTPS with the token in `GITHUB_TOKEN` (`delivery.publisher.tokenEnv`).
Nothing is decided on the pull request.

The workflow below keeps the bundles on a branch `harness-evidence` that is never merged, and
checks the pull request against the bundle its closure commit names. It has not been run as part
of this repository's CI; it shows the commands, which are covered by
`tests/integration/test_delivery.py` and the `delivery` flow of `scripts/demo_flows.py`.

```yaml
name: approved-change

on:
  pull_request:

permissions:
  contents: read

jobs:
  verify-approval:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          fetch-depth: 0
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - name: Install the harness
        run: python -m pip install governed-agent-harness  # pin the version you use
      - name: Verify that the merged tree is an approved ChangeSet
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          git fetch --no-tags origin harness-evidence
          run_id=$(git log --format='%(trailers:key=Harness-Run,valueonly)' "origin/${BASE_REF}..HEAD" | sed -n '/./{p;q}')
          git show "origin/harness-evidence:${run_id}.tar.gz" > approval.tar.gz
          harness verify --bundle approval.tar.gz
          harness verify-approval --base "origin/${BASE_REF}" --head HEAD \
            --bundle approval.tar.gz --no-workspace
```

## Evidence only CI can produce

A criterion that only CI, staging or a device lab can verify declares
`verification: {level: L4, deferred: "CI job e2e"}`. The run leaves a pending item `D-<criterion>`
bound to the ChangeSet digest and the closure commit; a job closes it with its report:

```bash
harness evidence attach --run "$RUN" --item D-ac_e2e --file e2e-junit.xml
```

JUnit, SARIF and a CI status JSON (`state`, `sha`) are read; evidence about another commit is
refused. See [verification ladder](verification-ladder.md#deferred-verification).

## Limits to keep in mind

- **State is local to the runner.** The SQLite state, the event chain and the artifacts live in
  the job workspace's `.harness/`, or, under `runtime.stateDir: auto` (written by `harness init`),
  in `$HARNESS_STATE_DIR` or the runner's data directory: set `HARNESS_STATE_DIR` to a path you
  upload with the job's artifacts. The run stops at `DECISION`; a person can download the `harness-state` artifact and
  decide locally with `harness gate decide`, or the pattern can run on a persistent (self-hosted)
  workspace. A hosted runner cannot keep a run open between jobs; what it can check without the
  state is an evidence bundle (`harness verify --bundle`, `harness verify-approval`).
- **Not a sandbox.** In CI the harness runs the project's commands with the job's permissions and
  network. Keep `permissions` minimal and do not expose secrets to the job.
- **Human decisions are never automated.** A pipeline must not call `harness gate decide` on
  behalf of a person; `requireHumanDecision` is a locked policy.
- **Token and cost metrics** stay `NOT_AVAILABLE` unless the agent provider reports them.
