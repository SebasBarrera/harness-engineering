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
   during the run is included.
2. **Install the project's test dependencies where the harness runs.** Validators run the
   project's own commands (`python -m pytest`, `npm test`) with the interpreter or tools found on
   `PATH`. A missing test dependency is not detected in advance: the tests fail to collect.
3. **The baseline may already be broken.** The harness does not yet tell pre-existing failures from
   introduced ones (the `PREEXISTING_ERROR` status exists but is never assigned, issue #7). The
   evidence shows which files failed, so you can see that they are outside the ChangeSet.
4. **Respect the existing conventions.** Detection is read-only: the weights of the marker files
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
6
```

The run stopped in `VERIFICATION` with status `FAILED`. `harness findings list --path . --run <run>`
returns one finding: severity `HIGH`, rule `python.pytest.failed`, message `python.pytest failed with
exit code 2`. The pytest output stored as evidence shows
why, and that the failing files are not part of the ChangeSet:

```text
____________ ERROR collecting tests/test_itsdangerous/test_timed.py ____________
E   ModuleNotFoundError: No module named 'freezegun'
__________ ERROR collecting tests/test_itsdangerous/test_url_safe.py ___________
E   ModuleNotFoundError: No module named 'freezegun'
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
```

ChangeSet digest at this point:
`sha256:e775aa2dc82793fea6af88f63e6b01564e29398f7e0c0b563c416383e8723d46`.

### Two ways out

**A. Fix the baseline and resume.** Install the declared dependency and continue the same run; the
ChangeSet is untouched:

```console
$ python -m pip install "freezegun==1.4.0"
$ harness run continue --path . --run <run>
$ echo $?
4
```

The retry ran the project's suite: **`298 passed`**. The ChangeSet still has 2 files and the same
digest (`sha256:e775aa2d…23d46`).

However, the gate is **`FAILED`**, with reasons `python.pytest_FAILED` and the blocking HIGH
finding of the first attempt. The gate consolidates every validation of the current digest across
attempts, so a failure caused by the environment in attempt 1 keeps counting after attempt 2
passes. This is issue #2 (`needs-decision`), kept unchanged because the thesis measured this
behavior.

**B. Decide with a justified exception.** An ordinary approval over a gate that did not pass is
refused; an exception with a rationale is accepted and stays visible in the trace:

```console
$ harness gate decide --path . --run <run> --decision APPROVE \
    --change-set-digest sha256:e775aa2d…23d46 --actor human.reviewer --rationale "Plain approval"
$ echo $?
5
$ harness gate decide --path . --run <run> --decision APPROVE_EXCEPTION \
    --change-set-digest sha256:e775aa2d…23d46 --actor human.reviewer \
    --rationale "Attempt 1 failed only because freezegun was missing; all project tests pass with the same ChangeSet"
$ echo $?
0
```

Final state: status `PASSED`, 46 events, event chain valid. These figures match the thesis
(298 tests after installing the dependency, invariant ChangeSet digest, closure by justified
exception, 46 events).

## Checklist for your own repository

- [ ] Commit or stash unrelated work before `harness run start`.
- [ ] Install the project's test dependencies in the harness environment
      (`pip install -r requirements/tests.txt`, `npm ci`, …).
- [ ] Run the project's test command once by hand to know whether the baseline passes.
- [ ] Declare the files the task may change (`patch` paths or `metadata.ownedPaths`).
- [ ] If the baseline is broken, fix it and `run continue`, then decide knowingly between
      `APPROVE_EXCEPTION` (with the reason) and `REJECT`.
