# Brownfield example: pallets/itsdangerous 2.2.0

Reproduces the brownfield case of the thesis on a real third-party repository. See
[docs/guides/brownfield.md](../../docs/guides/brownfield.md) for the walkthrough and the outputs.

```bash
PYTHON=python3.12 examples/brownfield-itsdangerous/reproduce.sh <harness wheel or requirement> [workdir]
```

- `task.yaml`: hardens `itsdangerous.encoding.base64_decode` to reject non-ASCII input and adds a
  test (`patch` mode, two owned files).
- `reproduce.sh`: downloads the GitHub tag archive, verifies its SHA-256
  (`7b0c6d41…37f2b`), prepares a virtual environment without `freezegun`, runs the task, installs
  the declared dependency, runs the task again and records the decision, checking every exit
  code: `run start` 6 (pytest stops while collecting, so no test runs and the shared baseline
  failure does not count as pre-existing, #190), `findings list` 0, a new `run start` 4 (298 tests
  pass, gate `PASSED`; the first run's changes went to quarantine, so `run continue` would verify
  an empty ChangeSet), `APPROVE` 0. Up to 1.0.0 the second step was `run continue` 4; on `v0.8.0`,
  `APPROVE` was then refused with 5 and the run closed with `APPROVE_EXCEPTION`.

Network access to github.com and to a Python package index is required. On Windows, run it from
Git Bash; the work directory may be given as `/c/...` or `C:/...`.
