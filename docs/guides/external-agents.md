# Connecting an external agent

An agent is connected in one of two ways:

- a **built-in adapter** (since 1.1) for Claude Code, Codex, Gemini CLI or Aider: the harness runs
  the agent CLI itself in its non-interactive mode, renders the task as a prompt and reads the
  CLI's own output, including the tokens and cost it reports (see
  [built-in adapters](#built-in-adapters));
- the **command provider**: a program that the harness launches in the `IMPLEMENTATION` phase,
  sends the task and plan as JSON on standard input, and reads one JSON result from standard
  output. Anything that can be wrapped in such a program can be governed.

The harness calls no model API itself.

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
else, or, under `governance.phaseCapabilities` (which `harness init` writes and where
`capabilities.grants` only narrows the profiles), in `capabilities.extend`. A provider command no
grant allows is refused before it starts: under `governance.phaseCapabilities` the run records a
`HIGH` `capabilities.command-denied` finding and stops `BLOCKED` (exit 6), and
`harness config validate` warns about it beforehand. The process runs with `cwd` set to the
workspace, without a shell, with the `runtime.commandTimeoutSeconds` timeout and the
`runtime.maxOutputBytes` output bound.

## Built-in adapters

```yaml
agentProvider: claude
agentProviders:
  claude:
    kind: claude-code              # or codex, gemini-cli, aider
    model: claude-sonnet-4-5       # passed as --model and recorded on the invocation
    passEnv: [ANTHROPIC_API_KEY, HTTPS_PROXY]
capabilities:
  grants: []
  extend:                          # added to the profiles with or without phaseCapabilities
    - capability: process.execute
      scope: [claude]
```

| `kind` | Runs (before `args`) | Prompt | Reads |
|---|---|---|---|
| `claude-code` | `claude -p --output-format json --permission-mode acceptEdits` | standard input | `result`, `is_error`, `subtype`, `session_id`, `usage` (input tokens are input, cache-creation and cache-read tokens), `total_cost_usd` |
| `codex` | `codex exec --json --full-auto --skip-git-repo-check -` | standard input | JSON events: the last `agent_message`, `turn.completed` usage (no cost), `turn.failed` or `error`, the `thread_id` |
| `gemini-cli` | `gemini --output-format json --approval-mode auto_edit --prompt <a pointer to standard input>` | standard input | `response`, `error`, `stats.models.*.tokens` (`prompt`, `candidates`, `thoughts`; no cost) |
| `aider` | `aider --yes-always --no-auto-commits --no-dirty-commits --no-stream --no-pretty --no-gitignore --no-check-update --analytics-disable`, history files in the null device, `--message <prompt>` | command line (recorded as `<prompt>`) | the `Tokens: … sent, … received. Cost: $… message, $… session.` lines (counts above 1,000 are printed rounded) |

`command` replaces the executable (for example `[npx, -y, "@anthropic-ai/claude-code"]` or a
wrapper script); `args` adds arguments after the adapter's own, such as `--allowedTools` for
Claude Code. The first element of the command needs a `process.execute` grant, as for any
provider. The adapters run under the same sandbox, capability grant, timeout, output bound and
transient-failure retries as a command provider, and a failed call's summary carries the end of
its standard error.

The prompt holds the task (title, intent, requirements, acceptance criteria, constraints, the
files it owns or the patches it describes), the plan, the governed memory selected for the run, the
`feedback` block of a correction attempt (gate, findings with their location, the end of the
failing validators' output and a reviewer's rationale) and, under `provenance.selfReport`, the
request for a self-report. The prompt digest recorded on the invocation is the digest of the same
request a command provider would receive.

The command lines follow each CLI's documentation. They are covered by tests with fake CLIs that
print each documented output format (`tests/integration/test_native_adapters.py`); they have not
been run against the live agents in this repository. Codex has its own sandbox: under
`runtime.agentSandbox: enforce` on macOS a sandbox inside `sandbox-exec` may be refused by the
operating system (not verified); `args: [--sandbox, danger-full-access]` leaves confinement to the
harness's sandbox.

## Environment and secrets

The process runner gives a provider only `PATH`, `HOME`, `SYSTEMROOT`, `TMPDIR`, `TEMP`, `LANG`
and `LC_ALL`. A provider declares what else it receives:

```yaml
agentProviders:
  claude:
    kind: claude-code
    passEnv: [HTTPS_PROXY, SSL_CERT_FILE]     # passed as they are, when set
    env:
      ANTHROPIC_API_KEY: {fromEnv: HARNESS_ANTHROPIC_KEY}   # read when the provider starts
      CLAUDE_CODE_MAX_OUTPUT_TOKENS: "16000"                 # a literal value
```

A `fromEnv` variable that is not set stops `IMPLEMENTATION` (`BLOCKED`) before the provider starts,
instead of running the agent without its credentials. Only the names are recorded (evidence and an
`agent.environment.applied` event). Every value that comes from the environment (`passEnv` and
`fromEnv`, of every configured provider and of the project validators' `passEnv`) of at least 8
characters is redacted from every stored artifact and from the agent's summary as
`<REDACTED_ENV>`; shorter values are not, so that a value such as `1` does not erase every
occurrence in an output. With `runtime.extendedRedaction` the stored artifacts are also cleaned of
model-API keys, Slack tokens, JSON Web Tokens and passwords in URLs.

## Write confinement

A project created by `harness init` has `runtime.agentSandbox: enforce`: on macOS the provider runs
under `sandbox-exec` and on Linux under `bwrap`, and the operating system denies its writes outside
the workspace, `$TMPDIR` and `runtime.sandboxWritePaths`. Reads, network and process execution are
not restricted. On a host without a mechanism (Linux without bubblewrap, Windows) `IMPLEMENTATION`
is `BLOCKED` with a `sandbox.unavailable` finding instead of running the agent unconfined. Details,
the default write paths and the recorded evidence: [agent sandbox](../reference/configuration.md#agent-sandbox).

With a provider that writes outside the workspace, `harness run start` on macOS exited with 6, the
run was `FAILED` in `IMPLEMENTATION`, the file was not created and the run had the finding
`sandbox.write-denied MEDIUM The agent sandbox denied a write: /Users/<user>/harness-cli-escape.txt`.

Claude Code 2.1.287 was run directly (not through the harness) under the profile the harness builds
from the `init` defaults, on macOS 15 with the user's own login:
`sandbox-exec -p <profile> claude -p "Create a file hello.txt containing hi in the current
directory, then reply done." --model claude-haiku-4-5-20251001 --output-format json
--permission-mode acceptEdits --tools Write,Read --no-session-persistence` exited with 0
(`"subtype":"success"`, `"result":"done"`) and created `hello.txt`. Asked to write to a file in the
home directory (with `--add-dir` on the home directory, so that Claude Code's own permission check
allowed it), its `Write` tool failed with `EPERM: operation not permitted` and no file was created.

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
| `usage` | optional object with any of `inputTokens`, `outputTokens`, `reasoningTokens`, `cacheTokens` (non-negative integers; `cacheTokens`, since 1.1, is the part of `inputTokens` read from or written to a prompt cache) and `costUsd` (non-negative number), as reported by the agent |

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

### Request kinds (protocol 1.1)

With the agent-results settings that `harness init` writes, the provider also receives
read-only requests of kind `clarify`, `acceptance`, `plan`, `review` and (since #55)
`locate`, and the implement request may carry `gate`, `permissions`, `routing`, `budget`,
`contextFiles`, `lessons`, `acceptanceTests`, `locations` and `attachments`. An adapter that speaks only the 1.0 protocol treats every request as an
implementation: on a read-only request it changes the workspace, the harness undoes the change
and blocks the phase. Read `kind` (absent means `implement`), answer the read-only kinds with a
`result` object and apply `routing.flags` to your CLI if you want the router's model and effort.
The kinds, results and keys are described in [better agent results](agent-results.md).

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
harness is not a full sandbox: with `agentSandbox: enforce` the agent cannot write outside the
allowed paths, but it reads, connects and runs programs with your user's permissions.

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
# The provider receives it through passEnv (see below).
command = shlex.split(os.environ["AGENT_COMMAND"]) + [prompt]
result = subprocess.run(command, capture_output=True, text=True)
sys.stderr.write(result.stdout + result.stderr)          # keep stdout for the protocol only
status = "PASSED" if result.returncode == 0 else "FAILED"
print(json.dumps({"status": status, "summary": f"agent exited with {result.returncode}"}))
```

Register it and declare the variables it reads; before 1.1 the guide said to set
`AGENT_COMMAND` in the environment of the harness process, but the provider only received
`PATH`, `HOME`, `LANG` and `TMPDIR`, and the wrapper failed with `KeyError: 'AGENT_COMMAND'`:

```yaml
agentProviders:
  wrapped:
    kind: command
    command: [python, tools/agent_adapter.py]
    passEnv: [AGENT_COMMAND, ANTHROPIC_API_KEY]
```

For Claude Code, Codex, Gemini CLI and Aider a [built-in adapter](#built-in-adapters) replaces
the wrapper. If the agent CLI reports its token and cost usage, forward it in `usage`; otherwise the
metrics `tokens.*` and `cost.usd` stay `NOT_AVAILABLE` (see [metrics](../metrics.md)). Under
`provenance.selfReport` the request also carries `selfReport`, and the response may answer
`selfReport` with `assumptions`, `alternativesDiscarded` (lists of text), `lowConfidenceAreas` and
`unrequestedChanges` (lists of `{path, description}`); it is stored as `REPORTED` data and a
malformed one is recorded with its `problems`, never as a protocol error.

## External plugins

Separately from agent providers, `src/governed_harness/plugins` defines a versioned one-request,
one-response JSON protocol for out-of-process extensions (`harness plugins list` shows the built-in
extensions and the protocol version `1.0`). It is implemented and tested as a contract; the
built-in profiles use internal validators, so external plugins are not yet part of the main run.
