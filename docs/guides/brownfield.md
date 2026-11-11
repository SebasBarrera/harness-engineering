# Brownfield guide: governing a change in an existing repository

The flow is the same as in the [greenfield guide](greenfield.md). What changes in an existing
repository is the starting point: history, conventions, dependencies you did not install and,
often, a baseline that is already broken. This guide reproduces the brownfield case of the thesis
on a real third-party project, **`pallets/itsdangerous` 2.2.0**, and shows every output.

The whole case is scripted in
[`examples/brownfield-itsdangerous/reproduce.sh`](https://github.com/SebasBarrera/harness-engineering/tree/develop/examples/brownfield-itsdangerous):

```bash
PYTHON=python3.12 examples/brownfield-itsdangerous/reproduce.sh <harness wheel or requirement> /tmp/bf
```

## What is different in a brownfield repository

1. **Start from a clean tree, or know what the baseline contains.** The baseline is captured when
   the run starts. Uncommitted changes that already exist are part of the baseline and stay out of
   the ChangeSet. Only files the run owns count: the patch paths in `patch` mode, or
   `metadata.ownedPaths` for an external agent. If neither is declared, every file that changes
   during the run is included. `harness init` keeps the tree clean: in a Git repository it writes
   its `.harness/` entry to `.git/info/exclude`, which Git reads like a `.gitignore` but which is
   not part of the tree, so the first run's dirty-tree check (`environment.dirtyTree`) finds
   nothing caused by init (issue #86). An entry already in `.gitignore` is left as it is;
   `harness init --ignore-file gitignore` writes the `.gitignore` instead (a change to commit).
2. **Install the project's test dependencies where the harness runs.** Validators run the
   project's own commands (`python -m pytest`, `npm test`) with the interpreter or tools found on
   `PATH`; under `toolchain.interpreter: auto` (written by `harness init` since 1.1) the Python
   validators use the project's `.venv` or `venv`, `uv run --no-sync` or `poetry run` instead. A
   project whose commands differ declares its own validators under `toolchain.validators` (see
   [project toolchain](../reference/configuration.md#project-toolchain)). A missing test
   dependency is not detected in advance: the tests fail to collect.
3. **The baseline may already be broken.** `harness init` writes `environment.baseline: report`
   and `verification.differential: true`: the mandatory validators also run on the baseline, a
   failure the change shares with it is pre-existing (`LOW` finding `differential.preexisting`)
   and does not block, and only introduced failures do. A pre-existing failure can hide that the
   tests never ran (below, pytest stops while collecting), so read the evidence before deciding.
4. **Large repositories.** Since 1.1 `harness init` writes `workspace.snapshot: git`,
   `workspace.baseline: manifest` and `workspace.snapshotCache: true`: files that `.gitignore`
   excludes are never read or stored, the baseline is a manifest of digests, and unchanged files
   are not hashed again. See [large repositories](../reference/configuration.md#large-repositories)
   for the measurement on 10,001 files.
5. **Respect the existing conventions.** Detection is read-only: the weights of the marker files
   found are added up to a confidence of at most 1.0 and reported with the files as evidence. Lock
   files (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`) add to the Node.js confidence, but the
   profile always runs `npm`; its `ambiguousPackageManager` policy is declared and not enforced. If
   the project uses another package manager, treat the Node.js results with care.

## The case: itsdangerous 2.2.0

### Obtain and verify the source

The thesis used the tag archive published by the project on GitHub. Its SHA-256 matches the value
recorded in the thesis (the sdist on PyPI is a different file with a different digest):

```console
$ curl -sSfL -o itsdangerous-2.2.0.tar.gz \
    https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz
$ shasum -a 256 itsdangerous-2.2.0.tar.gz
7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b  itsdangerous-2.2.0.tar.gz
```

Treat third-party code as untrusted: the harness is not a sandbox. Before running it, the thesis
checked the archive for absolute or traversal paths, symbolic links (there are none), install
scripts and network or subprocess calls in code and tests.

### Environment

A virtual environment with the harness and `pytest==8.1.1` (the version pinned in
`requirements/tests.txt`), the project installed in editable mode, and **without** `freezegun`,
which the project also declares in `requirements/tests.txt`:

```bash
python3.12 -m venv venv
venv/bin/python -m pip install <harness wheel> "pytest==8.1.1"
venv/bin/python -m pip install -e ./itsdangerous-2.2.0 --no-deps
cd itsdangerous-2.2.0 && git init -q && git add . && git commit -qm "itsdangerous 2.2.0"
```

### Detection and task

```console
$ harness init --path .
$ harness inspect --path .
{
  "workspace": "…/itsdangerous-2.2.0",
  "detections": [
    {
      "profileId": "python_default",
      "technology": "python",
      "confidence": 1.0,
      "evidence": [
        "pyproject.toml",
        "tox.ini"
      ],
      "warnings": []
    }
  ]
}
```

The task (`examples/brownfield-itsdangerous/task.yaml`) hardens `base64_decode` so that non-ASCII
input raises `BadData` instead of being silently dropped, and adds one test. It is a `patch` task,
so the ChangeSet owns exactly `src/itsdangerous/encoding.py` and
`tests/test_itsdangerous/test_encoding.py`.

### First attempt: the baseline is broken

```console
$ harness run start --path . --task task_itsdangerous_base64_non_ascii
$ echo $?
4
```

The run waits in `DECISION` and the gate is **`PASSED`**, although `freezegun` is missing. Two keys
that `harness init` writes compare the change with the baseline: `environment.baseline: report`
runs the mandatory validators on a scratch copy of the baseline in `DISCOVERY`, and
`verification.differential: true` marks the failures the change shares with the baseline as
pre-existing, so they do not block (see the
[configuration reference](../reference/configuration.md)). `harness findings list --path . --run
<run>` returns:

| Severity | Rule | Message (abridged) |
|---|---|---|
| `LOW` | `environment.baseline-failing` | The baseline already fails before the change: python.pytest FAILED |
| `HIGH` | `python.pytest.failed` | python.pytest failed with exit code 2 |
| `HIGH` | `python.pytest.test-error` | tests/test_itsdangerous/test_timed.py error |
| `HIGH` | `python.pytest.test-error` | tests/test_itsdangerous/test_url_safe.py error |
| `INFO` | `tests.coverage-unavailable` | Coverage of the changed lines could not be measured |
| `LOW` | `differential.preexisting` | 2 problem(s) reported by python.pytest also fail on the baseline (pre-existing) |

The pytest output stored as evidence shows why, and that the failing files are not part of the
ChangeSet:

```text
____________ ERROR collecting tests/test_itsdangerous/test_timed.py ____________
E   ModuleNotFoundError: No module named 'freezegun'
__________ ERROR collecting tests/test_itsdangerous/test_url_safe.py ___________
E   ModuleNotFoundError: No module named 'freezegun'
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
```

### Before deciding: repair the environment and run the suite

The gate passed, but pytest stopped while collecting, so **no test ran**, the added one included;
only the `INFO` coverage finding says so. Install the declared dependency (the environment, not the
ChangeSet) and run the project's suite on the change:

```console
$ python -m pip install "freezegun==1.4.0"
$ python -m pytest -q -p no:cacheprovider
298 passed in 0.98s
```

`harness run continue` does not help here: the run already waits in `DECISION` and the ChangeSet
did not change, so it exits 4 again without verifying again. Approve with the digest of the
ChangeSet:

```console
$ harness gate decide --path . --run <run> --decision APPROVE \
    --change-set-digest <digest> --actor human.reviewer \
    --rationale "The baseline failures (freezegun missing) are pre-existing; with freezegun installed the project's suite passes on the same ChangeSet"
$ echo $?
0
```

Final state: status `PASSED`, 137 events, event chain valid, ChangeSet of 2 files with digest
`sha256:5702c3865fad03eb8ba9c449a42269e03afd2b93794bac34be4625f252b70a07` (recorded on Windows with
Git Bash and Python 3.12.10).

If a failure is not shared with the baseline, or `verification.differential` is off, the gate is
`FAILED`: repair what fails and `run continue`, or decide `APPROVE_EXCEPTION` with a rationale (an
ordinary `APPROVE` is refused with exit 5).

### Difference with earlier figures and the thesis

The thesis evaluated version 0.8.0. There the run stopped in `VERIFICATION` (`run start` exit 6,
one `HIGH` finding `python.pytest.failed`), installing `freezegun` and `run continue` gave exit 4
and `298 passed`, and the gate consolidated every validation of the digest across attempts, so the
failure of attempt 1 kept counting (issue #2): the plain `APPROVE` was refused with exit 5 and the
run was closed with `APPROVE_EXCEPTION` (46 events, ChangeSet digest
`sha256:e775aa2dc82793fea6af88f63e6b01564e29398f7e0c0b563c416383e8723d46`). From 0.9.0 the latest
attempt of each validator counted, and the same steps closed with a plain `APPROVE` (46 events). The
baseline comparison that `harness init` now writes (#7, #52, #55) moves the decision before any
repair, as above. The number of events is higher because `VERIFICATION` runs more checks (for
example the requirement traceability of `base64_decode_rejects_non_ascii`). The ChangeSet digest
recorded above differs from the earlier one; the cause has not been established. The tag `v0.8.0`
keeps the evaluated behavior.

## Checklist for your own repository

- [ ] Commit or stash unrelated work before `harness run start`.
- [ ] Install the project's test dependencies in the harness environment
      (`pip install -r requirements/tests.txt`, `npm ci`, …).
- [ ] Run the project's test command once by hand to know whether the baseline passes.
- [ ] Declare the files the task may change (`patch` paths or `metadata.ownedPaths`).
- [ ] If the gate passed over pre-existing failures, check in the evidence that the tests ran; if
      they did not, repair the environment and run the suite before you approve.
- [ ] If the gate failed, fix what fails and `run continue`; if it cannot be fixed, decide
      knowingly between `APPROVE_EXCEPTION` (with the reason) and `REJECT`.
