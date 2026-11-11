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
  the declared dependency, runs the project's suite and records the decision, checking every exit
  code: `run start` 4 (the failures are shared with the baseline, so the gate passes),
  `findings list` 0, `pytest` 0, `APPROVE` 0. Up to 1.0.0 `run start` exited 6 and `run continue`
  4; on `v0.8.0`, `APPROVE` was then refused with 5 and the run closed with `APPROVE_EXCEPTION`.

Network access to github.com and to a Python package index is required. On Windows, run it from
Git Bash; the work directory may be given as `/c/...` or `C:/...`.
