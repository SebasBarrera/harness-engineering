# Connecting an external agent

The harness does not ship a native integration with Claude Code, Codex or any model API. An agent
is connected through the **command provider**: a program that the harness launches in the
`IMPLEMENTATION` phase, sends the task and plan as JSON on standard input, and reads one JSON result
from standard output. Anything that can be wrapped in such a program can be governed.

The provider only proposes a change. It never evaluates or approves it: verification, independent
review, the gate and the human decision stay with the harness.

## Register the provider

In `.harness/project.yaml`:

```yaml
agentProvider: local_wrapper          # default provider for `harness run start`
agentProviders:
  local_wrapper:
    kind: command
    command: [python, examples/structured-command-agent.py]
    model: deterministic-example      # label recorded on the agent invocation
```

`harness run start --task <id> --provider local_wrapper` selects it explicitly. The first element
of `command` must be allowed by a `process.execute` capability (the Python profile allows
`python`, the Node.js profile `npm` and `node`); add a grant in `capabilities.grants` for anything
else. The process runs with `cwd` set to the workspace, without a shell, with the
`runtime.commandTimeoutSeconds` timeout and the `runtime.maxOutputBytes` output bound.

## The protocol

**Request (stdin)**, one JSON document:

```json
{
  "schemaVersion": "1.0",
  "task": { "task_id": "…", "title": "…", "intent": "…", "requirements": [], "acceptance_criteria": [],
            "constraints": [], "implementation": {"mode": "…", "patches": []}, "metadata": {} },
  "plan": { "…": "the recorded plan: steps, capabilities, expected evidence" }
}
```

The task and plan are serialized with the model field names (snake_case). When the project has
governed memory that applies to the run, the request also carries `context`, with the selected
records and their digest (`{"records": [...], "digest": "sha256:…"}`); a run without memory sends
no `context` key. See [memory and retrospective decisions](memory.md).

When `runtime.providerFeedback` is true (written by `harness init`), the attempt that follows a
failed `VERIFICATION` (see `runtime.verificationCorrections`) or a `REQUEST_CHANGES` decision also
carries `feedback`: why the previous attempt was not accepted. Its contract is
`schemas/v1/provider-feedback.schema.json`; unlike the task and plan it uses camelCase names.
This one was sent by the fixture agent of `tests/integration/test_verification_corrections.py`,
whose first attempt left `apply_discount` unchanged (abridged: a `python.ruff` finding and the
start of the pytest output are left out):

```json
"feedback": {
  "schemaVersion": "1.0",
  "attempt": 2,
  "trigger": "VERIFICATION_FAILED",
  "changeSetDigest": "sha256:70dccacdd7bf806dd7f315a6386f90f966f1a431557e70030fed13a832c576d8",
  "gate": {"gateId": "verification", "gateEvaluationId": null, "status": "FAILED",
           "reasonCodes": ["python.pytest_FAILED"]},
  "findings": [
    {"ruleId": "python.pytest.failed", "severity": "HIGH", "validatorId": "python.pytest",
     "location": {"path": null, "startLine": null, "endLine": null},
     "message": "python.pytest failed with exit code 1"},
    {"ruleId": "agent.unsupported-claim", "severity": "MEDIUM", "validatorId": "harness.claim-check",
     "location": null,
     "message": "The agent reported PASSED ('Implemented the threshold discount') but verification failed: python.pytest FAILED"}
  ],
  "omittedFindings": 0,
  "validators": [{"validatorId": "python.pytest", "status": "FAILED", "exitCode": 1,
                  "summary": "python.pytest failed with exit code 1",
                  "stdout": "…E       assert 100 == 90\nE        +  where 100 = apply_discount(100, 100, 0.1)\n\ntests/test_pricing.py:4: AssertionError\n…FAILED tests/test_pricing.py::test_at_threshold - assert 100 == 90\n1 failed in 0.01s\n",
                  "stderr": "", "stdoutTruncated": false, "stderrTruncated": false}],
  "decision": null
}
```

| Field | Content |
|---|---|
| `attempt` | Number of the `IMPLEMENTATION` attempt that receives the request (2 or more). |
| `trigger` | `VERIFICATION_FAILED` (automatic correction) or `CHANGES_REQUESTED` (human decision). |
| `gate` | For `VERIFICATION_FAILED`, the verification status and one reason code per failing mandatory validator (`<validatorId>_<STATUS>`); for `CHANGES_REQUESTED`, the `delivery_candidate` gate the person decided on, with its id, status and reason codes. |
| `findings` | Up to 20 findings, most severe first: rule, severity, validator, location and message (at most 1,000 characters). `omittedFindings` counts the rest. |
| `validators` | Each failing mandatory validator with the end of its redacted stdout and stderr: at most 4,000 characters per stream and 16,000 for all streams together; `*Truncated` says that something was cut. |
| `decision` | After `REQUEST_CHANGES`: the decision, the rationale (at most 4,000 characters) and the actor id; otherwise `null`. |

The first attempt of a run, and every request of a project without the setting, has no
`feedback` key, so the request and its prompt digest are the same as before.

**Response (stdout)**: the whole standard output must be one JSON object, and the process must
exit with 0:

```json
{"status": "PASSED", "summary": "Structured patches applied"}
```

| Field | Values |
|---|---|
| `status` | `PASSED`, `FAILED` or `BLOCKED` |
| `summary` | optional text recorded on the agent invocation |
| `usage` | optional object with any of `inputTokens`, `outputTokens`, `reasoningTokens` (non-negative integers) and `costUsd` (non-negative number), as reported by the agent |

When `usage` is present it is stored as a `ResourceUsage` record of quality `REPORTED` and summed
into the metrics `tokens.*` and `cost.usd`. The harness records what the provider reports and
never estimates it; an empty object, an unknown field or a negative value is a `PROTOCOL_ERROR`.

Anything else on standard output (logs, progress) breaks the protocol and the invocation is
recorded as `ERROR` (`PROTOCOL_ERROR`). Write diagnostics to standard error; both streams are stored
as redacted artifacts. A non-zero exit code is recorded with the process status without parsing
the output.

A call that does not pass with a transient cause (a network, overload, rate-limit or usage-limit
message on standard error or in the JSON result) is repeated with the same request when
`runtime.providerRetries` is set; a process killed at the timeout is not. If the agent answers
`PASSED` and the verification of its change fails, the run records an `agent.unsupported-claim`
finding. See [provider feedback loop](../reference/configuration.md#provider-feedback-loop).

## Declare what the agent may change

The ChangeSet is what a person approves, so declare the files the task owns:

- `implementation.mode: patch` with `patches`: the patch paths are owned (the bundled example
  agent applies exactly these patches).
- Otherwise, `metadata.ownedPaths`: a list of exact relative paths.
- With neither, every file that changes during the run is part of the ChangeSet.

```yaml
metadata:
  ownedPaths: [src/pricing/__init__.py, tests/test_pricing.py]
```

## Verified example

`examples/structured-command-agent.py` applies the task's structured patches and answers
`{"status": "PASSED", …}`. Registered as above on the greenfield project of the
[greenfield guide](greenfield.md), `harness run start --task task_discount_rule` exited with 4,
the gate was `PASSED` (`ALL_MANDATORY_VALIDATIONS_PASSED`), the run recorded one agent invocation
with the model label, and the ChangeSet contained the two owned files.

## Wrapping an agent CLI (template, not tested against a live agent)

The following wrapper shows the shape of an adapter for an agent CLI such as Claude Code or Codex.
It has **not** been run against a real agent in this repository; adapt the command line to the
agent's documented non-interactive mode and review its permission settings. Remember that the
harness is not a sandbox: the agent runs with your user's permissions.

```python
#!/usr/bin/env python3
"""Adapter: harness command-provider protocol -> an agent CLI (template)."""
import json
import os
import shlex
import subprocess
import sys

request = json.load(sys.stdin)
task = request["task"]
criteria = "\n".join(f"- {item['text']}" for item in task["acceptance_criteria"])
owned = task.get("metadata", {}).get("ownedPaths", [])
prompt = (
    f"{task['title']}\n\n{task['intent']}\n\nAcceptance criteria:\n{criteria}\n\n"
    f"Only modify these files: {', '.join(owned)}. Do not commit."
)
# AGENT_COMMAND is the agent's non-interactive invocation, for example the documented
# 'print' or 'exec' mode of your agent CLI; the prompt is appended as the last argument.
command = shlex.split(os.environ["AGENT_COMMAND"]) + [prompt]
result = subprocess.run(command, capture_output=True, text=True)
sys.stderr.write(result.stdout + result.stderr)          # keep stdout for the protocol only
status = "PASSED" if result.returncode == 0 else "FAILED"
print(json.dumps({"status": status, "summary": f"agent exited with {result.returncode}"}))
```

Register it with `command: [python, tools/agent_adapter.py]` and pass `AGENT_COMMAND` in the
environment of the harness process. If the agent CLI reports its token and cost usage, forward it
in `usage`; otherwise the metrics `tokens.*` and `cost.usd` stay `NOT_AVAILABLE`
(see [metrics](../metrics.md)).

## External plugins

Separately from agent providers, `src/governed_harness/plugins` defines a versioned one-request,
one-response JSON protocol for out-of-process extensions (`harness plugins list` shows the built-in
extensions and the protocol version `1.0`). It is implemented and tested as a contract; the
built-in profiles use internal validators, so external plugins are not yet part of the main run.
