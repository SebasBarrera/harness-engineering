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
  the declared dependency, resumes and records the decisions, checking every exit code:
  `run start` 6, `run continue` 4, `APPROVE` 5, `APPROVE_EXCEPTION` 0.

Network access to github.com and to a Python package index is required.
