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
no `context` key.

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
